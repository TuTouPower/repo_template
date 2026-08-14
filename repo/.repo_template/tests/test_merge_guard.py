"""merge_guard.py 的 merge 识别：复合命令纳入授权、非 merge 不误判。"""
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"
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
        # 审阅修复：git -C / wrapper 前缀不再绕过
        ("git -C . merge feature/X", ("git-merge", "git-merge:feature/X")),
        ("git --no-pager merge feature/X", ("git-merge", "git-merge:feature/X")),
        ("command git merge feature/X", ("git-merge", "git-merge:feature/X")),
        ("env git merge feature/X", ("git-merge", "git-merge:feature/X")),
        ("env FOO=bar git merge feature/X", ("git-merge", "git-merge:feature/X")),
        ("sudo -u root git merge feature/X", ("git-merge", "git-merge:feature/X")),
        # 第二轮审阅：无空格分隔符 / 换行 / env 赋值 / 绝对路径 wrapper / gh 全局参数
        ("echo ok;git merge feature/x", ("git-merge", "git-merge:feature/x")),
        ("true&&git merge feature/x", ("git-merge", "git-merge:feature/x")),
        ("echo ok\ngit merge feature/x", ("git-merge", "git-merge:feature/x")),
        ("FOO=bar git merge feature/x", ("git-merge", "git-merge:feature/x")),
        ("/usr/bin/env git merge feature/x", ("git-merge", "git-merge:feature/x")),
        (
            "gh --repo owner/repo pr merge 12",
            ("gh-pr-merge", "gh-pr-merge:gh --repo owner/repo pr merge 12"),
        ),
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
        # 审阅修复：非命令位置文本不再误判
        "echo gh pr merge 12",
        "cat gh pr merge 12",
        "env git status",
        "command git push",
    ],
)
def test_detect_merge_ignores_non_merge(command):
    assert merge_guard.detect_merge(command) is None


class _StdinStub:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text


def test_stale_token_refires_with_reason(monkeypatch, tmp_path, capsys):
    """token 失效（过期/已用/不匹配）时重发新 token，并说明原 token 失效原因。"""
    state_path = tmp_path / "merge_tokens.json"
    monkeypatch.setattr(merge_guard, "STATE_PATH", state_path)

    cmd = "git merge feature/X"
    first = merge_guard.issue_token("git-merge:feature/X", cmd)
    records = json.loads(state_path.read_text(encoding="utf-8"))
    records[0]["used"] = True  # 模拟已用过
    state_path.write_text(json.dumps(records), encoding="utf-8")

    event = {
        "tool_name": "Bash",
        "tool_input": {"command": f"{cmd} # merge-token={first}"},
    }
    monkeypatch.setattr(sys, "stdin", _StdinStub(json.dumps(event)))
    with pytest.raises(SystemExit):
        merge_guard.main()

    out = capsys.readouterr().out
    msg = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "原 token 失效" in msg
    assert "已废弃" in msg
    assert "新 token" in msg

    new_records = json.loads(state_path.read_text(encoding="utf-8"))
    new_token = next(r["token"] for r in new_records if not r["used"])
    assert new_token != first
    assert new_token in msg
