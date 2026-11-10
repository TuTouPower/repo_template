"""路径隐私策略：只阻断本次新增文本行，所有交付门禁共用同一比较器。

完整 blob 先解码，再按文本行比较；不解析 Git patch，也不按文件后缀猜二进制。
UTF-8、UTF-16/32、GBK 共用严格解码；Git 标为二进制且无法解码的内容跳过。
全量 index/tree 扫描仅用于显式审计，不参与日常提交门禁。
"""

from __future__ import annotations

import difflib
import os
import re
import stat
import subprocess
import tempfile
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
    _LEADING_BOUNDARY + _FILE_URI + r"/+(?:Users|home)" + _SEP + r"(?P<user>" + _USER_CHARS + r")",
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
# macOS firmlink 数据卷前缀：家目录位于路径中段，只识别完整绝对前缀。
_SYSTEM_DATA_HOME = re.compile(
    _LEADING_BOUNDARY + _FILE_URI + r"/System/Volumes/Data/Users" + _SEP
    + r"(?P<user>" + _USER_CHARS + r")",
    re.IGNORECASE,
)
# UNC 反斜杠形态：\\\\server\\Users\\<user>。不认 //server/Users/<user>
# （与 URL 路径重叠）与盘符相对写法 C:Users\\<user>（无分隔符）。
_UNC_HOME = re.compile(
    _LEADING_BOUNDARY + r"\\\\[^\\/\s]+\\Users\\" + r"(?P<user>" + _USER_CHARS + r")",
    re.IGNORECASE,
)

_BOM_UTF8 = b"\xef\xbb\xbf"
_BOM_UTF16_LE = b"\xff\xfe"
_BOM_UTF16_BE = b"\xfe\xff"
_BOM_UTF32_LE = b"\xff\xfe\x00\x00"
_BOM_UTF32_BE = b"\x00\x00\xfe\xff"
# 空树：根 commit 无第一父提交时，变更集扫描以空树为基。


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


def find_developer_path_leaks(text: str) -> list[str]:
    """返回 *text* 中出现的开发者家目录路径 token（确定性、去重、保持出现顺序）。"""
    win_matches = list(_WIN_PATH.finditer(text)) + list(_UNC_HOME.finditer(text))
    # 全部 Windows 命中（含被豁免的系统 profile）都用于排除 POSIX/root 重叠子串。
    win_spans = [(m.start(), m.end()) for m in win_matches]

    spans: list[tuple[int, int, str]] = []
    for match in win_matches:
        if match.group("user") in WINDOWS_SYSTEM_PROFILE_LEADS:
            continue
        spans.append((match.start(), match.end(), match.group(0)))

    for pattern in (_POSIX_HOME, _ROOT_HOME, _SYSTEM_DATA_HOME):
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
        # 句末路径的标点不属于用户名：上报 token 去掉尾随点（_USER_CHARS 含 .）。
        token = leak.rstrip(".")
        if token in seen or _is_exempt(token):
            continue
        seen.add(token)
        result.append(token)
    return result


def _is_plausible_text(text: str) -> bool:
    if not text:
        return True
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    return printable / len(text) >= 0.9


