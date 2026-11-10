"""skill_registry 一致性与调用图校验专项测试（Issue #82）。"""

import json
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from skill_registry import render_registry


def _write_skill(root: Path, name: str, *, internal: bool = False, text: str = "") -> None:
    path = root / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    body = text or (
        "---\n"
        f"name: {name}\n"
        'opencode/autoinvoke: "false"\n'
        "disable-model-invocation: true\n"
        + ("user-invocable: false\n" if internal else "")
        + "---\nrun\n"
    )
    path.write_text(body, encoding="utf-8")


def _write_registry(root: Path, skills: dict, calls: list | None = None) -> None:
    path = root / "skills" / "registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"skills": skills, "calls": calls or []}), encoding="utf-8"
    )


def test_registry_renders_consistent_tree(tmp_path):
    _write_skill(
        tmp_path, "parent",
        text=(
            "---\n"
            "name: parent\n"
            'opencode/autoinvoke: "false"\n'
            "disable-model-invocation: true\n"
            "---\n"
            "load child\n"
        ),
    )
    _write_skill(tmp_path, "child", internal=True)
    _write_registry(
        tmp_path,
        {"parent": {"role": "public"}, "child": {"role": "internal"}},
        [{"parent": "parent", "child": "child"}],
    )
    output = render_registry(tmp_path)
    assert "`parent`" in output and "`child`" in output


def test_registry_rejects_unlisted_skill(tmp_path):
    _write_skill(tmp_path, "extra")
    _write_registry(tmp_path, {})
    with pytest.raises(ValueError, match="不一致"):
        render_registry(tmp_path)


def test_registry_rejects_unknown_role(tmp_path):
    _write_skill(tmp_path, "a")
    _write_registry(tmp_path, {"a": {"role": "owner"}})
    with pytest.raises(ValueError, match="未知 skill 角色"):
        render_registry(tmp_path)


def test_registry_rejects_missing_explicit_gate(tmp_path):
    _write_skill(tmp_path, "a", text="---\nname: a\n---\nrun\n")
    _write_registry(tmp_path, {"a": {"role": "public"}})
    with pytest.raises(ValueError, match="缺显式调用门禁"):
        render_registry(tmp_path)


def test_registry_rejects_internal_user_invocable(tmp_path):
    _write_skill(
        tmp_path, "a",
        text='---\nname: a\nopencode/autoinvoke: "false"\ndisable-model-invocation: true\n---\nrun\n',
    )
    _write_registry(tmp_path, {"a": {"role": "internal"}})
    with pytest.raises(ValueError, match="内部 skill 不能成为用户入口"):
        render_registry(tmp_path)


def test_registry_rejects_call_without_body_evidence(tmp_path):
    _write_skill(tmp_path, "parent")
    _write_skill(tmp_path, "child")
    _write_registry(
        tmp_path,
        {"parent": {"role": "public"}, "child": {"role": "public"}},
        [{"parent": "parent", "child": "child"}],
    )
    with pytest.raises(ValueError, match="调用边无正文依据"):
        render_registry(tmp_path)
