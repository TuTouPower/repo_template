"""种子仓库与独立副本隔离性及正确性测试（Plan 9.1 节）。

覆盖验证：
1. 种子发布不变量（真实 .git、无 linked worktree、无 alternates、干净工作区与 HEAD）；
2. 副本与种子、副本与副本之间的 inode 独立性与无软链/alternates 交叉；
3. 两个副本同时操作相同 tid 与分支名互不干扰；
4. 副本 A 的深层篡改（文件、Git config、hooks、对象文件破坏、runtime ledger）对副本 B 和种子零污染；
5. 跨进程业务锁隔离（进程 A 持锁时，独立副本进程 B 在受控超时内顺畅获取）；
6. 种子构建失败快速失败且不发布半成品；
7. 只读 loose object 复制与无残留清理。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from git_repo_support import (
    state_dir,
    build_workflow_three_tasks_seed,
    copy_seed_repo,
    fast_copy_repo,
    git_run,
    validate_seed,
    verify_repo_isolation,
)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _collect_tree_hashes(root: Path) -> dict[str, str]:
    """收集目录下所有常规文件的相对路径及其 SHA256 哈希字典。"""
    hashes = {}
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            hashes[str(p.relative_to(root))] = _hash_file(p)
    return hashes


def test_seed_invariants_and_integrity(workflow_three_tasks_seed: Path):
    """验证种子发布不变量（Plan 4.2 节）。"""
    seed = workflow_three_tasks_seed
    validate_seed(seed)

    # 1. .git 必须是真实目录且非软链
    git_dir = seed / ".git"
    assert git_dir.is_dir() and not git_dir.is_symlink()

    # 2. 无 linked worktrees
    worktrees_dir = git_dir / "worktrees"
    if worktrees_dir.exists():
        assert not any(worktrees_dir.iterdir())

    # 3. 无 alternates
    alternates = git_dir / "objects" / "info" / "alternates"
    assert not alternates.exists()

    # 4. HEAD 指向 main
    res_head = git_run(seed, "symbolic-ref", "HEAD")
    assert res_head.returncode == 0
    assert res_head.stdout.strip() == "refs/heads/main"

    # 5. commit 记录与提交信息
    res_log = git_run(seed, "log", "-1", "--pretty=%s")
    assert res_log.returncode == 0
    assert res_log.stdout.strip() == "init"

    # 6. status 干净
    res_status = git_run(seed, "status", "--porcelain")
    assert res_status.stdout.strip() == ""

    # 7. 三任务文档完整性
    for tid, slug in (("t001", "alpha"), ("t002", "beta"), ("t003", "gamma")):
        task_dir = seed / "docs" / "tasks" / f"{tid}_{slug}"
        assert (task_dir / "progress.md").is_file()
        assert (task_dir / "task.md").is_file()


def test_copy_inode_isolation_and_no_alternates(
    workflow_three_tasks_seed: Path, tmp_path: Path
):
    """验证副本与种子、副本间普通文件及 Git 元数据不共享 inode，且无 alternates。"""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    copy_seed_repo(workflow_three_tasks_seed, repo_a)
    copy_seed_repo(workflow_three_tasks_seed, repo_b)

    verify_repo_isolation(repo_a, repo_b, workflow_three_tasks_seed)

    # 详细核对 HEAD inode 独立性
    head_a = repo_a / ".git" / "HEAD"
    head_b = repo_b / ".git" / "HEAD"
    head_seed = workflow_three_tasks_seed / ".git" / "HEAD"
    assert head_a.stat().st_ino != head_b.stat().st_ino
    assert head_a.stat().st_ino != head_seed.stat().st_ino


def test_independent_worktree_operations_do_not_interfere(
    workflow_three_tasks_seed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """两个副本同时操作相同 tid 与分支名，各自创建、修改、提交、删除 worktree 互不干扰。"""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    fast_copy_repo(workflow_three_tasks_seed, repo_a, tmp_path / "bin_a", monkeypatch)
    fast_copy_repo(workflow_three_tasks_seed, repo_b, tmp_path / "bin_b", monkeypatch)

    cli_a = repo_a / ".repo_template" / "scripts" / "task.py"
    cli_b = repo_b / ".repo_template" / "scripts" / "task.py"

    # 1. 在 repo_a 中启动 t001
    res_a_start = subprocess.run(
        [sys.executable, str(cli_a), "start", "t001"],
        cwd=repo_a,
        capture_output=True,
        text=True,
    )
    assert res_a_start.returncode == 0, res_a_start.stderr

    wt_a = tmp_path / "repo_a_t001"
    assert wt_a.is_dir()
    assert (wt_a / ".git").is_file()  # worktree gitfile

    # 2. 检查此时 repo_b 仍然干净且处于 backlog
    res_b_branches = git_run(repo_b, "branch", "--list")
    assert "t001_alpha" not in res_b_branches.stdout
    assert not (tmp_path / "repo_b_t001").exists()

    # 3. 在 repo_a 的 worktree 中修改并提交
    (wt_a / "file_a.txt").write_text("content A", encoding="utf-8")
    git_run(wt_a, "add", "file_a.txt")
    git_run(wt_a, "commit", "-m", "commit from a")
    sha_a = git_run(wt_a, "rev-parse", "HEAD").stdout.strip()

    # 4. 在 repo_b 中同样启动 t001（同名 tid 与分支名）
    res_b_start = subprocess.run(
        [sys.executable, str(cli_b), "start", "t001"],
        cwd=repo_b,
        capture_output=True,
        text=True,
    )
    assert res_b_start.returncode == 0, res_b_start.stderr

    wt_b = tmp_path / "repo_b_t001"
    assert wt_b.is_dir()

    # 5. 在 repo_b 中写入不同内容并提交
    (wt_b / "file_b.txt").write_text("content B", encoding="utf-8")
    git_run(wt_b, "add", "file_b.txt")
    git_run(wt_b, "commit", "-m", "commit from b")
    sha_b = git_run(wt_b, "rev-parse", "HEAD").stdout.strip()

    # 验证两个提交 SHA 绝对不同，且各自文件仅在各自 worktree 存在
    assert sha_a != sha_b
    assert not (wt_b / "file_a.txt").exists()
    assert not (wt_a / "file_b.txt").exists()

    # 6. 删除 repo_a 的 worktree，验证 repo_b 的 worktree 依然健全
    git_run(repo_a, "worktree", "remove", "--force", str(wt_a))
    assert not wt_a.exists()
    assert wt_b.exists()
    res_b_check = git_run(wt_b, "rev-parse", "HEAD")
    assert res_b_check.returncode == 0
    assert res_b_check.stdout.strip() == sha_b


def test_mutation_of_repo_a_does_not_affect_repo_b_or_seed(
    workflow_three_tasks_seed: Path, tmp_path: Path
):
    """副本 A 修改工作区、Git config、hooks、refs、破坏对象文件与运行态后，B 和种子摘要保持不变。"""
    seed_before_hashes = _collect_tree_hashes(workflow_three_tasks_seed)

    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    copy_seed_repo(workflow_three_tasks_seed, repo_a)
    copy_seed_repo(workflow_three_tasks_seed, repo_b)

    repo_b_before_hashes = _collect_tree_hashes(repo_b)

    # 1. 在 repo_a 修改 git config
    git_run(repo_a, "config", "test.isolation.key", "dirty_value_in_a")

    # 2. 在 repo_a 写入恶意 hook
    hook_path = repo_a / ".git" / "hooks" / "pre-commit"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook_path.chmod(0o755)

    # 3. 在 repo_a 修改现有任务文件
    (repo_a / "docs" / "tasks" / "t001_alpha" / "task.md").write_text(
        "MUTATED CONTENT IN A\n", encoding="utf-8"
    )

    # 4. 在 repo_a 写入 runtime ledger 文件
    runtime_dir = state_dir(repo_a)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "ledger.jsonl").write_text('{"event": "fake"}\n', encoding="utf-8")

    # 5. 对象破坏测试：只给自有目标文件增加 owner 写权限后破坏内容
    loose_objects = list((repo_a / ".git" / "objects").glob("??/*"))
    assert loose_objects, "repo_a 必须包含至少一个 loose object"
    target_obj = loose_objects[0]
    orig_mode = target_obj.stat().st_mode
    target_obj.chmod(orig_mode | stat.S_IWUSR)
    target_obj.write_bytes(b"CORRUPTED_OBJECT_CONTENT")

    # 验证 repo_b 对应文件完全不受影响
    res_b_config = git_run(repo_b, "config", "--get", "test.isolation.key", check=False)
    assert res_b_config.returncode != 0

    assert not (repo_b / ".git" / "hooks" / "pre-commit").exists()
    assert not (state_dir(repo_b) / "ledger.jsonl").exists()

    rel_obj = target_obj.relative_to(repo_a)
    seed_obj = workflow_three_tasks_seed / rel_obj
    repo_b_obj = repo_b / rel_obj
    assert repo_b_obj.read_bytes() != b"CORRUPTED_OBJECT_CONTENT"
    assert seed_obj.read_bytes() != b"CORRUPTED_OBJECT_CONTENT"

    # 种子文件哈希严格不变
    seed_after_hashes = _collect_tree_hashes(workflow_three_tasks_seed)
    assert seed_before_hashes == seed_after_hashes, "种子文件被意外篡改！"

    # repo_b 未被修改的文件与初始一致
    repo_b_after_hashes = _collect_tree_hashes(repo_b)
    assert repo_b_before_hashes == repo_b_after_hashes, "副本 B 被副本 A 污染！"


def test_cross_process_control_lock_isolation(
    workflow_three_tasks_seed: Path, tmp_path: Path
):
    """A 持有其业务锁时，B 对独立副本的锁操作能顺利完成；文件事件同步，不依赖墙钟阈值。"""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    copy_seed_repo(workflow_three_tasks_seed, repo_a)
    copy_seed_repo(workflow_three_tasks_seed, repo_b)

    release = tmp_path / "release_a"
    lock_worker_code = """
