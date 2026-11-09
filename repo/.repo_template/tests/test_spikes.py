"""test_spikes.py - Spike 实体稳定存储、元数据与状态机契约测试。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = SCRIPTS_DIR.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import spikes as spikes_mod
from _id_scan import IdScanError
from repo_task.context import TaskDataError
from repo_task.documents import parse_front_matter, write_front_matter
from repo_task.finalization import check_finalization, validate_spike
from repo_task.spikes import (
    SpikeMetadataError,
    find_spike_dir,
    validate_spike_metadata,
)

pytestmark = pytest.mark.contract


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.com")

    # 准备 specs_index
    specs_index = repo / "docs/specs_index.md"
    specs_index.parent.mkdir(parents=True, exist_ok=True)
    specs_index.write_text("|slug|task 清单|最后更新时间|\n|---|---|---|\n", encoding="utf-8")

    # 准备模板文件
    template = repo / ".repo_template/docs/spike_report_template.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(
        "---\n"
        'sid: "{id}"\n'
        'slug: "{slug}"\n'
        'status: "active"\n'
        "---\n\n"
        "# Spike report\n\n"
        "## 问题\n\n{需要验证什么。}\n\n"
        "## 成功判据\n\n- {什么证据足以支持结论。}\n\n"
        "## 尝试\n\n- {做了什么；有代码时引用 `code/`。}\n\n"
        "## 证据\n\n- {测量、日志、原型行为或资料来源。}\n\n"
        "## 结论\n\n{结果、限制和可信度。}\n\n"
        "## 是否采纳\n\n"
        "- 决定：{是 / 否}\n"
        "- 理由：{原因}\n"
        "- 后续 task：{采纳时填写 {tid}，如 t001；否则写“无”}\n"
        "- 无需后续理由：{后续 task 为无时说明理由；有后续时写“不适用”}\n\n"
        "## 关联 findings\n\n"
        "- {结论已抽取到的 dNNN；须指向现存 docs/findings 条目。}\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: init template")
    return repo


def _bind(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setattr(spikes_mod, "REPO_ROOT", repo)
    monkeypatch.setattr(spikes_mod, "SPIKES_DIR", repo / "docs/spikes")
    monkeypatch.setattr(
        spikes_mod,
        "TEMPLATE_PATH",
        repo / ".repo_template/docs/spike_report_template.md",
    )


def _make_finding(repo: Path, did: str = "d001", slug: str = "verified") -> Path:
    f = repo / f"docs/findings/{did}_{slug}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"# {did} 已验证事实\n", encoding="utf-8")
    return f


def _fill_report(
    repo: Path,
    spike_dir: Path,
    decision: str = "- 决定：是",
    followup: str = "- 后续 task：t001",
    did: str = "d001",
) -> None:
    _make_finding(repo, did)
    report = spike_dir / "report.md"
    fm, _ = parse_front_matter(report)
    body = (
        "# Spike report\n\n"
        "## 问题\n验证问题。\n\n"
        "## 成功判据\n- 判据成功。\n\n"
        "## 尝试\n- 尝试操作。\n\n"
        "## 证据\n- 证据充分。\n\n"
        "## 结论\n实验结果符合预期。\n\n"
        "## 是否采纳\n"
        f"{decision}\n"
        "- 理由：方案优良\n"
        f"{followup}\n"
        "- 无需后续理由：不适用\n\n"
        "## 关联 findings\n"
        f"- `{did}`\n"
    )
    write_front_matter(report, fm, body)


# ===========================================================================
# 1. 元数据与 Front Matter 校验
# ===========================================================================


def test_validate_spike_metadata_active_and_completed():
    validate_spike_metadata({"sid": "s001", "slug": "uv_lock", "status": "active"})
    validate_spike_metadata({"sid": "s001", "slug": "uv_lock", "status": "completed"})


def test_validate_spike_metadata_superseded():
    validate_spike_metadata({
        "sid": "s001",
        "slug": "uv_lock",
        "status": "superseded",
        "superseded_by": "s002",
        "reason": "更优替代方案",
    })


def test_validate_spike_metadata_rejects_unknown_field():
    with pytest.raises(SpikeMetadataError, match="未知字段"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "uv_lock",
            "status": "active",
            "extra_key": "val",
        })


def test_validate_spike_metadata_rejects_invalid_sid_or_slug():
    with pytest.raises(SpikeMetadataError, match="sid 格式非法"):
        validate_spike_metadata({"sid": "s1", "slug": "uv_lock", "status": "active"})
    with pytest.raises(SpikeMetadataError, match="slug 格式非法"):
        validate_spike_metadata({"sid": "s001", "slug": "Bad-Slug", "status": "active"})


def test_validate_spike_metadata_rejects_active_or_completed_with_superseded_by():
    with pytest.raises(SpikeMetadataError, match="不得填写 superseded_by"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "uv_lock",
            "status": "active",
            "superseded_by": "s002",
        })
    with pytest.raises(SpikeMetadataError, match="不得填写 superseded_by"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "uv_lock",
            "status": "completed",
            "superseded_by": "s002",
        })


def test_validate_spike_metadata_rejects_active_or_completed_with_reason():
    with pytest.raises(SpikeMetadataError, match="不得填写 reason"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "uv_lock",
            "status": "active",
            "reason": "某种理由",
        })
    with pytest.raises(SpikeMetadataError, match="不得填写 reason"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "uv_lock",
            "status": "completed",
            "reason": "某种理由",
        })


def test_validate_spike_metadata_superseded_requires_fields():
    with pytest.raises(SpikeMetadataError, match="必须提供合法的 superseded_by"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "uv_lock",
            "status": "superseded",
            "reason": "替代理由",
        })
    with pytest.raises(SpikeMetadataError, match="reason 不能为空"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "uv_lock",
            "status": "superseded",
            "superseded_by": "s002",
            "reason": "   ",
        })


def test_validate_spike_metadata_strict_types():
    with pytest.raises(SpikeMetadataError, match="必须为 dict"):
        validate_spike_metadata("not a dict")  # type: ignore

    with pytest.raises(SpikeMetadataError, match="类型非法.*期望 str"):
        validate_spike_metadata({"sid": 100, "slug": "probe", "status": "active"})

    with pytest.raises(SpikeMetadataError, match="类型非法.*期望 str"):
        validate_spike_metadata({"sid": "s001", "slug": ["probe"], "status": "active"})

    with pytest.raises(SpikeMetadataError, match="类型非法.*期望 str"):
        validate_spike_metadata({"sid": "s001", "slug": "probe", "status": 123})

    with pytest.raises(SpikeMetadataError, match="类型非法.*期望 str"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "probe",
            "status": "superseded",
            "superseded_by": 2,
            "reason": "text",
        })

    with pytest.raises(SpikeMetadataError, match="类型非法.*期望 str"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "probe",
            "status": "superseded",
            "superseded_by": "s002",
            "reason": {"why": "not string"},
        })


def test_validate_spike_metadata_rejects_newlines_and_empty_fields():
    for field, val in [
        ("sid", "s001\n"),
        ("slug", "probe\r"),
        ("status", "active\r\n"),
    ]:
        with pytest.raises(SpikeMetadataError, match="严禁包含换行符"):
            validate_spike_metadata({"sid": "s001", "slug": "probe", "status": "active", field: val})

    with pytest.raises(SpikeMetadataError, match="严禁包含换行符"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "probe",
            "status": "superseded",
            "superseded_by": "s002\n",
            "reason": "valid reason",
        })

    with pytest.raises(SpikeMetadataError, match="严禁包含换行符"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "probe",
            "status": "superseded",
            "superseded_by": "s002",
            "reason": "line1\nline2",
        })

    with pytest.raises(SpikeMetadataError, match="sid 不能为空"):
        validate_spike_metadata({"sid": "", "slug": "probe", "status": "active"})
    with pytest.raises(SpikeMetadataError, match="slug 不能为空"):
        validate_spike_metadata({"sid": "s001", "slug": "   ", "status": "active"})
    with pytest.raises(SpikeMetadataError, match="必须提供合法的 superseded_by"):
        validate_spike_metadata({
            "sid": "s001",
            "slug": "probe",
            "status": "superseded",
            "superseded_by": "   ",
            "reason": "valid",
        })


# ===========================================================================
# 2. spikes.py new
# ===========================================================================


def test_spikes_new_creates_directory_and_frontmatter(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)

    spikes_mod.main(["new", "uv_lock", "--title", "UV Lock Probe"])
    out = capsys.readouterr().out.strip()
    assert out == "docs/spikes/s001_uv_lock"

    spike_dir = repo / "docs/spikes/s001_uv_lock"
    assert spike_dir.is_dir()
    report = spike_dir / "report.md"
    assert report.is_file()

    fm, body = parse_front_matter(report)
    assert fm == {"sid": "s001", "slug": "uv_lock", "status": "active"}
    assert "# Spike report: UV Lock Probe" in body

    # 验证不自动 git add（工作区保留为未暂存）
    staged = _git(repo, "status", "--porcelain").stdout
    assert "??" in staged or "docs/spikes/s001_uv_lock" in staged


def test_spikes_new_supports_opt_slug_and_increments_id(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)

    spikes_mod.main(["new", "--slug", "first_probe"])
    assert capsys.readouterr().out.strip() == "docs/spikes/s001_first_probe"

    spikes_mod.main(["new", "second_probe"])
    assert capsys.readouterr().out.strip() == "docs/spikes/s002_second_probe"


def test_spikes_new_rejects_invalid_slug(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)

    with pytest.raises(SystemExit, match="slug 须匹配"):
        spikes_mod.main(["new", "Invalid-Slug"])


def test_spikes_new_rejects_template_missing_front_matter(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    tpl = repo / ".repo_template/docs/spike_report_template.md"
    tpl.write_text("# Spike report\n\nNo front matter.\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="缺少标准 Front Matter"):
        spikes_mod.main(["new", "probe_no_fm"])


# ===========================================================================
# 3. spikes.py list
# ===========================================================================


def test_spikes_list_state_filtering(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)

    # 默认空
    spikes_mod.main(["list"])
    assert capsys.readouterr().out.strip() == "(no spikes)"

    # 创建两条 spike
    spikes_mod.main(["new", "probe_a"])
    spikes_mod.main(["new", "probe_b"])
    capsys.readouterr()

    # 填并 complete probe_b
    _fill_report(repo, repo / "docs/spikes/s002_probe_b")
    spikes_mod.main(["complete", "s002", "--write"])
    capsys.readouterr()

    # 默认列出 active
    spikes_mod.main(["list"])
    out = capsys.readouterr().out
    assert "active     s001_probe_a" in out
    assert "s002_probe_b" not in out

    # 列出 completed
    spikes_mod.main(["list", "--state", "completed"])
    out = capsys.readouterr().out
    assert "completed  s002_probe_b" in out
    assert "s001_probe_a" not in out

    # 列出 all
    spikes_mod.main(["list", "--state", "all"])
    out = capsys.readouterr().out
    assert "active     s001_probe_a" in out
    assert "completed  s002_probe_b" in out


# ===========================================================================
# 4. spikes.py complete
# ===========================================================================


def test_spikes_complete_dry_run_and_validation_checks(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)

    spikes_mod.main(["new", "probe_complete"])
    spike_dir = repo / "docs/spikes/s001_probe_complete"
    capsys.readouterr()

    # 1. 占位符未填，校验失败阻断
    with pytest.raises(SystemExit, match="仍有占位符"):
        spikes_mod.main(["complete", "s001"])

    # 2. 填写报告
    _fill_report(repo, spike_dir)

    # 3. dry-run：校验通过但文件零修改
    spikes_mod.main(["complete", "s001"])
    out = capsys.readouterr().out
    assert "dry-run" in out
    fm_dry, _ = parse_front_matter(spike_dir / "report.md")
    assert fm_dry["status"] == "active"

    # 4. --write：原位更新为 completed
    spikes_mod.main(["complete", "s001", "--write"])
    out = capsys.readouterr().out
    assert "已完成 spike s001" in out
    fm_done, _ = parse_front_matter(spike_dir / "report.md")
    assert fm_done["status"] == "completed"

    # 验证不自动 git add
    staged = _git(repo, "diff", "--cached", "--name-only").stdout
    assert "docs/spikes/s001_probe_complete/report.md" not in staged

    # 5. 重复 complete 报错
    with pytest.raises(SystemExit, match="已处于 completed 状态"):
        spikes_mod.main(["complete", "s001", "--write"])


def test_spikes_complete_rejects_missing_findings(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)

    spikes_mod.main(["new", "probe_missing_finding"])
    spike_dir = repo / "docs/spikes/s001_probe_missing_finding"
    _fill_report(repo, spike_dir, did="d999")
    # 删除关联 finding
    (repo / "docs/findings/d999_verified.md").unlink(missing_ok=True)

    with pytest.raises(SystemExit, match="关联现存 findings"):
        spikes_mod.main(["complete", "s001"])


# ===========================================================================
# 5. spikes.py supersede
# ===========================================================================


def test_spikes_supersede_lifecycle_and_constraints(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)

    spikes_mod.main(["new", "probe_old"])
    spikes_mod.main(["new", "probe_new"])
    s001_dir = repo / "docs/spikes/s001_probe_old"
    s002_dir = repo / "docs/spikes/s002_probe_new"
    capsys.readouterr()

    # 1. 自引用拦截
    with pytest.raises(SystemExit, match="严禁自引用"):
        spikes_mod.main(["supersede", "s001", "--by", "s001", "--reason", "自替代"])

    # 2. 缺失后继拦截
    with pytest.raises(SystemExit, match="替代目标 s999 不存在"):
        spikes_mod.main(["supersede", "s001", "--by", "s999", "--reason", "目标缺失"])

    # 3. 后继状态为 active 拦截
    with pytest.raises(SystemExit, match="状态必须为 completed"):
        spikes_mod.main(["supersede", "s001", "--by", "s002", "--reason", "后继未完成"])

    # 4. 后继完成
    _fill_report(repo, s002_dir)
    spikes_mod.main(["complete", "s002", "--write"])
    capsys.readouterr()

    # 5. dry-run：零修改
    spikes_mod.main(["supersede", "s001", "--by", "s002", "--reason", "更优方案"])
    out = capsys.readouterr().out
    assert "dry-run" in out
    fm_s1, _ = parse_front_matter(s001_dir / "report.md")
    assert fm_s1["status"] == "active"

    # 6. --write：原位更新 superseded
    spikes_mod.main(["supersede", "s001", "--by", "s002", "--reason", "更优方案", "--write"])
    out = capsys.readouterr().out
    assert "已替代 spike s001 -> s002" in out
    fm_s1_done, _ = parse_front_matter(s001_dir / "report.md")
    assert fm_s1_done["status"] == "superseded"
    assert fm_s1_done["superseded_by"] == "s002"
    assert fm_s1_done["reason"] == "更优方案"

    # 验证不自动 git add
    staged = _git(repo, "diff", "--cached", "--name-only").stdout
    assert "docs/spikes/s001_probe_old/report.md" not in staged

    # 7. 循环替代拦截（如 s002 试图反向替代 s001）
    with pytest.raises(SystemExit, match="严禁循环引用|替代目标 s001 状态必须为 completed"):
        spikes_mod.main(["supersede", "s002", "--by", "s001", "--reason", "循环", "--write"])


def test_spikes_supersede_cycle_detection(tmp_path, monkeypatch):
    """人工构造元数据循环，验证 supersede 命令能防御性拦截循环引用。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)

    spikes_mod.main(["new", "probe_a"])
    spikes_mod.main(["new", "probe_b"])
    s001_dir = repo / "docs/spikes/s001_probe_a"
    s002_dir = repo / "docs/spikes/s002_probe_b"
    _fill_report(repo, s001_dir)
    _fill_report(repo, s002_dir)
    spikes_mod.main(["complete", "s001", "--write"])
    spikes_mod.main(["complete", "s002", "--write"])

    # 手动让 s002 指向 s001（superseded）
    fm2, b2 = parse_front_matter(s002_dir / "report.md")
    fm2["status"] = "superseded"
    fm2["superseded_by"] = "s001"
    fm2["reason"] = "to s1"
    write_front_matter(s002_dir / "report.md", fm2, b2)

    # 试图把 s001 supersede 为 s002 时，应当拦截循环
    with pytest.raises(SystemExit, match="严禁循环引用|状态必须为 completed"):
        spikes_mod.main(["supersede", "s001", "--by", "s002", "--reason", "cycle", "--write"])


