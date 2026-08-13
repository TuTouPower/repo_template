"""task 调度看板本地服务。

只读；stdlib http.server；无 WebSocket、无后台轮询。每次请求重新计算
schedule，页面通过注入 JSON 交给客户端 JS 渲染；静态资源（board.css /
board.js）从同目录 view_static/ 读取，无构建、无 CDN 依赖。
"""

import contextlib
import json
import os
import platform
import socket
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import repo_task.context as ctx

from .plan import (
    CATEGORIES,
    build_board_model,
    classify_node,
    compute_batch_plan,
)
from .scheduling import compute_schedule

_STATIC_DIR = Path(__file__).resolve().parent / "view_static"
_DOC_NAMES = {"spec": "spec.md", "task": "task.md"}

# 兼容旧测试与外部引用
_classify = classify_node


def _find_free_port(host: str) -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _build_model():
    """调度图 → 前端模型：节点、边、统计 + 后端权威 plan。

    使用本模块 ``compute_schedule`` 引用，便于测试 monkeypatch。
    """
    model = build_board_model(compute_schedule())
    model["plan"] = compute_batch_plan(model)
    return model


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


def _is_wsl() -> bool:
    """检测是否在 WSL 内运行（与 open-in-software skill 同口径）。"""
    if os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        version = Path("/proc/version").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    lower = version.lower()
    return "microsoft" in lower or "wsl" in lower


def _is_windows() -> bool:
    """原生 Windows 或 Git Bash / MSYS / Cygwin。"""
    if os.name == "nt":
        return True
    system = platform.system().upper()
    return system.startswith(("MINGW", "MSYS", "CYGWIN"))


def _open_browser_windows(url: str) -> bool:
    """用 Windows 默认浏览器打开 URL。成功返回 True。"""
    # PowerShell Start-Process：WSL / Git Bash 均可用；经环境变量传 URL 避免引号问题。
    for pwsh in (
        "pwsh.exe",
        "powershell.exe",
        "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    ):
        try:
            env = os.environ.copy()
            env["TASK_VIEW_OPEN_URL"] = url
            if _is_wsl():
                existing = env.get("WSLENV", "")
                env["WSLENV"] = (existing + ":" if existing else "") + "TASK_VIEW_OPEN_URL"
            result = subprocess.run(
                [
                    pwsh,
                    "-NoProfile",
                    "-Command",
                    "Start-Process -FilePath $env:TASK_VIEW_OPEN_URL",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                env=env,
            )
            if result.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    # 兜底：cmd start；空标题避免 URL 被当成窗口标题。
    try:
        result = subprocess.run(
            ["cmd.exe", "/c", "start", "", url],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _open_browser(url: str) -> None:
    """打开默认浏览器：WSL → Windows 浏览器；Windows 直接开；其它 webbrowser。"""
    if _is_wsl():
        if _open_browser_windows(url):
            return
    elif _is_windows():
        if os.name == "nt":
            try:
                os.startfile(url)  # type: ignore[attr-defined]
                return
            except OSError:
                pass
        if _open_browser_windows(url):
            return
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
            if name not in ("board.css", "board.js", "chain_plan.js"):
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
            self._send_bytes(200, body, "text/plain; charset=utf-8")
            return
        self._send_error_text(404, "未找到资源")

    def log_message(self, *args):
        pass


def _is_loopback_host(host: str) -> bool:
    """本机回环地址才视为默认安全绑定。"""
    normalized = (host or "").strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def serve(host="127.0.0.1", port=0, *, allow_non_loopback=False):
    port = port or _find_free_port(host)
    # ThreadingHTTPServer 对 IPv6 字面量需要方括号才能当 URL host
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    url = f"http://{url_host}:{port}/"
    if not _is_loopback_host(host):
        if not allow_non_loopback:
            sys.exit(
                f"看板拒绝绑定非 loopback 主机 {host!r}"
                "（只读但暴露 task spec/task 正文）；确认网络边界后加"
                " --allow-non-loopback，或改用默认 127.0.0.1"
            )
        print(
            f"WARNING: 看板绑定 {host!r}（非 loopback）；"
            "只读但会暴露 task spec/task 正文，确认网络边界后再用",
            file=sys.stderr,
        )
    try:
        httpd = ThreadingHTTPServer((host, port), _Handler)
    except OSError as error:
        sys.exit(f"看板端口 {port} 绑定失败（{error}）；改用 --port 0 自动选空闲端口")
    print(f"task 看板已启动：{url}")
    print("只读服务；Ctrl+C 退出。")
    _open_browser(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n关闭。")
        httpd.shutdown()