import sys
import time
from pathlib import Path

repo_path = Path(sys.argv[1]).resolve()
release = Path(sys.argv[2])
sys.path.insert(0, str(repo_path / ".repo_template" / "scripts"))
from repo_task.locks import git_common_lock, CONTROL_LOCK_NAME

with git_common_lock(repo_path, CONTROL_LOCK_NAME):
    sys.stdout.write("ACQUIRED\\n")
    sys.stdout.flush()
    while not release.exists():
        time.sleep(0.02)
sys.stdout.write("RELEASED\\n")
sys.stdout.flush()
"""

    # A 获取 repo_a 的锁后一直持有，直到父进程写入 release 事件。
    proc_a = subprocess.Popen(
        [sys.executable, "-c", lock_worker_code, str(repo_a), str(release)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        assert proc_a.stdout is not None
        line = proc_a.stdout.readline().strip()
        assert line == "ACQUIRED", f"proc_a 未能成功获取锁: {proc_a.stderr.read()}"

        # 锁隔离正确时 B 立即拿到 repo_b 的锁；若两个副本错误共享同一把锁，
        # B 会被 A 阻塞，而 A 在等 release、父进程在等 B，形成死锁，
        # 最终由 proc_b 的 timeout 暴露。timeout 只是死锁安全网，不是墙钟阈值断言。
        test_b_code = """