def test_spikes_supersede_rejects_newline_in_reason_and_empty_reason(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)

    with pytest.raises(SystemExit, match="--reason 不能为空"):
        spikes_mod.main(["supersede", "s001", "--by", "s002", "--reason", "   "])

    with pytest.raises(SystemExit, match="--reason 严禁包含换行符"):
        spikes_mod.main(["supersede", "s001", "--by", "s002", "--reason", "line1\nline2"])

    with pytest.raises(SystemExit, match="--reason 严禁包含换行符"):
        spikes_mod.main(["supersede", "s001", "--by", "s002", "--reason", "line1\rline2"])


# ===========================================================================
# 6. Finalization 引用与替代链递归校验
# ===========================================================================


def test_finalization_rejects_active_spike(tmp_path):
    repo = _init_repo(tmp_path)
    spike_dir = repo / "docs/spikes/s001_probe"
    spike_dir.mkdir(parents=True)
    report = spike_dir / "report.md"
    report.write_text(
        "---\nsid: \"s001\"\nslug: \"probe\"\nstatus: \"active\"\n---\n# Report\n",
        encoding="utf-8",
    )

    task_dir = repo / "docs/tasks/t001_feat"
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text(
        "---\ntid: \"t001\"\nstatus: \"active\"\n---\n- specs_index: not_applicable: none\n参考 `s001`\n",
        encoding="utf-8",
    )
    (task_dir / "task.md").write_text("# task\n", encoding="utf-8")

    with pytest.raises(ValueError, match="尚未完成.*status 为 active"):
        check_finalization(repo, "t001", task_dir)


