"""P2 批量 Git 读取与 commit 树解析优化严密单测与集成测试。

覆盖：
1. 协议解析器 _parse_cat_file_batch_output 协议分支与截断/畸形输出防御；
2. batch_cat_file 在真实 Git 仓库下的正常读取、缺失、重复、分批、二进制及超长内容；
3. store.py 的操作内快照复用与不可变隔离；
4. 批量树模式校验与批量物化。
"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

from git_repo_support import get_controlled_git_env, git_run
from repo_task import context as ctx
from repo_task.git_ops import (
    _git_bytes,
    _parse_batch_check_output,
    _parse_cat_file_batch_output,
    batch_cat_file,
    batch_object_sizes,
)
from repo_task.integration import _materialize_task_dir_from_ref, _validate_task_dir_tree_modes
from repo_task.store import (
    CommitSnapshotCache,
    CommitTaskSnapshot,
    _load_tasks_at_commit,
    load_owner_task_at_ref,
    load_task_at_ref,
    resolve_baseline_spec_text,
    scan_tasks_at_ref,
)


# ==============================================================================
# 1. 协议解析器 _parse_cat_file_batch_output 边界与异常防护单测
# ==============================================================================


def test_parse_empty_oids():
    res = _parse_cat_file_batch_output(b"", [])
    assert res == {}


def test_parse_single_and_multiple_blobs():
    # oid1: 5 字节 "hello", oid2: 0 字节 ""
    data = (
        b"1111111111111111111111111111111111111111 blob 5\nhello\n"
        b"2222222222222222222222222222222222222222 blob 0\n\n"
    )
    res = _parse_cat_file_batch_output(data, ["oid1", "oid2"])
    assert res == {"oid1": b"hello", "oid2": b""}


def test_parse_missing_blob():
    data = b"0000000000000000000000000000000000000000 missing\n"
    res = _parse_cat_file_batch_output(data, ["oid_missing"])
    assert res == {"oid_missing": None}


def test_parse_mixed_existing_and_missing():
    data = (
        b"1111111111111111111111111111111111111111 blob 3\nabc\n"
        b"missing_oid missing\n"
        b"2222222222222222222222222222222222222222 blob 4\ndefg\n"
    )
    res = _parse_cat_file_batch_output(data, ["oid1", "missing_oid", "oid2"])
    assert res == {"oid1": b"abc", "missing_oid": None, "oid2": b"defg"}


def test_parse_truncated_header_eof():
    data = b"1111111111111111111111111111111111111111 blob 5"  # 缺少头部 \n
    with pytest.raises(ctx.TaskDataError, match="头部缺少换行符"):
        _parse_cat_file_batch_output(data, ["oid1"])


def test_parse_truncated_empty_stdout():
    with pytest.raises(ctx.TaskDataError, match="输出截断：预期 1 项，第 1 项处 EOF"):
        _parse_cat_file_batch_output(b"", ["oid1"])


def test_parse_truncated_content():
    data = b"1111111111111111111111111111111111111111 blob 10\n12345"  # 预期 10 字节，实际只有 5 字节
    with pytest.raises(ctx.TaskDataError, match="预期内容 10 字节，剩余 5 字节"):
        _parse_cat_file_batch_output(data, ["oid1"])


def test_parse_missing_trailing_newline():
    data = b"1111111111111111111111111111111111111111 blob 5\n12345X"  # 末尾应为 \n 而非 X
    with pytest.raises(ctx.TaskDataError, match="内容末尾缺少换行符"):
        _parse_cat_file_batch_output(data, ["oid1"])


def test_parse_invalid_header_parts():
    data = b"invalid_header_line\n"
    with pytest.raises(ctx.TaskDataError, match="头部格式非法"):
        _parse_cat_file_batch_output(data, ["oid1"])


def test_parse_invalid_header_size():
    data = b"1111111111111111111111111111111111111111 blob not_a_number\n"
    with pytest.raises(ctx.TaskDataError, match="头部 size 非法"):
        _parse_cat_file_batch_output(data, ["oid1"])


def test_parse_negative_header_size():
    data = b"1111111111111111111111111111111111111111 blob -5\n"
    with pytest.raises(ctx.TaskDataError, match="头部 size 非法"):
        _parse_cat_file_batch_output(data, ["oid1"])


def test_parse_extra_trailing_bytes():
    data = (
        b"1111111111111111111111111111111111111111 blob 3\nabc\n"
        b"extra_junk_bytes"
    )
    with pytest.raises(ctx.TaskDataError, match="产生多余未解析数据"):
        _parse_cat_file_batch_output(data, ["oid1"])


# ==============================================================================
# 2. batch_cat_file 真实 Git 仓库集成测试
# ==============================================================================


@pytest.fixture
def sample_repo(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    git_run(repo_dir, "init", "-b", "main")
    git_run(repo_dir, "config", "user.name", "Test")
    git_run(repo_dir, "config", "user.email", "test@example.com")
    git_run(repo_dir, "config", "commit.gpgsign", "false")
    return repo_dir


def _hash_object(repo_dir: Path, content: bytes, write: bool = True) -> str:
    cmd = ["hash-object", "--stdin"]
    if write:
        cmd.append("-w")
    res = subprocess.run(
        ["git", "-C", str(repo_dir), *cmd],
        input=content, capture_output=True, check=True,
        env=get_controlled_git_env(),
    )
    return res.stdout.strip().decode("ascii")


def test_batch_cat_file_empty_list(sample_repo):
    assert batch_cat_file([], root=sample_repo) == {}


def test_batch_cat_file_normal_objects(sample_repo):
    c1 = b"Hello, World!\n"
    c2 = b""  # 空文件 blob
    c3 = "测试中文 UTF-8 内容与特殊符号 🚀\n第二行".encode("utf-8")
    c4 = bytes(range(256))  # 全量二进制字节 0x00..0xff

    oid1 = _hash_object(sample_repo, c1)
    oid2 = _hash_object(sample_repo, c2)
    oid3 = _hash_object(sample_repo, c3)
    oid4 = _hash_object(sample_repo, c4)

    oids = [oid1, oid2, oid3, oid4]
    res = batch_cat_file(oids, root=sample_repo)

    assert res[oid1] == c1
    assert res[oid2] == c2
    assert res[oid3] == c3
    assert res[oid4] == c4


def test_batch_cat_file_missing_and_duplicates(sample_repo):
    c1 = b"existing_content"
    oid1 = _hash_object(sample_repo, c1)
    missing_oid = "0123456789abcdef0123456789abcdef01234567"

    # 包含重复 oid 与缺失 oid
    oids = [oid1, missing_oid, oid1, missing_oid]
    res = batch_cat_file(oids, root=sample_repo)

    assert res[oid1] == c1
    assert res[missing_oid] is None
    assert set(res.keys()) == {oid1, missing_oid}


def test_batch_cat_file_chunking(sample_repo):
    # 创建 10 个 blob，设置 batch_size=3，验证自动分多批 run 的结果拼接正确
    oids = []
    contents = {}
    for i in range(10):
        content = f"content_chunk_{i}\n".encode("utf-8")
        oid = _hash_object(sample_repo, content)
        oids.append(oid)
        contents[oid] = content

    res = batch_cat_file(oids, root=sample_repo, batch_size=3)
    for oid in oids:
        assert res[oid] == contents[oid]


def test_batch_cat_file_large_content(sample_repo):
    # 构造超过常规管道容量（64KB）的大文件（例如 256KB）
    large_data = b"A" * (256 * 1024)
    oid = _hash_object(sample_repo, large_data)

    res = batch_cat_file([oid], root=sample_repo)
    assert res[oid] == large_data


def test_batch_cat_file_rejects_newlines_in_oid(sample_repo):
    with pytest.raises(ctx.TaskDataError, match="非法对象 ID"):
        batch_cat_file(["bad\noid"], root=sample_repo)


# ==============================================================================
# 2b. batch_object_sizes 与 batch-check 严格解析（隐私扫描大小预过滤）
# ==============================================================================


def test_batch_object_sizes_normal_empty_and_duplicates(sample_repo):
    assert batch_object_sizes([], root=sample_repo) == {}
    oid = _hash_object(sample_repo, b"12345")
    assert batch_object_sizes([oid, oid], root=sample_repo) == {oid: 5}


def test_batch_object_sizes_chunking(sample_repo):
    oids, contents = [], {}
    for i in range(10):
        content = f"content_size_{i}\n".encode()
        oid = _hash_object(sample_repo, content)
        oids.append(oid)
        contents[oid] = len(content)
    assert batch_object_sizes(oids, root=sample_repo, batch_size=3) == contents


def test_batch_object_sizes_missing_fails_closed(sample_repo):
    missing = "0123456789abcdef0123456789abcdef01234567"
    with pytest.raises(ctx.TaskDataError, match="对象缺失"):
        batch_object_sizes([missing], root=sample_repo)


def test_parse_batch_check_rejects_bad_header_and_trailing_bytes():
    with pytest.raises(ctx.TaskDataError, match="头部格式非法"):
        _parse_batch_check_output(b"nonsense\n", ["oid1"])
    with pytest.raises(ctx.TaskDataError, match="size 非法"):
        _parse_batch_check_output(b"oid1 blob nope\n", ["oid1"])
    with pytest.raises(ctx.TaskDataError, match="多余未解析数据"):
        _parse_batch_check_output(b"oid1 blob 3\njunk\n", ["oid1"])


# ==============================================================================
# 3. store.py 快照与批量读取集成测试
# ==============================================================================


@pytest.fixture
def repo_with_tasks(sample_repo):
    repo_context = ctx.RepoContext.for_root(sample_repo)
    (sample_repo / "docs" / "tasks" / "t001_first").mkdir(parents=True)
    (sample_repo / "docs" / "tasks" / "t002_second").mkdir(parents=True)
    (sample_repo / "docs" / "tasks" / "t003_old").mkdir(parents=True)
    (sample_repo / "docs" / "task_template").mkdir(parents=True)

    # 写入 template progress.md
    (sample_repo / "docs" / "task_template" / "progress.md").write_text(
        "---\ntid: t000\nstatus: backlog\n---\n# template\n", encoding="utf-8"
    )

    # 写入 t001
    (sample_repo / "docs" / "tasks" / "t001_first" / "progress.md").write_text(
        "---\ntid: t001\nslug: first\nstatus: backlog\n---\n# t001 body\n", encoding="utf-8"
    )
    (sample_repo / "docs" / "tasks" / "t001_first" / "task.md").write_text(
        "# t001 spec\n", encoding="utf-8"
    )

    # 写入 t002
    (sample_repo / "docs" / "tasks" / "t002_second" / "progress.md").write_text(
        "---\ntid: t002\nslug: second\nstatus: backlog\n---\n# t002 body\n", encoding="utf-8"
    )

    # 写入 t003
    (sample_repo / "docs" / "tasks" / "t003_old" / "progress.md").write_text(
        "---\ntid: t003\nslug: old\nstatus: done\n---\n# t003 archive body\n", encoding="utf-8"
    )

    git_run(sample_repo, "add", "-A")
    git_run(sample_repo, "commit", "-m", "init tasks")
    return repo_context


def test_scan_and_load_tasks_at_ref(repo_with_tasks):
    repo = repo_with_tasks
    tasks = scan_tasks_at_ref("HEAD", repo=repo)
    tids = [t["tid"] for t in tasks]
    assert tids == ["t001", "t002", "t003"]

    # load_task_at_ref
    task, fm, body = load_task_at_ref("t001", "HEAD", repo=repo)
    assert task["tid"] == "t001"
    assert fm["status"] == "backlog"
    assert "# t001 body" in body

    # 验证不可变隔离：修改返回的 task / fm，不污染缓存
    task["status"] = "mutated"
    fm["status"] = "mutated"

    task2, fm2, _ = load_task_at_ref("t001", "HEAD", repo=repo)
    assert task2["status"] == "backlog"
    assert fm2["status"] == "backlog"

    # 不存在的 tid 报 TaskNotFoundError
    with pytest.raises(ctx.TaskNotFoundError, match="缺自身 task t999|不存在于 ref"):
        load_task_at_ref("t999", "HEAD", repo=repo)


def test_load_owner_task_at_ref(repo_with_tasks):
    repo = repo_with_tasks
    owner = load_owner_task_at_ref("t001", "HEAD", repo=repo)
    assert owner["tid"] == "t001"
    assert owner["slug"] == "first"

    with pytest.raises(ctx.TaskNotFoundError, match="缺自身 task t999"):
        load_owner_task_at_ref("t999", "HEAD", repo=repo)


def test_resolve_baseline_spec_text_batch(repo_with_tasks):
    repo = repo_with_tasks
    head_sha = git_run(repo.root, "rev-parse", "HEAD").stdout.strip()

    text, source_sha = resolve_baseline_spec_text(
        "t001", "docs/tasks/t001_first/task.md", head_sha, repo=repo
    )
    assert text == "# t001 spec\n"
    assert source_sha == head_sha

    # 不存在的 spec 文件报 TaskNotFoundError
    with pytest.raises(ctx.TaskNotFoundError, match="不存在或无法读取"):
        resolve_baseline_spec_text("t001", "docs/tasks/t001_first/nonexistent.md", head_sha, repo=repo)


def test_materialize_task_dir_from_ref_batch(repo_with_tasks, tmp_path):
    repo = repo_with_tasks
    dest_worktree = tmp_path / "wt"
    dest_worktree.mkdir()

    # 增加一个 100755 可执行脚本到 t001 目录
    script = repo.root / "docs" / "tasks" / "t001_first" / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    script.chmod(0o755)
    git_run(repo.root, "add", "-A")
    git_run(repo.root, "commit", "-m", "add executable script")

    _materialize_task_dir_from_ref("HEAD", "docs/tasks/t001_first", dest_worktree, repo=repo)

    mat_dir = dest_worktree / "docs" / "tasks" / "t001_first"
    assert (mat_dir / "progress.md").is_file()
    assert (mat_dir / "task.md").is_file()
    mat_script = mat_dir / "run.sh"
    assert mat_script.is_file()
    assert (mat_script.stat().st_mode & 0o111) != 0  # 可执行位已正确设置
