"""test_pending.py - Pending 待办实体固定路径与元数据状态流转契约测试。

覆盖：
1. 新条目创建（固定路径 docs/pending/pNNN_slug.md、严格 front matter、bug 模板、slug 推导）
2. 原位状态流转（todo -> parked -> todo、todo -> closed）
3. 非法转换拦截（parked -> closed、closed 终态不可逆、todo -> revive 等）
4. 缺少理由拦截（park / close 必须提供非空 reason）
5. 批量操作与 dry-run（dry-run 零写入、批量执行、部分失败原子回滚）
6. 取消自动 git add（验证操作后 index 保持原样）
7. 取号隔离（closed / parked 占号防重、顺序分配）
8. Front matter 严格校验（未知字段、编号/slug 不一致、非法状态、换行注入）
9. list 过滤（--state todo / parked / closed / all）
10. 旧命令与旧状态移除（archive、rename、--state archived 被拒绝）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from git_env_support import git_run as _git
from git_env_support import init_repo

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from _id_scan import IdScanError, scan_max_id
import pending as pending_mod
from functools import partial
from repo_task.context import RepoContext
from repo_task.documents import atomic_write_text

pytestmark = pytest.mark.contract


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = init_repo(tmp_path / "repo")
    (r / "docs" / "pending").mkdir(parents=True)
    (r / "docs" / "pending" / ".gitkeep").write_text("", encoding="utf-8")
    (r / ".repo_template").mkdir(exist_ok=True)
    (r / ".repo_template/entity_schema.json").write_text('{"version": 1}\n')
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "init")

    monkeypatch.setattr(pending_mod, "main", partial(pending_mod.main, repo=RepoContext.for_root(r)))
    return r


def test_pending_new_creates_canonical_path_and_todo_state(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pending_mod.main(["new", "demo_task", "--title", "Demo Title"])
    out = capsys.readouterr().out.strip()
    assert out == "docs/pending/p001_demo_task.md"

    path = repo / "docs/pending/p001_demo_task.md"
    assert path.is_file()

    fm, body = pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))
    assert fm == {
        "pid": "p001",
        "slug": "demo_task",
        "status": "todo",
    }
    assert "reason" not in fm
    assert "resolution" not in fm
    assert "fix_ref" not in fm
    assert "# p001 Demo Title" in body
    assert "- 处理：未开" in body

    # 验证不自动 git add（index 无任何 staged 变化）
    staged = _git(repo, "diff", "--cached", "--name-status").stdout
    assert staged == ""


def test_pending_new_slug_options_and_derivation(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 选项 --slug
    pending_mod.main(["new", "--slug", "opt_slug"])
    assert (repo / "docs/pending/p001_opt_slug.md").is_file()

    # 从 --title 自动生成 slug：只产生一个 p002_*.md，且 slug 符合规范（DS L293）
    pending_mod.main(["new", "--title", "CLI 退出码异常"])
    matches = sorted((repo / "docs/pending").glob("p002_*.md"))
    assert len(matches) == 1
    assert pending_mod.SLUG_RE.match(matches[0].stem.split("_", 1)[1])


def test_pending_new_bug_kind(repo: Path) -> None:
    pending_mod.main(["new", "crash_item", "--kind", "bug"])
    path = repo / "docs/pending/p001_crash_item.md"
    assert path.is_file()
    fm, body = pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))
    assert fm["status"] == "todo"
    assert "- 现象：" in body
    assert "- 测试缺口：" in body
    assert "- 线索：" in body


def test_pending_new_rejects_missing_or_bad_slug(repo: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        pending_mod.main(["new"])
    assert exc.value.code == 2

    with pytest.raises(SystemExit):
        pending_mod.main(["new", "Bad-Slug"])


def test_park_then_revive_round_trip(repo: Path) -> None:
    pending_mod.main(["new", "demo"])
    path = repo / "docs/pending/p001_demo.md"

    # park
    pending_mod.main(["park", "p001", "--reason", "2026-11-08 等外部依赖", "--write"])
    assert path.is_file()
    fm, body = pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))
    assert fm == {
        "pid": "p001",
        "slug": "demo",
        "status": "parked",
        "reason": "2026-11-08 等外部依赖",
    }
    assert "resolution" not in fm
    assert "fix_ref" not in fm
    assert "- 暂搁：2026-11-08 等外部依赖" in body
    assert "- 处理：不办" in body

    # revive
    pending_mod.main(["revive", "p001", "--write"])
    assert path.is_file()
    fm, body = pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))
    assert fm == {
        "pid": "p001",
        "slug": "demo",
        "status": "todo",
    }
    assert "reason" not in fm
    assert "resolution" not in fm
    assert "- 处理：未开" in body
    # 历史记录在正文中保留
    assert "- 暂搁：2026-11-08 等外部依赖" in body


def test_close_direct_from_todo(repo: Path) -> None:
    pending_mod.main(["new", "demo"])
    path = repo / "docs/pending/p001_demo.md"

    pending_mod.main([
        "close", "p001",
        "--reason", "已在 main 修复并通过回归",
        "--fix-ref", "t042",
        "--resolution", "transferred",
        "--write",
    ])

    assert path.is_file()
    fm, body = pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))
    assert fm == {
        "pid": "p001",
        "slug": "demo",
        "status": "closed",
        "resolution": "transferred",
        "reason": "已在 main 修复并通过回归",
        "fix_ref": "t042",
    }
    assert "- 处理：t042；resolution=transferred；reason=已在 main 修复并通过回归" in body


def test_close_defaults_to_resolved_without_fix_ref(repo: Path) -> None:
    pending_mod.main(["new", "demo"])
    path = repo / "docs/pending/p001_demo.md"

    pending_mod.main(["close", "p001", "--reason", "经复核无需处理", "--write"])

    fm, body = pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))
    assert fm["status"] == "closed"
    assert fm["resolution"] == "resolved"
    assert fm["reason"] == "经复核无需处理"
    assert "fix_ref" not in fm
    assert "- 处理：resolution=resolved；reason=经复核无需处理" in body


def test_illegal_transitions_rejected(repo: Path) -> None:
    pending_mod.main(["new", "demo"])

    # todo 不能直接 revive
    with pytest.raises(SystemExit, match="只有 parked 可复活"):
        pending_mod.main(["revive", "p001", "--write"])

    # park 之后
    pending_mod.main(["park", "p001", "--reason", "wait", "--write"])

    # parked 不能再 park
    with pytest.raises(SystemExit, match="只有 todo 可暂搁"):
        pending_mod.main(["park", "p001", "--reason", "wait again", "--write"])

    # parked 不能直接 close，必须先 revive
    with pytest.raises(SystemExit, match="如需闭环请先 revive"):
        pending_mod.main(["close", "p001", "--reason", "done", "--write"])

    # revive 恢复 todo 后 close
    pending_mod.main(["revive", "p001", "--write"])
    pending_mod.main(["close", "p001", "--reason", "finished", "--write"])

    # closed 是终态：不能重复 close、不能 park、不能 revive
    with pytest.raises(SystemExit, match="禁止重复闭环"):
        pending_mod.main(["close", "p001", "--reason", "again", "--write"])

    with pytest.raises(SystemExit, match="终态"):
        pending_mod.main(["park", "p001", "--reason", "wait", "--write"])

    with pytest.raises(SystemExit, match="终态"):
        pending_mod.main(["revive", "p001", "--write"])


def test_missing_reason_rejected(repo: Path) -> None:
    pending_mod.main(["new", "demo"])

    with pytest.raises(SystemExit):
        pending_mod.main(["park", "p001", "--write"])

    with pytest.raises(SystemExit, match="非空 --reason"):
        pending_mod.main(["park", "p001", "--reason", "  ", "--write"])

    with pytest.raises(SystemExit):
        pending_mod.main(["close", "p001", "--write"])

    with pytest.raises(SystemExit, match="非空 --reason"):
        pending_mod.main(["close", "p001", "--reason", "", "--write"])


def test_dry_run_zero_writes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pending_mod.main(["new", "demo"])
    path = repo / "docs/pending/p001_demo.md"
    orig_text = path.read_text(encoding="utf-8")

    # park dry-run
    pending_mod.main(["park", "p001", "--reason", "wait"])
    assert path.read_text(encoding="utf-8") == orig_text
    err = capsys.readouterr().err
    assert "dry-run" in err
    assert "status: parked" in err

    # close dry-run
    pending_mod.main(["close", "p001", "--reason", "done"])
    assert path.read_text(encoding="utf-8") == orig_text
    err = capsys.readouterr().err
    assert "dry-run" in err
    assert "status: closed" in err


def test_batch_operations_and_atomic_rollback(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pending_mod.main(["new", "one"])
    pending_mod.main(["new", "two"])
    path1 = repo / "docs/pending/p001_one.md"
    path2 = repo / "docs/pending/p002_two.md"

    # 批量 park
    pending_mod.main(["park", "p001", "p002", "--reason", "batch wait", "--write"])
    assert pending_mod.parse_pending_file(path1, repo=RepoContext.for_root(repo))[0]["status"] == "parked"
    assert pending_mod.parse_pending_file(path2, repo=RepoContext.for_root(repo))[0]["status"] == "parked"

    # 批量 revive
    pending_mod.main(["revive", "p001", "p002", "--write"])
    assert pending_mod.parse_pending_file(path1, repo=RepoContext.for_root(repo))[0]["status"] == "todo"
    assert pending_mod.parse_pending_file(path2, repo=RepoContext.for_root(repo))[0]["status"] == "todo"

    # 批量更新中途失败回滚
    orig1 = path1.read_bytes()
    orig2 = path2.read_bytes()
    calls = {"n": 0}
    real_write = pending_mod.atomic_write_text

    def flaky_write(path: Path | str, content: str, *args, **kwargs) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated disk error on second write")
        real_write(path, content, *args, **kwargs)

    monkeypatch.setattr(pending_mod, "atomic_write_text", flaky_write)

    with pytest.raises(SystemExit, match="simulated disk error"):
        pending_mod.main(["close", "p001", "p002", "--reason", "done", "--write"])

    # 第一条已被成功回滚，正文与状态不变
    assert path1.read_bytes() == orig1
    assert path2.read_bytes() == orig2
    assert pending_mod.parse_pending_file(path1, repo=RepoContext.for_root(repo))[0]["status"] == "todo"
    assert pending_mod.parse_pending_file(path2, repo=RepoContext.for_root(repo))[0]["status"] == "todo"


def test_no_auto_git_add_on_state_changes(repo: Path) -> None:
    pending_mod.main(["new", "demo"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "commit p001")

    # park
    pending_mod.main(["park", "p001", "--reason", "wait", "--write"])
    staged = _git(repo, "diff", "--cached", "--name-status").stdout
    assert staged == ""
    unstaged = _git(repo, "diff", "--name-status").stdout
    assert "docs/pending/p001_demo.md" in unstaged

    # revive
    pending_mod.main(["revive", "p001", "--write"])
    assert _git(repo, "diff", "--cached", "--name-status").stdout == ""

    # close
    pending_mod.main(["close", "p001", "--reason", "done", "--write"])
    assert _git(repo, "diff", "--cached", "--name-status").stdout == ""


def test_id_scan_and_allocation_isolation(repo: Path) -> None:
    pending_mod.main(["new", "first"])
    pending_mod.main(["close", "p001", "--reason", "done", "--write"])

    # closed 条目继续占号，不能复用 p001
    pending_mod.main(["new", "second"])
    assert (repo / "docs/pending/p002_second.md").is_file()

    # parked 条目同样占号
    pending_mod.main(["park", "p002", "--reason", "wait", "--write"])
    pending_mod.main(["new", "third"])
    assert (repo / "docs/pending/p003_third.md").is_file()

    assert scan_max_id(repo, "p", ("docs/pending",)) == 3


def test_front_matter_validation_strictness(repo: Path) -> None:
    path = repo / "docs/pending/p001_demo.md"

    # 未知字段
    atomic_write_text(path, '---\npid: "p001"\nslug: "demo"\nstatus: "todo"\nunknown: "val"\n---\n# p001\n')
    with pytest.raises(IdScanError, match="未知 front matter 字段"):
        pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))

    # pid 与文件名不一致
    atomic_write_text(path, '---\npid: "p002"\nslug: "demo"\nstatus: "todo"\n---\n# p001\n')
    with pytest.raises(IdScanError, match="元数据 pid .* 不一致"):
        pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))

    # slug 与文件名不一致
    atomic_write_text(path, '---\npid: "p001"\nslug: "other"\nstatus: "todo"\n---\n# p001\n')
    with pytest.raises(IdScanError, match="元数据 slug .* 不一致"):
        pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))

    # todo 状态包含 resolution
    atomic_write_text(path, '---\npid: "p001"\nslug: "demo"\nstatus: "todo"\nresolution: "resolved"\n---\n# p001\n')
    with pytest.raises(IdScanError, match="status=todo 时不得包含 resolution"):
        pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))

    # 包含换行符
    atomic_write_text(path, '---\npid: "p001"\nslug: "demo"\nstatus: "parked"\nreason: "line1\\nline2"\n---\n# p001\n')
    with pytest.raises(IdScanError, match="不能包含换行符"):
        pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))

    # closed 缺少 resolution
    atomic_write_text(path, '---\npid: "p001"\nslug: "demo"\nstatus: "closed"\nreason: "done"\n---\n# p001\n')
    with pytest.raises(IdScanError, match="resolution 非法"):
        pending_mod.parse_pending_file(path, repo=RepoContext.for_root(repo))


def test_list_filtering(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pending_mod.main(["new", "item_a"])
    pending_mod.main(["new", "item_b"])
    pending_mod.main(["new", "item_c"])

    pending_mod.main(["park", "p002", "--reason", "wait", "--write"])
    pending_mod.main(["close", "p003", "--reason", "done", "--write"])

    capsys.readouterr()

    # 默认只列出 todo
    pending_mod.main(["list"])
    out = capsys.readouterr().out
    assert "p001_item_a" in out
    assert "p002_item_b" not in out
    assert "p003_item_c" not in out

    # list --state parked
    pending_mod.main(["list", "--state", "parked"])
    out = capsys.readouterr().out
    assert "p001_item_a" not in out
    assert "p002_item_b" in out
    assert "p003_item_c" not in out

    # list --state closed
    pending_mod.main(["list", "--state", "closed"])
    out = capsys.readouterr().out
    assert "p001_item_a" not in out
    assert "p002_item_b" not in out
    assert "p003_item_c" in out

    # list --state all
    pending_mod.main(["list", "--state", "all"])
    out = capsys.readouterr().out
    assert "p001_item_a" in out
    assert "p002_item_b" in out
    assert "p003_item_c" in out


def test_removed_commands_not_available() -> None:
    with pytest.raises(SystemExit):
        pending_mod.main(["archive", "p001"])

    with pytest.raises(SystemExit):
        pending_mod.main(["rename", "p001"])

    with pytest.raises(SystemExit):
        pending_mod.main(["list", "--state", "archived"])


def test_list_corrupted_metadata_fails_closed(repo: Path) -> None:
    pending_mod.main(["new", "valid_entry"])
    corrupt_file = repo / "docs/pending/p002_corrupt.md"
    corrupt_file.write_text("---\npid: p002\ninvalid: true\n---\n# Corrupted\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="未知 front matter 字段|解析 front matter 失败"):
        pending_mod.main(["list"])


def test_slug_from_title_is_deterministic():
    """slug 推导是单一确定性行为，不接受两个候选文件名（DS L293）。"""
    from _id_scan import slug_from_title

    assert slug_from_title("CLI 退出码异常（2026-07-01 发现）") == "cli"
    assert slug_from_title("UV Lock Platform Marker") == "uv_lock_platform_marker"
    assert slug_from_title("!!!") == "entry"


def test_concurrent_modification_rejected_by_sha256(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pending_mod.main(["new", "race_entry"])
    entry = repo / "docs/pending/p001_race_entry.md"

    real_find = pending_mod.find_entry
    calls = {"n": 0}

    def modify_on_second_find(number: int, **kwargs) -> tuple[str, Path]:
        calls["n"] += 1
        state, path = real_find(number, **kwargs)
        if calls["n"] == 2:
            # 锁外读取完成，锁内定位时外部写入了新内容
            path.write_text(path.read_text(encoding="utf-8") + "\n- 并发写入\n", encoding="utf-8")
        return state, path

    monkeypatch.setattr(pending_mod, "find_entry", modify_on_second_find)
    with pytest.raises(SystemExit, match="内容已被并发修改|内容摘要不一致"):
        pending_mod.main(["close", "p001", "--reason", "完成", "--write"])