def test_finalization_accepts_completed_spike(tmp_path):
    repo = _init_repo(tmp_path)
    spike_dir = repo / "docs/spikes/s001_probe"
    spike_dir.mkdir(parents=True)
    report = spike_dir / "report.md"
    report.write_text(
        "---\nsid: \"s001\"\nslug: \"probe\"\nstatus: \"completed\"\n---\n# Report\n",
        encoding="utf-8",
    )
    _fill_report(repo, spike_dir)

    task_dir = repo / "docs/tasks/t001_feat"
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text(
        "---\ntid: \"t001\"\nstatus: \"active\"\n---\n- specs_index: not_applicable: none\n参考 `s001`\n",
        encoding="utf-8",
    )
    (task_dir / "task.md").write_text("# task\n", encoding="utf-8")

    check_finalization(repo, "t001", task_dir)


def test_finalization_recursively_resolves_superseded_spike(tmp_path):
    repo = _init_repo(tmp_path)

    # s001 superseded by s002
    s001_dir = repo / "docs/spikes/s001_probe_old"
    s001_dir.mkdir(parents=True)
    (s001_dir / "report.md").write_text(
        "---\n"
        'sid: "s001"\n'
        'slug: "probe_old"\n'
        'status: "superseded"\n'
        'superseded_by: "s002"\n'
        'reason: "superseded by s002"\n'
        "---\n"
        "# Old Report\n",
        encoding="utf-8",
    )

    # s002 completed
    s002_dir = repo / "docs/spikes/s002_probe_new"
    s002_dir.mkdir(parents=True)
    (s002_dir / "report.md").write_text(
        "---\n"
        'sid: "s002"\n'
        'slug: "probe_new"\n'
        'status: "completed"\n'
        "---\n"
        "# New Report\n",
        encoding="utf-8",
    )
    _fill_report(repo, s002_dir)

    task_dir = repo / "docs/tasks/t001_feat"
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text(
        "---\ntid: \"t001\"\nstatus: \"active\"\n---\n- specs_index: not_applicable: none\n参考 `s001`\n",
        encoding="utf-8",
    )
    (task_dir / "task.md").write_text("# task\n", encoding="utf-8")

    # 引用被替代的 s001，能够递归验证其后继 s002 并通过
    check_finalization(repo, "t001", task_dir)


