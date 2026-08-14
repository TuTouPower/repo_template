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


def test_apply_unlinks_symlink_dir(tree):
    link = tree / "src" / "__pycache__"
    link.symlink_to(tree / "src" / "app" / "__pycache__")
    hits, _ = _collect(tree, ["pycache"])
    assert any(h["path"] == "src/__pycache__" and h["kind"] == "dir" for h in hits)
    rc.apply_delete(tree, hits)
    assert not link.exists()
    assert not link.is_symlink()


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
