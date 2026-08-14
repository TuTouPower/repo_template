"""render_review_prompts.py 测试。"""
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pytest
import render_review_prompts as rrp
from render_review_prompts import (
    apply_placeholders,
    extract_section,
    parse_front_matter,
    render_review_prompts,
)

REAL_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "docs" / "review_prompts"
REAL_VALIDATE_DIFF_ANCHOR = rrp.validate_diff_anchor


@pytest.fixture(autouse=True)
def stub_diff_anchor_validation(monkeypatch):
    """普通渲染单测不依赖真实 Git；drift 测试仍用真实 git show。"""
    monkeypatch.setattr(rrp, "validate_diff_anchor", lambda anchor: anchor)


# --- parse_front_matter ---

def test_parse_front_matter_reads_keys(tmp_path):
    p = tmp_path / "task.md"
    p.write_text(
        '---\ntid: t001\nslug: foo\ndiff_anchor: "abc1234"\n---\nbody\n',
        encoding="utf-8",
    )
    fm = parse_front_matter(p)
    assert fm["tid"] == "t001"
    assert fm["slug"] == "foo"
    assert fm["diff_anchor"] == "abc1234"


def test_parse_front_matter_strips_quotes(tmp_path):
    p = tmp_path / "task.md"
    p.write_text(
        '---\ntitle: "含空格 的 标题"\n---\nx\n',
        encoding="utf-8",
    )
    fm = parse_front_matter(p)
    assert fm["title"] == "含空格 的 标题"


# --- extract_section ---

def test_extract_section_returns_body_until_next_h2():
    text = "前文\n## 契约区\n内容A\n\n更多\n## 上下文区\n其它\n"
    out = extract_section(text, "## 契约区")
    assert "内容A" in out
    assert "更多" in out
    assert "其它" not in out


def test_extract_section_missing_returns_empty():
    assert extract_section("## 别的\n内容\n", "## 契约区") == ""


def test_extract_section_last_to_end():
    text = "## 契约区\n仅此一节\n内容\n"
    out = extract_section(text, "## 契约区")
    assert "仅此一节" in out
    assert "内容" in out


def test_extract_section_requires_exact_h2():
    text = (
        "正文引用 `## 契约区`\n"
        "## 契约区补充\n错误\n"
        "### 契约区\n仍错误\n"
        "## 契约区\n正确\n"
    )
    assert extract_section(text, "## 契约区") == "正确"


def test_extract_section_ignores_fenced_heading():
    text = (
        "```markdown\n## 契约区\n伪内容\n```\n"
        "## 契约区\n真实内容\n## 上下文区\n其它\n"
    )
    assert extract_section(text, "## 契约区") == "真实内容"


def test_extract_section_ignores_shorter_fence_inside_block():
    text = (
        "````markdown\n## 契约区\n伪内容\n```\n"
        "仍是伪内容\n````\n"
        "## 契约区\n真实内容\n"
    )
    assert extract_section(text, "## 契约区") == "真实内容"


# --- apply_placeholders ---

def test_apply_placeholders_substitutes_all_keys():
    template = "{tid} {slug} {spec_path} {task_dir_rel} {task_dir} {repo_root} {diff_anchor} {review_level} {contract_section} {context_section}"
    values = {
        "tid": "t001", "slug": "foo", "spec_path": "s.md",
        "task_dir_rel": "d", "task_dir": "/abs/d", "repo_root": "/abs",
        "diff_anchor": "abc",
        "review_level": "full", "contract_section": "C", "context_section": "X",
    }
    assert apply_placeholders(template, values) == "t001 foo s.md d /abs/d /abs abc full C X"


def test_apply_placeholders_leaves_unknown_braces():
    out = apply_placeholders("{tid} {unknown}", {"tid": "t001"})
    assert "t001" in out
    assert "{unknown}" in out


# --- render_review_prompts ---

def _make_prompts_dir(tmp_path):
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "code_prompt.txt").write_text(
        "[code]\ntid={tid}\ncontract={contract_section}\ncontext={context_section}\n",
        encoding="utf-8",
    )
    (d / "test_prompt.txt").write_text(
        "[test]\ntid={tid}\ncontract={contract_section}\ncontext={context_section}\n",
        encoding="utf-8",
    )
    (d / "general_prompt.txt").write_text(
        "[general]\ntid={tid}\ncontract={contract_section}\n",
        encoding="utf-8",
    )
    (d / "share_prompt.txt").write_text("[share]\n", encoding="utf-8")
    return d


def _make_task_dir(tmp_path, level="full", contract="AC1", context="策略"):
    task_dir = tmp_path / "t001_foo"
    task_dir.mkdir()
    (task_dir / "task.md").write_text(
        f'---\ntid: t001\nslug: foo\ndiff_anchor: "abc1234"\nreview_level: "{level}"\n---\nbody\n',
        encoding="utf-8",
    )
    (task_dir / "spec.md").write_text(
        f"# Spec\n## 契约区\n\n{contract}\n\n## 上下文区\n\n{context}\n",
        encoding="utf-8",
    )
    return task_dir


