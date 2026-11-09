"""repo_cleanup.py 机械化清理器的真实目录测试。

在 tmp_path 构造仓库形目录树，直接调用 collect / apply_delete / classify /
main 子进程，覆盖默认类别扫描、docs/ 保护、node/scratch/artifacts/data 点名
清理、--keep 引用保护、硬保护路径永不删、scan 只读。
"""

import json
import os
from contextlib import contextmanager
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_env_support import get_controlled_git_env
from git_env_support import init_repo

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_cleanup as rc

pytestmark = pytest.mark.contract


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """构造带各类垃圾与保护文件的目录树。"""
    repo = tmp_path / "repo"
    (repo / "src/app").mkdir(parents=True)
    (repo / "src/app/__pycache__").mkdir(parents=True)
    (repo / "src/app/__pycache__/m.pyc").write_bytes(b"x")
    (repo / "src/.pytest_cache").mkdir(parents=True)
    (repo / "src/.pytest_cache/README.md").write_text("cache\n")
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts/debug.log").write_text("log\n")
    (repo / "docs").mkdir(parents=True)
    (repo / "docs/runtime").mkdir(parents=True)
    (repo / "docs/runtime/tasks_audit.log").write_text("audit\n")
    (repo / "docs/runtime/dispatch_ledger.jsonl").write_text('{"event": "start"}\n')
    (repo / "docs/guide.log").write_text("log\n")           # docs/ 下 *.log 保护
    (repo / "docs/.DS_Store").write_bytes(b"x")             # docs/ 下 os 类可清
    (repo / ".DS_Store").write_bytes(b"x")
    (repo / "notes.txt~").write_text("swap\n")
    (repo / "AGENTS.md").write_text("agents\n")              # 硬保护
    (repo / "README.md").write_text("readme\n")              # 硬保护
    (repo / "node_modules/pkg").mkdir(parents=True)
    (repo / "node_modules/pkg/index.js").write_text("x\n")
    (repo / ".scratch/exp").mkdir(parents=True)
    (repo / ".scratch/exp/tmp.py").write_text("x\n")
    (repo / ".scratch/exp/ref.txt").write_text("x\n")
    (repo / "artifacts").mkdir(parents=True)
    (repo / "artifacts/out.bin").write_bytes(b"x")
    (repo / "data").mkdir(parents=True)
    (repo / "data/db.sqlite").write_bytes(b"x")
    init_repo(repo)
    return repo


def _collect(repo, categories, keeps=()):
    return rc.collect(repo, categories, list(keeps))


def test_scan_default_categories(tree):
    hits, skipped = _collect(tree, rc.DEFAULT_CATEGORIES)
    paths = {h["path"] for h in hits}
    # 命中：src pycache、pytest、logs(scripts)、os 两处、editor
    assert "src/app/__pycache__" in paths
    assert "src/.pytest_cache" in paths
    assert "scripts/debug.log" in paths
    assert ".DS_Store" in paths
    assert "docs/.DS_Store" in paths
    assert "notes.txt~" in paths
    # 保护：docs/ 下 *.log 不清；硬保护文件不在命中
    assert "docs/guide.log" not in paths
    assert "AGENTS.md" not in paths
    assert "README.md" not in paths
    assert "docs/runtime/tasks_audit.log" not in paths
    assert "docs/runtime/dispatch_ledger.jsonl" not in paths
    # node/artifacts/data 仍须点名；scratch 默认行为由专项测试覆盖
    assert "node_modules" not in paths


def test_apply_deletes_only_listed(tree):
    hits, _ = _collect(tree, ["pycache", "pytest", "logs", "os", "editor"])
    deleted = rc.apply_delete(tree, hits)
    assert "src/app/__pycache__" in deleted
    assert not (tree / "src/app/__pycache__").exists()
    assert (tree / "docs/guide.log").exists()
    assert (tree / "AGENTS.md").exists()
    assert "src/app/__pycache__" in deleted


