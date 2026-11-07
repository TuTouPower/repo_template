"""task.py review-status 的真实指纹接线测试（Issue #82）。"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task import review_status as crs
from review_support import ac_review_section
from test_dispatch_integration import git_repo, _task_cli  # noqa: F401  (fixture + CLI helper)

SPEC = "# task\n\n## 契约区\n\n### 验收标准\n\n- [x] AC-001：甲\n"


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True, timeout=60,
    )


def test_main_real_fingerprint_pass_then_stale(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    task_dir = repo / "docs/tasks/t001_alpha"
    task_dir.mkdir(parents=True)
    (task_dir / "task.md").write_text(SPEC, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    anchor = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (task_dir / "progress.md").write_text(
        "---\ntid: t001\nreview_level: single\ndiff_anchor: "
        + anchor + "\n---\n## Review 处置\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(crs, "REPO_ROOT", repo)
    scope = crs.current_scope_fingerprint(task_dir, anchor)
    assert scope
    (task_dir / "review_general.md").write_text(
        f"verdict: PASS\n\nreviewed_scope: {scope}\n\n{ac_review_section(SPEC)}", encoding="utf-8"
    )
    monkeypatch.setattr(
        sys, "argv", ["task.py", "--task-dir", str(task_dir)]
    )
    crs.main()
    out = capsys.readouterr().out
    assert "overall=PASS" in out
    assert "review_scope=ok" in out
    assert "ac_review=ok" in out

    (task_dir / "task.md").write_text(SPEC + "\n# changed\n", encoding="utf-8")
    crs.main()
    out = capsys.readouterr().out
    assert "overall=INCOMPLETE" in out
    assert "review_scope=stale" in out


def test_task_cli_review_status_is_single_entry_without_legacy_param(git_repo):
    """A12：review 状态门禁只经 task.py review-status 暴露，旧脚本与 --max-review-round 清零。"""
    assert not (SCRIPTS_DIR / "check_review_status.py").exists()

    help_result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "task.py"), "review-status", "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "--task-dir" in help_result.stdout
    assert "--max-review-round" not in help_result.stdout

    task_dir = git_repo / "docs/tasks/t001_alpha"
    (task_dir / "task.md").write_text(SPEC, encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "base")
    anchor = _git(git_repo, "rev-parse", "HEAD").stdout.strip()
    (task_dir / "progress.md").write_text(
        "---\ntid: t001\nreview_level: single\nreview_limit: 8\nverify_limit: 7\n"
        "diff_anchor: " + anchor + "\n---\n## Review 处置\n",
        encoding="utf-8",
    )
    scope = crs.monitoring_scope_fingerprint(
        anchor, "docs/tasks/t001_alpha", repo_root=git_repo
    )
    (task_dir / "review_general.md").write_text(
        f"verdict: PASS\n\nreviewed_scope: {scope}\n\n{ac_review_section(SPEC)}",
        encoding="utf-8",
    )

    result = _task_cli(git_repo, "review-status", "--task-dir", "docs/tasks/t001_alpha")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "max_review_round=8" in result.stdout
    assert "max_verify_round=7" in result.stdout
    assert "overall=PASS" in result.stdout