def test_render_full_returns_two_prompts(tmp_path, monkeypatch):
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", _make_prompts_dir(tmp_path))
    task_dir = _make_task_dir(tmp_path)
    out = render_review_prompts(task_dir)
    assert set(out.keys()) == {"code_review_prompt.md", "test_review_prompt.md"}
    for content in out.values():
        assert "t001" in content
        assert "AC1" in content
        assert "策略" in content


def test_render_single_returns_one_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", _make_prompts_dir(tmp_path))
    task_dir = _make_task_dir(tmp_path, level="single")
    out = render_review_prompts(task_dir)
    assert list(out.keys()) == ["general_review_prompt.md"]
    assert "t001" in out["general_review_prompt.md"]


def test_render_rejects_invalid_review_level(tmp_path, monkeypatch):
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", _make_prompts_dir(tmp_path))
    task_dir = _make_task_dir(tmp_path, level="singel")
    with pytest.raises(SystemExit, match="review_level"):
        render_review_prompts(task_dir)


def test_render_rejects_spec_outside_repository(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "private.md"
    outside.write_text("## 契约区\nsecret\n", encoding="utf-8")
    monkeypatch.setattr(rrp, "REPO_ROOT", repo)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", _make_prompts_dir(repo))
    task_dir = _make_task_dir(repo)
    (task_dir / "task.md").write_text(
        "---\ntid: t001\nslug: foo\ndiff_anchor: abc\n"
        "spec_path: ../private.md\n---\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="inside repository"):
        render_review_prompts(task_dir)


def test_render_rejects_external_spec_symlink(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "private.md"
    outside.write_text("## 契约区\nsecret\n", encoding="utf-8")
    monkeypatch.setattr(rrp, "REPO_ROOT", repo)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", _make_prompts_dir(repo))
    task_dir = _make_task_dir(repo)
    (repo / "linked_spec.md").symlink_to(outside)
    (task_dir / "task.md").write_text(
        "---\ntid: t001\nslug: foo\ndiff_anchor: abc\n"
        "spec_path: linked_spec.md\n---\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="inside repository"):
        render_review_prompts(task_dir)


def test_render_rejects_backslash_spec_path(tmp_path, monkeypatch):
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", _make_prompts_dir(tmp_path))
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "task.md").write_text(
        "---\ntid: t001\nslug: foo\ndiff_anchor: abc\n"
        "spec_path: docs\\spec.md\n---\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="POSIX"):
        render_review_prompts(task_dir)


def test_render_missing_contract_section_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", _make_prompts_dir(tmp_path))
    task_dir = tmp_path / "t001_foo"
    task_dir.mkdir()
    (task_dir / "task.md").write_text(
        '---\ntid: t001\nslug: foo\ndiff_anchor: "abc"\n---\nx\n',
        encoding="utf-8",
    )
    (task_dir / "spec.md").write_text("# Spec\n无契约区\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="契约区"):
        render_review_prompts(task_dir)


def test_render_missing_diff_anchor_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", _make_prompts_dir(tmp_path))
    task_dir = tmp_path / "t001_foo"
    task_dir.mkdir()
    (task_dir / "task.md").write_text(
        '---\ntid: t001\nslug: foo\ndiff_anchor: ""\n---\nx\n',
        encoding="utf-8",
    )
    (task_dir / "spec.md").write_text("# Spec\n## 契约区\nAC\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="diff_anchor"):
        render_review_prompts(task_dir)


def test_render_includes_share_section(tmp_path, monkeypatch):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "code_prompt.txt").write_text("CODE {tid}\n", encoding="utf-8")
    (prompts / "test_prompt.txt").write_text("TEST {tid}\n", encoding="utf-8")
    (prompts / "general_prompt.txt").write_text("GEN {tid}\n", encoding="utf-8")
    (prompts / "share_prompt.txt").write_text("SHARED\n", encoding="utf-8")
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", prompts)
    task_dir = _make_task_dir(tmp_path)
    out = render_review_prompts(task_dir)
    # share 内容附加在每路 prompt 末尾
    assert "SHARED" in out["code_review_prompt.md"]
    assert "SHARED" in out["test_review_prompt.md"]


# --- contract drift notice（真实 git 仓库） ---

def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
    )


def _init_repo_with_task(tmp_path, monkeypatch, contract="AC1"):
    """真实 git 仓库：prompts 模板 + t001_foo task（spec 已提交），返回 task_dir。"""
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", _make_prompts_dir(tmp_path))
    task_dir = _make_task_dir(tmp_path, contract=contract)
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    anchor = _git(tmp_path, "rev-parse", "--short", "HEAD").stdout.strip()
    (task_dir / "task.md").write_text(
        f'---\ntid: t001\nslug: foo\ndiff_anchor: "{anchor}"\nreview_level: "full"\n---\nbody\n',
        encoding="utf-8",
    )
    return task_dir


def test_validate_diff_anchor_returns_full_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "x").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", "x")
    _git(tmp_path, "commit", "-m", "init")
    short = _git(tmp_path, "rev-parse", "--short", "HEAD").stdout.strip()
    full = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    assert REAL_VALIDATE_DIFF_ANCHOR(short) == full


def test_validate_diff_anchor_rejects_unknown_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    _git(tmp_path, "init", "-b", "main")
    with pytest.raises(SystemExit, match="invalid diff_anchor"):
        REAL_VALIDATE_DIFF_ANCHOR("definitely-not-a-ref")


def test_drift_notice_appended_when_contract_changed(tmp_path, monkeypatch):
    task_dir = _init_repo_with_task(tmp_path, monkeypatch)
    (task_dir / "spec.md").write_text(
        "# Spec\n## 契约区\n\nAC1 改宽\n\n## 上下文区\n\n策略\n",
        encoding="utf-8",
    )
    out = render_review_prompts(task_dir)
    for content in out.values():
        assert "契约区 drift 警告" in content
        assert "AC1 改宽" in content
        assert "\n-AC1\n" in content  # unified diff 的删除行


def test_no_drift_notice_when_only_context_changed(tmp_path, monkeypatch):
    task_dir = _init_repo_with_task(tmp_path, monkeypatch)
    (task_dir / "spec.md").write_text(
        "# Spec\n## 契约区\n\nAC1\n\n## 上下文区\n\n策略 v2\n",
        encoding="utf-8",
    )
    out = render_review_prompts(task_dir)
    for content in out.values():
        assert "drift" not in content


def test_drift_check_skipped_outside_git_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", _make_prompts_dir(tmp_path))
    task_dir = _make_task_dir(tmp_path)
    out = render_review_prompts(task_dir)
    assert "drift" not in out["code_review_prompt.md"]
    assert "drift 检查跳过" in capsys.readouterr().err


def test_parse_front_matter_strips_inline_comment(tmp_path):
    p = tmp_path / "task.md"
    p.write_text(
        '---\ndiff_anchor: abc1234  # start 写入\n---\nx\n',
        encoding="utf-8",
    )
    assert parse_front_matter(p)["diff_anchor"] == "abc1234"


# --- 真实模板锁（防落点回退为相对路径 / 假防线复活） ---

def _render_real_templates(tmp_path, monkeypatch, level="full"):
    """真实 prompt 模板渲染：task_dir 必须展开为绝对路径、repo_root 阻断校验就位。"""
    monkeypatch.setattr(rrp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(rrp, "TEMPLATES_DIR", REAL_TEMPLATES_DIR)
    task_dir = _make_task_dir(tmp_path, level=level)
    return render_review_prompts(task_dir), str(tmp_path.resolve())


def test_real_templates_cwd_probe_and_anchored_git(tmp_path, monkeypatch):
    """首步须用不带 -C 的 rev-parse 探测会话 cwd（去空白精确比对）；后续 git 一律 -C 带引号锚定，不依赖 cd。"""
    out, root = _render_real_templates(tmp_path, monkeypatch)
    for content in out.values():
        # 首步：无 -C 探测 cwd（-C 指定路径后恒真，探测不到 cwd 错位）
        assert "不带" in content and "`git rev-parse --show-toplevel`" in content
        assert f"git -C {root} rev-parse" not in content
        # 去首尾空白后精确比对（rev-parse stdout 带末尾换行，字面相等恒假）
        assert f"去掉首尾空白后必须精确等于 `{root}`" in content
        # 审阅用 git 全部 -C '{repo_root}' 锚定（diff_anchor 为 stub 值 abc1234）
        assert f"git -C '{root}' diff abc1234" in content
        # 不得依赖 cd（bash cd 不改变后续工具工作根）
        assert "随后 `cd" not in content
        # 旧假防线不得复活
        assert "所属仓库一致" not in content


def test_real_templates_output_line_absolute_and_rel(tmp_path, monkeypatch):
    """输出指令行：绝对落点 + 仓库内相对路径 task_dir_rel 一并展示。"""
    out, root = _render_real_templates(tmp_path, monkeypatch)
    assert f"输出到 `{root}/t001_foo/review_code.md`（仓库内相对路径 `t001_foo`）" in out["code_review_prompt.md"]
    assert f"输出到 `{root}/t001_foo/review_test.md`（仓库内相对路径 `t001_foo`）" in out["test_review_prompt.md"]


def test_real_templates_single_general_anchored(tmp_path, monkeypatch):
    out, root = _render_real_templates(tmp_path, monkeypatch, level="single")
    content = out["general_review_prompt.md"]
    assert f"输出到 `{root}/t001_foo/review_general.md`（仓库内相对路径 `t001_foo`）" in content
    assert "不带" in content and "`git rev-parse --show-toplevel`" in content
    assert f"git -C '{root}' diff abc1234" in content
