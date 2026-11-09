"""开发者家目录隐私扫描：单一 token 边界 + 单一文本扫描入口。

识别规则按完整路径 token 判定（Users/home 大小写不敏感、1+ 连续分隔符、URI 前缀）：
- macOS/Linux 用户家目录（Users/home 前缀）；
- Linux root 家目录 token（不得从另一条路径中间误起匹配）；
- Windows 盘符 + Users 家目录（正/反斜杠、系统 profile 白名单精确匹配）。

所有来源（index blob / tree blob / worktree 文件 / 软链文本 / commit message / UTF-16 字节）
统一汇入 ``scan_text_privacy_leaks``；字节先经 ``decode_candidate_bytes`` 做
BOM/UTF-16/UTF-8 判定，未知编码 fail-closed 而非静默跳过。Git 对象读取复用
``git_ops.batch_cat_file`` 的严格协议解析，读取前按路径后缀与大小预过滤。

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
BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff", ".woff2",
    ".ttf", ".otf", ".ttc", ".eot", ".zip", ".gz", ".bz2", ".xz", ".7z",
    ".pdf", ".wasm", ".so", ".dylib", ".dll", ".exe", ".bin", ".mp4",
    ".webm", ".mov", ".pyc", ".class", ".jar", ".db", ".sqlite",
})

# 单个 blob 超过该阈值时不再读入内存：显式 fail-closed，避免超时后无诊断阻断。
MAX_SCAN_BLOB_BYTES = 8 * 1024 * 1024

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
    """字节 → 可扫描文本；BOM/UTF-16/UTF-8 判定，未知编码 fail-closed（不跳过）。

    - BOM 显式指定编码；
    - 无 BOM 且 UTF-8 失败时，按 NUL 交错位置试探 UTF-16 LE/BE；
    - 仍无法确定则抛 PrivacyScanError，绝不 ``errors='ignore'`` 静默放行。
    """
    if data.startswith((_BOM_UTF16_LE, _BOM_UTF16_BE)):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError as error:
            raise PrivacyScanError(f"{label}：UTF-16 BOM 解码失败（{error}）") from error
    if data.startswith(_BOM_UTF8):
        data = data[len(_BOM_UTF8):]
    # NUL 交错是 UTF-16 的强信号，必须优先于 UTF-8：NUL 本身是合法 UTF-8 字符，
    # 否则 UTF-16 编码的家目录会被 NUL 隔断而漏报。
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
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        utf8_error = error
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
    """复用 git_ops 严格解析器的分批读取；读取前做大小预过滤，超大对象显式失败。"""
    try:
        sizes = git_ops.batch_object_sizes(oids, root=root)
    except TaskDataError as error:
        raise PrivacyScanError(f"Git 对象大小读取失败：{error}") from error
    oversized = [(oid, size) for oid, size in sizes.items() if size > MAX_SCAN_BLOB_BYTES]
    if oversized:
        detail = ", ".join(f"{oid[:12]} ({size} B)" for oid, size in oversized[:5])
        raise PrivacyScanError(
            f"以下对象超过 {MAX_SCAN_BLOB_BYTES} B，无法安全扫描：{detail}；"
            "请拆分或改放 docs/archive/、.scratch/ 等豁免目录"
        )
    try:
        blobs = git_ops.batch_cat_file(oids, root=root)
    except TaskDataError as error:
        raise PrivacyScanError(f"Git 对象读取失败：{error}") from error
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
        violations.extend(scan_candidate_bytes(blobs[sha], label=rel))
    return violations


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


def _scan_worktree_path(root: Path, rel: str) -> list[str]:
    """未暂存文件扫描：lstat 不跟随软链，软链只扫 os.readlink 文本。"""
    path = root / rel
    try:
        info = path.lstat()
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
    if info.st_size > MAX_SCAN_BLOB_BYTES:
        raise PrivacyScanError(f"待提交文件过大（{info.st_size} B）：{rel}")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise PrivacyScanError(f"无法读取待提交文件：{rel}（{error}）") from error
    return scan_candidate_bytes(data, label=rel)


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
