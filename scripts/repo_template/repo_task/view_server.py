"""task 调度看板本地服务。

只读；stdlib http.server；无 WebSocket、无后台轮询。每次请求重新计算
schedule，页面通过注入 JSON 交给客户端 JS 渲染；静态资源（board.css /
board.js）从同目录 view_static/ 读取，无构建、无 CDN 依赖。
"""

import contextlib
import json
import socket
import subprocess
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import repo_task.context as ctx

from .scheduling import compute_schedule

_STATIC_DIR = Path(__file__).resolve().parent / "view_static"
_DOC_NAMES = {"spec": "spec.md", "task": "task.md"}

CATEGORIES = (
    "active",
    "runnable",
    "blocked_deps",
    "blocked_conflict",
    "backlog",
    "done",
    "dropped",
)


def _find_free_port(host: str) -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _classify(tid, tasks, schedule):
    """返回节点分类：active / runnable / blocked_deps / blocked_conflict / backlog / done / dropped。"""
    task = tasks.get(tid)
    if not task:
        return "backlog"
    status = task["status"]
    if status == "active":
        return "active"
    if status in ctx.ARCHIVED_STATUSES:
        return "done" if status == "done" else "dropped"
    if tid in schedule["selected"]:
        return "runnable"
    if tid in [row[1] for row in schedule["waiting_deps"]]:
        return "blocked_deps"
    if tid in [row[0] for row in schedule["blocked_conflicts"]]:
        return "blocked_conflict"
    return "backlog"


def _build_model():
    """调度图 → 前端模型：节点、边、全类别统计。"""
    schedule = compute_schedule()
    tasks = schedule["tasks"]
    nodes = []
    for tid, task in tasks.items():
        nodes.append({
            "id": tid,
            "title": task["title"],
            "status": task["status"],
            "category": _classify(tid, tasks, schedule),
            "depends_on": [t for t in str(task.get("depends_on", "")).split(",") if t.strip()],
            "conflicts_with": [t for t in str(task.get("conflicts_with", "")).split(",") if t.strip()],
        })
    edges = []
    seen = set()
    for n in nodes:
        for dep in n["depends_on"]:
            key = ("dep", dep, n["id"])
            if key not in seen and any(m["id"] == dep for m in nodes):
                edges.append({"type": "dep", "from": dep, "to": n["id"]})
                seen.add(key)
        for c in n["conflicts_with"]:
            key = ("conflict", tuple(sorted([n["id"], c])))
            if key not in seen and any(m["id"] == c for m in nodes):
                edges.append({"type": "conflict", "from": n["id"], "to": c})
                seen.add(key)
    summary = {category: 0 for category in CATEGORIES}
    for n in nodes:
        summary[n["category"]] += 1
    return {
        "project": ctx.REPO_ROOT.name,
        "nodes": nodes,
        "edges": edges,
        "summary": summary,
    }


def _render_html(model: dict) -> str:
    """纯函数：读取模板，注入模型 JSON（转义 </script> 防闭合注入）。"""
    template = (_STATIC_DIR / "board.html").read_text(encoding="utf-8")
    payload = json.dumps(model, ensure_ascii=False).replace("</", "<\\/")
    return template.replace("__BOARD_JSON__", payload)


def _resolve_task_doc(tasks: dict[str, dict], tid: str, doc: str) -> Path:
    """校验并解析任务文档路径；非法请求抛 ctx.TaskDataError。"""
    if tid not in tasks:
        raise ctx.TaskDataError(f"未知任务 {tid!r}")
    filename = _DOC_NAMES.get(doc)
    if filename is None:
        raise ctx.TaskDataError(f"未知文档类型 {doc!r}（仅 spec/task）")
    directory = tasks[tid].get("dir", "")
    root = ctx.REPO_ROOT.resolve()
    path = (root / directory / filename).resolve()
    allowed = (ctx.TASKS_DIR.resolve(), ctx.ARCHIVE_TASKS_DIR.resolve())
    if not any(path.is_relative_to(base) for base in allowed):
        raise ctx.TaskDataError(f"任务 {tid} 文档路径越界：{path}")
    if not path.is_file():
        raise ctx.TaskDataError(f"任务 {tid} 无 {filename}")
    return path


def _open_browser_wsl(url):
    """从 WSL 打开 Windows 默认浏览器；非 WSL 用 webbrowser 兜底。"""
    try:
        # cmd.exe /c start 可唤醒默认浏览器；用空标题避免路径被当标题。
        subprocess.run(
            ["cmd.exe", "/c", "start", "", url],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        )
        return
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        webbrowser.open(url)
    except Exception:
        pass


class _Handler(BaseHTTPRequestHandler):
    def _send_bytes(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_text(self, status: int, message: str):
        self._send_bytes(
            status, message.encode("utf-8"), "text/plain; charset=utf-8"
        )

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        if path in ("/", "/index.html"):
            try:
                html = _render_html(_build_model())
            except Exception as e:
                self._send_error_text(500, f"渲染失败：{e}")
                return
            self._send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            if name not in ("board.css", "board.js"):
                self._send_error_text(404, "静态资源不存在")
                return
            try:
                body = (_STATIC_DIR / name).read_bytes()
            except OSError as e:
                self._send_error_text(500, f"读取静态资源失败：{e}")
                return
            content_type = (
                "text/css; charset=utf-8" if name.endswith(".css")
                else "text/javascript; charset=utf-8"
            )
            self._send_bytes(200, body, content_type)
            return
        if path == "/task-doc":
            query = parse_qs(parts.query)
            tid = (query.get("tid") or [""])[0]
            doc = (query.get("doc") or [""])[0]
            try:
                doc_path = _resolve_task_doc(
                    compute_schedule()["tasks"], tid, doc
                )
                body = doc_path.read_bytes()
            except ctx.TaskDataError as e:
                self._send_error_text(404, str(e))
                return
            except OSError as e:
                self._send_error_text(500, f"读取文档失败：{e}")
                return
            self._send_bytes(body, 200, "text/plain; charset=utf-8")
            return
        self._send_error_text(404, "未找到资源")

    def log_message(self, *args):
        pass


def serve(host="127.0.0.1", port=0):
    port = port or _find_free_port(host)
    url = f"http://{host}:{port}/"
    httpd = ThreadingHTTPServer((host, port), _Handler)
    print(f"task 看板已启动：{url}")
    print("只读服务；Ctrl+C 退出。")
    _open_browser_wsl(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n关闭。")
        httpd.shutdown()
