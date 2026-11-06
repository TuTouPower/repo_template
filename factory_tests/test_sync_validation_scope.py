"""SYNC validates project policy but executes only toolkit contract tests."""

from argparse import Namespace

import pytest

from test_repo_sync import env, _git
from repo_task.file_transaction import FileTransaction, fingerprint
from template_sync import context as ctx
from template_sync.apply import cmd_apply
from template_sync.state import read_state
from template_sync.transaction import cmd_transaction


def _install_contract_test(env, *, failing=False):
    test = env["src"] / ".repo_template/tests/test_x.py"
    test.write_text(
        "from pathlib import Path\n"
        "import pytest\n\n"
        "@pytest.mark.contract\n"
        "def test_toolkit_contract():\n"
        "    with Path('contract_gate_runs').open('a') as stream:\n"
        "        stream.write('contract\\n')\n"
        f"    assert {not failing!r}\n",
        encoding="utf-8",
    )
    _git(env["src"], "add", "-A")
    _git(env["src"], "commit", "-m", "Configure toolkit contract")
    return _git(env["src"], "rev-parse", "HEAD").stdout.strip()


def _set_business_policy(consumer, state):
    tests = consumer / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_business.py").write_text(
        "raise AssertionError('SYNC must not collect business tests')\n",
        encoding="utf-8",
    )
    policy = consumer / "docs/blueprint/testing.md"
    text = policy.read_text(encoding="utf-8")
    if state == "missing":
        policy.unlink()
        return None
    if state == "unconfigured":
        text = text.replace("project_configured", "template_default")
    elif state == "malformed":
        start = text.index("## test_cmd")
        end = text.index("## blackbox_verify", start)
        text = text[:start] + "## test_cmd\n\npytest -q\n\n" + text[end:]
    else:
        assert state == "failing_commands"
        for name in ("doctor_cmd", "test_cmd", "blackbox_verify"):
            text = text.replace(
                f"## {name}\n\n无",
                f"## {name}\n\n```bash\necho {name} >> business_gate_ran\nexit 7\n```",
            )
    policy.write_text(text, encoding="utf-8")
    return policy.read_bytes()


def _transaction(consumer):
    return FileTransaction(consumer, ".scratch/repo_sync_rollback")


def _commit_consumer(consumer):
    _git(consumer, "config", "user.name", "test")
    _git(consumer, "config", "user.email", "test@example.com")
    _git(consumer, "add", "-A")
    _git(consumer, "commit", "-m", "Apply template sync")


def test_apply_runs_only_toolkit_contracts(env):
    consumer = env["consumer"]
    head = _install_contract_test(env)
    policy_before = _set_business_policy(consumer, "failing_commands")

    assert cmd_apply(Namespace(skip_tests=False, decisions={})) == 0

    assert (consumer / "contract_gate_runs").read_text() == "contract\n"
    assert not (consumer / "business_gate_ran").exists()
    policy = consumer / "docs/blueprint/testing.md"
    assert (policy.read_bytes() if policy.exists() else None) == policy_before
    assert read_state()["last_synced_commit"] == head
    data = _transaction(consumer).read()
    assert data["status"] == "applied" and data["gates_verified"] is True


@pytest.mark.parametrize("policy_state", ["unconfigured", "malformed", "missing"])
@pytest.mark.parametrize("skip_tests", [False, True])
def test_apply_always_validates_policy_before_contract(env, policy_state, skip_tests):
    consumer = env["consumer"]
    _install_contract_test(env)
    policy_before = _set_business_policy(consumer, policy_state)
    state_before = env["state_file"].read_bytes()

    assert cmd_apply(Namespace(skip_tests=skip_tests, decisions={})) == 1

    assert not (consumer / "contract_gate_runs").exists()
    assert env["state_file"].read_bytes() == state_before
    policy = consumer / "docs/blueprint/testing.md"
    assert (policy.read_bytes() if policy.exists() else None) == policy_before
    assert not (consumer / ".repo_template/scripts/task.py").exists()
    assert _transaction(consumer).read()["status"] == "rolled_back"


