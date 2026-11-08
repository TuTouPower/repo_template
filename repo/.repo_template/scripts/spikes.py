#!/usr/bin/env python3
"""spikes.py - spike 目录的条目分配、状态流转与列举入口。

一 spike 一目录：`docs/spikes/sNNN_{slug}/`，`report.md` 包含元数据 Front Matter；有实验代码
自行加 `code/`。终生固定在 `docs/spikes/`，不再移动到 `docs/archive/spikes/`。

用法：
  python3 .repo_template/scripts/spikes.py new <slug> [--title ...]
  python3 .repo_template/scripts/spikes.py list [--state {active,completed,superseded,all}]
  python3 .repo_template/scripts/spikes.py complete <sid> [--write]
  python3 .repo_template/scripts/spikes.py supersede <sid> --by <successor_sid> --reason "..." [--write]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _id_scan import IdScanError, allocate_dir, id_lock
from md_format import MdFormatError, format_file
from repo_task import context as ctx
from repo_task.documents import (
    parse_front_matter,
    parse_front_matter_text,
    write_front_matter,
)
from repo_task.finalization import validate_spike
from repo_task.spikes import (
    ENTRY_DIR_RE,
    SLUG_RE,
    SPIKE_SID_RE,
    SpikeMetadataError,
    find_spike_dir,
    validate_spike_metadata,
)

TOOLKIT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = TOOLKIT_ROOT.parent
PREFIX = "s"
SPIKES_DIR = REPO_ROOT / "docs/spikes"
SCAN_DIRS = ("docs/spikes",)
TEMPLATE_PATH = TOOLKIT_ROOT / "docs/spike_report_template.md"


def cmd_new(args: argparse.Namespace) -> None:
    slug = args.slug or args.slug_opt
    if not slug:
        sys.exit("错误: 缺少 slug 参数")
    if not SLUG_RE.match(slug):
        sys.exit(f"错误: slug 须匹配 {SLUG_RE.pattern}（收到 {slug!r}）")
    if not TEMPLATE_PATH.is_file():
        raise IdScanError(f"缺模板 {TEMPLATE_PATH.relative_to(REPO_ROOT).as_posix()}")

    template_text = TEMPLATE_PATH.read_text(encoding="utf-8")
    if not template_text.startswith("---"):
        raise IdScanError(
            f"模板缺少标准 Front Matter (---)：{TEMPLATE_PATH.relative_to(REPO_ROOT).as_posix()}"
        )
    content = template_text.replace("{slug}", slug)

    if args.title:
        title = args.title.strip()
        if "# Spike report" in content:
            content = content.replace("# Spike report", f"# Spike report: {title}")
        elif "{title}" in content:
            content = content.replace("{title}", title)

    try:
        path = allocate_dir(
            REPO_ROOT,
            prefix=PREFIX,
            dirs=SCAN_DIRS,
            target_dir=SPIKES_DIR,
            slug=slug,
            files={"report.md": content},
            formatter=format_file,
        )
    except (IdScanError, MdFormatError) as error:
        sys.exit(f"错误: {error}")
    print(path.relative_to(REPO_ROOT).as_posix())


def cmd_list(args: argparse.Namespace) -> None:
    if not SPIKES_DIR.is_dir():
        print("(no spikes)")
        return
    rows: list[tuple[str, str]] = []
    for path in sorted(SPIKES_DIR.iterdir()):
        if not path.is_dir() or not ENTRY_DIR_RE.match(path.name):
            continue
        report = path / "report.md"
        status = "unknown"
        if report.is_file():
            try:
                fm, _ = parse_front_matter(report)
                status = fm.get("status", "unknown")
            except Exception:
                status = "unknown"
        if args.state == "all" or status == args.state:
            rows.append((status, path.name))
    if not rows:
        print("(no spikes)")
        return
    for status, name in sorted(rows, key=lambda row: row[1]):
        print(f"{status:<10} {name}")


def cmd_complete(args: argparse.Namespace) -> None:
    sid = args.sid
    if not SPIKE_SID_RE.match(sid):
        sys.exit(f"错误: spike sid 格式应为 sNNN，收到 {sid!r}")
    try:
        spike_dir = find_spike_dir(REPO_ROOT, sid)
    except SpikeMetadataError as err:
        sys.exit(f"错误: {err}")

    report = spike_dir / "report.md"
    if not report.is_file():
        sys.exit(f"错误: 缺 {report.relative_to(REPO_ROOT).as_posix()}")

    try:
        fm, body = parse_front_matter(report)
        validate_spike_metadata(fm, expected_sid=sid)
    except Exception as err:
        sys.exit(f"错误: {err}")

    current_status = fm.get("status")
    if current_status == "completed":
        sys.exit(f"错误: spike {sid} 已处于 completed 状态")
    if current_status == "superseded":
        sys.exit(f"错误: spike {sid} 已处于 superseded 状态，无法 complete")

    try:
        validate_spike(REPO_ROOT, spike_dir)
    except (ValueError, ctx.TaskDataError) as err:
        sys.exit(f"错误: spike {sid} 闭环校验未通过：{err}")

    if not args.write:
        print(f"dry-run: spike {sid} 闭环校验通过，可标记为 completed（加 --write 落盘）")
        return

    with id_lock(REPO_ROOT):
        fm, body = parse_front_matter(report)
        validate_spike_metadata(fm, expected_sid=sid)
        if fm.get("status") == "completed":
            sys.exit(f"错误: spike {sid} 已处于 completed 状态")
        if fm.get("status") == "superseded":
            sys.exit(f"错误: spike {sid} 已处于 superseded 状态，无法 complete")
        try:
            validate_spike(REPO_ROOT, spike_dir)
        except (ValueError, ctx.TaskDataError) as err:
            sys.exit(f"错误: spike {sid} 闭环校验未通过：{err}")
        fm["status"] = "completed"
        fm.pop("superseded_by", None)
        fm.pop("reason", None)
        write_front_matter(report, fm, body)

    print(f"已完成 spike {sid}：{spike_dir.name}")


def cmd_supersede(args: argparse.Namespace) -> None:
    sid = args.sid
    if not SPIKE_SID_RE.match(sid):
        sys.exit(f"错误: spike sid 格式应为 sNNN，收到 {sid!r}")
    successor_sid = args.by
    if not SPIKE_SID_RE.match(successor_sid):
        sys.exit(f"错误: --by 参数格式应为 sNNN，收到 {successor_sid!r}")

    if not args.reason or not args.reason.strip():
        sys.exit("错误: --reason 不能为空")
    if "\n" in args.reason or "\r" in args.reason:
        sys.exit("错误: --reason 严禁包含换行符")
    reason = args.reason.strip()

    if sid == successor_sid:
        sys.exit(f"错误: 严禁自引用：{sid} 不能替代自身")

    try:
        spike_dir = find_spike_dir(REPO_ROOT, sid)
    except SpikeMetadataError as err:
        sys.exit(f"错误: {err}")

    report = spike_dir / "report.md"
    if not report.is_file():
        sys.exit(f"错误: 缺 {report.relative_to(REPO_ROOT).as_posix()}")

    try:
        fm, body = parse_front_matter(report)
        validate_spike_metadata(fm, expected_sid=sid)
    except Exception as err:
        sys.exit(f"错误: {err}")

    if fm.get("status") == "superseded":
        sys.exit(f"错误: spike {sid} 已处于 superseded 状态")

    try:
        succ_dir = find_spike_dir(REPO_ROOT, successor_sid)
    except SpikeMetadataError:
        sys.exit(f"错误: 替代目标 {successor_sid} 不存在")

    succ_report = succ_dir / "report.md"
    if not succ_report.is_file():
        sys.exit(f"错误: 替代目标缺 report.md：{succ_dir.name}")

    try:
        succ_fm, _ = parse_front_matter(succ_report)
        validate_spike_metadata(succ_fm, expected_sid=successor_sid)
    except Exception as err:
        sys.exit(f"错误: 替代目标元数据非法：{err}")

    if succ_fm.get("status") != "completed":
        sys.exit(
            f"错误: 替代目标 {successor_sid} 状态必须为 completed（当前为 {succ_fm.get('status')}）"
        )

    try:
        validate_spike(REPO_ROOT, succ_dir)
    except (ValueError, ctx.TaskDataError) as err:
        sys.exit(f"错误: 替代目标 {successor_sid} 证据校验未通过：{err}")

    # 循环引用检查
    visited = {sid}
    curr = successor_sid
    while curr:
        if curr in visited:
            sys.exit(f"错误: 严禁循环引用：检测到替代循环涉及 {curr}")
        visited.add(curr)
        try:
            curr_dir = find_spike_dir(REPO_ROOT, curr)
            curr_fm, _ = parse_front_matter(curr_dir / "report.md")
            curr = curr_fm.get("superseded_by")
        except Exception:
            break

    if not args.write:
        print(f"dry-run: spike {sid} 可替代为 {successor_sid}（加 --write 落盘）")
        return

    with id_lock(REPO_ROOT):
        fm, body = parse_front_matter(report)
        validate_spike_metadata(fm, expected_sid=sid)
        if fm.get("status") == "superseded":
            sys.exit(f"错误: spike {sid} 已处于 superseded 状态")
        succ_dir = find_spike_dir(REPO_ROOT, successor_sid)
        succ_fm, _ = parse_front_matter(succ_dir / "report.md")
        if succ_fm.get("status") != "completed":
            sys.exit(
                f"错误: 替代目标 {successor_sid} 状态必须为 completed（当前为 {succ_fm.get('status')}）"
            )
        try:
            validate_spike(REPO_ROOT, succ_dir)
        except (ValueError, ctx.TaskDataError) as err:
            sys.exit(f"错误: 替代目标 {successor_sid} 证据校验未通过：{err}")
        fm["status"] = "superseded"
        fm["superseded_by"] = successor_sid
        fm["reason"] = reason
        write_front_matter(report, fm, body)

    print(f"已替代 spike {sid} -> {successor_sid}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="spike 条目分配与状态流转")
    sub = parser.add_subparsers(dest="command", required=True)

    new_parser = sub.add_parser("new", help="锁内取号并从模板建 spike 目录")
    new_parser.add_argument("slug", nargs="?", help="snake_case 主题标识")
    new_parser.add_argument("--slug", dest="slug_opt", help="snake_case 主题标识")
    new_parser.add_argument("--title", help="可选 Spike 标题")
    new_parser.set_defaults(func=cmd_new)

    list_parser = sub.add_parser("list", help="列举 spike")
    list_parser.add_argument(
        "--state",
        choices=("active", "completed", "superseded", "all"),
        default="active",
        help="按状态过滤（默认 active）",
    )
    list_parser.set_defaults(func=cmd_list)

    complete_parser = sub.add_parser("complete", help="闭环并标记 spike 为 completed")
    complete_parser.add_argument("sid", help="spike sid（如 s001）")
    complete_parser.add_argument("--write", action="store_true", help="落盘（默认 dry-run）")
    complete_parser.set_defaults(func=cmd_complete)

    supersede_parser = sub.add_parser("supersede", help="标记 spike 为 superseded 并指定有效后继")
    supersede_parser.add_argument("sid", help="被替代的 spike sid（如 s001）")
    supersede_parser.add_argument("--by", required=True, help="替代目标的 spike sid（如 s002）")
    supersede_parser.add_argument("--reason", required=True, help="替代原因")
    supersede_parser.add_argument("--write", action="store_true", help="落盘（默认 dry-run）")
    supersede_parser.set_defaults(func=cmd_supersede)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except IdScanError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
