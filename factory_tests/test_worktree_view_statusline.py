"""视图/worktree 主题：statusline 事件模型与账本健壮性回归（工厂集成测试，不进产物）。

覆盖采纳项：P1-12（恢复重试后解析为空）、DS L266~L269、L275。
"""
import json
import logging
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from repo_task import statusline


def _write_ledger(project: Path, events: list[dict]) -> Path:
    runtime = project / "docs" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    ledger = runtime / "dispatch_ledger.jsonl"
    ledger.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    return ledger


def test_report_then_reserve_new_attempt_keeps_window_open(tmp_path):
    """恢复重试：report 后直接 reserve 新 attempt、不追加 start，窗口仍应打开。

    旧实现逆序扫描撞上历史 report 即 break；新模型以最新事件为门禁，回溯 start。
    """
    project = tmp_path / "project"
    _write_ledger(project, [
        {"tid": "t001", "event": "start", "worktree": "../project_t001"},
        {"tid": "t001", "event": "attempt_reserved", "attempt": 1},
        {"tid": "t001", "event": "execution_claimed", "attempt": 1},
        {"tid": "t001", "event": "attempt_terminal", "attempt": 1, "status": "failed"},
        {"tid": "t001", "event": "report", "attempt": 1, "status": "failed"},
        {"tid": "t001", "event": "attempt_reserved", "attempt": 2},
    ])
    worktree_dir = tmp_path / "project_t001"
    worktree_dir.mkdir()
    expected = str(worktree_dir.resolve())

    assert statusline.resolve_worktree(str(project), "t001") == expected
    assert statusline.bind(str(project), "owner-a", "t001", 2, "exec-2") is True
    assert statusline.current_worktree(str(project), "owner-a") == expected


def test_latest_terminal_event_closes_window(tmp_path):
    """最新事件为终态时窗口关闭，即使更早存在 start。"""
    project = tmp_path / "project"
    _write_ledger(project, [
        {"tid": "t001", "event": "start", "worktree": "../project_t001"},
        {"tid": "t001", "event": "attempt_reserved", "attempt": 1},
        {"tid": "t001", "event": "report", "attempt": 1, "status": "done"},
    ])
    assert statusline.resolve_worktree(str(project), "t001") == ""
    assert statusline.bind(str(project), "owner-a", "t001") is False


def test_resolve_worktree_skips_non_object_ledger_lines(tmp_path):
    """账本含合法 JSON 非对象行（数字/数组）时跳过，不抛 AttributeError。"""
    project = tmp_path / "project"
    runtime = project / "docs" / "runtime"
    runtime.mkdir(parents=True)
    ledger = runtime / "dispatch_ledger.jsonl"
    ledger.write_text(
        "\n".join([
            "123",
            "[1, 2, 3]",
            json.dumps({"tid": "t001", "event": "start", "worktree": "../project_t001"}),
            "null",
            "456",
        ]) + "\n",
        encoding="utf-8",
    )
    assert statusline.resolve_worktree(str(project), "t001") == str(
        (tmp_path / "project_t001").resolve()
    )


def test_current_worktree_skips_non_object_statusline_lines(tmp_path):
    """状态行账本的非对象行不破坏会话解析。"""
    project = tmp_path / "project"
    worktree_dir = tmp_path / "project_t001"
    worktree_dir.mkdir()
    _write_ledger(project, [
        {"tid": "t001", "event": "start", "worktree": "../project_t001"},
    ])
    path = project / statusline.STATUSLINE_FILE
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join([
            "123",
            json.dumps({
                "session": "owner-a", "tid": "t001", "attempt": 1,
                "execution_id": "e", "worktree": str(worktree_dir.resolve()),
                "ts": "2026-01-01T00:00:00+00:00",
            }),
            "[1, 2]",
        ]) + "\n",
        encoding="utf-8",
    )
    assert statusline.current_worktree(str(project), "owner-a") == str(worktree_dir.resolve())


def test_bind_failure_emits_locatable_diagnostic(tmp_path, caplog):
    """bind 无打开窗口时输出含 project/session/tid 的诊断。"""
    project = tmp_path / "project"
    with caplog.at_level(logging.WARNING):
        assert statusline.bind(str(project), "sess-1", "t404") is False
    text = caplog.text
    assert "sess-1" in text
    assert "t404" in text
    assert str(project) in text


def test_current_worktree_rejects_deleted_worktree_dir(tmp_path, caplog):
    """worktree 目录被删后 current_worktree 返回空，不显示失效路径。"""
    project = tmp_path / "project"
    worktree_dir = tmp_path / "project_t001"
    worktree_dir.mkdir()
    _write_ledger(project, [
        {"tid": "t001", "event": "start", "worktree": "../project_t001"},
    ])
    assert statusline.bind(str(project), "owner-a", "t001") is True
    assert statusline.current_worktree(str(project), "owner-a") == str(worktree_dir.resolve())

    shutil.rmtree(worktree_dir)
    with caplog.at_level(logging.WARNING):
        assert statusline.current_worktree(str(project), "owner-a") == ""


def test_append_record_dedup_ignores_ts(tmp_path):
    """bind/unbind 记录带不同 ts 时，同身份记录仍被去重（旧实现按全序列化比较失效）。"""
    path = tmp_path / ".scratch" / "statusline_workdir.jsonl"
    first = {
        "session": "s", "tid": "t001", "attempt": 1,
        "execution_id": "e", "worktree": "w",
        "ts": "2026-01-01T00:00:00+00:00",
    }
    statusline.append_record(str(path), first)
    statusline.append_record(str(path), {**first, "ts": "2026-01-02T00:00:00+00:00"})
    assert path.read_text(encoding="utf-8").count("\n") == 1

    statusline.append_record(str(path), {**first, "attempt": 2, "ts": "2026-01-03T00:00:00+00:00"})
    assert path.read_text(encoding="utf-8").count("\n") == 2


def test_read_last_line_reads_only_tail(tmp_path):
    """大账本尾部只读即可拿到最后一行，无需全量 handle.read()。"""
    path = tmp_path / "large.jsonl"
    lines = [json.dumps({"i": i}) for i in range(5000)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert statusline._read_last_line(str(path)) == lines[-1]
