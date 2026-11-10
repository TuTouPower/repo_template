"""factory_tests/test_spikes.py - Spike 工厂集成与端到端 CLI 测试。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

from git_repo_support import get_controlled_git_env

TEMPLATE_SOURCE = FACTORY_ROOT / "repo/.repo_template"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env=get_controlled_git_env(),
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "FactoryTester")
    _git(repo, "config", "user.email", "factory@example.com")

    # 复制 .repo_template 到临时消费仓，模拟真实消费仓环境
    shutil.copytree(TEMPLATE_SOURCE, repo / ".repo_template", dirs_exist_ok=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: init repo template")
    return repo


def _run_spikes(repo: Path, *args: str) -> subprocess.CompletedProcess:
    script = repo / ".repo_template/scripts/spikes.py"
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=get_controlled_git_env(),
    )


def test_factory_spikes_cli_lifecycle(tmp_path: Path):
    repo = _init_repo(tmp_path)

    # 1. spikes new
    res = _run_spikes(repo, "new", "uv_resolution", "--title", "UV Resolution Probe")
    assert res.returncode == 0, res.stderr
    assert "docs/spikes/s001_uv_resolution" in res.stdout

    spike_dir = repo / "docs/spikes/s001_uv_resolution"
    report_file = spike_dir / "report.md"
    assert report_file.is_file()
    text = report_file.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert 'sid: "s001"' in text
    assert 'status: "active"' in text

    # 2. spikes list
    res_list = _run_spikes(repo, "list")
    assert res_list.returncode == 0
    assert "active     s001_uv_resolution" in res_list.stdout

    # 3. 准备关联 finding
    finding = repo / "docs/findings/d001_uv.md"
    finding.parent.mkdir(parents=True, exist_ok=True)
    finding.write_text("# d001\n", encoding="utf-8")

    # 4. 填充报告并 complete
    completed_report = (
        "---\n"
        'sid: "s001"\n'
        'slug: "uv_resolution"\n'
        'status: "active"\n'
        "---\n\n"
        "# Spike report: UV Resolution Probe\n\n"
        "## 问题\n解析问题\n\n"
        "## 成功判据\n- 支持uv\n\n"
        "## 尝试\n- 运行uv\n\n"
        "## 证据\n- 成功输出\n\n"
        "## 结论\n方案可行\n\n"
        "## 是否采纳\n"
        "- 决定：是\n"
        "- 理由：稳定高效\n"
        "- 后续 task：t001\n"
        "- 无需后续理由：不适用\n\n"
        "## 关联 findings\n- `d001`\n"
    )
    report_file.write_text(completed_report, encoding="utf-8")

    # dry-run
    res_dry = _run_spikes(repo, "complete", "s001")
    assert res_dry.returncode == 0, res_dry.stderr
    assert "dry-run" in res_dry.stdout

    # write
    res_done = _run_spikes(repo, "complete", "s001", "--write")
    assert res_done.returncode == 0, res_done.stderr
    assert "已完成 spike s001" in res_done.stdout

    # 验证状态变更为 completed
    res_list_done = _run_spikes(repo, "list", "--state", "completed")
    assert "completed  s001_uv_resolution" in res_list_done.stdout


def test_factory_spikes_cli_rejects_newline_in_reason(tmp_path: Path):
    repo = _init_repo(tmp_path)
    res_new1 = _run_spikes(repo, "new", "probe_old")
    assert res_new1.returncode == 0, res_new1.stderr
    res_new2 = _run_spikes(repo, "new", "probe_new")
    assert res_new2.returncode == 0, res_new2.stderr

    # 拦截换行符
    res_nl = _run_spikes(
        repo, "supersede", "s001", "--by", "s002", "--reason", "line1\nline2"
    )
    assert res_nl.returncode != 0
    assert "--reason 严禁包含换行符" in res_nl.stderr

    # 拦截回车符
    res_cr = _run_spikes(
        repo, "supersede", "s001", "--by", "s002", "--reason", "line1\rline2"
    )
    assert res_cr.returncode != 0
    assert "--reason 严禁包含换行符" in res_cr.stderr

    # 拦截空理由
    res_empty = _run_spikes(
        repo, "supersede", "s001", "--by", "s002", "--reason", "   "
    )
    assert res_empty.returncode != 0
    assert "--reason 不能为空" in res_empty.stderr


def test_factory_spikes_cli_complete_fail_closed_without_front_matter(tmp_path: Path):
    repo = _init_repo(tmp_path)
    spike_dir = repo / "docs/spikes/s001_manual_no_fm"
    spike_dir.mkdir(parents=True, exist_ok=True)
    report = spike_dir / "report.md"
    report.write_text("# No Front Matter\n\n## 结论\nok\n- 决定：是\n- 后续 task：t001\n", encoding="utf-8")

    # 尝试 complete，必须 fail-closed 阻断
    res = _run_spikes(repo, "complete", "s001")
    assert res.returncode != 0
    assert "YAML front matter" in res.stderr


def test_factory_spikes_cli_ignores_non_canonical_padding(tmp_path: Path):
    """s0001_bad 非规范 padding 不得被 list/find 识别（DS L258）。"""
    repo = _init_repo(tmp_path)
    bad = repo / "docs/spikes/s0001_bad"
    bad.mkdir(parents=True)
    (bad / "report.md").write_text(
        '---\nsid: "s001"\nslug: "bad"\nstatus: "active"\n---\n# Spike\n',
        encoding="utf-8",
    )

    listed = _run_spikes(repo, "list", "--state", "all")
    assert "s0001_bad" not in listed.stdout

    completed = _run_spikes(repo, "complete", "s001")
    assert completed.returncode != 0
    assert "未找到 spike" in completed.stderr
