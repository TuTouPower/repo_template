"""工厂测试专用 Git 仓库基础设施支持模块。

职责：
1. 建立受控隔离的 Git 执行环境（屏蔽宿主配置、清除外部定位变量、确定身份）；
2. 构建标准 profile 的只读种子仓库（workflow 3-tasks, workflow 2-tasks, sync-source）；
3. 验证种子发布不变量（Plan 4.2 节）；
4. 提供基于普通复制的高速独立仓库实例化（shutil.copytree(seed, dst, symlinks=True)）；
5. 检验多副本与种子间的文件系统、Git 状态与跨进程锁隔离性（Plan 9.1 节）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

FACTORY_TESTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = FACTORY_TESTS_DIR.parent
TEMPLATE_DIR = REPO_ROOT / "repo" / ".repo_template"
PRODUCT_ROOT = REPO_ROOT / "repo"
SCRIPTS_DIR = TEMPLATE_DIR / "scripts"
TASK_TEMPLATE_DIR = TEMPLATE_DIR / "docs" / "task_template"

# 确保 review_support 与 repo_task 可在 worker 中被导入
for _p in (SCRIPTS_DIR, TEMPLATE_DIR / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from repo_task.documents import parse_front_matter, write_front_matter
from review_support import configure_project_verification, valid_spec
from factory_paths import FACTORY_ROOT


def get_controlled_git_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """返回屏蔽宿主配置、无外部变量污染、身份确定的受控 Git 环境变量字典。"""
    env = os.environ.copy()
    git_vars_to_clear = [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_CEILING_DIRECTORIES",
    ]
    for key in list(env.keys()):
        if key in git_vars_to_clear or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key, None)

    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_AUTHOR_NAME"] = "test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    env["GIT_TERMINAL_PROMPT"] = "0"

    if overrides:
        env.update(overrides)
    return env


def git_run(
    repo: Path,
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """在受控环境中执行 git 命令。"""
    run_env = get_controlled_git_env(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        timeout=timeout,
        env=run_env,
    )


def install_fake_md_kx(bin_parent: Path, monkeypatch: Any = None) -> Path:
    """安装恒等 md_kx 替身并按需注入 PATH。"""
    fake_bin = bin_parent / "bin_md"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_md = fake_bin / "md_kx"
    if not fake_md.exists():
        fake_md.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
        fake_md.chmod(0o755)
    if monkeypatch is not None:
        monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))
    return fake_bin


def _build_workflow_tasks_seed(seed_dir: Path, tasks: list[tuple[str, str]]) -> Path:
    """构建标准工作流任务种子仓库的通用实现。"""
    if seed_dir.exists():
        shutil.rmtree(seed_dir)
    seed_dir.mkdir(parents=True)

    empty_template = seed_dir.parent / f"_empty_template_{seed_dir.name}"
    empty_template.mkdir(parents=True, exist_ok=True)

    try:
        tasks_dir = seed_dir / "docs" / "tasks"
        scripts_dir = seed_dir / ".repo_template" / "scripts"
        tasks_dir.mkdir(parents=True)
        scripts_dir.mkdir(parents=True)
        (scripts_dir.parent / "entity_schema.json").write_text('{"version": 1}\n')
        shutil.copy2(SCRIPTS_DIR / "_id_scan.py", scripts_dir / "_id_scan.py")

        shutil.copy2(SCRIPTS_DIR / "task.py", scripts_dir / "task.py")
        shutil.copy2(SCRIPTS_DIR / "md_format.py", scripts_dir / "md_format.py")
        shutil.copytree(
            SCRIPTS_DIR / "repo_task",
            scripts_dir / "repo_task",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copytree(TASK_TEMPLATE_DIR, seed_dir / ".repo_template" / "docs" / "task_template")
        shutil.copytree(TASK_TEMPLATE_DIR, tasks_dir / "task_template")

        _, body = parse_front_matter(TASK_TEMPLATE_DIR / "progress.md")
        spec_text = valid_spec()

        for tid, slug in tasks:
            task_dir = tasks_dir / f"{tid}_{slug}"
            shutil.copytree(TASK_TEMPLATE_DIR, task_dir)
            write_front_matter(
                task_dir / "progress.md",
                {
                    "tid": tid,
                    "slug": slug,
                    "title": slug,
                    "status": "backlog",
                    "branch": "",
                    "worktree": "",
                    "review_level": "full",
                    "diff_anchor": "",
                    "note": "",
                },
                body,
            )
            (task_dir / "task.md").write_text(spec_text, encoding="utf-8")

        (seed_dir / ".gitignore").write_text(
            "__pycache__/\n*.py[cod]\n", encoding="utf-8"
        )

        git_run(seed_dir, "init", "-b", "main", f"--template={empty_template}")
        git_run(seed_dir, "config", "user.email", "test@example.com")
        git_run(seed_dir, "config", "user.name", "test")
        git_run(seed_dir, "config", "commit.gpgsign", "false")
        git_run(seed_dir, "config", "gc.auto", "0")
        configure_project_verification(seed_dir)
        git_run(seed_dir, "add", "-A")
        git_run(seed_dir, "commit", "-m", "init")

        validate_seed(seed_dir)
        return seed_dir
    finally:
        if empty_template.exists():
            shutil.rmtree(empty_template)


def build_workflow_three_tasks_seed(seed_dir: Path) -> Path:
    """构建工作流三任务种子仓库 (t001, t002, t003)。"""
    return _build_workflow_tasks_seed(
        seed_dir,
        tasks=[("t001", "alpha"), ("t002", "beta"), ("t003", "gamma")],
    )


def build_workflow_two_tasks_seed(seed_dir: Path) -> Path:
    """构建工作流两任务种子仓库 (t001, t002)。"""
    return _build_workflow_tasks_seed(
        seed_dir,
        tasks=[("t001", "alpha"), ("t002", "beta")],
    )


def build_sync_source_seed(seed_dir: Path) -> Path:
    """构建 repo_sync 测试所用的模板源仓库种子。"""
    if seed_dir.exists():
        shutil.rmtree(seed_dir)
    seed_dir.mkdir(parents=True)

    empty_template = seed_dir.parent / f"_empty_template_{seed_dir.name}"
    empty_template.mkdir(parents=True, exist_ok=True)

    try:
        git_run(seed_dir, "init", "-b", "main", f"--template={empty_template}")
        git_run(seed_dir, "config", "user.email", "test@example.com")
        git_run(seed_dir, "config", "user.name", "test")
        git_run(seed_dir, "config", "commit.gpgsign", "false")
        git_run(seed_dir, "config", "gc.auto", "0")

        toolkit = seed_dir / ".repo_template"
        (toolkit / "scripts").mkdir(parents=True)
        (toolkit / "entity_schema.json").write_text('{"version": 1}\n')
        (toolkit / "scripts/task.py").write_text("print('task')\n", encoding="utf-8")
        (toolkit / "tests").mkdir(parents=True)
        (toolkit / "tests/test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
        (toolkit / "docs/task_template").mkdir(parents=True)
        (toolkit / "docs/task_template/task.md").write_text("# task\n", encoding="utf-8")
        (toolkit / "docs/task_template/progress.md").write_text(
            "---\ntid: t000\n---\n## 实施笔记\n无\n", encoding="utf-8"
        )
        (toolkit / "docs/review_prompts").mkdir(parents=True)
        (toolkit / "docs/review_prompts/general_prompt.txt").write_text(
            "general\n", encoding="utf-8"
        )
        (toolkit / "docs/spike_report_template.md").write_text("# report\n", encoding="utf-8")
        (toolkit / "docs/architecture.md").write_text("# arch\n", encoding="utf-8")
        (toolkit / "hooks").mkdir(parents=True)
        hook = toolkit / "hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        (toolkit / "skills/task-run").mkdir(parents=True)
        (toolkit / "skills/task-run/SKILL.md").write_text(
            "---\nname: task-run\ndescription: none\ndisable-model-invocation: true\n---\nrun\n",
            encoding="utf-8",
        )
        (seed_dir / ".md_kx.toml").write_text('table_mode = "compact"\n', encoding="utf-8")
        (seed_dir / "AGENTS.md").write_text("SRC PROJECT\n\n<!-- repo-template:begin -->\nSRC AGENTS\n<!-- repo-template:end -->\n", encoding="utf-8")
        (seed_dir / ".gitignore").write_text("# repo-template:begin\nnode_modules/\n*.log\n# repo-template:end\n", encoding="utf-8")
        git_run(seed_dir, "add", "-A")
        git_run(seed_dir, "commit", "-m", "init")

        validate_seed(seed_dir)
        return seed_dir
    finally:
        if empty_template.exists():
            shutil.rmtree(empty_template)


def build_full_product_tree_repo(dst: Path) -> Path:
    """把完整产物树 ``repo/`` 复制为独立消费仓并初始化 Git（P0-1 回归专用）。

    既有种子仓只复制 ``scripts/`` + ``docs/task_template``，不含
    ``.repo_template/tests/``、``hooks/``、``docs/``、``AGENTS.md`` 等随硬同步
    分发并被 Git 跟踪的文件，因而无法暴露「模板自带测试夹具命中隐私门禁」。
    本 helper 显式提供完整产物树布局；不改变任何既有种子仓的默认行为，
    也不注册 session fixture，故不拖慢全量测试。
    """
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        PRODUCT_ROOT, dst, symlinks=True,
        ignore=shutil.ignore_patterns(".git", ".scratch", "__pycache__", "*.pyc", ".pytest_cache"),
    )

    task_dir = dst / "docs" / "tasks" / "t001_alpha"
    shutil.copytree(TASK_TEMPLATE_DIR, task_dir)
    _, body = parse_front_matter(TASK_TEMPLATE_DIR / "progress.md")
    write_front_matter(
        task_dir / "progress.md",
        {
            "tid": "t001", "slug": "alpha", "title": "alpha", "status": "backlog",
            "branch": "", "worktree": "", "review_level": "full",
            "diff_anchor": "", "note": "",
        },
        body,
    )
    (task_dir / "task.md").write_text(valid_spec(), encoding="utf-8")
    configure_project_verification(dst)

    git_run(dst, "init", "-b", "main")
    git_run(dst, "config", "user.email", "test@example.com")
    git_run(dst, "config", "user.name", "test")
    git_run(dst, "config", "commit.gpgsign", "false")
    git_run(dst, "config", "gc.auto", "0")
    git_run(dst, "add", "-A")
    git_run(dst, "commit", "-m", "init")
    return dst


def validate_seed(seed: Path) -> None:
    """验证种子发布不变量（Plan 4.2 节）。"""
    git_dir = seed / ".git"
    assert git_dir.is_dir() and not git_dir.is_symlink(), f"种子 .git 必须为真实目录: {git_dir}"

    # 无 linked worktree
    worktrees_dir = git_dir / "worktrees"
    if worktrees_dir.exists():
        assert not any(worktrees_dir.iterdir()), f"种子不得包含 linked worktree 登记: {worktrees_dir}"

    # 无 alternates
    alternates = git_dir / "objects" / "info" / "alternates"
    assert not alternates.exists(), f"种子不得包含 alternates 引用: {alternates}"

    # 无外部绝对 core.worktree
    res = git_run(seed, "config", "--local", "--get", "core.worktree", check=False)
    assert not res.stdout.strip(), f"种子不得设置 core.worktree: {res.stdout}"

    # HEAD 指向 main
    res_head = git_run(seed, "symbolic-ref", "HEAD", check=False)
    assert res_head.returncode == 0 and res_head.stdout.strip() == "refs/heads/main", (
        f"种子 HEAD 必须指向 main: {res_head.stdout}"
    )

    # status 干净
    res_status = git_run(seed, "status", "--porcelain", check=True)
    assert not res_status.stdout.strip(), f"种子工作区必须干净: {res_status.stdout}"

    # 无运行时 ledger（状态在 Git common dir）
    from repo_task.context import common_state_dir

    state_dir = common_state_dir(seed)
    if state_dir.exists():
        assert not list(state_dir.glob("*.jsonl")), (
            f"种子不得包含 runtime ledger 文件: {state_dir}"
        )

    # 关键代码与模板文件完整
    scripts_task = seed / ".repo_template" / "scripts" / "task.py"
    assert scripts_task.is_file(), f"种子必须包含 scripts/task.py: {scripts_task}"


def copy_seed_repo(seed: Path, target: Path) -> Path:
    """普通独立复制完整种子仓库（symlinks=True，创建独立 inode）。"""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(seed, target, symlinks=True)
    return target


def state_file(repo: Path, name: str) -> Path:
    from repo_task.context import common_state_dir

    path = common_state_dir(Path(repo)) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def state_dir(repo: Path) -> Path:
    from repo_task.context import common_state_dir

    return common_state_dir(Path(repo))


def worktree_state_file(worktree: Path, name: str) -> Path:
    from repo_task.context import git_private_dir

    return git_private_dir(Path(worktree)) / name


def fast_copy_repo(
    seed: Path,
    target: Path,
    bin_parent: Path | None = None,
    monkeypatch: Any = None,
) -> Path:
    """高速复制种子并按需在用例环境中安装 fake md_kx 替身。"""
    repo = copy_seed_repo(seed, target)
    if bin_parent is not None and monkeypatch is not None:
        install_fake_md_kx(bin_parent, monkeypatch)
    return repo


def verify_repo_isolation(repo_a: Path, repo_b: Path, seed: Path | None = None) -> None:
    """验证两个副本之间（以及与种子之间）的文件系统与 Git 状态完全隔离（Plan 9.1 节）。"""
    assert repo_a != repo_b, "副本 A 和 B 路径必须不同"
    assert repo_a.resolve() != repo_b.resolve(), "副本 A 和 B 绝对路径必须不同"

    # 1. 检查普通文件 inode 独立
    test_file_rel = "docs/tasks/task_template/task.md"
    file_a = repo_a / test_file_rel
    file_b = repo_b / test_file_rel
    if file_a.exists() and file_b.exists():
        assert file_a.stat().st_ino != file_b.stat().st_ino, "副本间普通文件共享了 inode"
        if seed:
            file_s = seed / test_file_rel
            if file_s.exists():
                assert file_a.stat().st_ino != file_s.stat().st_ino, "副本与种子共享了普通文件 inode"

    # 2. 检查 .git/config inode 独立
    config_a = repo_a / ".git" / "config"
    config_b = repo_b / ".git" / "config"
    assert config_a.stat().st_ino != config_b.stat().st_ino, ".git/config 共享了 inode"
    if seed:
        assert config_a.stat().st_ino != (seed / ".git" / "config").stat().st_ino, (
            "副本与种子共享了 .git/config inode"
        )

    # 3. 检查 .git/index inode 独立
    index_a = repo_a / ".git" / "index"
    index_b = repo_b / ".git" / "index"
    if index_a.exists() and index_b.exists():
        assert index_a.stat().st_ino != index_b.stat().st_ino, ".git/index 共享了 inode"

    # 4. 检查不存在 alternates
    assert not (repo_a / ".git" / "objects" / "info" / "alternates").exists()
    assert not (repo_b / ".git" / "objects" / "info" / "alternates").exists()

    # 5. 检查 common dir 为各自私有目录
    res_a = git_run(repo_a, "rev-parse", "--git-common-dir")
    res_b = git_run(repo_b, "rev-parse", "--git-common-dir")
    common_a = Path(res_a.stdout.strip())
    common_b = Path(res_b.stdout.strip())
    if not common_a.is_absolute():
        common_a = (repo_a / common_a).resolve()
    if not common_b.is_absolute():
        common_b = (repo_b / common_b).resolve()
    assert common_a != common_b, f"Git common directory 共享冲突: {common_a} vs {common_b}"
