"""模板工具链统一启动层：版本契约、路径注入、流配置与入口返回码。"""

import sys
from collections.abc import Callable
from pathlib import Path

PYTHON_VERSION = "3.14.7"
MIN_PYTHON = (3, 14, 7)

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def require_python() -> None:
    """低于契约版本时给出明确提示并退出，避免解析期错误。"""
    if sys.version_info[:3] >= MIN_PYTHON:
        return
    current = ".".join(str(part) for part in sys.version_info[:3])
    sys.stderr.write(
        f"模板工具链需要 Python {PYTHON_VERSION} 或更高版本；当前为 {current}。"
        f"请升级 PATH 中的 python3 后重试。\n"
    )
    raise SystemExit(2)


def _configure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def run(main: Callable[[], object]) -> None:
    _configure_streams()
    require_python()
    raise SystemExit(main())