def test_named_categories_clear_contents_keep_dir(tree):
    hits, _ = _collect(tree, ["node", "scratch", "artifacts", "data"])
    paths = {h["path"] for h in hits}
    assert "node_modules" in paths                       # 目录整体命中
    assert ".scratch/exp/tmp.py" in paths
    assert ".scratch/exp/ref.txt" in paths
    assert "artifacts/out.bin" in paths
    assert "data/db.sqlite" in paths
    # bulk 目录本身不在命中（清内容保留目录）
    assert ".scratch" not in paths
    assert "artifacts" not in paths
    assert "data" not in paths
    rc.apply_delete(tree, hits)
    assert not (tree / "node_modules").exists()
    assert not (tree / ".scratch/exp").exists()
    assert (tree / ".scratch").is_dir()           # 目录保留
    assert (tree / "artifacts").is_dir()
    assert (tree / "data").is_dir()


def test_keep_protects_scratch_reference(tree):
    hits, skipped = _collect(tree, ["scratch"], keeps=[".scratch/exp/ref.txt"])
    paths = {h["path"] for h in hits}
    assert ".scratch/exp/tmp.py" in paths
    assert ".scratch/exp/ref.txt" not in paths
    assert ".scratch/exp" not in paths
    assert ".scratch/exp/ref.txt" in skipped
    deleted = rc.apply_delete(tree, hits, [".scratch/exp/ref.txt"])
    assert ".scratch/exp/tmp.py" in deleted
    assert not (tree / ".scratch/exp/tmp.py").exists()
    assert (tree / ".scratch/exp/ref.txt").is_file()
    assert (tree / ".scratch/exp").is_dir()


def test_keep_protects_file_level_hit_outside_bulk(tree):
    """普通 os.walk 分支的文件级命中（logs 类）也须被 --keep 排除。"""
    for keep in ("scripts/debug.log", "*.log", "scripts"):
        hits, skipped = _collect(tree, ["logs"], keeps=[keep])
        assert "scripts/debug.log" not in {h["path"] for h in hits}, keep
        assert "scripts/debug.log" in skipped, keep


def test_apply_delete_rechecks_keep_as_second_line_of_defense(tree):
    """apply 独立重查 keep：调用方传入未经 collect 过滤的 hits 也不误删。"""
    hits, _ = _collect(tree, ["logs"])
    assert "scripts/debug.log" in {h["path"] for h in hits}
    deleted = rc.apply_delete(tree, hits, ["scripts/debug.log"])
    assert "scripts/debug.log" not in deleted
    assert (tree / "scripts/debug.log").is_file()


def test_apply_unlinks_symlink_dir(tree):
    link = tree / "src" / "__pycache__"
    link.symlink_to(tree / "src" / "app" / "__pycache__")
    hits, _ = _collect(tree, ["pycache"])
    assert any(h["path"] == "src/__pycache__" and h["kind"] == "dir" for h in hits)
    rc.apply_delete(tree, hits)
    assert not link.exists()
    assert not link.is_symlink()


def test_default_categories_do_not_enter_unnamed_dirs(tmp_path):
    """默认类别不得命中未点名目录树（artifacts/data/node_modules）内文件。

    原「默认不进入 .scratch」断言按 L68 删除；scratch 默认清理由
    test_default_scratch_is_default_cleaned 覆盖。
    """
    repo = tmp_path / "repo"
    (repo / "artifacts/out/__pycache__").mkdir(parents=True)
    (repo / "artifacts/out/__pycache__/m.pyc").write_bytes(b"x")
    (repo / "artifacts/run.log").write_text("log\n")
    (repo / "data/cache").mkdir(parents=True)
    (repo / "data/cache/.DS_Store").write_bytes(b"x")
    (repo / "node_modules/pkg").mkdir(parents=True)
    (repo / "node_modules/pkg/.DS_Store").write_bytes(b"x")

    hits, _ = rc.collect(repo, rc.DEFAULT_CATEGORIES, [])
    assert {h["path"] for h in hits} == set()

    # 点名类别仍能整树处理（不受剪枝影响）
    hits, _ = rc.collect(repo, ["artifacts", "data", "node"], [])
    paths = {h["path"] for h in hits}
    assert "artifacts/run.log" in paths
    assert "data/cache/.DS_Store" in paths
    assert "node_modules" in paths


def test_default_scratch_is_default_cleaned(tree):
    """L68：scratch 默认清理无主内容，目录本身保留。"""
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "repo_cleanup.py"), "apply"],
        cwd=tree, capture_output=True, text=True, encoding="utf-8",
        env=get_controlled_git_env(),
    )
    assert r.returncode == 0, r.stderr
    assert not (tree / ".scratch/exp/tmp.py").exists()
    assert not (tree / ".scratch/exp").exists()
    assert (tree / ".scratch").is_dir()


