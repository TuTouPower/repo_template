"""task 调度看板本地服务。

只读；stdlib http.server；无 WebSocket、无后台轮询。每次请求重新计算 schedule。
"""

import contextlib
import json
import socket
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import repo_task.context as ctx

from .scheduling import compute_schedule


def _find_free_port(host: str) -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _classify(tid, tasks, schedule):
    """返回节点分类：active / runnable / blocked_deps / blocked_conflict / done / dropped。"""
    task = tasks.get(tid)
    if not task:
        return "unknown"
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
    schedule = compute_schedule()
    tasks = schedule["tasks"]
    conflicts = schedule["conflicts"]
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
    return {
        "project": ctx.REPO_ROOT.name,
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "active": len(schedule["active_list"]),
            "runnable": len(schedule["selected"]),
            "done": len(schedule["main_done_set"]),
            "dropped": len(schedule["dropped_set"]),
        },
    }


HTML_TEMPLATE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>{project} · task 看板</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "PingFang SC", sans-serif;
         margin: 0; padding: 16px; background: #fafafa; color: #222; }}
  header {{ display: flex; align-items: baseline; gap: 16px; margin-bottom: 12px; }}
  h1 {{ font-size: 18px; margin: 0; }}
  .summary {{ font-size: 13px; color: #666; }}
  .actions {{ margin-left: auto; }}
  button {{ cursor: pointer; padding: 4px 12px; font-size: 13px; }}
  #graph {{ background: #fff; border: 1px solid #e0e0e0; padding: 16px; min-height: 200px; }}
  .legend {{ margin-top: 12px; font-size: 12px; color: #666; }}
  .legend span {{ display: inline-block; margin-right: 12px; }}
  .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%;
          margin-right: 4px; vertical-align: middle; }}
</style></head>
<body>
<header>
  <h1>{project}</h1>
  <div class="summary">运行中 {active} · 可跑 {runnable} · 已完成 {done} · 已弃 {dropped}</div>
  <div class="actions"><button onclick="location.reload()">刷新</button></div>
</header>
<div id="graph"><pre class="mermaid">{mermaid}</pre></div>
<div class="legend">
  <span><span class="dot" style="background:#f59e0b"></span>运行中</span>
  <span><span class="dot" style="background:#10b981"></span>可跑</span>
  <span><span class="dot" style="background:#6b7280"></span>待运行</span>
  <span><span class="dot" style="background:#ef4444"></span>被依赖阻塞</span>
  <span><span class="dot" style="background:#8b5cf6"></span>被冲突阻塞</span>
  <span><span class="dot" style="background:#9ca3af"></span>已结束</span>
</div>
<script>
  mermaid.initialize({{ startOnLoad: true, flowchart: {{ htmlLabels: true }} }});
</script>
</body></html>
"""


def _escape_mermaid_label(text: str) -> str:
    # Mermaid 节点 label 在双引号内，用 &quot;/&#35;/&gt; 等实体避免破坏渲染。
    return (
        text.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace("\n", " ")
    )


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _mermaid_graph(model):
    color = {
        "active": "#f59e0b",
        "runnable": "#10b981",
        "backlog": "#6b7280",
        "blocked_deps": "#ef4444",
        "blocked_conflict": "#8b5cf6",
        "done": "#9ca3af",
        "dropped": "#d1d5db",
    }
    lines = ["flowchart LR"]
    for n in model["nodes"]:
        c = color.get(n["category"], "#6b7280")
        label_text = _escape_mermaid_label(f'{n["id"]} {n["title"]}')
        lines.append(f'  {n["id"]}["{label_text}"];')
        lines.append(f'  style {n["id"]} fill:{c},color:#fff,stroke:#333;')
    for e in model["edges"]:
        if e["type"] == "dep":
            lines.append(f'  {e["from"]} --> {e["to"]}')
        else:
            lines.append(f'  {e["from"]} -.-> {e["to"]}')
    return "\n".join(lines)


def _render_html():
    model = _build_model()
    return HTML_TEMPLATE.format(
        project=_escape_html(model["project"]),
        active=model["summary"]["active"],
        runnable=model["summary"]["runnable"],
        done=model["summary"]["done"],
        dropped=model["summary"]["dropped"],
        mermaid=_mermaid_graph(model),
    )


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
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            html = _render_html()
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"渲染失败：{e}".encode("utf-8"))
            return
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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
