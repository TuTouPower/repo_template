"""merge_guard.py 的 merge 识别：复合命令纳入授权、非 merge 不误判。"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import merge_guard


@pytest.mark.parametrize(
    "command,expected",
    [
        ("git merge feature/X", ("git-merge", "git-merge:feature/X")),
        # A3：复合命令不再被尾部锚定绕过
        ("git merge feature/X && git push", ("git-merge", "git-merge:feature/X")),
        ("git merge --no-ff foo && npm test", ("git-merge", "git-merge:foo")),
        ("git merge -s ours foo", ("git-merge", "git-merge:ours")),
        ("git merge", ("git-merge", "git-merge:unspecified")),
        (
            "git merge feature/X # merge-token=abc",
            ("git-merge", "git-merge:feature/X"),
        ),
        ("gh pr merge 12", ("gh-pr-merge", "gh-pr-merge:gh pr merge 12")),
    ],
)
def test_detect_merge_catches_merge_commands(command, expected):
    assert merge_guard.detect_merge(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git status",
        "git merge-base HEAD origin/main",
        "git merge-base --is-ancestor A B",
        'git commit -m "fix: git merge conflict"',
        'git commit -m "git merge" && git push',
        "echo git merge foo",
        "git log --grep=merge",
    ],
)
def test_detect_merge_ignores_non_merge(command):
    assert merge_guard.detect_merge(command) is None