def test_default_categories_skip_nested_unnamed_dirs(tmp_path):
    """嵌套 node_modules/.git/artifacts/data/.scratch 也不得被默认类别遍历（Issue #71）。"""
    repo = tmp_path / "repo"
    for rel in (
        "src/node_modules/pkg/x.pyc",
        "src/node_modules/pkg/debug.log",
        "src/artifacts/run.log",
        "src/data/.DS_Store",
        "src/.scratch/note.log",
        "src/nested/.git/hooks/pre-commit.log",
    ):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")

    hits, _ = rc.collect(repo, rc.DEFAULT_CATEGORIES, [])
    assert {h["path"] for h in hits} == set()

    hits, _ = rc.collect(repo, ["node"], [])
    assert "src/node_modules" in {h["path"] for h in hits}


def test_private_local_dir_is_never_collected_or_deleted(tmp_path):
    """仓库根 local/ 是用户私有目录：任何类别都不进入，伪造 hits 也不删除。"""
    repo = tmp_path / "repo"
    for rel in (
        "local/backup/run.log",
        "local/backup/.DS_Store",
        "local/backup/__pycache__/m.pyc",
        "local/node_modules/pkg/index.js",
        "src/local/debug.log",
    ):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")

    hits, _ = rc.collect(repo, list(rc.ALL_CATEGORIES), [])
    paths = {h["path"] for h in hits}
    assert not any(path == "local" or path.startswith("local/") for path in paths)
    # 只保护仓库根 local/；嵌套同名目录仍按类别处理。
    assert "src/local/debug.log" in paths

    forged = [
        {"path": "local/backup/run.log", "category": "logs", "kind": "file"},
        {"path": "local/backup", "category": "data", "kind": "dir"},
        {"path": "local", "category": "data", "kind": "dir"},
    ]
    assert rc.apply_delete(repo, forged) == []
    assert (repo / "local/backup/run.log").is_file()


def test_cli_scan_is_read_only(tree):
    before = {str(p) for p in tree.rglob("*")}
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "repo_cleanup.py"), "scan"],
        cwd=tree, capture_output=True, text=True, encoding="utf-8",
        env=get_controlled_git_env(),
    )
    assert r.returncode == 0, r.stderr
    assert "repo-cleanup 预览" in r.stdout
    assert "src/app/__pycache__" in r.stdout
    assert "合计：" in r.stdout
    after = {str(p) for p in tree.rglob("*")}
    lock = str(tree / ".git" / "repo_template_control.lock")
    assert before == after - {lock}


def test_cli_apply_default(tree):
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "repo_cleanup.py"), "apply"],
        cwd=tree, capture_output=True, text=True, encoding="utf-8",
        env=get_controlled_git_env(),
    )
    assert r.returncode == 0, r.stderr
    assert "模式：apply" in r.stdout
    assert not (tree / "src/app/__pycache__").exists()
    assert (tree / "docs/guide.log").exists()


def test_cli_unknown_category_rejected(tree):
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "repo_cleanup.py"), "scan", "bogus"],
        cwd=tree, capture_output=True, text=True, encoding="utf-8",
        env=get_controlled_git_env(),
    )
    assert r.returncode != 0
    assert "未知类别" in r.stderr


def test_repo_cleanup_git_timeout(monkeypatch):
    """验证 repo_root 获取仓库顶层超时时抛出 CleanupError 并包含时限信息（Issue #66）。"""
    import repo_cleanup as rc

    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=30)

    monkeypatch.setattr(rc, "subprocess", SimpleNamespace(**{**vars(subprocess), "run": mock_run}))
    with pytest.raises(rc.CleanupError, match="超时（30s）"):
        rc.repo_root()


