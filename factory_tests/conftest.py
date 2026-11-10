"""工厂集成测试路径与全局种子 fixture。

本目录只在模板工厂运行，不随 repo/ 产物分发。测试经 sys.path 导入
repo/.repo_template/scripts 的脚本模块与 repo/.repo_template/tests 的 review_support。
提供 session 作用域种子仓库 fixture 与用例级高速复制 fixture。
"""

import sys
from pathlib import Path

import pytest

FACTORY_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = FACTORY_DIR.parent / "repo" / ".repo_template"

for _path in (
    FACTORY_DIR,
    FACTORY_DIR / "support",
    FACTORY_DIR / "state",
    FACTORY_DIR / "sync",
    FACTORY_DIR / "workflow",
    FACTORY_DIR / "review",
    FACTORY_DIR / "docs",
    TEMPLATE_DIR / "scripts",
    TEMPLATE_DIR / "tests",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from git_repo_support import (
    build_sync_source_seed,
    build_workflow_three_tasks_seed,
    build_workflow_two_tasks_seed,
    fast_copy_repo,
    validate_seed,
)


@pytest.fixture(scope="session")
def workflow_three_tasks_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session 作用域：三任务工作流种子仓库（每个 xdist worker 仅构建一次）。"""
    seed = tmp_path_factory.mktemp("seed_3tasks") / "repo"
    build_workflow_three_tasks_seed(seed)
    validate_seed(seed)
    return seed


@pytest.fixture(scope="session")
def workflow_two_tasks_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session 作用域：两任务工作流种子仓库（每个 xdist worker 仅构建一次）。"""
    seed = tmp_path_factory.mktemp("seed_2tasks") / "repo"
    build_workflow_two_tasks_seed(seed)
    validate_seed(seed)
    return seed


@pytest.fixture(scope="session")
def sync_source_seed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session 作用域：模板源仓库种子（每个 xdist worker 仅构建一次）。"""
    seed = tmp_path_factory.mktemp("seed_sync_src") / "src"
    build_sync_source_seed(seed)
    validate_seed(seed)
    return seed


@pytest.fixture
def git_repo(tmp_path: Path, workflow_three_tasks_seed: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """函数级快速复制 fixture：独立副本 + fake_bin + monkeypatch。"""
    repo = tmp_path / "repo"
    return fast_copy_repo(workflow_three_tasks_seed, repo, tmp_path, monkeypatch)

