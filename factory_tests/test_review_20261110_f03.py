"""F03：commit-msg hook 扫描最终提交消息的隐私泄漏。

普通提交与 merge 提交都读取 ``argv[1]`` 消息文件；``-m`` / ``-F`` 的注释样式
正文属于提交内容，全部扫描；只有 scissors 行后确认为 ``git commit -v`` 的
diff 时才截断。
"""

import importlib.util
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

from git_repo_support import get_controlled_git_env
from repo_task.context import TaskDataError

HOOK_PATH = Path(__file__).resolve().parents[1] / "repo/.repo_template/hooks/commit-msg"


def _leak(*parts):
    return "/".join(("", *parts))


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=get_controlled_git_env(), check=True, timeout=60,
    )


def _write_message(tmp_path, text):
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(text, encoding="utf-8")
    return path


def _run_hook(repo, message):
    return subprocess.run(
        [sys.executable, str(HOOK_PATH), str(message)],
        cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env=get_controlled_git_env(), timeout=60,
    )


def test_plain_commit_message_with_home_path_blocked(git_repo, tmp_path):
    leak = _leak("Users", "devuser", "proj")
    message = _write_message(tmp_path, f"chore: note\n\nsee {leak}\n")

    result = _run_hook(git_repo, message)

    assert result.returncode == 1
    assert "开发者家目录绝对路径" in result.stderr
    assert f"commit message:3: {_leak('Users', 'devuser')}" in result.stderr


def test_merge_commit_message_with_home_path_blocked(git_repo, tmp_path):
    head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    (git_repo / ".git" / "MERGE_HEAD").write_text(head + "\n", encoding="ascii")
    leak = _leak("Users", "devuser", "merged")
    message = _write_message(tmp_path, f"Merge branch 'feature'\n\npath {leak}\n")

    result = _run_hook(git_repo, message)

    assert result.returncode == 1
    assert "编号校验失败" not in result.stderr
    assert "开发者家目录绝对路径" in result.stderr
    assert f"commit message:3: {_leak('Users', 'devuser')}" in result.stderr


def test_verbose_diff_leak_after_scissors_not_blocked(git_repo, tmp_path):
    leak = _leak("Users", "devuser", "existing")
    message = _write_message(
        tmp_path,
        "feat: clean message\n\n"
        "# ------------------------ >8 ------------------------\n"
        "diff --git a/notes.txt b/notes.txt\n"
        f"+remote = {leak}\n",
    )

    result = _run_hook(git_repo, message)

    assert result.returncode == 0, result.stderr


def test_comment_like_lines_are_scanned(git_repo, tmp_path):
    _git(git_repo, "config", "core.commentChar", ";")
    leak = _leak("Users", "devuser", "commented")
    message = _write_message(
        tmp_path,
        "feat: clean message\n\n"
        f"; legacy note {leak}\n",
    )

    result = _run_hook(git_repo, message)

    assert result.returncode == 1
    assert "开发者家目录绝对路径" in result.stderr


def test_hash_prefixed_dash_m_message_blocked(git_repo):
    leak = _leak("Users", "devuser", "heading")
    _git(git_repo, "config", "core.hooksPath", str(HOOK_PATH.parent))
    (git_repo / "heading.txt").write_text("x\n", encoding="utf-8")
    _git(git_repo, "add", "heading.txt")
    result = subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-m", f"# {leak}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=get_controlled_git_env(), timeout=60,
    )
    assert result.returncode != 0
    assert "开发者家目录绝对路径" in result.stderr


def test_scissors_marker_without_diff_is_scanned(git_repo, tmp_path):
    leak = _leak("Users", "devuser", "after-scissors")
    message = _write_message(
        tmp_path,
        "feat: clean message\n\n"
        "# ------------------------ >8 ------------------------\n"
        f"{leak}\n",
    )

    result = _run_hook(git_repo, message)

    assert result.returncode == 1
    assert "开发者家目录绝对路径" in result.stderr


def test_missing_message_file_reports_readable_error(git_repo, tmp_path):
    result = _run_hook(git_repo, tmp_path / "missing.txt")

    assert result.returncode == 1
    assert "无法读取提交消息（fail-closed）" in result.stderr
    assert "Traceback" not in result.stderr


def test_context_parse_failure_reports_readable_error_without_traceback(
    git_repo, tmp_path, monkeypatch, capsys
):
    loader = SourceFileLoader("f03_commit_msg_hook", str(HOOK_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    loader.exec_module(module)
    message = _write_message(tmp_path, "feat: clean message\n")

    def fail(_root, **_kwargs):
        raise TaskDataError("仓库上下文损坏")

    monkeypatch.setattr(module.RepoContext, "for_root", staticmethod(fail))
    monkeypatch.setattr(sys, "argv", ["commit-msg", str(message)])

    assert module.main() == 1
    captured = capsys.readouterr()
    assert "上下文解析失败（fail-closed）" in captured.err
    assert "仓库上下文损坏" in captured.err
    assert "Traceback" not in captured.err