def test_cleanup_does_not_follow_directory_symlink_outside_repo(tmp_path):
    """验证中间目录软链绝不跟随进入仓库外，且 apply 仅删除软链本身不损毁外部文件（Issue #55）。"""
    import repo_cleanup as rc

    # 1. 仓库外的独立哨兵目录
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    sentinel_file = sentinel / "sentinel_data.txt"
    sentinel_file.write_text("critical outside data\n", encoding="utf-8")

    # 2. 仓库内的点名 bulk 目录包含指向哨兵的软链
    repo = tmp_path / "repo"
    scratch = repo / ".scratch"
    scratch.mkdir(parents=True)
    link = scratch / "external_link"
    link.symlink_to(sentinel)

    # 3. collect 扫描
    hits, skipped = rc.collect(repo, ["scratch"], [])
    hit_paths = {h["path"] for h in hits}
    # 候选只能是 external_link 本身，绝不能进入外部扫描出 sentinel_data.txt
    assert ".scratch/external_link" in hit_paths
    assert not any("sentinel_data" in p for p in hit_paths)

    # 4. apply 删除
    deleted = rc.apply_delete(repo, hits)
    assert ".scratch/external_link" in deleted
    assert not link.exists()
    assert not link.is_symlink()

    # 外部目标必须完好保留
    assert sentinel.is_dir()
    assert sentinel_file.is_file()
    assert sentinel_file.read_text(encoding="utf-8") == "critical outside data\n"


def test_cli_apply_expected_count_blocks_on_change(tree):
    """验证 apply 传入 --expected-count 时若候选数量不符则拒绝执行（Issue #18）。"""
    # tree 当前存在 src/app/__pycache__
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "repo_cleanup.py"), "apply", "--expected-count", "999"],
        cwd=tree, capture_output=True, text=True, encoding="utf-8",
        env=get_controlled_git_env(),
    )
    assert r.returncode != 0
    assert "清理候选数量发生变化" in r.stderr
    assert (tree / "src/app/__pycache__").is_dir()


def test_hard_protected_runtime_paths_are_never_collected_or_deleted(tree):
    """HARD_PROTECTED 必须真正生效：伪造 hits 直接调 apply_delete 也不删（DS L289）。

    默认扫描下 docs/ 的 logs 类本就不收集，只断言「不在默认扫描」即使删掉
    HARD_PROTECTED 两项也照过；这里直接验证 collect 与 apply_delete 的防线。
    """
    protected = [
        {"path": "docs/runtime/tasks_audit.log", "category": "logs", "kind": "file"},
        {"path": "docs/runtime/dispatch_ledger.jsonl", "category": "logs", "kind": "file"},
    ]
    deleted = rc.apply_delete(tree, protected)
    assert deleted == []
    assert (tree / "docs/runtime/tasks_audit.log").is_file()
    assert (tree / "docs/runtime/dispatch_ledger.jsonl").is_file()

    hits, _ = rc.collect(tree, ["logs"], [])
    paths = {h["path"] for h in hits}
    assert "docs/runtime/tasks_audit.log" not in paths
    assert "docs/runtime/dispatch_ledger.jsonl" not in paths
    assert "scripts/debug.log" in paths





def _txn(repo: Path, directory: str, status: str, *, corrupt: bool = False) -> Path:
    path = repo / directory
    path.mkdir(parents=True, exist_ok=True)
    (path / "backup.txt").write_text("backup\n", encoding="utf-8")
    journal = path / "transaction.json"
    journal.write_text("{bad" if corrupt else json.dumps({"status": status}), encoding="utf-8")
    return path


def _pending(repo: Path, pid: str, slug: str, status: str, body: str, **extra) -> Path:
    directory = repo / "docs/pending"
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["---", f'pid: "{pid}"', f'slug: "{slug}"', f'status: "{status}"']
    for key, value in extra.items():
        lines.append(f'{key}: "{value}"')
    lines.extend(["---", body, ""])
    path = directory / f"{pid}_{slug}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _old(path: Path, days: float = 4) -> None:
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def _issue(repo: Path, stamp: str, issue: str = "") -> tuple[Path, Path]:
    directory = repo / ".scratch/repo_template_issues"
    directory.mkdir(parents=True, exist_ok=True)
    full = directory / f"{stamp}.md"
    copy = directory / f"{stamp}.issue.md"
    full.write_text(f'---\nissue: "{issue}"\n---\n# 模板仓问题报告\n', encoding="utf-8")
    copy.write_text("issue body\n", encoding="utf-8")
    return full, copy