def test_finalization_superseded_chain_cycle_fails(tmp_path):
    repo = _init_repo(tmp_path)

    s001_dir = repo / "docs/spikes/s001_probe_a"
    s001_dir.mkdir(parents=True)
    (s001_dir / "report.md").write_text(
        "---\nsid: \"s001\"\nslug: \"probe_a\"\nstatus: \"superseded\"\nsuperseded_by: \"s002\"\nreason: \"r\"\n---\n",
        encoding="utf-8",
    )

    s002_dir = repo / "docs/spikes/s002_probe_b"
    s002_dir.mkdir(parents=True)
    (s002_dir / "report.md").write_text(
        "---\nsid: \"s002\"\nslug: \"probe_b\"\nstatus: \"superseded\"\nsuperseded_by: \"s001\"\nreason: \"r\"\n---\n",
        encoding="utf-8",
    )

    task_dir = repo / "docs/tasks/t001_feat"
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text(
        "---\ntid: \"t001\"\nstatus: \"active\"\n---\n- specs_index: not_applicable: none\n参考 `s001`\n",
        encoding="utf-8",
    )
    (task_dir / "task.md").write_text("# task\n", encoding="utf-8")

    with pytest.raises(ValueError, match="替代链存在循环引用"):
        check_finalization(repo, "t001", task_dir)


