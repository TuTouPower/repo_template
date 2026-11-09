"""task 看板 view_server 纯函数测试。"""
import logging
import sys
from pathlib import Path

import pytest
from types import SimpleNamespace

from git_env_support import git_run as _git
from git_env_support import init_repo

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import repo_task.context as ctx
from repo_task.context import RepoContext
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
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "t001_spec_doc").mkdir()
    (tasks_dir / "t001_spec_doc" / "task.md").write_text("# task 正文\n", encoding="utf-8")
    (tasks_dir / "t001_spec_doc" / "progress.md").write_text("# note 正文\n", encoding="utf-8")
    (tasks_dir / "t002_done").mkdir()
    (tasks_dir / "t002_done" / "task.md").write_text("# 终态 task\n", encoding="utf-8")
    return (tasks_dir, tasks_dir)


def test_resolve_task_doc_ok(repo_layout):
    tasks_dir = repo_layout[0]
    repo = RepoContext.for_root(tasks_dir.parent.parent)
    tasks = {"t001": {"dir": "docs/tasks/t001_spec_doc"}}
    path = view_server._resolve_task_doc(tasks, "t001", "task", repo=repo)
    assert path.read_text(encoding="utf-8") == "# task 正文\n"
    assert view_server._resolve_task_doc(tasks, "t001", "progress", repo=repo).name == "progress.md"


def test_resolve_task_doc_done_ok(repo_layout):
    tasks_dir = repo_layout[0]
    repo = RepoContext.for_root(tasks_dir.parent.parent)
    tasks = {"t002": {"dir": "docs/tasks/t002_done"}}
    path = view_server._resolve_task_doc(tasks, "t002", "task", repo=repo)
    assert path.read_text(encoding="utf-8") == "# 终态 task\n"


def test_resolve_task_doc_rejects_unknown_tid(repo_layout):
    repo = RepoContext.for_root(repo_layout[0].parent.parent)
    with pytest.raises(ctx.TaskDataError):
        view_server._resolve_task_doc({}, "t999", "task", repo=repo)


def test_resolve_task_doc_rejects_bad_doc(repo_layout):
    repo = RepoContext.for_root(repo_layout[0].parent.parent)
    tasks = {"t001": {"dir": "docs/tasks/t001_spec_doc"}}
    with pytest.raises(ctx.TaskDataError):
        view_server._resolve_task_doc(tasks, "t001", "readme", repo=repo)


def test_resolve_task_doc_rejects_missing_file(repo_layout):
    # 目录存在但文件缺失
    tasks_dir = repo_layout[0]
    (tasks_dir / "t003_missing").mkdir()
    repo = RepoContext.for_root(tasks_dir.parent.parent)
    tasks3 = {"t003": {"dir": "docs/tasks/t003_missing"}}
    with pytest.raises(ctx.TaskDataError):
        view_server._resolve_task_doc(tasks3, "t003", "task", repo=repo)


def test_resolve_task_doc_rejects_path_traversal(repo_layout):
    repo = RepoContext.for_root(repo_layout[0].parent.parent)
    tasks = {"t001": {"dir": "../../../etc"}}
    with pytest.raises(ctx.TaskDataError):
        view_server._resolve_task_doc(tasks, "t001", "task", repo=repo)


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
    body = view_server._read_task_doc(
        schedule, sources, "t001", "task", repo=RepoContext.for_root(main)
    )
    assert body.decode("utf-8") == "# worktree 最新\n"


