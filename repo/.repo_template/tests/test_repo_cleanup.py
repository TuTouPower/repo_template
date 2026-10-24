"""repo_cleanup.py 机械化清理器的真实目录测试。

在 tmp_path 构造仓库形目录树，直接调用 collect / apply_delete / classify /
main 子进程，覆盖默认类别扫描、docs/ 保护、node/scratch/artifacts/data 点名
清理、--keep 引用保护、硬保护路径永不删、scan 只读。
"""

import subprocess
import sys
from pathlib import Path

import pytest

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
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.c"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], capture_output=True)
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
    # 点名类别未点名 → 不入默认扫描
    assert "node_modules" not in paths
    assert ".scratch/exp/tmp.py" not in paths


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
    """默认类别不得命中未点名目录树（.scratch/artifacts/data/node_modules）内文件。"""
    repo = tmp_path / "repo"
    (repo / "artifacts/out/__pycache__").mkdir(parents=True)
    (repo / "artifacts/out/__pycache__/m.pyc").write_bytes(b"x")
    (repo / "artifacts/run.log").write_text("log\n")
    (repo / ".scratch/exp").mkdir(parents=True)
    (repo / ".scratch/exp/note.log").write_text("log\n")
    (repo / "data/cache").mkdir(parents=True)
    (repo / "data/cache/.DS_Store").write_bytes(b"x")
    (repo / "node_modules/pkg").mkdir(parents=True)
    (repo / "node_modules/pkg/.DS_Store").write_bytes(b"x")

    hits, _ = rc.collect(repo, rc.DEFAULT_CATEGORIES, [])
    assert {h["path"] for h in hits} == set()

    # 点名类别仍能整树处理（不受剪枝影响）
    hits, _ = rc.collect(repo, ["scratch", "artifacts", "data", "node"], [])
    paths = {h["path"] for h in hits}
    assert ".scratch/exp/note.log" in paths
    assert "artifacts/run.log" in paths
    assert "data/cache/.DS_Store" in paths
    assert "node_modules" in paths


def test_cli_scan_is_read_only(tree):
    before = {str(p) for p in tree.rglob("*")}
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "repo_cleanup.py"), "scan"],
        cwd=tree, capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    assert "repo-cleanup 预览" in r.stdout
    assert "src/app/__pycache__" in r.stdout
    assert "合计：" in r.stdout
    after = {str(p) for p in tree.rglob("*")}
    assert before == after


def test_cli_apply_default(tree):
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "repo_cleanup.py"), "apply"],
        cwd=tree, capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr
    assert "模式：apply" in r.stdout
    assert not (tree / "src/app/__pycache__").exists()
    assert (tree / "docs/guide.log").exists()


def test_cli_unknown_category_rejected(tree):
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "repo_cleanup.py"), "scan", "bogus"],
        cwd=tree, capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode != 0
    assert "未知类别" in r.stderr


def test_repo_cleanup_git_timeout(monkeypatch):
    """验证 repo_root 获取仓库顶层超时时抛出 CleanupError 并包含时限信息（Issue #66）。"""
    import repo_cleanup as rc

    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=30)

    monkeypatch.setattr(subprocess, "run", mock_run)
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
    )
    assert r.returncode != 0
    assert "清理候选数量发生变化" in r.stderr
    assert (tree / "src/app/__pycache__").is_dir()