@pytest.mark.parametrize("status", ["preparing", "applying", "applied", "restore_failed"])
def test_open_transaction_skips_scratch_and_keeps_other_categories(tmp_path, status):
    repo = tmp_path / "repo"
    _txn(repo, ".scratch/repo_sync_rollback", status)
    (repo / "scripts").mkdir()
    (repo / "scripts/debug.log").write_text("log\n", encoding="utf-8")
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    assert result.scratch_note and status in result.scratch_note
    assert "transaction accept" in result.scratch_note
    assert not any(h["path"].startswith(".scratch/") for h in result.hits)
    assert "scripts/debug.log" in {h["path"] for h in result.hits}

    rc.apply_delete(repo, result.hits, [])
    assert (repo / ".scratch/repo_sync_rollback/backup.txt").is_file()
    assert not (repo / "scripts/debug.log").exists()


def test_corrupt_transaction_skips_scratch(tmp_path):
    repo = tmp_path / "repo"
    _txn(repo, ".scratch/repo_sync_rollback", "", corrupt=True)
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    assert result.scratch_note and "记录损坏" in result.scratch_note
    assert not any(h["path"].startswith(".scratch/") for h in result.hits)
    assert (repo / ".scratch/repo_sync_rollback/transaction.json").is_file()


def test_open_graph_transaction_skips_scratch_with_graph_hint(tmp_path):
    repo = tmp_path / "repo"
    _txn(repo, ".scratch/task_graph_transaction", "preparing")
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    assert result.scratch_note and "图事务" in result.scratch_note
    assert "graph-transaction" in result.scratch_note
    assert not any(h["path"].startswith(".scratch/") for h in result.hits)


@pytest.mark.parametrize(("directory", "status"), [
    (".scratch/repo_sync_rollback", "accepted"),
    (".scratch/task_graph_transaction", "rolled_back"),
])
def test_finished_transaction_backups_are_cleaned(tmp_path, directory, status):
    repo = tmp_path / "repo"
    _txn(repo, directory, status)
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    reasons = [h.get("reason", "") for h in result.hits if h["path"].startswith(directory)]
    assert any(f"事务已结束（{status}）" in reason for reason in reasons)
    rc.apply_delete(repo, result.hits, [])
    assert not (repo / directory).exists()


def test_pending_reference_protects_recent_draft(tmp_path):
    repo = tmp_path / "repo"
    target = repo / ".scratch/repro_p001"
    target.mkdir(parents=True)
    (target / "out.txt").write_text("draft\n", encoding="utf-8")
    _pending(repo, "p001", "fix", "todo", "- 线索：`.scratch/repro_p001`")
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    assert any(item["path"] == ".scratch/repro_p001" for item in result.protected)
    assert not any(h["path"].startswith(".scratch/repro_p001") for h in result.hits)
    keeps = [item["path"] for item in result.protected]
    rc.apply_delete(repo, result.hits, keeps)
    assert (target / "out.txt").is_file()


def test_pending_reference_expires_after_three_days(tmp_path):
    repo = tmp_path / "repo"
    target = repo / ".scratch/repro_p001"
    target.mkdir(parents=True)
    (target / "out.txt").write_text("draft\n", encoding="utf-8")
    _pending(repo, "p001", "fix", "todo", "- 线索：`.scratch/repro_p001`")
    init_repo(repo)
    _old(target)
    _old(target / "out.txt")

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    hits = {h["path"]: h for h in result.hits}
    assert "超期" in hits[".scratch/repro_p001/out.txt"]["reason"]
    rc.apply_delete(repo, result.hits, [])
    assert not target.exists()


def test_closed_pending_reference_no_longer_protects(tmp_path):
    repo = tmp_path / "repo"
    target = repo / ".scratch/repro_p001"
    target.mkdir(parents=True)
    (target / "out.txt").write_text("draft\n", encoding="utf-8")
    _pending(
        repo, "p001", "fix", "closed", "- 线索：`.scratch/repro_p001`",
        resolution="resolved", reason="fixed",
    )
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    assert not result.protected
    rc.apply_delete(repo, result.hits, [])
    assert not target.exists()


def test_dir_mtime_old_but_inner_file_new_stays_protected(tmp_path):
    repo = tmp_path / "repo"
    target = repo / ".scratch/repro_p001"
    target.mkdir(parents=True)
    inner = target / "out.txt"
    inner.write_text("draft\n", encoding="utf-8")
    _pending(repo, "p001", "fix", "todo", "- 线索：`.scratch/repro_p001`")
    init_repo(repo)
    _old(target)
    os.utime(inner, None)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    assert any(item["path"] == ".scratch/repro_p001" for item in result.protected)


