"""Developer home-path privacy scanner for review reports, task docs, and commits."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

POSIX_HOME_PATH = re.compile(r"/(?:Users|home)/([A-Za-z0-9._-]+)/")
ROOT_HOME_PATH = re.compile(r"/root/[A-Za-z0-9._\-/]+")
WINDOWS_HOME_PATHS = (
    re.compile(r"[A-Za-z]:\\Users\\([A-Za-z0-9._% \-]+)\\"),
    re.compile(r"[A-Za-z]:/Users/([A-Za-z0-9._% \-]+)/"),
)
CONTAINER_USERS = frozenset({
    "cbrowser", "appuser", "selkies", "pulse", "root", "runner", "docker",
})
WINDOWS_SYSTEM_PROFILES = frozenset({
    "Public", "Default", "Default User", "All Users",
})
CONTAINER_FIXED_PATH_PREFIXES = ("/root/.config/google-chrome",)
BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff", ".woff2",
    ".ttf", ".otf", ".ttc", ".eot", ".zip", ".gz", ".bz2", ".xz", ".7z",
    ".pdf", ".wasm", ".so", ".dylib", ".dll", ".exe", ".bin", ".mp4",
    ".webm", ".mov", ".pyc", ".class", ".jar", ".db", ".sqlite",
})


def find_developer_path_leaks(text: str) -> list[str]:
    """Return developer home-path occurrences in *text*."""
    spans: list[tuple[int, int, str]] = []
    for match in POSIX_HOME_PATH.finditer(text):
        if match.group(1) not in CONTAINER_USERS:
            spans.append((match.start(), match.end(), match.group(0)))
    for pattern in WINDOWS_HOME_PATHS:
        for match in pattern.finditer(text):
            if (
                match.group(1) not in CONTAINER_USERS
                and match.group(1) not in WINDOWS_SYSTEM_PROFILES
            ):
                spans.append((match.start(), match.end(), match.group(0)))
    leaks = [
        value
        for start, end, value in spans
        if not any(
            other_start <= start and end <= other_end and (other_start, other_end) != (start, end)
            for other_start, other_end, _ in spans
        )
    ]
    for match in ROOT_HOME_PATH.finditer(text):
        token = match.group(0).rstrip("/.,;:)'\"`")
        if not any(
            token == prefix or token.startswith(prefix + "/")
            for prefix in CONTAINER_FIXED_PATH_PREFIXES
        ):
            leaks.append(token)
    return leaks


def decode_candidate_text(path: Path, data: bytes) -> str | None:
    """Decode text for privacy scanning; only known binary suffixes are skipped."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        if path.suffix.lower() in BINARY_SUFFIXES:
            return None
        return data.decode("utf-8", errors="ignore")


def scan_text_privacy_leaks(text: str, *, label: str = "") -> list[str]:
    """Scan *text* line by line and return ``label:line: leak`` entries."""
    findings: list[str] = []
    prefix = f"{label}:" if label else ""
    for line_no, line in enumerate(text.splitlines(), 1):
        for leak in find_developer_path_leaks(line):
            findings.append(f"{prefix}{line_no}: {leak}")
    return findings


def scan_path_privacy(root: Path, rel_paths: Iterable[str]) -> list[str]:
    """Scan repository-relative paths under *root* for developer home-path leaks."""
    violations: list[str] = []
    for rel in sorted(set(rel_paths)):
        if not rel:
            continue
        path = root / rel
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        text = decode_candidate_text(path, data)
        if text is None:
            continue
        violations.extend(scan_text_privacy_leaks(text, label=rel))
    return violations
