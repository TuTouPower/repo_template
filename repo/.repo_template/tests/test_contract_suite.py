"""消费仓契约套件边界：标记范围、默认门禁命令、同步验证命令。"""

from __future__ import annotations
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from template_sync import context as ctx
from template_sync.gates import _run_tests

pytestmark = pytest.mark.contract

CONTRACT_MODULES = frozenset({
    "test_task_save.py",
    "test_task_unverified.py",
    "test_task_document_validation.py",
    "test_task_archive_dir.py",
    "test_plan.py",
    "test_view_server.py",
    "test_check_review_status.py",
    "test_repo_cleanup.py",
    "test_specs_index.py",
    "test_contract_suite.py",
    "test_validation_policy.py",
})


def _h2_name(line: str) -> str | None:
    if not line.startswith("## ") or line.startswith("###"):
        return None
    return line[3:].strip().strip("`")


def _section(text: str, name: str) -> str:
    body: list[str] = []
    collecting = False
    for line in text.splitlines():
        heading = _h2_name(line)
        if heading is not None:
            if collecting:
                break
            collecting = heading == name
            continue
        if collecting:
            body.append(line)
    return "\n".join(body)


def _pytest_lines(section: str) -> list[str]:
    return [ln.strip() for ln in section.splitlines() if ln.strip().startswith("pytest ")]


def test_pytest_ini_registers_contract_marker():
    text = (TESTS_DIR / "pytest.ini").read_text(encoding="utf-8")
    assert "markers =" in text
    assert "contract:" in text
    assert "addopts" not in text


def test_contract_collection_matches_allowlist():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS_DIR), "--collect-only", "-q", "-m", "contract"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    modules = set()
    for line in result.stdout.splitlines():
        nodeid = line.strip()
        if "::" not in nodeid:
            continue
        name = Path(nodeid.split("::", 1)[0].replace("\\", "/")).name
        if name.endswith(".py"):
            modules.add(name)
    assert modules == CONTRACT_MODULES, (sorted(modules), result.stdout)


def test_testing_md_default_commands_use_contract_marker():
    text = (REPO_ROOT / "docs/blueprint/testing.md").read_text(encoding="utf-8")
    if _section(text, "verification_policy").strip() == "project_configured":
        pytest.skip("项目已显式选择验证策略，跳过模板默认命令检查")
    doctor = _section(text, "doctor_cmd")
    test_cmd = _section(text, "test_cmd")
    assert _pytest_lines(doctor) == [
        "pytest .repo_template/tests -q --collect-only -m contract",
    ], "自定义 test_cmd 的消费仓须先把 verification_policy 改为 project_configured 并填写 verification_notes"
    assert "command -v md_kx" in doctor
    assert _pytest_lines(test_cmd) == [
        "pytest .repo_template/tests -q -m contract",
    ], "自定义 test_cmd 的消费仓须先把 verification_policy 改为 project_configured 并填写 verification_notes"


def test_sync_apply_runs_contract_suite(monkeypatch):
    monkeypatch.setattr("template_sync.gates.validation_environment", lambda _root: {})
    assert ctx.CONSUMER_TEST_COMMAND == (
        "pytest", ".repo_template/tests/", "-q", "-m", "contract",
    )
    assert ctx.CONSUMER_TEST_TIMEOUT == 60
    calls = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if command and command[0] == "git":
            return real_run(command, **kwargs)
        calls["command"] = command
        calls["timeout"] = kwargs.get("timeout")
        return Result()

    monkeypatch.setattr("template_sync.gates.subprocess", SimpleNamespace(**{**vars(subprocess), "run": fake_run}))
    assert _run_tests(REPO_ROOT) is True
    assert calls["command"] == list(ctx.CONSUMER_TEST_COMMAND)
    assert calls["timeout"] == ctx.CONSUMER_TEST_TIMEOUT


def test_all_consumer_skills_disable_model_invocation():
    skills_dir = REPO_ROOT / ".repo_template/skills"
    skill_files = sorted(skills_dir.glob("*/SKILL.md"))
    assert skill_files
    for skill_md in skill_files:
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{skill_md} missing frontmatter"
        end = text.find("\n---\n", 4)
        assert end != -1, f"{skill_md} unclosed frontmatter"
        fm_lines = [ln.strip() for ln in text[4:end].splitlines() if ln.strip()]
        assert "disable-model-invocation: true" in fm_lines, (
            f"{skill_md.parent.name}/SKILL.md missing disable-model-invocation: true"
        )


def _runtime_union_annotations(tree) -> list[int]:
    """未启用延迟注解时，函数签名与模块/类级注解里的 `X | Y` 在导入期求值。"""
    import ast
    lines = []
    for node in ast.walk(tree):
        annotations = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
                if arg is not None and arg.annotation is not None:
                    annotations.append(arg.annotation)
            if node.returns is not None:
                annotations.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        for annotation in annotations:
            if any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(annotation)):
                lines.append(annotation.lineno)
    return lines


def test_toolchain_annotations_importable_on_python39():
    """模板脚本最低 Python 3.9：PEP 604 注解必须配 `from __future__ import annotations`。"""
    import ast
    problems = []
    for path in sorted(SCRIPTS_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        has_future = any(
            isinstance(node, ast.ImportFrom) and node.module == "__future__"
            and any(alias.name == "annotations" for alias in node.names)
            for node in tree.body
        )
        if has_future:
            continue
        for line in _runtime_union_annotations(tree):
            problems.append(f"{path.relative_to(SCRIPTS_DIR)}:{line}")
    assert not problems, "Python 3.9 导入期会求值 `X | Y` 注解：" + ", ".join(problems)
