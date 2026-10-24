"""task 看板 view_server 纯函数测试。"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_task.context as ctx
from repo_task import view_server

pytestmark = pytest.mark.contract


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
    classify = view_server.classify_node
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
    template = Path(__file__).resolve().parents[1] / "scripts" / "repo_task" / "view_static" / "board.html"
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
    (tasks_dir / "t001_spec_doc" / "task.md").write_text("# task 正文\n", encoding="utf-8")
    (tasks_dir / "t001_spec_doc" / "note.md").write_text("# note 正文\n", encoding="utf-8")
    (archive_dir / "t002_archived").mkdir()
    (archive_dir / "t002_archived" / "task.md").write_text("# 归档 task\n", encoding="utf-8")
    monkeypatch.setattr(ctx, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ctx, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(ctx, "ARCHIVE_TASKS_DIR", archive_dir)
    return tasks_dir, archive_dir


def test_resolve_task_doc_ok(repo_layout):
    tasks = {"t001": {"dir": "docs/tasks/t001_spec_doc"}}
    path = view_server._resolve_task_doc(tasks, "t001", "task")
    assert path.read_text(encoding="utf-8") == "# task 正文\n"
    assert view_server._resolve_task_doc(tasks, "t001", "note").name == "note.md"


def test_resolve_task_doc_archived_ok(repo_layout):
    tasks = {"t002": {"dir": "docs/archive/tasks/t002_archived"}}
    path = view_server._resolve_task_doc(tasks, "t002", "task")
    assert path.read_text(encoding="utf-8") == "# 归档 task\n"


def test_resolve_task_doc_rejects_unknown_tid(repo_layout):
    with pytest.raises(ctx.TaskDataError):
        view_server._resolve_task_doc({}, "t999", "task")


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
        view_server._resolve_task_doc(tasks3, "t003", "task")


def test_resolve_task_doc_rejects_path_traversal(repo_layout):
    tasks = {"t001": {"dir": "../../../etc"}}
    with pytest.raises(ctx.TaskDataError):
        view_server._resolve_task_doc(tasks, "t001", "task")


def test_read_task_doc_from_worktree_source(tmp_path):
    """worktree 来源任务：读该 worktree 的文件，而非主仓过期副本。"""
    main = tmp_path / "main"
    (main / "docs/tasks/t001_x").mkdir(parents=True)
    (main / "docs/tasks/t001_x/task.md").write_text("# 主仓旧版\n", encoding="utf-8")
    worktree = tmp_path / "main_t001"
    (worktree / "docs/tasks/t001_x").mkdir(parents=True)
    (worktree / "docs/tasks/t001_x/task.md").write_text("# worktree 最新\n", encoding="utf-8")

    schedule = {"tasks": {"t001": {"dir": "docs/tasks/t001_x"}}}
    sources = {"t001": {"source": "worktree", "read_at": str(worktree)}}
    body = view_server._read_task_doc(schedule, sources, "t001", "task")
    assert body.decode("utf-8") == "# worktree 最新\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    )


def test_read_task_doc_from_branch_source(tmp_path, monkeypatch):
    """branch 来源（done 未合并）：用 git show 读分支归档文档，不落主仓 404。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@e.c")
    _git(repo, "config", "user.name", "t")
    (repo / "base.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "checkout", "-b", "t001_x")
    (repo / "docs/archive/tasks/t001_x").mkdir(parents=True)
    (repo / "docs/archive/tasks/t001_x/note.md").write_text("# 分支归档 note\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "task")
    _git(repo, "checkout", "main")

    monkeypatch.setattr(ctx, "REPO_ROOT", repo)
    schedule = {"tasks": {"t001": {"dir": "docs/archive/tasks/t001_x"}}}
    sources = {"t001": {"source": "branch", "read_at": "t001_x"}}
    body = view_server._read_task_doc(schedule, sources, "t001", "note")
    assert body.decode("utf-8") == "# 分支归档 note\n"
    with pytest.raises(ctx.TaskDataError):
        view_server._read_task_doc(schedule, sources, "t001", "task")


def test_handler_maps_system_exit_to_error_response():
    """请求线程 SystemExit（worktree 运行）不再空响应：503 + 明确文本。"""

    class FakeHandler:
        path = "/"

        def _route_get(self):
            raise SystemExit("此命令只能在主工作区执行；请 cd 回主仓")

        def _send_error_text(self, status, message):
            self.result = (status, message)

    handler = FakeHandler()
    view_server._Handler.do_GET(handler)
    assert handler.result == (503, "此命令只能在主工作区执行；请 cd 回主仓")


def test_handler_maps_unexpected_exception_to_500():
    class FakeHandler:
        path = "/task-doc?tid=t001&doc=task"

        def _route_get(self):
            raise ValueError("boom")

        def _send_error_text(self, status, message):
            self.result = (status, message)

    handler = FakeHandler()
    view_server._Handler.do_GET(handler)
    assert handler.result == (500, "内部错误：boom")




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


def test_is_loopback_host():
    assert view_server._is_loopback_host("127.0.0.1")
    assert view_server._is_loopback_host("localhost")
    assert view_server._is_loopback_host("::1")
    assert not view_server._is_loopback_host("0.0.0.0")
    assert not view_server._is_loopback_host("192.168.1.1")


def test_serve_warns_non_loopback(monkeypatch, capsys):
    class FakeServer:
        def __init__(self, addr, handler):
            self.addr = addr

        def serve_forever(self):
            raise KeyboardInterrupt

        def shutdown(self):
            pass

    monkeypatch.setattr(view_server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(view_server, "_find_free_port", lambda host: 8765)
    monkeypatch.setattr(view_server, "_open_browser", lambda url: None)
    # RT-013 后非 loopback 须显式 allow_non_loopback 才绑定；绑定后仍打 WARNING
    view_server.serve(host="0.0.0.0", port=8765, allow_non_loopback=True)
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "0.0.0.0" in err


def test_serve_rejects_non_loopback_without_allow(monkeypatch, capsys):
    """非 loopback 绑定需显式 --allow-non-loopback，否则拒绝（RT-013）。"""
    class FakeServer:
        def __init__(self, addr, handler):
            self.addr = addr

        def serve_forever(self):
            raise KeyboardInterrupt

        def shutdown(self):
            pass

    monkeypatch.setattr(view_server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(view_server, "_find_free_port", lambda host: 8766)
    monkeypatch.setattr(view_server, "_open_browser", lambda url: None)
    with pytest.raises(SystemExit, match="拒绝绑定非 loopback"):
        view_server.serve(host="0.0.0.0", port=8766)


def test_build_model_has_no_schedule_status(monkeypatch):
    schedule = {
        'tasks': {
            't001': {'tid': 't001', 'title': 'one', 'status': 'backlog', 'depends_on': '', 'conflicts_with': 't002'},
            't002': {'tid': 't002', 'title': 'two', 'status': 'backlog', 'depends_on': '', 'conflicts_with': ''},
        },
        'selected': ['t001', 't002'],
        'waiting_deps': [], 'blocked_conflicts': [],
        'conflicts': {'t001': {'t002'}, 't002': {'t001'}},
        'main_done_set': set(), 'effective_done_set': set(), 'unmerged_done': [],
        'dropped_set': set(), 'active_list': [], 'active_set': set(),
        'backlog_tasks': {}, 'ready': ['t001', 't002'],
    }
    monkeypatch.setattr(view_server, 'compute_schedule', lambda: schedule)
    model = view_server._build_model()
    assert {node['id'] for node in model['nodes']} == {'t001', 't002'}
    assert all('schedule_status' not in node for node in model['nodes'])
    assert model['edges'] == [{'type': 'conflict', 'from': 't001', 'to': 't002'}]
