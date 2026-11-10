"""spec front matter 与 finish 闭环契约。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task.finalization import check_finalization
from repo_task.specs import SpecError, active_specs, load_specs, validate_specs

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
