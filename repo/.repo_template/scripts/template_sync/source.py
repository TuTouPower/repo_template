"""模板来源解析：来源身份、固定 checkout、URL 缓存、产物根识别与来源干净度。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import context as ctx
from .common import _git


def source_identity(value: str) -> dict:
    if not value or not isinstance(value, str):
        raise ctx.SyncError("模板源参数不能为空")
    stripped = value.strip()
    if stripped.startswith("-"):
        raise ctx.SyncError(f"模板源参数非法：{value!r} 不能为以 '-' 开头的 option 格式参数")
    path = Path(stripped).expanduser()
    if path.exists():
        if not path.is_dir():
            raise ctx.SyncError(f"模板源本地路径非目录：{value}")
        try:
            _as_product(path)
        except ctx.SyncError as err:
            raise ctx.SyncError(f"本地路径 {value} 不是有效模板源：{err}") from err
        return {"kind": "path", "value": str(path.resolve())}
    if re.match(r"^(?:https?://|ssh://|git://|file://|[^/\s]+@[^:]+:)", stripped):
        return {"kind": "url", "value": stripped}
    raise ctx.SyncError(f"模板源路径不存在：{value}；不把拼错路径当 URL")


def require_template_source(state: dict | None) -> tuple[str, str]:
    """校验 state 内嵌 template_source 为 {kind, value} 对象，非法即 SyncError。

    顶层 JSON 已由 _load_json_object 校验为对象，但嵌套字段仍可能是手改后的
    字符串/数组；直接 .get 会抛 AttributeError 逃逸事务回滚。此处统一校验，
    调用方不得再对 template_source 做裸 .get。
    """
    if not isinstance(state, dict):
        raise ctx.SyncError("未初始化：缺少 template_source。先运行 init --source <path|url>")
    ts = state.get("template_source")
    if not isinstance(ts, dict):
        raise ctx.SyncError(
            "状态文件 template_source 结构错误（须为对象）："
            "请修复或重新运行 init --source <path|url>"
        )
    kind = ts.get("kind", "path")
    value = ts.get("value")
    if not isinstance(kind, str) or not isinstance(value, str) or not value.strip():
        raise ctx.SyncError(
            "状态文件 template_source 缺 kind/value 字符串："
            "请修复或重新运行 init --source <path|url>"
        )
    return kind, value


def resolve_src(state: dict | None, *, refresh: bool = True, sync: ctx.SyncContext) -> Path:
    """解析模板源。URL 源 refresh=False 时只读缓存，不 clone/pull（Issue #72）。"""
    kind, value = require_template_source(state)
    if sync.checkout_override is not None:
        src = _as_product(sync.checkout_override)
        if kind == "url":
            origin = _git(src, "remote", "get-url", "origin")
            if origin.returncode or origin.stdout.strip() != value:
                raise ctx.SyncError("固定 checkout 的 origin 与来源身份不符")
        return src
    if kind == "url":
        cache = sync.consumer / ".scratch/repo_template_sync_src"
        if not (cache / ".git").exists():
            # 首次没有缓存只能 clone；有缓存后 refresh=False 完全离线。
            cache.parent.mkdir(parents=True, exist_ok=True)
            r = _git(cache.parent, "clone", value, cache.name)
            if r.returncode != 0:
                raise ctx.SyncError(f"clone 模板源失败：{r.stderr.strip()}")
        else:
            origin = _git(cache, "remote", "get-url", "origin")
            if origin.returncode or origin.stdout.strip() != value:
                raise ctx.SyncError("来源缓存 origin 与 template_source 不符；保留缓存，先核实来源并使用独立缓存或本地 checkout")
            if refresh:
                r = _git(cache, "pull", "--ff-only")
                if r.returncode != 0:
                    raise ctx.SyncError(f"更新模板源失败：{r.stderr.strip()}")
        src = cache.resolve()
    else:
        src = Path(value).expanduser().resolve()
    return _as_product(src)


def _as_product(src: Path) -> Path:
    """接受产物根，或工厂仓根（内含 repo/）。"""
    if (src / ctx.PRODUCT_MARKER).is_file():
        return src
    nested = src / "repo"
    if (nested / ctx.PRODUCT_MARKER).is_file():
        return nested
    raise ctx.SyncError(f"模板源无效：{src} 不含 {ctx.PRODUCT_MARKER}（也不是含 repo/ 的工厂根）")


def assert_not_self(src: Path, *, sync: ctx.SyncContext) -> None:
    if src == sync.consumer:
        raise ctx.SyncError("当前仓库与 template_source 同一路径，禁止在模板仓当推送源同步")


def src_dirty(src: Path) -> bool:
    paths = list(ctx.HARD_SYNC_DIRS) + list(ctx.HARD_SYNC_FILES) + list(ctx.SHARED_FILES)
    r = _git(src, "status", "--porcelain", "--", *paths)
    if r.returncode != 0:
        # git 失败时按 dirty 处理并打 WARNING，避免误推进 last_synced_commit（F36）
        print(
            f"WARNING: 无法判断模板源是否干净（{r.stderr.strip()}）；按 dirty 处理",
            file=sys.stderr,
        )
        return True
    return bool(r.stdout.strip())
