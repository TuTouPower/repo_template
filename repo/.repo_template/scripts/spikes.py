#!/usr/bin/env python3
"""spikes.py - spike 目录的条目分配、状态流转与列举入口。

一 spike 一目录：`docs/spikes/sNNN_{slug}/`，`report.md` 包含元数据 Front Matter；有实验代码
自行加 `code/`。终生固定在 `docs/spikes/`，不再移动到 `docs/archive/spikes/`。

用法：
  python3 .repo_template/scripts/spikes.py new <slug> [--title ...]
  python3 .repo_template/scripts/spikes.py list [--state {active,completed,superseded,unknown,all}]
  python3 .repo_template/scripts/spikes.py complete <sid> [--write]
  python3 .repo_template/scripts/spikes.py supersede <sid> --by <successor_sid> --reason "..." [--write]

list 对 front matter 损坏或缺失的 spike 打印 stderr 警告并归入 unknown，不静默吞错。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _id_scan import (
    IdScanError,
    _assert_no_symlink,
    allocate_dir,
    id_lock,
    parse_entry_dir_name,
)
from md_format import MdFormatError, format_file
from repo_task import context as ctx
from repo_task.documents import (
    parse_front_matter,
    parse_front_matter_text,
    write_front_matter,
)
from repo_task.finalization import validate_spike
from repo_task.spikes import (
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


def _dir_slug(spike_dir: Path) -> str:
    """从规范 spike 目录名取 slug；目录名非法直接拒绝，避免文件身份与目录分叉。"""
    parsed = parse_entry_dir_name(PREFIX, spike_dir.name)
    if parsed is None:
        raise IdScanError(f"spike 目录名不符合规范 sNNN_slug：{spike_dir.name}")
    return parsed[1]


def _require_canonical_sid(sid: str) -> int:
    """sid 须为规范编号（s001 而非 s0001）：正则 + 零填充双校验。"""
    if not SPIKE_SID_RE.match(sid):
        raise IdScanError(f"spike sid 格式应为 sNNN，收到 {sid!r}")
    try:
        number = int(sid[1:])
    except ValueError as error:
        raise IdScanError(f"spike sid 格式非法：{sid!r}") from error
    if sid != f"s{number:03d}":
        raise IdScanError(
            f"spike sid 须为规范编号（s{number:03d}，不允许 s0001 这类非规范 padding）：收到 {sid!r}"
        )
    return number


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
    if SPIKES_DIR.is_symlink():
        raise IdScanError(f"拒绝经软链目录列举 spike：{SPIKES_DIR}")
    if not SPIKES_DIR.is_dir():
        print("(no spikes)")
        return
    rows: list[tuple[str, str]] = []
    for path in sorted(SPIKES_DIR.iterdir()):
        if path.is_symlink() or not path.is_dir() or parse_entry_dir_name(PREFIX, path.name) is None:
            continue
        report = path / "report.md"
        status = "unknown"
        if report.is_file() and not report.is_symlink():
            try:
                fm, _ = parse_front_matter(report)
                validate_spike_metadata(
                    fm,
                    expected_sid=f"s{parse_entry_dir_name(PREFIX, path.name)[0]:03d}",
                    expected_slug=_dir_slug(path),
                )
                status = fm.get("status", "unknown")
            except Exception as error:
                status = "unknown"
                sys.stderr.write(
                    f"警告：{path.name} 元数据非法，按 unknown 处理：{error}\n"
                )
        else:
            if report.is_symlink():
                sys.stderr.write(f"警告：{path.name} report.md 为软链，按 unknown 处理\n")
            else:
                sys.stderr.write(f"警告：{path.name} 缺 report.md，按 unknown 处理\n")
        if args.state == "all" or status == args.state:
            rows.append((status, path.name))
    if not rows:
        print("(no spikes)")
        return
    for status, name in sorted(rows, key=lambda row: row[1]):
        print(f"{status:<10} {name}")


def cmd_complete(args: argparse.Namespace) -> None:
    sid = args.sid
    try:
        _require_canonical_sid(sid)
    except IdScanError as err:
        sys.exit(f"错误: {err}")
    try:
        spike_dir = find_spike_dir(REPO_ROOT, sid)
    except SpikeMetadataError as err:
        sys.exit(f"错误: {err}")
    _assert_no_symlink(REPO_ROOT, spike_dir)

    report = spike_dir / "report.md"
    if report.is_symlink() or not report.is_file():
        sys.exit(f"错误: 缺 {report.relative_to(REPO_ROOT).as_posix()}")
    _assert_no_symlink(REPO_ROOT, report)

    try:
        fm, body = parse_front_matter(report)
        validate_spike_metadata(fm, expected_sid=sid, expected_slug=_dir_slug(spike_dir))
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
        _assert_no_symlink(REPO_ROOT, spike_dir)
        _assert_no_symlink(REPO_ROOT, report)
        fm, body = parse_front_matter(report)
        validate_spike_metadata(fm, expected_sid=sid, expected_slug=_dir_slug(spike_dir))
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
        if report.is_symlink():
            sys.exit(f"错误: 拒绝向软链写入：{report}")
        _assert_no_symlink(REPO_ROOT, report)
        write_front_matter(report, fm, body)

    print(f"已完成 spike {sid}：{spike_dir.name}")


def check_supersede_cycle(root: Path, sid: str, successor_sid: str) -> None:
    """检测若将 sid 替代为 successor_sid 是否会形成循环引用；检测到环则退出阻断。"""
    visited = {sid}
    curr = successor_sid
    while curr:
        if curr in visited:
            sys.exit(f"错误: 严禁循环引用：检测到替代循环涉及 {curr}")
        visited.add(curr)
        try:
            curr_dir = find_spike_dir(root, curr)
        except SpikeMetadataError:
            break
        report = curr_dir / "report.md"
        if not report.is_file():
            break
        try:
            curr_fm, _ = parse_front_matter(report)
        except Exception:
            break
        if curr_fm.get("status") == "superseded":
            curr = curr_fm.get("superseded_by")
        else:
            break


def cmd_supersede(args: argparse.Namespace) -> None:
    sid = args.sid
    try:
        _require_canonical_sid(sid)
    except IdScanError as err:
        sys.exit(f"错误: {err}")
    successor_sid = args.by
    try:
        _require_canonical_sid(successor_sid)
    except IdScanError:
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
    _assert_no_symlink(REPO_ROOT, spike_dir)

    report = spike_dir / "report.md"
    if report.is_symlink() or not report.is_file():
        sys.exit(f"错误: 缺 {report.relative_to(REPO_ROOT).as_posix()}")
    _assert_no_symlink(REPO_ROOT, report)

    try:
        fm, body = parse_front_matter(report)
        validate_spike_metadata(fm, expected_sid=sid, expected_slug=_dir_slug(spike_dir))
    except Exception as err:
        sys.exit(f"错误: {err}")

    if fm.get("status") == "superseded":
        sys.exit(f"错误: spike {sid} 已处于 superseded 状态")

    try:
        succ_dir = find_spike_dir(REPO_ROOT, successor_sid)
    except SpikeMetadataError:
        sys.exit(f"错误: 替代目标 {successor_sid} 不存在")
    _assert_no_symlink(REPO_ROOT, succ_dir)

    succ_report = succ_dir / "report.md"
    if succ_report.is_symlink() or not succ_report.is_file():
        sys.exit(f"错误: 替代目标缺 report.md：{succ_dir.name}")
    _assert_no_symlink(REPO_ROOT, succ_report)

    try:
        succ_fm, _ = parse_front_matter(succ_report)
        validate_spike_metadata(
            succ_fm, expected_sid=successor_sid, expected_slug=_dir_slug(succ_dir)
        )
    except Exception as err:
        sys.exit(f"错误: 替代目标元数据非法：{err}")

    # 循环引用检查：若将 sid 替代为 successor_sid，检测是否存在反向或传递循环
    check_supersede_cycle(REPO_ROOT, sid, successor_sid)

    if succ_fm.get("status") != "completed":
        sys.exit(
            f"错误: 替代目标 {successor_sid} 状态必须为 completed（当前为 {succ_fm.get('status')}）"
        )

    try:
        validate_spike(REPO_ROOT, succ_dir)
    except (ValueError, ctx.TaskDataError) as err:
        sys.exit(f"错误: 替代目标 {successor_sid} 证据校验未通过：{err}")

    if not args.write:
        print(f"dry-run: spike {sid} 可替代为 {successor_sid}（加 --write 落盘）")
        return

    with id_lock(REPO_ROOT):
        _assert_no_symlink(REPO_ROOT, spike_dir)
        _assert_no_symlink(REPO_ROOT, report)
        _assert_no_symlink(REPO_ROOT, succ_dir)
        _assert_no_symlink(REPO_ROOT, succ_report)
        fm, body = parse_front_matter(report)
        validate_spike_metadata(fm, expected_sid=sid, expected_slug=_dir_slug(spike_dir))
        if fm.get("status") == "superseded":
            sys.exit(f"错误: spike {sid} 已处于 superseded 状态")
        check_supersede_cycle(REPO_ROOT, sid, successor_sid)
        succ_dir = find_spike_dir(REPO_ROOT, successor_sid)
        succ_fm, _ = parse_front_matter(succ_dir / "report.md")
        validate_spike_metadata(
            succ_fm, expected_sid=successor_sid, expected_slug=_dir_slug(succ_dir)
        )
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
        if report.is_symlink():
            sys.exit(f"错误: 拒绝向软链写入：{report}")
        _assert_no_symlink(REPO_ROOT, report)
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
        choices=("active", "completed", "superseded", "unknown", "all"),
        default="active",
        help="按状态过滤（默认 active；损坏/缺 report.md 的条目归 unknown）",
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