def test_corrupt_pending_skips_scratch(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".scratch/exp").mkdir(parents=True)
    (repo / ".scratch/exp/tmp.txt").write_text("x\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts/debug.log").write_text("log\n", encoding="utf-8")
    _pending(repo, "p001", "fix", "todo", "- 线索：`.scratch/exp`")
    (repo / "docs/pending/p001_fix.md").write_text(
        '---\npid: "p001"\n---\n# broken\n', encoding="utf-8"
    )
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    assert result.scratch_note and "pending 条目损坏" in result.scratch_note
    assert not any(h["path"].startswith(".scratch/") for h in result.hits)
    assert "scripts/debug.log" in {h["path"] for h in result.hits}


def test_unpublished_issue_pair_is_protected_when_recent(tmp_path):
    repo = tmp_path / "repo"
    full, copy = _issue(repo, "20261110-101500")
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    protected = {item["path"] for item in result.protected}
    assert {".scratch/repo_template_issues/20261110-101500.md",
            ".scratch/repo_template_issues/20261110-101500.issue.md"} <= protected
    rc.apply_delete(repo, result.hits, [item["path"] for item in result.protected])
    assert full.is_file() and copy.is_file()


def test_unpublished_issue_pair_expires_after_three_days(tmp_path):
    repo = tmp_path / "repo"
    full, copy = _issue(repo, "20261110-101500")
    init_repo(repo)
    _old(full)
    _old(copy)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    hits = {h["path"]: h for h in result.hits}
    assert "超期" in hits[".scratch/repo_template_issues/20261110-101500.md"]["reason"]
    rc.apply_delete(repo, result.hits, [])
    assert not full.exists() and not copy.exists()


def test_published_issue_pair_is_unowned_and_cleaned(tmp_path):
    repo = tmp_path / "repo"
    full, copy = _issue(repo, "20261110-101500", "https://github.com/o/r/issues/12")
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    hits = {h["path"]: h for h in result.hits}
    assert "已发布 issue" in hits[".scratch/repo_template_issues/20261110-101500.md"]["reason"]
    rc.apply_delete(repo, result.hits, [])
    assert not full.exists() and not copy.exists()


def test_orphan_issue_copy_is_unowned_and_cleaned(tmp_path):
    repo = tmp_path / "repo"
    directory = repo / ".scratch/repo_template_issues"
    directory.mkdir(parents=True)
    copy = directory / "20261110-101500.issue.md"
    copy.write_text("body\n", encoding="utf-8")
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    hits = {h["path"]: h for h in result.hits}
    assert "仅 .issue.md" in hits[".scratch/repo_template_issues/20261110-101500.issue.md"]["reason"]
    rc.apply_delete(repo, result.hits, [])
    assert not copy.exists()


def test_corrupt_issue_front_matter_is_treated_as_unpublished(tmp_path):
    repo = tmp_path / "repo"
    directory = repo / ".scratch/repo_template_issues"
    directory.mkdir(parents=True)
    full = directory / "20261110-101500.md"
    full.write_text("---\nissue: [\n---\n# broken\n", encoding="utf-8")
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    assert any(item["path"] == ".scratch/repo_template_issues/20261110-101500.md"
               for item in result.protected)


def test_unknown_transaction_status_skips_scratch(tmp_path):
    repo = tmp_path / "repo"
    _txn(repo, ".scratch/repo_sync_rollback", "foo")
    init_repo(repo)

    result = rc.scan_repository(repo, list(rc.DEFAULT_CATEGORIES), [])
    assert result.scratch_note and "记录损坏" in result.scratch_note
    assert not any(h["path"].startswith(".scratch/") for h in result.hits)


@pytest.mark.parametrize("error", [
    rc.TaskDataError("同步验证子进程禁止修改当前仓库"),
    OSError("无法解析 git 公共目录"),
])
def test_lock_errors_become_cleanup_errors(tmp_path, monkeypatch, error):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(rc, "repo_root", lambda: repo)

    @contextmanager
    def boom(*args, **kwargs):
        raise error
        yield

    monkeypatch.setattr(rc, "git_common_lock", boom)
    with pytest.raises(SystemExit) as exc:
        rc.main(["scan"])
    assert "repo_cleanup:" in str(exc.value)
    assert "无法获取控制面锁" in str(exc.value)
