"""消费仓契约套件边界：标记范围、默认门禁命令、同步验证命令。"""

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

from git_env_support import get_controlled_git_env

pytestmark = pytest.mark.contract

CONTRACT_MODULES = frozenset({
    "test_task_save.py",
    "test_task_unverified.py",
    "test_task_document_validation.py",
    "test_task_archive_dir.py",
    "test_plan.py",
    "test_view_server.py",
    "test_review_status.py",
    "test_repo_cleanup.py",
    "test_specs.py",
    "test_contract_suite.py",
    "test_validation_policy.py",
    "test_pending.py",
    "test_spikes.py",
    "test_task_core_asset_guard.py",
    "test_tooling_guards.py",
    "test_format_exempt.py",
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
        cwd=str(REPO_ROOT), env=get_controlled_git_env(),
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
            kwargs.setdefault("env", get_controlled_git_env())
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


def test_contract_suite_runs_on_contract_python():
    """消费仓契约测试须运行于工具链契约版本；模板文档一致性由工厂测试校验。"""
    from _runtime import MIN_PYTHON, PYTHON_VERSION

    assert sys.version_info[:3] >= MIN_PYTHON, (
        f"契约测试需在 Python {PYTHON_VERSION}+ 运行；当前 {sys.version.split()[0]}"
    )
