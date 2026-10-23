"""End-to-end regressions for the architectural review, with real Git state."""

import json
import shutil
import subprocess
import sys
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from test_dispatch_integration import (
    git_repo, _git, _task_cli, _reserve, _identity_args, _prepare_done,
    _cleanup, _worktree,
)
from test_repo_sync import env
from repo_task.documents import parse_front_matter, write_front_matter
from repo_task.file_transaction import FileTransaction
from repo_task.monitoring import review_scope_fingerprint
from repo_task.validation import ValidationError, run_checks
import repo_sync as rs
from export_template import export_template
from skill_registry import render_registry

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _edit(repo, tid, **values):
    path = next((repo / "docs/tasks").glob(f"{tid}_*/task.md"))
    fm, body = parse_front_matter(path)
    fm.update(values)
    write_front_matter(path, fm, body)


def _fm(repo, tid):
    return parse_front_matter(next((repo / "docs/tasks").glob(f"{tid}_*/task.md")))[0]


@pytest.mark.parametrize("new_target", [False, True])
def test_merge_preserves_downstream_and_unions_external_dependencies(git_repo, new_target):
    repo = git_repo
    for slug in ("delta", "epsilon"):
        result = _task_cli(repo, "add", "--title", slug, "--slug", slug)
        assert result.returncode == 0, result.stderr
    target = "t005" if new_target else "t001"
    _edit(repo, "t001", depends_on="t004", conflicts_with="t004")
    _edit(repo, "t003", depends_on="t001,t002", conflicts_with="t002")
    result = _task_cli(repo, "merge-backlog", target, "--sources", "t001,t002")
    assert result.returncode == 0, result.stderr
    assert _fm(repo, "t003")["depends_on"] == target
    assert _fm(repo, "t003")["conflicts_with"] == target
    assert _fm(repo, target)["depends_on"] == "t004"
    assert "t004" in _fm(repo, target)["conflicts_with"]
    for tid in {"t001", "t002"} - {target}:
        assert list((repo / "docs/archive/tasks").glob(f"{tid}_*/task.md"))
    assert _task_cli(repo, "plan", "--serial", "--json").returncode == 0
    # The downstream still cannot start before the replacement completes.
    assert _task_cli(repo, "start", "t003").returncode != 0


def test_merge_cycle_rejection_is_zero_write(git_repo):
    _edit(git_repo, "t001", depends_on="t003")
    _edit(git_repo, "t003", depends_on="t002")
    before = {p: p.read_bytes() for p in (git_repo / "docs/tasks").glob("t*/task.md")}
    result = _task_cli(git_repo, "merge-backlog", "t001", "--sources", "t001,t002")
    assert result.returncode != 0 and "依赖环" in result.stderr
    assert all(path.read_bytes() == content for path, content in before.items())


def test_merge_rejects_active_conflict_peer_without_partial_rewrite(git_repo):
    _edit(git_repo, "t003", conflicts_with="t001")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "graph")
    assert _task_cli(git_repo, "start", "t003").returncode == 0
    before = _fm(git_repo, "t001")
    result = _task_cli(git_repo, "merge-backlog", "t002", "--sources", "t001,t002")
    assert result.returncode != 0 and "不可编辑" in result.stderr
    assert _fm(git_repo, "t001") == before


def test_execution_claim_serializes_two_contexts_and_requires_stopped_takeover(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    identity = _reserve(git_repo, "t001")
    def claim(owner):
        return _task_cli(git_repo, "attempt", "claim", "t001", *_identity_args(identity), "--owner-id", owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["session_a", "session_b"]))
    assert sorted(result.returncode for result in results) == [0, 1]
    winner = json.loads(next(result.stdout for result in results if result.returncode == 0))["owner_id"]
    worktree = _worktree(git_repo, "t001")
    refused = _task_cli(worktree, "preflight", "t001", "--owner-id", "session_c")
    assert refused.returncode != 0 and "执行权" in refused.stdout
    assert _task_cli(worktree, "preflight", "t001", "--owner-id", winner).returncode == 0
    wrong = _task_cli(git_repo, "attempt", "claim", "t001", *_identity_args(identity),
                      "--owner-id", "session_c", "--replace-owner", "outdated", "--confirm-stopped")
    assert wrong.returncode != 0
    takeover = _task_cli(git_repo, "attempt", "claim", "t001", *_identity_args(identity),
                         "--owner-id", "session_c", "--replace-owner", winner, "--confirm-stopped")
    assert takeover.returncode == 0, takeover.stderr
    assert _task_cli(worktree, "preflight", "t001", "--owner-id", winner).returncode != 0