def test_validate_spike_fail_closed_without_front_matter_or_invalid_metadata(tmp_path):
    repo = _init_repo(tmp_path)
    spike_dir = repo / "docs/spikes/s001_probe"
    spike_dir.mkdir(parents=True)
    report = spike_dir / "report.md"

    # 1. 彻底无 Front Matter，必须 fail-closed 抛出 TaskDataError
    report.write_text("# Spike report\n\n## 结论\n已验证。\n- 决定：是\n- 后续 task：t001\n", encoding="utf-8")
    with pytest.raises(TaskDataError, match="必须以 YAML front matter.*开头"):
        validate_spike(repo, spike_dir)

    # 2. Front Matter 格式不合法（未闭合），必须 fail-closed 抛出 TaskDataError
    report.write_text("---\nsid: \"s001\"\n# 未闭合\n# Spike report\n", encoding="utf-8")
    with pytest.raises(TaskDataError):
        validate_spike(repo, spike_dir)

    # 3. 元数据非法（如 status 为非法值），必须 fail-closed 抛出 TaskDataError
    report.write_text("---\nsid: \"s001\"\nslug: \"probe\"\nstatus: \"invalid_status\"\n---\n# Spike report\n", encoding="utf-8")
    with pytest.raises(TaskDataError, match="元数据非法"):
        validate_spike(repo, spike_dir)


