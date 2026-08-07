"""task 看板 view_server 纯函数测试。"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "repo_template"
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_task.context as ctx
from repo_task import view_server


def test_classify_maps_categories():
    tasks = {
        "t001": {"status": "active"},
        "t002": {"status": "done"},
        "t003": {"status": "dropped"},
        "t004": {"status": "backlog"},
        "t005": {"status": "backlog"},
        "t006": {"status": "backlog"},
        "t007": {"status": "backlog"},
    }
    schedule = {
        "selected": ["t004"],
        "waiting_deps": [("t001", "t005")],
        "blocked_conflicts": [("t006", "t001")],
    }
    classify = view_server._classify
    assert classify("t001", tasks, schedule) == "active"
    assert classify("t002", tasks, schedule) == "done"
    assert classify("t003", tasks, schedule) == "dropped"
    assert classify("t004", tasks, schedule) == "runnable"
    assert classify("t005", tasks, schedule) == "blocked_deps"
    assert classify("t006", tasks, schedule) == "blocked_conflict"
    assert classify("t007", tasks, schedule) == "backlog"


def test_render_html_injects_model_json(tmp_path, monkeypatch):
    # 隔离 view_static 读取：用真实模板，仅替换注入内容断言转义行为
    model = {
        "project": "demo",
        "nodes": [{"id": "t001", "title": "</script><b>x</b>", "category": "runnable"}],
        "edges": [],
        "summary": {"active": 0, "runnable": 1},
    }
    html = view_server._render_html(model)
    assert "window.__BOARD__ =" in html
    assert "</script><b>x</b>" not in html  # 防止注入闭合 script 标签
    assert "<\\/script>" in html
    assert "/static/board.css" in html
    assert "/static/board.js" in html


def test_render_html_uses_existing_template():
    template = Path(__file__).resolve().parents[2] / "scripts" / "repo_template" / "repo_task" / "view_static" / "board.html"
    assert template.is_file()
    text = template.read_text(encoding="utf-8")
    assert "__BOARD_JSON__" in text


@pytest.fixture()
def repo_layout(tmp_path, monkeypatch):
    tasks_dir = tmp_path / "docs" / "tasks"
    archive_dir = tmp_path / "docs" / "archive" / "tasks"
    tasks_dir.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    (tasks_dir / "t001_spec_doc").mkdir()
    (tasks_dir / "t001_spec_doc" / "spec.md").write_text("# spec 正文\n", encoding="utf-8")
    (tasks_dir / "t001_spec_doc" / "task.md").write_text("# task 正文\n", encoding="utf-8")
    (archive_dir / "t002_archived").mkdir()
    (archive_dir / "t002_archived" / "spec.md").write_text("# 归档 spec\n", encoding="utf-8")
    monkeypatch.setattr(ctx, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ctx, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(ctx, "ARCHIVE_TASKS_DIR", archive_dir)
    return tasks_dir, archive_dir


def test_resolve_task_doc_ok(repo_layout):
    tasks = {"t001": {"dir": "docs/tasks/t001_spec_doc"}}
    path = view_server._resolve_task_doc(tasks, "t001", "spec")
    assert path.read_text(encoding="utf-8") == "# spec 正文\n"
    assert view_server._resolve_task_doc(tasks, "t001", "task").name == "task.md"


def test_resolve_task_doc_archived_ok(repo_layout):
    tasks = {"t002": {"dir": "docs/archive/tasks/t002_archived"}}
    path = view_server._resolve_task_doc(tasks, "t002", "spec")
    assert path.read_text(encoding="utf-8") == "# 归档 spec\n"


def test_resolve_task_doc_rejects_unknown_tid(repo_layout):
    with pytest.raises(ctx.TaskDataError):
        view_server._resolve_task_doc({}, "t999", "spec")


def test_resolve_task_doc_rejects_bad_doc(repo_layout):
    tasks = {"t001": {"dir": "docs/tasks/t001_spec_doc"}}
    with pytest.raises(ctx.TaskDataError):
        view_server._resolve_task_doc(tasks, "t001", "readme")


def test_resolve_task_doc_rejects_missing_file(repo_layout):
    # 目录存在但文件缺失
    tasks_dir = repo_layout[0]
    (tasks_dir / "t003_missing").mkdir()
    tasks3 = {"t003": {"dir": "docs/tasks/t003_missing"}}
    with pytest.raises(ctx.TaskDataError):
        view_server._resolve_task_doc(tasks3, "t003", "spec")


def test_resolve_task_doc_rejects_path_traversal(repo_layout):
    tasks = {"t001": {"dir": "../../../etc"}}
    with pytest.raises(ctx.TaskDataError):
        view_server._resolve_task_doc(tasks, "t001", "spec")


def test_build_model_shape(monkeypatch):
    schedule = {
        "tasks": {
            "t001": {"tid": "t001", "title": "任务一", "status": "active", "depends_on": "", "conflicts_with": ""},
            "t002": {"tid": "t002", "title": "任务二", "status": "backlog", "depends_on": "t001", "conflicts_with": "t003", "schedule_status": "scheduled"},
            "t003": {"tid": "t003", "title": "任务三", "status": "backlog", "depends_on": "", "conflicts_with": "t002"},
            "t004": {"tid": "t004", "title": "任务四", "status": "done", "depends_on": "", "conflicts_with": ""},
        },
        "selected": ["t002"],
        "waiting_deps": [],
        "blocked_conflicts": [],
        "active_list": ["t001"],
        "main_done_set": {"t004"},
        "dropped_set": set(),
    }
    monkeypatch.setattr(view_server, "compute_schedule", lambda: schedule)
    model = view_server._build_model()
    assert model["project"] == ctx.REPO_ROOT.name
    assert model["summary"]["active"] == 1
    assert model["summary"]["runnable"] == 1
    assert model["summary"]["done"] == 1
    assert len(model["edges"]) == 2  # 1 dep + 1 conflict
    by_id = {n["id"]: n for n in model["nodes"]}
    assert by_id["t002"]["category"] == "runnable"
    assert by_id["t002"]["depends_on"] == ["t001"]
    # 前端建链需要 schedule_status 区分未排程/待澄清 backlog
    assert by_id["t002"]["schedule_status"] == "scheduled"
    assert by_id["t003"]["schedule_status"] == ""


def test_is_wsl_detects_env(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert view_server._is_wsl() is True


def test_is_wsl_detects_proc_version(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)

    class FakePath:
        def __init__(self, path):
            self._path = str(path)

        def read_text(self, encoding="utf-8", errors="ignore"):
            assert self._path == "/proc/version"
            return "Linux version 5.15.0-microsoft-standard-WSL2"

    monkeypatch.setattr(view_server, "Path", FakePath)
    assert view_server._is_wsl() is True


def test_open_browser_wsl_uses_windows_default(monkeypatch):
    calls = []

    def fake_windows(url):
        calls.append(url)
        return True

    monkeypatch.setattr(view_server, "_is_wsl", lambda: True)
    monkeypatch.setattr(view_server, "_is_windows", lambda: False)
    monkeypatch.setattr(view_server, "_open_browser_windows", fake_windows)
    opened = []
    monkeypatch.setattr(view_server.webbrowser, "open", lambda url: opened.append(url))
    view_server._open_browser("http://127.0.0.1:1234/")
    assert calls == ["http://127.0.0.1:1234/"]
    assert opened == []


def test_open_browser_windows_nt_uses_startfile(monkeypatch):
    started = []
    monkeypatch.setattr(view_server, "_is_wsl", lambda: False)
    monkeypatch.setattr(view_server, "_is_windows", lambda: True)
    monkeypatch.setattr(view_server.os, "name", "nt")
    monkeypatch.setattr(
        view_server.os, "startfile", lambda url: started.append(url), raising=False
    )
    win_calls = []
    monkeypatch.setattr(
        view_server, "_open_browser_windows", lambda url: win_calls.append(url) or True
    )
    opened = []
    monkeypatch.setattr(view_server.webbrowser, "open", lambda url: opened.append(url))
    view_server._open_browser("http://127.0.0.1:9/")
    assert started == ["http://127.0.0.1:9/"]
    assert win_calls == []
    assert opened == []


def test_open_browser_other_uses_webbrowser(monkeypatch):
    monkeypatch.setattr(view_server, "_is_wsl", lambda: False)
    monkeypatch.setattr(view_server, "_is_windows", lambda: False)
    opened = []
    monkeypatch.setattr(view_server.webbrowser, "open", lambda url: opened.append(url))
    view_server._open_browser("http://127.0.0.1:9/")
    assert opened == ["http://127.0.0.1:9/"]