def test_active_review_upgrade_invalidates_old_scope_and_forbids_downgrade(git_repo):
    _edit(git_repo, "t001", review_level="single")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "single review")
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    w = _worktree(git_repo, "t001")
    fm = _fm(w, "t001")
    old = review_scope_fingerprint(fm["diff_anchor"], "docs/tasks/t001_alpha", repo_root=w)
    assert _task_cli(git_repo, "review-upgrade", "t001", "--reason", "auth surface").returncode != 0
    result = _task_cli(w, "review-upgrade", "t001", "--reason", "auth surface")
    assert result.returncode == 0, result.stderr
    assert _fm(w, "t001")["review_level"] == "full"
    assert review_scope_fingerprint(fm["diff_anchor"], "docs/tasks/t001_alpha", repo_root=w) != old
    assert _task_cli(w, "review-upgrade", "t001", "--reason", "again").returncode != 0
    assert _task_cli(w, "edit", "t001", "--review-level", "single").returncode != 0


def test_default_goal_uses_dependency_order_even_with_reverse_conflict(git_repo):
    _edit(git_repo, "t001", depends_on="t002", conflicts_with="t002")
    result = _task_cli(git_repo, "goal")
    assert result.returncode == 0, result.stderr
    queue = json.loads((git_repo / "docs/runtime/goal_queue.json").read_text())["queue"]
    assert queue.index("t002") < queue.index("t001")


def test_lost_ledger_import_validates_final_git_evidence_then_allows_cleanup(git_repo):
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    ledger = git_repo / "docs/runtime/dispatch_ledger.jsonl"
    ledger.unlink()
    refused = _task_cli(git_repo, "ledger", "import", "t001")
    assert refused.returncode != 0
    imported = _task_cli(git_repo, "ledger", "import", "t001", "--confirm-stopped")
    assert imported.returncode == 0, imported.stderr
    assert json.loads(imported.stdout)["execution_id"] == identity["execution_id"]
    assert _task_cli(git_repo, "ledger", "import", "t001", "--confirm-stopped").returncode != 0
    _cleanup(git_repo, "t001", identity)
    assert not _worktree(git_repo, "t001").exists()


def test_import_rejects_stale_review_without_creating_false_completed_record(git_repo):
    _prepare_done(git_repo, "t001", "alpha")
    w = _worktree(git_repo, "t001")
    (w / "unreviewed.txt").write_text("unreviewed")
    _git(w, "add", "-A")
    _git(w, "commit", "--amend", "--no-edit")
    ledger = git_repo / "docs/runtime/dispatch_ledger.jsonl"
    ledger.unlink()
    result = _task_cli(git_repo, "ledger", "import", "t001", "--confirm-stopped")
    assert result.returncode != 0 and "review" in result.stderr
    assert not ledger.exists()