def test_spikes_complete_rejects_slug_mismatch(tmp_path, monkeypatch, capsys):
    """complete 必须校验目录 slug，手工篡改 report.md slug 不得通过（DS L260）。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    spikes_mod.main(["new", "probe_complete"])
    spike_dir = repo / "docs/spikes/s001_probe_complete"
    _fill_report(repo, spike_dir)
    report = spike_dir / "report.md"
    fm, body = parse_front_matter(report)
    fm["slug"] = "tampered_slug"
    write_front_matter(report, fm, body)
    capsys.readouterr()

    with pytest.raises(SystemExit, match="slug 与预期不符"):
        spikes_mod.main(["complete", "s001"])
    assert parse_front_matter(report)[0]["status"] == "active"


def test_spikes_supersede_rejects_slug_mismatch(tmp_path, monkeypatch):
    """supersede 主条目同样校验目录 slug（DS L260）。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    spikes_mod.main(["new", "probe_old"])
    spikes_mod.main(["new", "probe_new"])
    s001_dir = repo / "docs/spikes/s001_probe_old"
    s002_dir = repo / "docs/spikes/s002_probe_new"
    _fill_report(repo, s002_dir)
    spikes_mod.main(["complete", "s002", "--write"])

    fm, body = parse_front_matter(s001_dir / "report.md")
    fm["slug"] = "tampered_slug"
    write_front_matter(s001_dir / "report.md", fm, body)

    with pytest.raises(SystemExit, match="slug 与预期不符"):
        spikes_mod.main(["supersede", "s001", "--by", "s002", "--reason", "更优方案"])


def test_spikes_list_warns_on_corrupted_front_matter(tmp_path, monkeypatch, capsys):
    """损坏 spike 归入 unknown 且打印 stderr 警告，不静默吞错（DS L259）。"""
    repo = _init_repo(tmp_path)
    _bind(monkeypatch, repo)
    spikes_mod.main(["new", "good_one"])
    broken = repo / "docs/spikes/s002_broken"
    broken.mkdir(parents=True)
    (broken / "report.md").write_text("---\nsid: s002\n未闭合\n", encoding="utf-8")
    capsys.readouterr()

    spikes_mod.main(["list"])
    default_view = capsys.readouterr()
    assert "s002_broken" not in default_view.out
    assert "警告" in default_view.err

    spikes_mod.main(["list", "--state", "unknown"])
    unknown_view = capsys.readouterr()
    assert "s002_broken" in unknown_view.out
    assert "警告" in unknown_view.err