def test_read_task_doc_from_branch_source(tmp_path, monkeypatch):
    """branch 来源（done 未合并）：用 git show 读分支文档，不落主仓 404。"""
    repo = init_repo(tmp_path / "repo")
    (repo / "base.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "checkout", "-b", "t001_x")
    (repo / "docs/tasks/t001_x").mkdir(parents=True)
    (repo / "docs/tasks/t001_x/progress.md").write_text("# 分支 note\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "task")
    _git(repo, "checkout", "main")

    repo_ctx = RepoContext.for_root(repo)
    schedule = {"tasks": {"t001": {"dir": "docs/tasks/t001_x"}}}
    sources = {"t001": {"source": "branch", "read_at": "t001_x"}}
    body = view_server._read_task_doc(schedule, sources, "t001", "progress", repo=repo_ctx)
    assert body.decode("utf-8") == "# 分支 note\n"
    with pytest.raises(ctx.TaskDataError):
        view_server._read_task_doc(schedule, sources, "t001", "task", repo=repo_ctx)


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


def test_handler_maps_unexpected_exception_to_generic_500(caplog):
    """意外异常详情只进服务端日志，HTTP 响应不回显内部文本（Issue #91 加固）。"""
    class FakeHandler:
        path = "/task-doc?tid=t001&doc=task"

        def _route_get(self):
            raise ValueError("boom")

        def _send_error_text(self, status, message):
            self.result = (status, message)

    handler = FakeHandler()
    with caplog.at_level(logging.ERROR):
        view_server._Handler.do_GET(handler)
    assert handler.result == (500, "内部错误，请查看服务端日志")
    assert "boom" not in handler.result[1]
    assert "boom" in caplog.text


def test_http_error_response_does_not_leak_internal_path(repo_layout, monkeypatch, caplog):
    """任务文档越界等 TaskDataError 详情只进日志，响应给通用文案，不回显绝对路径。"""
    tasks_dir, _ = repo_layout
    monkeypatch.setattr(view_server, "_SESSION_TOKEN", "test-session-token")
    tasks = {"t001": {"tid": "t001", "dir": "../../../etc"}}

    class MockHandler:
        def __init__(self, path):
            self.path = path
            self.server = SimpleNamespace(
                repo=RepoContext.for_root(tasks_dir.parent.parent),
                board=SimpleNamespace(snapshot=lambda: ("<html>", {"tasks": tasks}, {})),
            )

        def _route_get(self):
            view_server._Handler._route_get(self)

        def _send_error_text(self, status, message):
            self.result = (status, message)

        def _send_bytes(self, status, body, ct):
            self.result = (status, body)

    handler = MockHandler("/task-doc?tid=t001&doc=task&token=test-session-token")
    with caplog.at_level(logging.WARNING):
        view_server._Handler.do_GET(handler)
    assert handler.result == (404, "请求的任务资源不可用")
    assert "etc" not in handler.result[1]
    assert "etc" in caplog.text




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
    view_server.serve(repo=RepoContext.for_root(Path.cwd()), host="0.0.0.0", port=8765, allow_non_loopback=True)
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
        view_server.serve(repo=RepoContext.for_root(Path.cwd()), host="0.0.0.0", port=8766)


def test_build_model_has_no_schedule_status():
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
    model = view_server._build_model(schedule, "tok", repo=RepoContext.for_root(Path.cwd()))
    assert {node['id'] for node in model['nodes']} == {'t001', 't002'}
    assert all('schedule_status' not in node for node in model['nodes'])
    assert model['edges'] == [{'type': 'conflict', 'from': 't001', 'to': 't002'}]


def test_board_renders_server_provided_stop_reason():
    """前端直接使用服务端提供的 stop_reason，字段缺失时提示接口错误。"""
    static = Path(__file__).resolve().parents[1] / "scripts/repo_task/view_static"
    board = (static / "board.js").read_text(encoding="utf-8")
    assert "接口错误：链缺少 stop_reason 字段" in board
    assert "hasOwnProperty.call(chain, 'stop_reason')" in board
    assert "chain.stop_reason" in board


def test_compute_batch_plan_always_sets_stop_reason(tmp_path, monkeypatch):
    """A17：后端展示模型必须为每条链给出 stop_reason（字符串，可为空）。"""
    from repo_task import plan as plan_mod

    schedule = {
        "tasks": {
            "t001": {"tid": "t001", "title": "one", "status": "backlog", "depends_on": "", "conflicts_with": ""},
        },
        "selected": ["t001"],
        "waiting_deps": [], "blocked_conflicts": [],
        "conflicts": {"t001": set()},
        "main_done_set": set(), "effective_done_set": set(), "unmerged_done": [],
        "dropped_set": set(), "active_list": [], "active_set": set(),
        "backlog_tasks": {"t001": schedule_task("t001")},
        "ready": ["t001"],
        "dependencies": {"t001": []},
    }
    board = plan_mod.build_board_model(
        schedule, repo=RepoContext.for_root(Path.cwd())
    )
    batch = plan_mod.compute_batch_plan(board)
    assert batch["chains"]
    for chain in batch["chains"]:
        assert isinstance(chain.get("stop_reason"), str)


def schedule_task(tid):
    return {"tid": tid, "title": tid, "status": "backlog", "depends_on": "", "conflicts_with": ""}


def test_serve_rejects_out_of_range_port():
    """验证非法端口在启动前明确拒绝，避免原始 OverflowError 崩溃（Issue #63）。"""
    with pytest.raises(SystemExit, match="看板端口非法：-1；端口必须在 0 到 65535 之间"):
        view_server.serve(repo=RepoContext.for_root(Path.cwd()), port=-1)
    with pytest.raises(SystemExit, match="看板端口非法：70000；端口必须在 0 到 65535 之间"):
        view_server.serve(repo=RepoContext.for_root(Path.cwd()), port=70000)


def test_render_html_escapes_html_comments_and_separators():
    """验证标题包含 HTML 注释与特殊 Unicode 分隔符时被转义（Issue #60）。"""
    model = {
        "project": "demo",
        "nodes": [{"id": "t001", "title": "<!-- comment --> test\u2028split", "category": "runnable"}],
        "edges": [],
        "summary": {"active": 0, "runnable": 1},
    }
    html = view_server._render_html(model)
    assert "<!-- comment -->" not in html
    assert "<\\!-- comment -->" in html
    assert "\u2028" not in html
    assert "\\u2028" in html


def test_handler_sends_security_headers():
    """验证看板 HTTP 响应包含基础安全头（nosniff, X-Frame-Options, CSP）（Issue #60）。"""
    headers = []

    class DummyHandler:
        def send_response(self, status): pass
        def send_header(self, k, v): headers.append((k, v))
        def end_headers(self): pass
        wfile = type("DummyWFile", (), {"write": lambda self, b: None})()

    view_server._Handler._send_bytes(DummyHandler(), 200, b"ok", "text/html")
    hdr_dict = dict(headers)
    assert hdr_dict.get("X-Content-Type-Options") == "nosniff"
    assert hdr_dict.get("X-Frame-Options") == "DENY"
    assert "Content-Security-Policy" in hdr_dict


def test_board_cache_reuses_bundle_within_ttl(monkeypatch):
    """TTL 内多次 snapshot 只做一次有效状态发现，平抑高并发资源冲击（Issue #48）。"""
    bundle_calls = {"n": 0}

    def fake_bundle(*, repo):
        bundle_calls["n"] += 1
        return ({}, {})

    monkeypatch.setattr(view_server, "discover_effective_bundle", fake_bundle)
    monkeypatch.setattr(view_server, "compute_schedule", lambda tasks, **kwargs: {"tasks": tasks})
    monkeypatch.setattr(view_server, "_build_model", lambda schedule, token, **kwargs: {"nodes": []})
    monkeypatch.setattr(view_server, "_render_html", lambda model: "<html>board</html>")

    cache = view_server._BoardCache(RepoContext.for_root(Path.cwd()), "tok")
    for _ in range(5):
        html, _, _ = cache.snapshot()
        assert html == "<html>board</html>"
    assert bundle_calls["n"] == 1


def test_no_module_level_render_cache_state():
    """渲染缓存不再以模块级全局存在（避免同进程多 repo/token 串数据）。"""
    for name in ("_CACHED_HTML", "_LAST_RENDER_TIME", "_RENDER_CACHE_LOCK", "_get_rendered_html"):
        assert not hasattr(view_server, name)


def test_board_cache_key_is_repo_and_token(monkeypatch):
    """同一 cache 的 token 变化即失效，不返回旧 token 的渲染结果。"""
    calls = {"n": 0}

    def fake_bundle(*, repo):
        calls["n"] += 1
        return ({}, {})

    monkeypatch.setattr(view_server, "discover_effective_bundle", fake_bundle)
    monkeypatch.setattr(view_server, "compute_schedule", lambda tasks, **kwargs: {"tasks": tasks})
    monkeypatch.setattr(view_server, "_build_model", lambda schedule, token, **kwargs: {"token": token})
    monkeypatch.setattr(view_server, "_render_html", lambda model: model["token"])

    cache = view_server._BoardCache(RepoContext.for_root(Path.cwd()), "token-a")
    assert cache.snapshot()[0] == "token-a"
    cache._token = "token-b"
    assert cache.snapshot()[0] == "token-b"
    assert calls["n"] == 2


def test_task_doc_reuses_index_bundle(repo_layout, monkeypatch):
    """首页与 /task-doc 共享同一份 bundle，TTL 内不重复 discover_effective_bundle。"""
    tasks_dir, _ = repo_layout
    monkeypatch.setattr(view_server, "_SESSION_TOKEN", "test-session-token")
    repo = RepoContext.for_root(tasks_dir.parent.parent)
    tasks = {"t001": {"tid": "t001", "dir": "docs/tasks/t001_spec_doc"}}
    bundle_calls = {"n": 0}

    def fake_bundle(*, repo):
        bundle_calls["n"] += 1
        return (tasks, {})

    monkeypatch.setattr(view_server, "discover_effective_bundle", fake_bundle)
    monkeypatch.setattr(
        view_server, "compute_schedule",
        lambda tasks=None, **kwargs: {"tasks": tasks, "selected": ["t001"]},
    )
    monkeypatch.setattr(view_server, "_build_model", lambda schedule, token, **kwargs: {"nodes": []})
    monkeypatch.setattr(view_server, "_render_html", lambda model: "<html>board</html>")

    board = view_server._BoardCache(repo, "test-session-token")

    class MockHandler:
        def __init__(self, path):
            self.path = path
            self.server = SimpleNamespace(repo=repo, board=board)
            self.responses = []

        def _send_error_text(self, status, message):
            self.responses.append((status, message))

        def _send_bytes(self, status, body, ct):
            self.responses.append((status, body))

    index = MockHandler("/?token=test-session-token")
    doc = MockHandler("/task-doc?tid=t001&doc=task&token=test-session-token")
    view_server._Handler._route_get(index)
    view_server._Handler._route_get(doc)
    assert index.responses[-1][0] == 200
    assert doc.responses[-1][0] == 200
    assert b"# task \xe6\xad\xa3\xe6\x96\x87" in doc.responses[-1][1]
    assert bundle_calls["n"] == 1


def test_task_doc_endpoint_requires_valid_session_token(repo_layout, monkeypatch):
    """验证 /task-doc 接口校验 session token，未授权请求返回 403（Issue #44）。"""
    tasks_dir, _ = repo_layout
    monkeypatch.setattr(view_server, "_SESSION_TOKEN", "test-session-token")
    tasks = {"t001": {"tid": "t001", "dir": "docs/tasks/t001_spec_doc"}}
    schedule = {"tasks": tasks, "selected": ["t001"]}

    responses = []

    class MockHandler:
        def __init__(self, path):
            self.path = path
            self.server = SimpleNamespace(
                repo=RepoContext.for_root(tasks_dir.parent.parent),
                board=SimpleNamespace(
                    snapshot=lambda: ("<html>board</html>", schedule, {})
                ),
            )
        def _send_error_text(self, status, message):
            responses.append((status, message))
        def _send_bytes(self, status, body, ct):
            responses.append((status, body))

    # 1. 缺少 token 返回 403
    h1 = MockHandler("/task-doc?tid=t001&doc=task")
    view_server._Handler._route_get(h1)
    assert responses[-1][0] == 403
    assert "禁止访问" in responses[-1][1]

    # 2. 错误 token 返回 403
    h2 = MockHandler("/task-doc?tid=t001&doc=task&token=wrong_token")
    view_server._Handler._route_get(h2)
    assert responses[-1][0] == 403

    # 3. 正确 token 返回 200
    h3 = MockHandler("/task-doc?tid=t001&doc=task&token=test-session-token")
    view_server._Handler._route_get(h3)
    assert responses[-1][0] == 200
    assert b"# task \xe6\xad\xa3\xe6\x96\x87" in responses[-1][1]


def test_root_requires_session_token_and_does_not_leak_it(repo_layout, monkeypatch):
    """首页也必须校验 token，未持有者无法从 403 响应取得 token（Issue #73）。"""
    tasks_dir, _ = repo_layout
    monkeypatch.setattr(view_server, "_SESSION_TOKEN", "test-session-token")
    responses = []

    class MockHandler:
        def __init__(self, path):
            self.path = path
            self.server = SimpleNamespace(
                repo=RepoContext.for_root(tasks_dir.parent.parent),
                board=SimpleNamespace(snapshot=lambda: ("<html>board</html>", {}, {})),
            )
        def _send_error_text(self, status, message):
            responses.append((status, message))
        def _send_bytes(self, status, body, ct):
            responses.append((status, body))

    view_server._Handler._route_get(MockHandler("/"))
    assert responses[-1][0] == 403
    assert "test-session-token" not in responses[-1][1]

    view_server._Handler._route_get(MockHandler("/index.html?token=wrong"))
    assert responses[-1][0] == 403

    view_server._Handler._route_get(
        MockHandler("/?token=test-session-token")
    )
    assert responses[-1][0] == 200
    assert responses[-1][1] == b"<html>board</html>"


def test_find_free_port_and_serve_ipv6_loopback(monkeypatch):
    """验证 IPv6 loopback (::1) 能够正确分配空闲端口并识别 AF_INET6（Issue #38）。"""
    port = view_server._find_free_port("::1")
    assert isinstance(port, int)
    assert port > 0

    started = []
    class FakeServer:
        def __init__(self, addr, handler):
            started.append((addr, self.address_family if hasattr(self, "address_family") else None))
        def serve_forever(self):
            raise KeyboardInterrupt
        def shutdown(self):
            pass

    monkeypatch.setattr(view_server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(view_server, "_open_browser", lambda url: None)

    view_server.serve(repo=RepoContext.for_root(Path.cwd()), host="::1", port=port)
    assert len(started) == 1
    assert started[0][0] == ("::1", port)




