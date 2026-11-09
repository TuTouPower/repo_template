"""开发者家目录隐私扫描：单一 token 边界 + 单一文本扫描入口。

识别规则按完整路径 token 判定（Users/home 大小写不敏感、1+ 连续分隔符、URI 前缀）：
- macOS/Linux 用户家目录（Users/home 前缀）；
- Linux root 家目录 token（不得从另一条路径中间误起匹配）；
- Windows 盘符 + Users 家目录（正/反斜杠、系统 profile 白名单精确匹配）。

所有来源（index blob / tree blob / worktree 文件 / 软链文本 / commit message /
新增行字节 / UTF-16/UTF-32 字节）统一汇入 ``scan_text_privacy_leaks``；字节先经
``decode_candidate_bytes`` 做 BOM/UTF-16/UTF-32/UTF-8/GBK 判定，未知编码
fail-closed 而非静默跳过。Git 对象读取复用 ``git_ops.batch_cat_file`` 的严格协议
解析，读取前按路径后缀、gitlink 模式与大小预过滤；超大对象逐个顺序读取扫描，
不再让单个大对象阻断整体；commit 引入内容按新增行变更集扫描，不翻历史存量。

非个人环境的固定路径需求只能通过 ``FIXED_PATH_EXCEPTIONS`` 显式列出有依据的完整路径，
该策略同时用于 review、finish 与 pre-commit 全部门禁。
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Iterable

from repo_task import git_ops
from repo_task.context import TaskDataError

# 归档与草稿不属于已跟踪交付契约。
PRIVACY_EXCLUDE_PREFIXES = ("docs/archive/", ".scratch/")

# 非个人身份的 Windows 系统配置目录；不属于开发者家目录。
WINDOWS_SYSTEM_PROFILES = frozenset({"Public", "Default", "Default User", "All Users"})
# 用户名按单 token 判定，多词 profile 取其首词精确比较（不吞后续词、不整体豁免）。
WINDOWS_SYSTEM_PROFILE_LEADS = frozenset(
    profile.split()[0] for profile in WINDOWS_SYSTEM_PROFILES
)

# 确有非个人环境固定路径需求时，在此列出有环境依据的完整路径前缀。
FIXED_PATH_EXCEPTIONS: tuple[str, ...] = ()

# 已知二进制格式：读取前即跳过，不进入 cat-file，避免把二进制内容当文本扫描。
# office/压缩包/音视频/目标文件等常见二进制后缀在此列；未知后缀的二进制由
# _decode_or_skip_binary 的 NUL 嗅探兜底（嗅探只跳过含 NUL 的不可解码字节，
# ASCII 兼容文本不受影响）。
BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
    ".woff", ".woff2", ".ttf", ".otf", ".ttc", ".eot",
    ".zip", ".gz", ".bz2", ".xz", ".7z", ".tar", ".tgz", ".rar", ".cab",
    ".dmg", ".iso",
    ".pdf",
    ".wasm", ".so", ".dylib", ".dll", ".exe", ".bin", ".msi",
    ".o", ".a", ".obj", ".lib", ".pdb",
    ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mid", ".midi",
    ".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v", ".ogv", ".flv", ".3gp",
    ".heic", ".heif",
    ".pyc", ".pyo", ".class", ".jar", ".db", ".sqlite", ".sqlite3",
    ".psd", ".ai",
    ".doc", ".xls", ".ppt", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
})

# 单个 blob 超过该阈值时不再批量读入：改为逐个顺序读取并扫描，避免单个超大
# 对象阻断整体扫描。只有超过硬上限的对象才显式 fail-closed。
MAX_SCAN_BLOB_BYTES = 8 * 1024 * 1024
MAX_SCAN_BLOB_HARD_CAP = 256 * 1024 * 1024

# ---- 单一 token 边界定义 ----
# 前导边界只排除 ASCII 路径字符：\w 在 str 模式含汉字，会把汉字紧邻的家目录路径漏报。
_LEADING_BOUNDARY = r"(?<![A-Za-z0-9._/\\-])"
# 1+ 连续路径分隔符：兼容前置 //、C://Users//、C:\\Users\\。
_SEP = r"[\\/]+"
_FILE_URI = r"(?:file://(?:localhost)?|file:)?"
# 用户名截止空白与路径分隔符（POSIX/Windows 用户名都是单 token，DS L227）。
_USER_CHARS = r"[\w.%-]+"

_POSIX_HOME = re.compile(
    _LEADING_BOUNDARY + _FILE_URI + r"/+(?:Users|home)" + _SEP + r"(" + _USER_CHARS + r")",
    re.IGNORECASE,
)
_ROOT_HOME = re.compile(
    _LEADING_BOUNDARY + _FILE_URI + r"/+root(?![A-Za-z0-9._-])",
    re.IGNORECASE,
)
_WIN_PATH = re.compile(
    _LEADING_BOUNDARY + r"[A-Za-z]:" + _SEP + r"Users" + _SEP + r"(?P<user>" + _USER_CHARS + r")",
    re.IGNORECASE,
)

_BOM_UTF8 = b"\xef\xbb\xbf"
_BOM_UTF16_LE = b"\xff\xfe"
_BOM_UTF16_BE = b"\xfe\xff"
_BOM_UTF32_LE = b"\xff\xfe\x00\x00"
_BOM_UTF32_BE = b"\x00\x00\xfe\xff"
# 空树：根 commit 无第一父提交时，变更集扫描以空树为基。
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_HUNK_RE = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class PrivacyScanError(Exception):
    """无法确定候选内容，隐私扫描 fail-closed。"""


def _is_exempt(token: str) -> bool:
    return any(
        token == prefix or token.startswith(prefix + "/") or token.startswith(prefix + "\\")
        for prefix in FIXED_PATH_EXCEPTIONS
    )


def is_privacy_candidate(rel: str) -> bool:
    """路径是否属于扫描范围；模板自带 tests/ 目录同样是候选，绝不豁免。"""
    return bool(rel) and not rel.startswith(PRIVACY_EXCLUDE_PREFIXES)


def is_binary_candidate(rel: str) -> bool:
    """已知二进制后缀在读取前预过滤，避免无意义地 cat-file 大二进制对象。"""
    return Path(rel).suffix.lower() in BINARY_SUFFIXES


def find_developer_path_leaks(text: str) -> list[str]:
    """返回 *text* 中出现的开发者家目录路径 token（确定性、去重、保持出现顺序）。"""
    win_matches = list(_WIN_PATH.finditer(text))
    # 全部 Windows 命中（含被豁免的系统 profile）都用于排除 POSIX/root 重叠子串。
    win_spans = [(m.start(), m.end()) for m in win_matches]

    spans: list[tuple[int, int, str]] = []
    for match in win_matches:
        if match.group("user") in WINDOWS_SYSTEM_PROFILE_LEADS:
            continue
        spans.append((match.start(), match.end(), match.group(0)))

    for pattern in (_POSIX_HOME, _ROOT_HOME):
        for match in pattern.finditer(text):
            m_start, m_end = match.start(), match.end()
            if any(ws <= m_start and m_end <= we for ws, we in win_spans):
                continue
            spans.append((m_start, m_end, match.group(0)))

    spans.sort(key=lambda item: (item[0], item[1]))

    # 宽匹配优先：外层路径包含内层子串时只保留外层。
    leaks = [
        value
        for start, end, value in spans
        if not any(
            other_start <= start and end <= other_end and (other_start, other_end) != (start, end)
            for other_start, other_end, _ in spans
        )
    ]
    seen: set[str] = set()
    result: list[str] = []
    for leak in leaks:
        if leak in seen or _is_exempt(leak):
            continue
        seen.add(leak)
        result.append(leak)
    return result


def _is_plausible_text(text: str) -> bool:
    if not text:
        return True
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    return printable / len(text) >= 0.9


def decode_candidate_bytes(data: bytes, *, label: str) -> str:
    """字节 → 可扫描文本；BOM/UTF-16/UTF-32/UTF-8/GBK 判定，未知编码 fail-closed（不跳过）。

    - BOM 显式指定编码；UTF-32 BOM 以 4 字节优先判定（LE32 前缀与 LE16 相同），
      解码后必须过可读性 gate，否则 fail-closed（防 BOM 伪装绕过）；
    - 无 BOM 且 UTF-8 失败时，按 NUL 交错位置试探 UTF-16 LE/BE，再按 4 字节
      周期试探 UTF-32 LE/BE（NUL 本身是合法 UTF-8 字符，必须先于 UTF-8 试探，
      否则宽字符编码的家目录会被 NUL 隔断而漏报）；
    - ASCII 兼容的 GBK 作最后一次尝试（中文语境常见），同样必须过可读性
      gate，二进制垃圾解码后多半不可读，仍 fail-closed；
    - 仍无法确定则抛 PrivacyScanError，绝不 ``errors='ignore'`` 静默放行。
    """
    if data.startswith((_BOM_UTF32_LE, _BOM_UTF32_BE)):
        try:
            text = data.decode("utf-32")
        except UnicodeDecodeError as error:
            raise PrivacyScanError(f"{label}：UTF-32 BOM 解码失败（{error}）") from error
        if not _is_plausible_text(text):
            raise PrivacyScanError(f"{label}：UTF-32 BOM 解码内容非可读文本，隐私扫描 fail-closed")
        return text
    if data.startswith((_BOM_UTF16_LE, _BOM_UTF16_BE)):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError as error:
            raise PrivacyScanError(f"{label}：UTF-16 BOM 解码失败（{error}）") from error
    if data.startswith(_BOM_UTF8):
        data = data[len(_BOM_UTF8):]
    # NUL 交错是宽字符编码的强信号，必须优先于 UTF-8：NUL 本身是合法 UTF-8 字符，
    # 否则宽字符编码的家目录会被 NUL 隔断而漏报。
    if len(data) >= 4 and len(data) % 2 == 0:
        nul_even = data[0::2].count(0)
        nul_odd = data[1::2].count(0)
        if nul_even + nul_odd >= len(data) // 4:
            order = ("utf-16-le", "utf-16-be") if nul_odd >= nul_even else ("utf-16-be", "utf-16-le")
            for encoding in order:
                try:
                    candidate = data.decode(encoding)
                except UnicodeDecodeError:
                    continue
                if _is_plausible_text(candidate):
                    return candidate
    if len(data) >= 8 and len(data) % 4 == 0 and data.count(0) * 2 >= len(data):
        # 无 BOM 的 UTF-32：ASCII 字符呈 4 字节周期，NUL 占比不低于一半。
        for encoding in ("utf-32-le", "utf-32-be"):
            try:
                candidate = data.decode(encoding)
            except UnicodeDecodeError:
                continue
            if _is_plausible_text(candidate):
                return candidate
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        utf8_error = error
    if b"\x00" not in data:
        # GBK 与 UTF-8 同为 ASCII 兼容，ASCII 字节不会被误释；含 NUL 的不可
        # 解码字节交由调用方的二进制嗅探处理，这里不尝试。
        try:
            candidate = data.decode("gbk")
        except UnicodeDecodeError:
            pass
        else:
            if _is_plausible_text(candidate):
                return candidate
    raise PrivacyScanError(
        f"{label}：编码无法判定（非 UTF-8/UTF-16），隐私扫描 fail-closed"
    ) from utf8_error


def scan_text_privacy_leaks(text: str, *, label: str = "") -> list[str]:
    """单一文本扫描入口：逐行扫描任意文本（文件内容 / commit message / 软链文本 / review 报告）。"""
    findings: list[str] = []
    prefix = f"{label}:" if label else ""
    for line_no, line in enumerate(text.splitlines(), 1):
        for leak in find_developer_path_leaks(line):
            findings.append(f"{prefix}{line_no}: {leak}")
    return findings


def scan_candidate_bytes(data: bytes, *, label: str) -> list[str]:
    """字节候选统一入口：fail-closed 解码后汇入单一文本扫描入口。"""
    return scan_text_privacy_leaks(decode_candidate_bytes(data, label=label), label=label)


def _decode_or_skip_binary(data: bytes, *, label: str) -> list[str]:
    """字节候选解码：后缀未覆盖的二进制由 NUL 嗅探跳过，其余 fail-closed。

    能解码（UTF-8/UTF-16/UTF-32/GBK）的都正常扫描；解码失败且含 NUL 的视为
    二进制内容跳过（真实宽字符文本走上面的解码分支，不会落到这里）；解码
    失败且无 NUL 的仍抛 PrivacyScanError，避免静默放行未知编码。
    """
    try:
        return scan_candidate_bytes(data, label=label)
    except PrivacyScanError:
        if b"\x00" in data:
            return []
        raise


def _git(root: Path, *args: str, binary: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=not binary,
            encoding=None if binary else "utf-8",
            errors=None if binary else "replace",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PrivacyScanError(f"git {' '.join(args)} 执行失败：{error}") from error


def _read_blobs(root: Path, oids: list[str]) -> dict[str, bytes]:
    """复用 git_ops 严格解析器的分批读取；超大对象逐个顺序读，不再整体失败。

    - ``<= MAX_SCAN_BLOB_BYTES``：批量一次读入（原行为）；
    - 介于阈值与硬上限之间：逐个顺序读取并返回，内存占用以单个对象为界；
    - 超过硬上限：显式 fail-closed（防 GB 级对象 OOM），调用方不得吞掉。
    """
    try:
        sizes = git_ops.batch_object_sizes(oids, root=root)
    except TaskDataError as error:
        raise PrivacyScanError(f"Git 对象大小读取失败：{error}") from error
    huge = [(oid, size) for oid, size in sizes.items() if size > MAX_SCAN_BLOB_HARD_CAP]
    if huge:
        detail = ", ".join(f"{oid[:12]} ({size} B)" for oid, size in huge[:5])
        raise PrivacyScanError(
            f"以下对象超过 {MAX_SCAN_BLOB_HARD_CAP} B，无法安全扫描：{detail}；"
            "请拆分或改放 docs/archive/、.scratch/ 等豁免目录"
        )
    normal = [oid for oid in oids if sizes[oid] <= MAX_SCAN_BLOB_BYTES]
    large = [oid for oid in oids if sizes[oid] > MAX_SCAN_BLOB_BYTES]
    try:
        blobs = git_ops.batch_cat_file(normal, root=root) if normal else {}
    except TaskDataError as error:
        raise PrivacyScanError(f"Git 对象读取失败：{error}") from error
    for oid in large:
        try:
            single = git_ops.batch_cat_file([oid], root=root)
        except TaskDataError as error:
            raise PrivacyScanError(f"Git 对象读取失败：{error}") from error
        blobs.update(single)
    missing = [oid for oid, content in blobs.items() if content is None]
    if missing:
        raise PrivacyScanError(f"无法读取已跟踪文件内容（对象缺失）：{', '.join(missing[:5])}")
    return {oid: content for oid, content in blobs.items() if content is not None}


def _scan_blob_map(
    root: Path, entries: Iterable[tuple[str, str, str]],
) -> list[str]:
    """entries 为 (rel, mode, sha)；跳过 gitlink、非候选路径与二进制后缀，无法读取内容时 fail-closed。"""
    selected = [
        (rel, mode, sha)
        for rel, mode, sha in entries
        if mode != "160000" and is_privacy_candidate(rel) and not is_binary_candidate(rel)
    ]
    if not selected:
        return []
    oids = list(dict.fromkeys(sha for _rel, _mode, sha in selected))
    blobs = _read_blobs(root, oids)
    violations: list[str] = []
    for rel, _mode, sha in selected:
        violations.extend(_decode_or_skip_binary(blobs[sha], label=rel))
    return violations


def scan_staged_blob_candidates(root: Path, rels: Iterable[str]) -> list[str]:
    """扫描已暂存候选的 index blob：gitlink/非候选/二进制预过滤，不在 index 的
    视为已暂存删除直接跳过（删文件不引入新内容）。"""
    wanted = set(rels)
    if not wanted:
        return []
    entries = [
        (rel, mode, sha)
        for rel, mode, sha in _index_entries(root)
        if rel in wanted
    ]
    return _scan_blob_map(root, entries)


def _index_entries(root: Path) -> list[tuple[str, str, str]]:
    result = _git(root, "ls-files", "-s", "-z")
    if result.returncode != 0:
        raise PrivacyScanError(f"git ls-files 失败：{result.stderr.strip()}")
    entries = []
    for item in result.stdout.split("\0"):
        if not item:
            continue
        meta, rel = item.split("\t", 1)
        mode, sha, _stage = meta.split()
        entries.append((rel, mode, sha))
    return entries


def _tree_entries(root: Path, ref: str) -> list[tuple[str, str, str]]:
    result = _git(root, "ls-tree", "-r", "-z", "--end-of-options", ref)
    if result.returncode != 0:
        raise PrivacyScanError(f"无法读取 {ref} tree：{result.stderr.strip()}")
    entries = []
    for item in result.stdout.split("\0"):
        if not item:
            continue
        meta, rel = item.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind != "blob":
            continue
        entries.append((rel, mode, sha))
    return entries


def _scan_worktree_path(root: Path, rel: str, *, missing_ok: bool = False) -> list[str]:
    """未暂存文件扫描：lstat 不跟随软链，软链只扫 os.readlink 文本。

    missing_ok=True 时 lstat 报文件不存在返回空（已删除，删文件不引入新内容）；
    否则 fail-closed（调用方传入了本应存在的显式路径）。
    """
    path = root / rel
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        if missing_ok:
            return []
        raise PrivacyScanError(f"待提交文件无法 lstat：{rel}（{error}）") from error
    except OSError as error:
        raise PrivacyScanError(f"待提交文件无法 lstat：{rel}（{error}）") from error
    if stat.S_ISLNK(info.st_mode):
        try:
            text = os.readlink(path)
        except OSError as error:
            raise PrivacyScanError(f"无法读取软链文本：{rel}（{error}）") from error
        return scan_text_privacy_leaks(text, label=rel)
    if not stat.S_ISREG(info.st_mode):
        raise PrivacyScanError(f"待提交路径不是普通文件：{rel}")
    if info.st_size > MAX_SCAN_BLOB_HARD_CAP:
        raise PrivacyScanError(f"待提交文件过大（{info.st_size} B）：{rel}")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise PrivacyScanError(f"无法读取待提交文件：{rel}（{error}）") from error
    return _decode_or_skip_binary(data, label=rel)


def scan_worktree_candidates(root: Path, rels: Iterable[str]) -> list[str]:
    """扫描 worktree 候选文件（finish 提交候选）：软链只扫链接文本，不跟随目标。

    - 非候选路径与二进制后缀跳过，不读内容；
    - lstat 报「文件不存在」视为已删除跳过（删文件不引入新内容），这是
      ``git ls-files --cached`` 含已删除条目时的正常情况，不得阻断；
    - 其它 lstat/读取失败仍 fail-closed。
    """
    violations: list[str] = []
    for rel in rels:
        if not is_privacy_candidate(rel) or is_binary_candidate(rel):
            continue
        violations.extend(_scan_worktree_path(root, rel, missing_ok=True))
    return violations


def scan_git_index_privacy(
    root: Path, *, extra_worktree_paths: Iterable[str] = (),
) -> list[str]:
    """扫描整个 Git index 的非归档文本 blob，并附带尚未暂存但将纳入的文件（A07）。"""
    violations = _scan_blob_map(root, _index_entries(root))
    for rel in sorted({p for p in extra_worktree_paths
                       if is_privacy_candidate(p) and not is_binary_candidate(p)}):
        violations.extend(_scan_worktree_path(root, rel))
    return violations


def scan_git_tree_privacy(root: Path, ref: str = "HEAD") -> list[str]:
    """扫描指定提交快照的非归档文本 blob，用于提交后核对实际快照（A07）。"""
    return _scan_blob_map(root, _tree_entries(root, ref))


def _first_parent_or_empty(root: Path, ref: str) -> str:
    """ref 的第一父提交；根 commit 无父提交时以空树为基。"""
    result = _git(root, "rev-parse", f"{ref}^")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return _EMPTY_TREE_SHA


def _changed_rels(root: Path, base: str, ref: str) -> list[str]:
    result = _git(
        root, "diff", "--name-only", "-z", "--diff-filter=ACMRT",
        "--end-of-options", base, ref, "--",
    )
    if result.returncode != 0:
        raise PrivacyScanError(f"无法列出 {base[:12]}..{ref[:12]} 变更：{result.stderr.strip()}")
    return [item for item in result.stdout.split("\0") if item]


def _tip_blob_shas(root: Path, ref: str, rels: list[str]) -> dict[str, tuple[str, str]]:
    """tip 快照中 rel → (mode, sha)；只取 blob，gitlink 等非 blob 不在其中。"""
    result = _git(root, "ls-tree", "-r", "-z", "--end-of-options", ref, "--", *rels)
    if result.returncode != 0:
        raise PrivacyScanError(f"无法读取 {ref} tree：{result.stderr.strip()}")
    found: dict[str, tuple[str, str]] = {}
    for item in result.stdout.split("\0"):
        if not item:
            continue
        meta, rel = item.split("\t", 1)
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob":
            found[rel] = (parts[0], parts[2])
    return found


def _scan_added_lines(rel: str, numbered: list[tuple[int, bytes]]) -> list[str]:
    """扫描新增行字节：逐行 fail-closed 解码后用 token 规则找泄漏。

    ``git diff --text`` 把二进制也强制文本化，解码失败且含 NUL 的行视为
    二进制残片跳过；解码失败且无 NUL 的仍 fail-closed，避免静默放行未知编码。
    宽字符编码的新增行保持 NUL 交错，逐行解码启发式依然有效。
    """
    violations: list[str] = []
    for lineno, raw in numbered:
        line = raw[:-1] if raw.endswith(b"\r") else raw
        try:
            text = decode_candidate_bytes(line, label=rel)
        except PrivacyScanError:
            if b"\x00" in line:
                continue
            raise
        for leak in find_developer_path_leaks(text):
            violations.append(f"{rel}:{lineno}: {leak}")
    return violations


def scan_commit_introduced_privacy(root: Path, ref: str) -> list[str]:
    """扫描 ref 相对第一父提交新增的行（变更集扫描），fail-closed。

    只看本次引入的新增内容，不翻历史存量：历史泄漏不阻断本次收尾/合入，
    由显式审计入口（scan_git_tree_privacy）另行覆盖。二进制后缀与 gitlink
    预过滤，不读内容；新增行逐行解码扫描，行号为新文件真实行号。
    """
    base = _first_parent_or_empty(root, ref)
    rels = [
        rel for rel in _changed_rels(root, base, ref)
        if is_privacy_candidate(rel) and not is_binary_candidate(rel)
    ]
    if not rels:
        return []
    shas = _tip_blob_shas(root, ref, rels)
    selected = [(rel, mode, sha) for rel, (mode, sha) in shas.items() if mode != "160000"]
    if not selected:
        return []
    try:
        sizes = git_ops.batch_object_sizes(
            [sha for _rel, _mode, sha in selected], root=root
        )
    except TaskDataError as error:
        raise PrivacyScanError(f"Git 对象大小读取失败：{error}") from error
    violations: list[str] = []
    for rel, _mode, sha in selected:
        if sizes.get(sha, 0) > MAX_SCAN_BLOB_HARD_CAP:
            raise PrivacyScanError(
                f"{rel} 新增内容所在对象超过 {MAX_SCAN_BLOB_HARD_CAP} B，无法安全扫描"
            )
        result = _git(
            root, "diff", "--text", "--no-color", "--no-ext-diff", "-U0",
            "--end-of-options", base, ref, "--", rel, binary=True,
        )
        if result.returncode != 0:
            raise PrivacyScanError(
                f"无法比对 {rel}（{base[:12]}..{ref[:12]}）："
                f"{result.stderr.decode('utf-8', errors='replace').strip()}"
            )
        numbered: list[tuple[int, bytes]] = []
        lineno = 0
        for raw in result.stdout.split(b"\n"):
            if raw.startswith(b"+++ ") or raw.startswith(b"--- "):
                continue
            match = _HUNK_RE.match(raw)
            if match:
                lineno = int(match.group(1))
                continue
            if raw.startswith(b"\\"):
                continue
            if raw.startswith(b"+"):
                if len(raw) > 1:
                    numbered.append((lineno, raw[1:]))
                lineno += 1
        violations.extend(_scan_added_lines(rel, numbered))
    return violations
