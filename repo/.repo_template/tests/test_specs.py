"""spec front matter 与 finish 闭环契约。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.finalization import check_finalization
from repo_task.specs import (
    SpecError, active_specs, load_specs, parse_spec_text, render_spec_text, validate_specs,
)

pytestmark = pytest.mark.contract


def _spec(root: Path, slug: str, status: str = "active", tasks: str = "t001", body: str = "# Spec\n") -> Path:
    directory = root / "docs/specs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.md"
    path.write_text(f"---\nslug: {slug}\nstatus: {status}\ntasks: {tasks}\n---\n\n{body}", encoding="utf-8")
    return path


def _root(tmp_path: Path, *, note: str = "", spec=None) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    if spec is not None:
        _spec(root, *spec)
    task_dir = root / "docs/tasks/t001_alpha"
    task_dir.mkdir(parents=True)
    (task_dir / "progress.md").write_text("# Task 进展\n" + (note + "\n" if note else ""), encoding="utf-8")
    (task_dir / "task.md").write_text("# Task spec\n", encoding="utf-8")
    return root, task_dir


def test_load_active_spec(tmp_path):
    root, _ = _root(tmp_path, spec=("feature", "active", "t001, baseline"))
    specs = active_specs(root)
    assert specs[0]["slug"] == "feature"
    assert specs[0]["tasks"] == ["t001", "baseline"]
    assert specs[0]["title"] == "Spec"


def test_retired_spec_is_excluded_from_active(tmp_path):
    root, _ = _root(tmp_path, spec=("old", "retired", "t001"))
    assert active_specs(root) == []
    assert load_specs(root)[0]["status"] == "retired"


@pytest.mark.parametrize("slug,status,tasks,keyword", [
    ("feature", "blocked", "t001", "status"),
    ("feature", "active", "t001, t001", "重复"),
    ("feature", "active", "story", "token"),
    ("feature", "active", "", "至少"),
])
def test_invalid_spec_reports_error(tmp_path, slug, status, tasks, keyword):
    root = tmp_path / "repo"
    _spec(root, slug, status, tasks)
    with pytest.raises(SpecError, match=keyword):
        load_specs(root)
    assert validate_specs(root)


def test_slug_must_match_filename(tmp_path):
    root = tmp_path / "repo"
    path = _spec(root, "feature")
    path.write_text(path.read_text().replace("slug: feature", "slug: other"), encoding="utf-8")
    with pytest.raises(SpecError, match="文件名一致"):
        load_specs(root)


def test_current_tid_in_spec_passes_finalization(tmp_path):
    root, task_dir = _root(tmp_path, spec=("feature", "active", "t001"))
    check_finalization(root, "t001", task_dir)


def test_not_applicable_exemption_passes(tmp_path):
    root, task_dir = _root(tmp_path, note="- specs_index: not_applicable: 无长期 spec 变更")
    check_finalization(root, "t001", task_dir)


def test_missing_inclusion_and_exemption_fails(tmp_path):
    root, task_dir = _root(tmp_path)
    with pytest.raises(ValueError, match="未进入任何 active spec"):
        check_finalization(root, "t001", task_dir)


EXTRA_FIELD_TEXT = (
    "---\nslug: feature\nstatus: active\ntasks: [t001]\nnote: legacy\n---\n\n# Feature\n"
)


def test_extra_front_matter_field_is_rejected(tmp_path):
    root = tmp_path / "repo"
    (root / "docs/specs").mkdir(parents=True)
    (root / "docs/specs/feature.md").write_text(EXTRA_FIELD_TEXT, encoding="utf-8")
    with pytest.raises(SpecError, match="多余字段|只允许"):
        load_specs(root)
    problems = validate_specs(root)
    assert problems and any("多余字段" in item or "只允许" in item for item in problems)


def test_finish_rejects_extra_front_matter_field(tmp_path):
    root, task_dir = _root(tmp_path)
    (root / "docs/specs").mkdir(parents=True)
    (root / "docs/specs/feature.md").write_text(EXTRA_FIELD_TEXT, encoding="utf-8")
    with pytest.raises(ValueError, match="spec front matter 校验失败"):
        check_finalization(root, "t001", task_dir)


def test_parse_spec_text_is_public_codec():
    text = render_spec_text("feature", "active", ["t001", "baseline"], "# Feature\n")
    record = parse_spec_text(
        text, source="docs/specs/feature.md", path=Path("docs/specs/feature.md"),
    )
    assert record["slug"] == "feature"
    assert record["status"] == "active"
    assert record["tasks"] == ["t001", "baseline"]
    assert record["body"].strip() == "# Feature"
    assert record["title"] == "Feature"


@pytest.mark.parametrize("text,keyword", [
    ("---\nslug: feature\nstatus: active\n---\n\n# F\n", "至少"),
    ("---\nslug: feature\ntasks: [t001]\n---\n\n# F\n", "status"),
    ("---\nslug: feature\nstatus: active\ntasks: [story]\n---\n\n# F\n", "token"),
    ("---\nslug: feature\nstatus: active\ntasks: [t001, t001]\n---\n\n# F\n", "重复"),
    ("---\nslug: feature\nstatus: bogus\ntasks: [t001]\n---\n\n# F\n", "status"),
    ("---\nslug: other\nstatus: active\ntasks: [t001]\n---\n\n# F\n", "文件名一致"),
    (EXTRA_FIELD_TEXT, "多余字段"),
])
def test_parse_spec_text_rejects_invalid(text, keyword):
    with pytest.raises(SpecError, match=keyword):
        parse_spec_text(text, source="docs/specs/feature.md", path=Path("docs/specs/feature.md"))


@pytest.mark.parametrize("front,body", [
    ("---\nslug: feature\nstatus: active\ntasks: [t001, baseline]\n---\n", "\n# Feature\n"),
    ("---\nslug: feature\nstatus: active\ntasks: [t001,baseline]\n---\n", "\n\n# Feature\n\n正文\n"),
    ("---\nslug: feature\nstatus: active\ntasks: t001, baseline\n---\n", "# Feature\n"),
    ("---\nslug: feature\n\nstatus: active\ntasks: [t001, baseline]\n---\n", "# Feature\n"),
])
def test_formatting_is_not_part_of_contract(tmp_path, front, body):
    root = tmp_path / "repo"
    (root / "docs/specs").mkdir(parents=True)
    (root / "docs/specs/feature.md").write_text(front + "\n" + body, encoding="utf-8")
    assert validate_specs(root) == []
    spec = load_specs(root)[0]
    assert spec["tasks"] == ["t001", "baseline"]
    assert spec["title"] == "Feature"


@pytest.mark.parametrize("text,keyword", [
    ("---\nslug: feature\nstatus: active\ntasks: [t001]\n", "未闭合"),
    ("---\nslug: feature\nslug: feature\nstatus: active\ntasks: [t001]\n---\n", "重复键"),
    ("---\nslug: feature\nstatus active\ntasks: [t001]\n---\n", "缺冒号"),
])
def test_malformed_front_matter_uses_spec_error_boundary(tmp_path, text, keyword):
    with pytest.raises(SpecError, match=keyword):
        parse_spec_text(text, source="docs/specs/feature.md")
    root = tmp_path / "repo"
    path = _spec(root, "feature")
    path.write_text(text, encoding="utf-8")
    problems = validate_specs(root)
    assert len(problems) == 1 and keyword in problems[0]
    assert "feature.md" in problems[0]


@pytest.mark.parametrize("tasks", ["t001, baseline", ["t001", "baseline"]])
def test_build_spec_validates_fields_without_text_roundtrip(tasks):
    from repo_task.specs import build_spec

    record = build_spec(
        "feature", "active", tasks, "# Feature\n\n正文保留。\n",
        source="docs/specs/feature.md", path=Path("docs/specs/feature.md"),
    )
    assert record["tasks"] == ["t001", "baseline"]
    assert record["title"] == "Feature"
    assert record["body"] == "# Feature\n\n正文保留。\n"
    assert record["path"] == Path("docs/specs/feature.md")


@pytest.mark.parametrize("slug,status,tasks,keyword", [
    ("bad-slug", "active", ["t001"], "slug"),
    ("feature", "bogus", ["t001"], "status"),
    ("feature", "active", ["t001", "t001"], "重复"),
    ("feature", "active", ["story"], "token"),
    ("feature", "active", [], "至少"),
])
def test_build_spec_rejects_invalid_fields(slug, status, tasks, keyword):
    from repo_task.specs import build_spec

    with pytest.raises(SpecError, match=keyword):
        build_spec(slug, status, tasks, "# Feature\n", source="docs/specs/feature.md")


def test_missing_spec_front_matter_diagnosis_names_spec():
    with pytest.raises(SpecError, match="docs/specs/feature.md:1: 必须") as error:
        parse_spec_text("# Feature\n", source="docs/specs/feature.md")
    assert "progress.md" not in str(error.value)