def test_active_missing_ledger_is_not_mistaken_for_start_without_reservation(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    _reserve(git_repo, "t001")
    (git_repo / "docs/runtime/dispatch_ledger.jsonl").unlink()
    state = _task_cli(git_repo, "recovery", "t001")
    assert state.returncode == 0, state.stderr
    assert json.loads(state.stdout)["phase"] == "runtime_missing"
    assert _task_cli(git_repo, "attempt", "reserve", "t001", "--executor", "inline").returncode != 0


def test_goal_reports_execution_complete_separately_from_delivery(git_repo):
    assert _task_cli(git_repo, "goal", "t001").returncode == 0
    identity, _, _ = _prepare_done(git_repo, "t001", "alpha")
    _cleanup(git_repo, "t001", identity)
    result = _task_cli(git_repo, "goal-check")
    assert result.returncode == 0, result.stderr
    assert "GOAL_QUEUE_COMPLETE" in result.stdout
    assert "execution_complete=true integrated=0/1" in result.stdout
    assert "awaiting_merge=1 tids=t001" in result.stdout


def test_unconfigured_business_verification_blocks_execution_but_not_creation(git_repo):
    path = git_repo / "docs/blueprint/testing.md"
    path.write_text(path.read_text().replace("project_configured", "template_default"))
    creation = _task_cli(git_repo, "preflight", "t001", "--creation")
    assert creation.returncode == 0 and "项目验证策略尚未确认" in creation.stdout
    execution = _task_cli(git_repo, "preflight", "t001", "--allow-backlog")
    assert execution.returncode != 0 and "不能代替业务验收" in execution.stdout


def test_project_gates_stop_on_failure_and_pipe_failure(env):
    consumer = env["consumer"]
    path = consumer / "docs/blueprint/testing.md"
    path.write_text(path.read_text().replace("## test_cmd\n\n无", "## test_cmd\n\n```bash\nfalse | true\n```"))
    with pytest.raises(ValidationError, match="test_cmd 失败"):
        run_checks(consumer)


def test_sync_business_failure_restores_assets_state_and_preserves_backup(env, monkeypatch):
    consumer = env["consumer"]
    old_state = env["state_file"].read_bytes()
    agents = consumer / "AGENTS.md"
    agents.write_text("private project rules\n")
    policy = consumer / "docs/blueprint/testing.md"
    policy.write_text(policy.read_text().replace("## test_cmd\n\n无", "## test_cmd\n\n```bash\nexit 7\n```"))
    monkeypatch.setattr(rs, "_run_tests", lambda _: True)
    result = rs.cmd_apply(Namespace(skip_tests=False, decisions={"AGENTS.md": "update"}))
    assert result == 1
    assert agents.read_text() == "private project rules\n"
    assert env["state_file"].read_bytes() == old_state
    assert not (consumer / ".repo_template/scripts/task.py").exists()
    backup = consumer / ".scratch/repo_sync_rollback"
    assert (backup / "AGENTS.md").read_text() == "private project rules\n"
    assert json.loads((backup / "transaction.json").read_text())["status"] == "rolled_back"


def test_transaction_recovers_across_process_boundary_and_protects_later_edits(tmp_path):
    root = tmp_path / "consumer"
    root.mkdir()
    (root / "asset").write_text("original")
    first = FileTransaction(root, ".scratch/recovery")
    first.begin(["asset", "new_asset"])
    (root / "asset").write_text("upgrade")
    (root / "new_asset").write_text("created")
    first.applied()
    (root / "asset").write_text("later user edit")
    second = FileTransaction(root, ".scratch/recovery")
    with pytest.raises(ValueError, match="现场发生变化"):
        second.rollback()
    second.rollback(confirm_restore=True)
    assert (root / "asset").read_text() == "original"
    assert not (root / "new_asset").exists()
    assert (second.backup / "asset").read_text() == "original"


@pytest.mark.parametrize("source_kind", ["product", "factory", "url"])
def test_source_runner_supports_all_layouts_without_preparatory_asset_writes(env, source_kind):
    src, consumer = env["src"], env["consumer"]
    if source_kind == "factory":
        factory = src.parent / "factory"
        shutil.copytree(src, factory / "repo", ignore=shutil.ignore_patterns(".git"))
        _git(factory, "init", "-b", "main")
        _git(factory, "config", "user.email", "test@example.com")
        _git(factory, "config", "user.name", "test")
        _git(factory, "add", "-A")
        _git(factory, "commit", "-m", "factory")
        source = str(factory)
    else:
        source = src.as_uri() if source_kind == "url" else str(src)
    script = SCRIPTS / "repo_sync.py"
    before = env["state_file"].read_bytes()
    for command in ("status", "plan"):
        result = subprocess.run([sys.executable, str(script), "--consumer", str(consumer),
                                 "--source", source, command], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert env["state_file"].read_bytes() == before
        assert not (consumer / ".repo_template/scripts").exists()
    result = subprocess.run([sys.executable, str(script), "--consumer", str(consumer),
                             "--source", source, "apply", "--skip-tests"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (consumer / ".repo_template/scripts/task.py").exists()
    assert (consumer / ".repo_template/sync_manifest.json").exists()


def test_dirty_source_requires_explicit_development_mode(env):
    (env["src"] / ".repo_template/scripts/task.py").write_text("changed\n")
    with pytest.raises(rs.SyncError, match="dirty"):
        rs.cmd_apply(Namespace(skip_tests=True, decisions={}))
    assert not (env["consumer"] / ".repo_template/scripts/task.py").exists()


def test_sync_rejects_source_drift_before_asset_transaction(env):
    with pytest.raises(rs.SyncError, match="来源版本已变化"):
        rs.cmd_apply(Namespace(skip_tests=True, decisions={}, expected_commit="0" * 40))
    assert not (env["consumer"] / ".scratch/repo_sync_rollback").exists()


def test_url_cache_never_silently_reuses_a_different_source(env):
    state = {"template_source": {"kind": "url", "value": env["src"].as_uri()}}
    rs.resolve_src(state)
    other = env["src"].parent / "other_source"
    shutil.copytree(env["src"], other)
    state["template_source"]["value"] = other.as_uri()
    with pytest.raises(rs.SyncError, match="origin 与 template_source 不符"):
        rs.resolve_src(state)
    assert not (env["consumer"] / ".repo_template/scripts/task.py").exists()


def test_fixed_checkout_keeps_canonical_url_identity_and_supports_offline_apply(env, monkeypatch):
    identity = env["src"].as_uri()
    state = {"template_source": {"kind": "url", "value": identity}}
    checkout = rs.resolve_src(state)
    monkeypatch.setattr(rs, "SOURCE_OVERRIDE", identity)
    monkeypatch.setattr(rs, "CHECKOUT_OVERRIDE", checkout)
    assert rs.cmd_apply(Namespace(skip_tests=True, decisions={}, expected_commit=env["head"])) == 0
    stored = json.loads(env["state_file"].read_text())
    assert stored["template_source"] == {"kind": "url", "value": identity}
    assert stored["resolved_checkout"] == str(checkout)


def test_shared_sync_policy_and_version_survive_local_state_loss(env):
    rs.cmd_prompt(Namespace(action="add", text="保留业务规则", tags="business", scope="shared"))
    assert rs.cmd_apply(Namespace(skip_tests=True, decisions={})) == 0
    assert any(prompt["text"] == "保留业务规则" for prompt in rs.active_prompts(rs.read_state()))
    env["state_file"].unlink()
    assert rs.read_state()["last_synced_commit"] == env["head"]
    rs.cmd_init(Namespace(source=str(env["src"])))
    assert rs.read_state()["last_synced_commit"] == env["head"]
    assert any(prompt["text"] == "保留业务规则" for prompt in rs.active_prompts(rs.read_state()))


def test_sync_state_write_failure_restores_entire_upgrade(env, monkeypatch):
    previous = env["state_file"].read_bytes()
    def fail_write(_):
        raise OSError("simulated state write failure")
    monkeypatch.setattr(rs, "write_state", fail_write)
    assert rs.cmd_apply(Namespace(skip_tests=True, decisions={})) == 1
    assert env["state_file"].read_bytes() == previous
    assert not (env["consumer"] / ".repo_template/sync_manifest.json").exists()
    assert not (env["consumer"] / ".repo_template/scripts/task.py").exists()


def test_restore_failure_is_reported_and_leaves_recoverable_backup(tmp_path, monkeypatch):
    import repo_task.file_transaction as module
    root = tmp_path / "consumer"
    root.mkdir()
    (root / "asset").write_text("original")
    transaction = FileTransaction(root, ".scratch/recovery")
    transaction.begin(["asset"])
    (root / "asset").write_text("changed")
    def fail_copy(*_):
        raise OSError("simulated restore failure")
    monkeypatch.setattr(module, "_copy", fail_copy)
    with pytest.raises(OSError, match="restore failure"):
        transaction.rollback(automatic=True)
    assert transaction.read()["status"] == "restore_failed"
    assert (transaction.backup / "asset").read_text() == "original"


def test_export_ignores_dirty_secrets_runtime_and_keeps_valid_symlinks(env):
    src = env["src"]
    (src / "tracked.txt").write_text("committed")
    (src / "link.txt").symlink_to("tracked.txt")
    _git(src, "add", "tracked.txt", "link.txt")
    _git(src, "commit", "-m", "exportable")
    (src / "tracked.txt").write_text("dirty")
    for rel in (".env", ".env.local", "docs/runtime/ledger", ".claude/state/cache", ".commandcode/state", ".repo_template/sync_state.json"):
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("must not export")
    target = src.parent / "export"
    export_template(src, target)
    assert (target / "tracked.txt").read_text() == "committed"
    assert (target / "link.txt").is_symlink()
    assert (target / "link.txt").resolve() == target / "tracked.txt"
    assert not (target / ".env").exists()
    assert not (target / "docs/runtime").exists()
    assert not (target / ".repo_template/sync_state.json").exists()
    with pytest.raises(ValueError, match="目标"):
        export_template(src, target)


def test_export_rejects_accidentally_tracked_secret_before_touching_target(env):
    src = env["src"]
    (src / ".env").write_text("secret")
    _git(src, "add", ".env")
    _git(src, "commit", "-m", "bad secret")
    target = src.parent / "export"
    with pytest.raises(ValueError, match="不可分发"):
        export_template(src, target)
    assert not target.exists()


def test_registry_has_complete_roles_and_real_composition_edges():
    output = render_registry()
    assert "repo-template-sync-core" in output
    assert "bug-to-pending" in output and "task-create" in output
    result = subprocess.run([sys.executable, str(SCRIPTS / "skill_registry.py")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_finish_requires_spec_index_update_or_explicit_exemption(git_repo):
    assert _task_cli(git_repo, "start", "t001").returncode == 0
    w = _worktree(git_repo, "t001")
    task = w / "docs/tasks/t001_alpha/task.md"
    task.write_text("\n".join(line for line in task.read_text().splitlines()
                              if not line.startswith("- specs_index: not_applicable:")) + "\n")
    result = _task_cli(w, "finish", "t001")
    assert result.returncode != 0 and "specs_index" in result.stderr
    (w / "docs/specs").mkdir()
    (w / "docs/specs/feature.md").write_text("# feature\n")
    (w / "docs/specs_index.md").write_text("|slug|task 清单|最后更新时间|\n|---|---|---|\n|feature|t001|2026-10-24T00:00:00+08:00|\n")
    result = _task_cli(w, "finish", "t001")
    assert result.returncode == 0, result.stderr


def test_actual_product_export_is_clean_and_passes_contract(tmp_path):
    product = SCRIPTS.parents[1]
    files = subprocess.run(["git", "-C", str(product), "ls-files", "--cached", "--others",
                            "--exclude-standard", "-z", "."], capture_output=True, check=True).stdout
    factory = tmp_path / "factory"
    for name in files.decode().split("\0"):
        if not name:
            continue
        source, target = product / name, factory / "repo" / name
        if not source.exists() and not source.is_symlink():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(source.readlink())
        else:
            shutil.copy2(source, target)
    _git(factory, "init", "-b", "main")
    _git(factory, "config", "user.email", "test@example.com")
    _git(factory, "config", "user.name", "test")
    _git(factory, "add", "-A")
    _git(factory, "commit", "-m", "candidate template")
    (factory / "repo/.env").write_text("dirty secret")
    target = tmp_path / "exported"
    export_template(factory, target)
    assert not (target / ".env").exists()
    for host in (".claude/skills", ".agents/skills"):
        assert all(path.is_symlink() and path.exists() for path in (target / host).iterdir())
    result = subprocess.run([sys.executable, "-m", "pytest", ".repo_template/tests", "-q", "-m", "contract"],
                            cwd=target, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