def _decode_text_candidates(data: bytes, *, label: str) -> list[str]:
    """返回全部可读解码候选；无 BOM 时宽字符与 UTF-8 可同时成立，交由调用方并集扫描。"""
    candidates: list[str] = []

    def add(text: str) -> None:
        if text not in candidates:
            candidates.append(text)

    if data.startswith((_BOM_UTF32_LE, _BOM_UTF32_BE)):
        try:
            text = data.decode("utf-32")
        except UnicodeDecodeError as error:
            raise PrivacyScanError(f"{label}：UTF-32 BOM 解码失败（{error}）") from error
        if not _is_plausible_text(text):
            raise PrivacyScanError(f"{label}：UTF-32 BOM 解码内容非可读文本，隐私扫描 fail-closed")
        return [text]
    if data.startswith((_BOM_UTF16_LE, _BOM_UTF16_BE)):
        try:
            return [data.decode("utf-16")]
        except UnicodeDecodeError as error:
            raise PrivacyScanError(f"{label}：UTF-16 BOM 解码失败（{error}）") from error
    if data.startswith(_BOM_UTF8):
        data = data[len(_BOM_UTF8):]
    # NUL 交错是宽字符编码的强信号，必须优先于 UTF-8：NUL 本身是合法 UTF-8 字符，
    # 否则宽字符编码的家目录会被 NUL 隔断而漏报。不设 NUL 占比门槛：CJK 主导的
    # UTF-16 文本 NUL 很少，能否作文本交由解码结果与可读性检查决定。
    if len(data) >= 2 and len(data) % 2 == 0 and b"\x00" in data:
        nul_even = data[0::2].count(0)
        nul_odd = data[1::2].count(0)
        order = ("utf-16-le", "utf-16-be") if nul_odd >= nul_even else ("utf-16-be", "utf-16-le")
        for encoding in order:
            try:
                candidate = data.decode(encoding)
            except UnicodeDecodeError:
                continue
            if _is_plausible_text(candidate):
                add(candidate)
    if len(data) >= 8 and len(data) % 4 == 0 and data.count(0) * 2 >= len(data):
        # 无 BOM 的 UTF-32：ASCII 字符呈 4 字节周期，NUL 占比不低于一半。
        for encoding in ("utf-32-le", "utf-32-be"):
            try:
                candidate = data.decode(encoding)
            except UnicodeDecodeError:
                continue
            if _is_plausible_text(candidate):
                add(candidate)
    utf8_error: UnicodeDecodeError | None = None
    try:
        utf8 = data.decode("utf-8")
    except UnicodeDecodeError as error:
        utf8_error = error
    else:
        # UTF-8 + 零散 NUL 是合法文本（NUL 本身合法）；与宽字符候选一起扫描。
        if b"\x00" not in data or _is_plausible_text(utf8):
            add(utf8)
    if not candidates and b"\x00" not in data:
        # GBK 与 UTF-8 同为 ASCII 兼容，ASCII 字节不会被误释；含 NUL 的不可
        # 解码字节交由调用方的二进制嗅探处理，这里不尝试。
        try:
            candidate = data.decode("gbk")
        except UnicodeDecodeError:
            pass
        else:
            if _is_plausible_text(candidate):
                add(candidate)
    if not candidates:
        raise PrivacyScanError(
            f"{label}：编码无法判定（非 UTF-8/UTF-16），隐私扫描 fail-closed"
        ) from utf8_error
    return candidates


def decode_candidate_bytes(data: bytes, *, label: str) -> str:
    """字节 → 单个可扫描文本（首个可读候选）；需要并集时用 _decode_text_candidates。"""
    return _decode_text_candidates(data, label=label)[0]


def scan_text_privacy_leaks(text: str, *, label: str = "") -> list[str]:
    """单一文本扫描入口：逐行扫描任意文本（文件内容 / commit message / 软链文本 / review 报告）。"""
    findings: list[str] = []
    prefix = f"{label}:" if label else ""
    for line_no, line in enumerate(text.splitlines(), 1):
        for leak in find_developer_path_leaks(line):
            findings.append(f"{prefix}{line_no}: {leak}")
    return findings


def _text_candidates_or_binary(
    data: bytes, *, label: str, binary: bool = False,
) -> list[str] | None:
    """门禁、审计与候选三入口共用判定：可读文本候选列表，不可读二进制返回 None。"""
    try:
        candidates = _decode_text_candidates(data, label=label)
    except PrivacyScanError:
        if binary or b"\x00" in data:
            return None
        raise
    return candidates or None


def _text_or_binary(data: bytes, *, label: str, binary: bool = False) -> str | None:
    """单候选兼容入口；扫描并集请用 _text_candidates_or_binary。"""
    candidates = _text_candidates_or_binary(data, label=label, binary=binary)
    return candidates[0] if candidates else None


def scan_candidate_bytes(data: bytes, *, label: str) -> list[str]:
    """字节候选统一入口：所有可读解码候选并集扫描，不可读二进制跳过。"""
    candidates = _text_candidates_or_binary(data, label=label)
    if candidates is None:
        return []
    findings: list[str] = []
    for text in candidates:
        findings.extend(scan_text_privacy_leaks(text, label=label))
    return list(dict.fromkeys(findings))


