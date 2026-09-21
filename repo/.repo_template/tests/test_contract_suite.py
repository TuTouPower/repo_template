"""消费仓契约套件边界：标记范围、默认门禁命令、同步验证命令。"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_sync as rs

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
    "test_contract_suite.py",
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
    doctor = _section(text, "doctor_cmd")
    test_cmd = _section(text, "test_cmd")
    assert _pytest_lines(doctor) == [
        "pytest .repo_template/tests -q --collect-only -m contract",
    ]
    assert "command -v md_kx" in doctor
    assert _pytest_lines(test_cmd) == [
        "pytest .repo_template/tests -q -m contract",
    ]


def test_sync_apply_runs_contract_suite():
    assert rs.CONSUMER_TEST_COMMAND == (
        "pytest", ".repo_template/tests/", "-q", "-m", "contract",
    )
    assert rs.CONSUMER_TEST_TIMEOUT == 60
    source = rs._run_tests.__code__.co_names
    assert "CONSUMER_TEST_COMMAND" in source
    assert "CONSUMER_TEST_TIMEOUT" in source