@pytest.mark.parametrize("recheck_reason", ["skipped", "assets_changed", "unchanged"])
def test_accept_uses_only_toolkit_contracts_and_rechecks_when_needed(env, recheck_reason):
    consumer = env["consumer"]
    _install_contract_test(env)
    _set_business_policy(consumer, "failing_commands")
    skipped = recheck_reason == "skipped"
    assert cmd_apply(Namespace(skip_tests=skipped, decisions={})) == 0
    if recheck_reason == "assets_changed":
        task = consumer / ".repo_template/scripts/task.py"
        task.write_text(task.read_text() + "# resolved after apply\n", encoding="utf-8")
    _commit_consumer(consumer)

    assert cmd_transaction(Namespace(action="accept")) == 0

    expected_runs = 2 if recheck_reason == "assets_changed" else 1
    assert (consumer / "contract_gate_runs").read_text() == "contract\n" * expected_runs
    assert not (consumer / "business_gate_ran").exists()
    assert _transaction(consumer).read()["status"] == "accepted"


@pytest.mark.parametrize("policy_state", ["unconfigured", "malformed", "missing"])
@pytest.mark.parametrize("verified", [False, True])
def test_accept_revalidates_policy_without_managed_asset_changes(env, policy_state, verified):
    consumer = env["consumer"]
    _install_contract_test(env)
    _set_business_policy(consumer, "failing_commands")
    assert cmd_apply(Namespace(skip_tests=not verified, decisions={})) == 0
    data = _transaction(consumer).read()
    assert data["gates_verified"] is verified
    assert "docs/blueprint/testing.md" not in data["paths"]
    _set_business_policy(consumer, policy_state)
    assert fingerprint(consumer, data["paths"]) == data["after"]
    _commit_consumer(consumer)

    with pytest.raises(ctx.SyncError, match="缺 docs/blueprint/testing.md|项目验证策略尚未确认|test_cmd 必须有"):
        cmd_transaction(Namespace(action="accept"))

    runs = consumer / "contract_gate_runs"
    assert (runs.read_text() if runs.exists() else "") == ("contract\n" if verified else "")
    assert not (consumer / "business_gate_ran").exists()
    assert _transaction(consumer).read()["status"] == "applied"


def test_apply_contract_failure_restores_assets_and_state(env):
    consumer = env["consumer"]
    _install_contract_test(env, failing=True)
    _set_business_policy(consumer, "failing_commands")
    state_before = env["state_file"].read_bytes()
    agents = consumer / "AGENTS.md"
    agents.write_text("private project rules\n", encoding="utf-8")

    assert cmd_apply(Namespace(skip_tests=False, decisions={"AGENTS.md": "update"})) == 1

    assert (consumer / "contract_gate_runs").read_text() == "contract\n"
    assert not (consumer / "business_gate_ran").exists()
    assert agents.read_text() == "private project rules\n"
    assert env["state_file"].read_bytes() == state_before
    assert not (consumer / ".repo_template/scripts/task.py").exists()
    transaction = _transaction(consumer)
    assert transaction.read()["status"] == "rolled_back"
    assert (transaction.backup / "AGENTS.md").read_text() == "private project rules\n"


def test_accept_contract_failure_keeps_transaction_applied(env):
    consumer = env["consumer"]
    _install_contract_test(env, failing=True)
    _set_business_policy(consumer, "failing_commands")
    assert cmd_apply(Namespace(skip_tests=True, decisions={})) == 0
    _commit_consumer(consumer)

    with pytest.raises(ctx.SyncError, match="contract 复验失败"):
        cmd_transaction(Namespace(action="accept"))

    assert (consumer / "contract_gate_runs").read_text() == "contract\n"
    assert _transaction(consumer).read()["status"] == "applied"