def _git(
    root: Path, *args: str, binary: bool = False, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=not binary,
            encoding=None if binary else "utf-8",
            errors=None if binary else "replace",
            env={**os.environ, **env} if env else None,
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
            "大文件不入库：移到仓库根 local/（已 gitignore，用户私有）或拆分后再提交"
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


def _entry_map(entries: Iterable[tuple[str, str, str]]) -> dict[str, tuple[str, str]]:
    return {rel: (mode, oid) for rel, mode, oid in entries}


def _index_entries(root: Path) -> list[tuple[str, str, str]]:
    result = _git(root, "ls-files", "-s", "-z")
    if result.returncode != 0:
        raise PrivacyScanError(f"git ls-files 失败：{result.stderr.strip()}")
    entries = []
    for item in result.stdout.split("\0"):
        if not item:
            continue
        meta, rel = item.split("\t", 1)
        mode, sha, stage = meta.split()
        if stage != "0":
            raise PrivacyScanError(f"index 含未解决冲突：{rel}")
        entries.append((rel, mode, sha))
    return entries


def _tree_entries(root: Path, ref: str) -> list[tuple[str, str, str]]:
    result = _git(root, "ls-tree", "-r", "-z", "--end-of-options", ref)
    if result.returncode != 0:
        raise PrivacyScanError(f"无法读取 {ref} tree：{result.stderr.strip()}")
    entries = []
    for item in result.stdout.split("\0"):
        if item:
            meta, rel = item.split("\t", 1)
            mode, kind, sha = meta.split()
            entries.append((rel, mode, sha))
    return entries


def _first_parent_or_empty(root: Path, ref: str) -> str:
    result = _git(root, "rev-list", "--parents", "-n", "1", "--end-of-options", ref, "--")
    if result.returncode != 0 or not result.stdout.strip():
        raise PrivacyScanError(f"无法读取 {ref} 父提交：{result.stderr.strip()}")
    parents = result.stdout.split()
    return parents[1] if len(parents) > 1 else _empty_tree(root)


def _empty_tree(root: Path) -> str:
    # hash-object 空输入；按当前仓库对象格式产生空树，兼容 SHA-256 仓库。
    try:
        result = git_ops._git_bytes(["hash-object", "-t", "tree", "--stdin"], input=b"", root=root)
    except TaskDataError as error:
        raise PrivacyScanError(f"无法计算空树：{error}") from error
    if result.returncode != 0:
        raise PrivacyScanError("无法计算空树")
    return result.stdout.decode("ascii").strip()


def _head_or_empty(root: Path) -> str:
    result = _git(root, "rev-parse", "--verify", "HEAD")
    if result.returncode == 0:
        return result.stdout.strip()
    symbolic = _git(root, "symbolic-ref", "-q", "HEAD")
    if symbolic.returncode != 0:
        raise PrivacyScanError(f"无法解析 HEAD：{result.stderr.strip()}")
    exists = _git(root, "show-ref", "--verify", "--quiet", symbolic.stdout.strip())
    if exists.returncode != 1:
        raise PrivacyScanError(f"无法确认未出生分支：{result.stderr.strip()}")
    return _empty_tree(root)


def _diff_name_status(root: Path, base: str, target: str | None) -> dict[str, str]:
    """返回 new_path -> old_path 的 rename 映射；非 rename 不入表。"""
    args = ["diff", "--name-status", "-M", "--no-ext-diff", "-z"]
    if target is None:
        args += ["--cached", "--end-of-options", base]
    else:
        args += ["--end-of-options", base, target]
    result = _git(root, *args, "--")
    if result.returncode != 0:
        raise PrivacyScanError(f"无法读取 Git rename 分类：{result.stderr.strip()}")
    fields = result.stdout.split("\0")
    mapping: dict[str, str] = {}
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status:
            continue
        if status[0] in {"R", "C"}:
            if index + 1 >= len(fields):
                raise PrivacyScanError("Git rename 输出截断")
            old, new = fields[index], fields[index + 1]
            index += 2
            if status[0] == "R":
                mapping[new] = old
        else:
            index += 1
    return mapping


def _binary_changes(root: Path, base: str, target: str | None) -> set[str]:
    args = ["diff", "--numstat", "-M", "--no-ext-diff", "-z"]
    if target is None:
        args += ["--cached", "--end-of-options", base]
    else:
        args += ["--end-of-options", base, target]
    result = _git(root, *args, "--")
    if result.returncode != 0:
        raise PrivacyScanError(f"无法读取 Git 文本/二进制分类：{result.stderr.strip()}")
    binary = set()
    fields = result.stdout.split("\0")
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        parts = record.split("\t")
        if len(parts) < 3:
            continue
        added, removed, path = parts[0], parts[1], parts[2]
        if not path and index + 1 < len(fields):
            index += 2  # rename: 旧路径 + 新路径
            path = fields[index - 1]
        if added == "-" and removed == "-":
            binary.add(path)
    return binary


def _worktree_bytes(root: Path, rel: str) -> bytes | None:
    path = root / rel
    # 只允许仓内词法路径；软链交付只读链接文本。
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise PrivacyScanError(f"候选路径越界：{rel}")
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            return os.fsencode(os.readlink(path))
        if not stat.S_ISREG(info.st_mode):
            raise PrivacyScanError(f"候选路径非普通文件：{rel}")
        if info.st_size > MAX_SCAN_BLOB_HARD_CAP:
            raise PrivacyScanError(f"候选文件过大：{rel}")
        return _normalized_worktree_bytes(root, rel)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise PrivacyScanError(f"无法读取候选 {rel}：{error}") from error


def _normalized_worktree_bytes(root: Path, rel: str) -> bytes:
    """读取工作区文件经 Git clean filter 规范化后的内容，不写入仓库对象库。

    ``hash-object -w`` 落在临时对象目录，随后从同一目录读取；临时目录随函数
    退出删除，避免每次 finish 预扫描在仓库对象库留下 loose object。
    """
    with tempfile.TemporaryDirectory(prefix="repo-task-privacy-") as temp:
        env = {"GIT_OBJECT_DIRECTORY": temp}
        hashed = _git(root, "hash-object", "-w", f"--path={rel}", "--", rel, env=env)
        if hashed.returncode != 0:
            raise PrivacyScanError(f"无法读取候选 Git 规范内容 {rel}：{hashed.stderr.strip()}")
        oid = hashed.stdout.strip()
        blob = _git(root, "cat-file", "blob", oid, binary=True, env=env)
        if blob.returncode != 0:
            detail = blob.stderr.decode("utf-8", "replace").strip()
            raise PrivacyScanError(f"无法读取候选 Git 对象 {rel}：{detail}")
        data = blob.stdout
    if len(data) > MAX_SCAN_BLOB_HARD_CAP:
        raise PrivacyScanError(f"候选文件过大：{rel}")
    return data


def _scan_text_change(before: str, after: str, *, label: str) -> list[str]:
    old_lines, new_lines = before.splitlines(), after.splitlines()
    findings = []
    if old_lines == new_lines:
        return []
    prefix = 0
    while prefix < min(len(old_lines), len(new_lines)) and old_lines[prefix] == new_lines[prefix]:
        prefix += 1
    old_end, new_end = len(old_lines), len(new_lines)
    while old_end > prefix and new_end > prefix and old_lines[old_end - 1] == new_lines[new_end - 1]:
        old_end -= 1
        new_end -= 1
    matcher = difflib.SequenceMatcher(None, old_lines[prefix:old_end], new_lines[prefix:new_end])
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in {"insert", "replace"}:
            for index in range(prefix + j1, prefix + j2):
                findings.extend(
                    f"{label}:{index + 1}: {leak}"
                    for leak in find_developer_path_leaks(new_lines[index])
                )
    return findings


def scan_privacy_changes(
    root: Path, *, base: str | None = None, target: str | None = None,
    worktree_paths: Iterable[str] = (), message: str = "",
) -> list[str]:
    """唯一交付扫描入口：base → index/tree，加显式 worktree 覆盖和提交消息。

    finish 候选、hook、merge staged tree、已落盘 tip 均调用本函数。未修改的
    历史行不阻断；新增/修改行必须通过同一解码与 token 策略。
    """
    base = base if base is not None else _head_or_empty(root)
    before = _entry_map(_tree_entries(root, base))
    after = _entry_map(_tree_entries(root, target) if target is not None else _index_entries(root))
    overrides = {rel: _worktree_bytes(root, rel) for rel in set(worktree_paths) if is_privacy_candidate(rel)}
    renames = _diff_name_status(root, base, target)
    changed = {
        rel for rel in set(before) | set(after) | set(overrides)
        if is_privacy_candidate(rel)
        and (rel in overrides or before.get(rel) != after.get(rel))
    }
    binary = _binary_changes(root, base, target)
    if overrides:
        attrs = _git(root, "check-attr", "-z", "--cached", "diff", "--", *sorted(overrides))
        if attrs.returncode != 0:
            raise PrivacyScanError(f"无法读取候选 Git 属性：{attrs.stderr.strip()}")
        fields = attrs.stdout.rstrip("\0").split("\0")
        for offset in range(0, len(fields), 3):
            path, _attribute, value = fields[offset:offset + 3]
            if value == "unset":
                binary.add(path)

    oids = {
        entry[1] for rel in changed for entry in (before.get(rel), before.get(renames.get(rel, "")), after.get(rel))
        if entry is not None and entry[0] != "160000"
    }
    blobs = _read_blobs(root, sorted(oids)) if oids else {}
    violations = []
    for rel in sorted(changed):
        new = after.get(rel)
        if rel not in overrides and (new is None or new[0] == "160000"):
            continue
        data = overrides[rel] if rel in overrides else blobs[new[1]]
        if data is None:
            continue
        candidates = _text_candidates_or_binary(data, label=rel, binary=rel in binary)
        if not candidates:
            continue
        old = before.get(renames.get(rel, rel))
        original = ""
        if old is not None and old[0] != "160000":
            old_candidates = _text_candidates_or_binary(
                blobs[old[1]], label=rel, binary=rel in binary
            ) or []
            original = old_candidates[0] if old_candidates else ""
        violations.extend(_scan_text_change(original, candidates[0], label=rel))
        for extra in candidates[1:]:
            violations.extend(scan_text_privacy_leaks(extra, label=rel))
    violations.extend(scan_text_privacy_leaks(message, label="commit message"))
    return list(dict.fromkeys(violations))


def scan_commit_introduced_privacy(root: Path, ref: str) -> list[str]:
    return scan_privacy_changes(root, base=_first_parent_or_empty(root, ref), target=ref)


def _is_excluded_path(rel: str, excluded_paths: Iterable[str]) -> bool:
    return any(rel == prefix.rstrip("/") or rel.startswith(prefix.rstrip("/") + "/") for prefix in excluded_paths)


def _audit_entries(
    root: Path, entries: Iterable[tuple[str, str, str]], *, binary: set[str],
    excluded_paths: Iterable[str] = (),
) -> list[str]:
    violations = []
    for rel, mode, oid in entries:
        if mode == "160000" or not is_privacy_candidate(rel) or _is_excluded_path(rel, excluded_paths):
            continue
        data = _read_blobs(root, [oid])[oid]
        candidates = _text_candidates_or_binary(data, label=rel, binary=rel in binary)
        for text in candidates or []:
            violations.extend(scan_text_privacy_leaks(text, label=rel))
    return list(dict.fromkeys(violations))


def scan_git_index_privacy(root: Path, *, extra_worktree_paths: Iterable[str] = ()) -> list[str]:
    """显式全量审计 index；日常门禁使用 scan_privacy_changes。"""
    violations = _audit_entries(root, _index_entries(root), binary=_binary_changes(root, _empty_tree(root), None))
    for rel in extra_worktree_paths:
        if is_privacy_candidate(rel):
            data = _worktree_bytes(root, rel)
            if data is not None:
                candidates = _text_candidates_or_binary(data, label=rel)
                for text in candidates or []:
                    violations.extend(scan_text_privacy_leaks(text, label=rel))
    return list(dict.fromkeys(violations))


def scan_git_tree_privacy(
    root: Path, ref: str = "HEAD", *, excluded_paths: Iterable[str] = (),
) -> list[str]:
    """显式全量审计 tree；不作为历史存量阻断门禁。"""
    return _audit_entries(
        root, _tree_entries(root, ref), binary=_binary_changes(root, _empty_tree(root), ref),
        excluded_paths=excluded_paths,
    )


def cmd_privacy_audit(args) -> None:
    findings = scan_git_tree_privacy(
        args.repo.root, args.ref, excluded_paths=getattr(args, "privacy_excluded", ()),
    )
    for finding in findings:
        print(finding)
    if findings:
        raise SystemExit(1)
    print("privacy-audit=PASS")
