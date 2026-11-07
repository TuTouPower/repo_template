"""Developer home-path privacy scanner for review reports, task docs, and commits.

识别规则按完整路径 token 判定，不依赖用户名白名单：
- macOS/Linux 用户家目录（`Users`/`home` 前缀）；
- Linux root 家目录 token（不得从另一条路径中间误起匹配）；
- Windows 正斜杠与反斜杠 `Users` 家目录。

非个人环境的固定路径需求只能通过 `FIXED_PATH_EXCEPTIONS` 显式列出有依据的完整路径，
该策略同时用于 review、finish 与 pre-commit 全部门禁。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterable

# 归档与草稿不属于已跟踪交付契约。
PRIVACY_EXCLUDE_PREFIXES = ("docs/archive/", ".scratch/")

# 非个人身份的 Windows 系统配置目录；不属于开发者家目录。
WINDOWS_SYSTEM_PROFILES = frozenset({"Public", "Default", "Default User", "All Users"})

# 确有非个人环境固定路径需求时，在此列出有环境依据的完整路径前缀。
FIXED_PATH_EXCEPTIONS: tuple[str, ...] = ()

BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff", ".woff2",
    ".ttf", ".otf", ".ttc", ".eot", ".zip", ".gz", ".bz2", ".xz", ".7z",
    ".pdf", ".wasm", ".so", ".dylib", ".dll", ".exe", ".bin", ".mp4",
    ".webm", ".mov", ".pyc", ".class", ".jar", ".db", ".sqlite",
})

# 前导边界避免从另一条路径中间起匹配：Linux root 家目录不得作为其它路径的子串被命中。
# token 以运行时拼接构造，避免本模块源码自身出现连续家目录路径被自身门禁命中。
_ROOT_TOKEN = "/" + "root"
_POSIX_HOME = re.compile(r"(?<![\w.\-/])/(?:Users|home)/([A-Za-z0-9._-]+)")
_ROOT_HOME = re.compile(
    r"(?<![\w.\-/])" + re.escape(_ROOT_TOKEN) + r"(?![A-Za-z0-9._-])"
)
_WIN_FORWARD = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:/Users/([A-Za-z0-9._% -]+)")
_WIN_BACKWARD = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\Users\\([A-Za-z0-9._% -]+)")


class PrivacyScanError(Exception):
    """无法确定候选内容，隐私扫描 fail-closed。"""


def _is_exempt(token: str) -> bool:
    return any(
        token == prefix or token.startswith(prefix + "/") or token.startswith(prefix + "\\")
        for prefix in FIXED_PATH_EXCEPTIONS
    )


def find_developer_path_leaks(text: str) -> list[str]:
    """返回 *text* 中出现的开发者家目录路径 token（确定性、去重、保持出现顺序）。"""
    spans: list[tuple[int, int, str]] = []
    for match in _POSIX_HOME.finditer(text):
        spans.append((match.start(), match.end(), match.group(0)))
    for match in _ROOT_HOME.finditer(text):
        spans.append((match.start(), match.end(), match.group(0)))
    for pattern in (_WIN_FORWARD, _WIN_BACKWARD):
        for match in pattern.finditer(text):
            if match.group(1).strip() in WINDOWS_SYSTEM_PROFILES:
                continue
            spans.append((match.start(), match.end(), match.group(0)))
    # 宽匹配优先：Windows 正斜杠路径内含 POSIX 形子串时只报告一次。
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


def decode_candidate_text(path: Path, data: bytes) -> str | None:
    """解码用于扫描的文本；仅已知二进制后缀直接跳过。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        if path.suffix.lower() in BINARY_SUFFIXES:
            return None
        return data.decode("utf-8", errors="ignore")


def scan_text_privacy_leaks(text: str, *, label: str = "") -> list[str]:
    """逐行扫描 *text*，返回 ``label:line: leak`` 列表。"""
    findings: list[str] = []
    prefix = f"{label}:" if label else ""
    for line_no, line in enumerate(text.splitlines(), 1):
        for leak in find_developer_path_leaks(line):
            findings.append(f"{prefix}{line_no}: {leak}")
    return findings


def is_privacy_candidate(rel: str) -> bool:
    return bool(rel) and not rel.startswith(PRIVACY_EXCLUDE_PREFIXES)


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


def read_blobs(root: Path, shas: list[str]) -> dict[str, bytes | None]:
    """批量读取 blob 内容；缺失对象返回 None。读取失败抛 PrivacyScanError。"""
    unique = list(dict.fromkeys(shas))
    if not unique:
        return {}
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "cat-file", "--batch"],
            input=("\n".join(unique) + "\n").encode("utf-8"),
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PrivacyScanError(f"git cat-file --batch 执行失败：{error}") from error
    if result.returncode != 0:
        raise PrivacyScanError(
            f"git cat-file --batch 失败：{result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    data = result.stdout
    blobs: dict[str, bytes | None] = {}
    cursor = 0
    for sha in unique:
        newline = data.find(b"\n", cursor)
        if newline < 0:
            raise PrivacyScanError("git cat-file --batch 输出不完整")
        header = data[cursor:newline].decode("utf-8", errors="replace").split()
        cursor = newline + 1
        if len(header) == 2 and header[1] == "missing":
            blobs[sha] = None
            continue
        if len(header) != 3 or not header[2].isdigit():
            raise PrivacyScanError(f"git cat-file --batch 头非法：{header!r}")
        size = int(header[2])
        blobs[sha] = data[cursor:cursor + size]
        cursor += size + 1
    return blobs


def _scan_blob_map(
    root: Path, entries: Iterable[tuple[str, str, str]],
) -> list[str]:
    """entries 为 (rel, mode, sha)；跳过 gitlink 与非候选路径，无法读取内容时 fail-closed。"""
    selected = [
        (rel, mode, sha)
        for rel, mode, sha in entries
        if mode != "160000" and is_privacy_candidate(rel)
    ]
    blobs = read_blobs(root, [sha for _rel, _mode, sha in selected])
    violations: list[str] = []
    for rel, _mode, sha in selected:
        data = blobs.get(sha)
        if data is None:
            raise PrivacyScanError(f"无法读取已跟踪文件内容：{rel}")
        text = decode_candidate_text(Path(rel), data)
        if text is None:
            continue
        violations.extend(scan_text_privacy_leaks(text, label=rel))
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


def scan_git_index_privacy(
    root: Path, *, extra_worktree_paths: Iterable[str] = (),
) -> list[str]:
    """扫描整个 Git index 的非归档文本 blob，并附带尚未暂存但将纳入的文件（A07）。"""
    violations = _scan_blob_map(root, _index_entries(root))
    for rel in sorted({p for p in extra_worktree_paths if is_privacy_candidate(p)}):
        path = root / rel
        if not path.is_file():
            raise PrivacyScanError(f"待提交文件不存在：{rel}")
        try:
            data = path.read_bytes()
        except OSError as error:
            raise PrivacyScanError(f"无法读取待提交文件：{rel}（{error}）") from error
        text = decode_candidate_text(path, data)
        if text is None:
            continue
        violations.extend(scan_text_privacy_leaks(text, label=rel))
    return violations


def scan_git_tree_privacy(root: Path, ref: str = "HEAD") -> list[str]:
    """扫描指定提交快照的非归档文本 blob，用于提交后核对实际快照（A07）。"""
    return _scan_blob_map(root, _tree_entries(root, ref))
