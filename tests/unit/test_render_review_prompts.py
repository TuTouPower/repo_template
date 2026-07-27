"""render_review_prompts.py 测试。"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pytest
import render_review_prompts as rrp
from render_review_prompts import (
    apply_placeholders,
    extract_section,
    parse_front_matter,
    render_review_prompts,
)


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


# --- apply_placeholders ---

def test_apply_placeholders_substitutes_all_keys():
    template = "{tid} {slug} {spec_path} {task_dir} {diff_anchor} {review_level} {contract_section} {context_section}"
    values = {
        "tid": "t001", "slug": "foo", "spec_path": "s.md",
        "task_dir": "d", "diff_anchor": "abc",
        "review_level": "full", "contract_section": "C", "context_section": "X",
    }
    assert apply_placeholders(template, values) == "t001 foo s.md d abc full C X"


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