import sys
from pathlib import Path

repo_path = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo_path / ".repo_template" / "scripts"))
from repo_task.locks import git_common_lock, CONTROL_LOCK_NAME

with git_common_lock(repo_path, CONTROL_LOCK_NAME):
    sys.stdout.write("B_SUCCESS\\n")
    sys.stdout.flush()
"""
        proc_b = subprocess.run(
            [sys.executable, "-c", test_b_code, str(repo_b)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc_b.returncode == 0, f"proc_b 失败: {proc_b.stderr}"
        assert "B_SUCCESS" in proc_b.stdout

    finally:
        release.write_text("release", encoding="utf-8")
        proc_a.terminate()
        try:
            proc_a.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc_a.kill()


def test_seed_build_failure_fails_fast_and_leaves_no_broken_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """种子构建失败即使 fixture 失败，不发布半成品（Plan 4.2 节）。"""
    bad_seed_dir = tmp_path / "bad_seed"

    # 模拟在种子构建途中 Git 发生致命错误
    import git_repo_support
    orig_git_run = git_repo_support.git_run

    def mock_broken_git(repo: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "commit" in args:
            raise subprocess.CalledProcessError(1, ["git", *args], output="", stderr="disk full")
        return orig_git_run(repo, *args, **kwargs)

    monkeypatch.setattr(git_repo_support, "git_run", mock_broken_git)

    with pytest.raises(subprocess.CalledProcessError):
        build_workflow_three_tasks_seed(bad_seed_dir)

    # 验证半成品无法通过 validate_seed
    with pytest.raises(Exception):
        validate_seed(bad_seed_dir)


def test_readonly_loose_object_copy_and_cleanup(
    workflow_three_tasks_seed: Path, tmp_path: Path
):
    """验证只读 loose object 的复制与无残留清理（Plan 9.1 节）。"""
    copy_dir = tmp_path / "copy_test"
    copy_seed_repo(workflow_three_tasks_seed, copy_dir)

    # 查找 loose object
    loose_objects = list((copy_dir / ".git" / "objects").glob("??/*"))
    assert loose_objects, "复制副本中必须存在 loose object"

    # 验证至少存在只读 (0444) 权限的文件（Git 默认对象权限）
    read_only_objs = [
        obj for obj in loose_objects if not (obj.stat().st_mode & stat.S_IWUSR)
    ]
    assert read_only_objs, "必须存在 Git 只读 loose object"

    # 验证无需对只读对象递归 chmod，shutil.rmtree 即可干净删除副本
    shutil.rmtree(copy_dir)
    assert not copy_dir.exists(), "复制副本清理失败"
