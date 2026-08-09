#!/usr/bin/env python3
"""pending.py - 待办条目的分配、列举与状态迁移入口。

一条目一文件，三态由所在目录表达：

  docs/pending/todo/pNNN_slug.md      未闭环
  docs/pending/parked/pNNN_slug.md    用户确认暂搁（不办）
  docs/archive/pending/pNNN_slug.md   已闭环

用法：
  python3 scripts/repo_template/pending.py new --slug cli_exit_code [--kind bug]
  python3 scripts/repo_template/pending.py list [--state todo|parked|archived|all]
  python3 scripts/repo_template/pending.py archive p047 p051 --fix-ref t012 [--write]
  python3 scripts/repo_template/pending.py park p047 --reason "等外部依赖" [--write]
  python3 scripts/repo_template/pending.py revive p047 [--write]
  python3 scripts/repo_template/pending.py rename p047 --slug new_slug [--write]

`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建文件」，并发 worker 不会撞号。
`archive` 要求 --fix-ref TID 作为闭环标识；parked 条目须先 revive 才能闭环。
`rename` 改条目文件名（保留编号，仅换 slug）：已入库文件用 `git mv` 保留历史，
未入库（新建尚未 git add）文件退化为普通改名；dry-run 默认，加 `--write` 落盘。
rename 不自动改正文引用——slug 改动需人工核对 docs/ 与 src/ 内的 pNNN 引用。
迁移用 git mv 保留历史；仓库无该文件记录时退化为普通移动。
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _id_scan import IdScanError, allocate

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PREFIX = "p"
TODO_DIR = REPO_ROOT / "docs/pending/todo"
PARKED_DIR = REPO_ROOT / "docs/pending/parked"
ARCHIVE_DIR = REPO_ROOT / "docs/archive/pending"
SCAN_DIRS = ("docs/pending", "docs/archive/pending")
STATE_DIRS = {"todo": TODO_DIR, "parked": PARKED_DIR, "archived": ARCHIVE_DIR}
ID_RE = re.compile(r"^p([0-9]{3,})$")
ENTRY_FILE_RE = re.compile(r"^p([0-9]{3,})_[a-z0-9_]+\.md$")
HANDLE_RE = re.compile(r"^[ \t]*-[ \t]*处理[ \t]*[:：].*$")
PARKED_RE = re.compile(r"^[ \t]*-[ \t]*暂搁[ \t]*[:：].*$")

ENTRY_TEMPLATE = """# {id} {一句话简述}

- 来源：{tNNN 遗留 / 用户提出 / 技术债自查}
- 内容：{该做什么、为什么现在没做}
- 处理：未开
"""

BUG_TEMPLATE = """# {id} {一句话简述}

- 现象：{发生什么预期外行为}
- 影响：{受影响的功能和范围；含已确认同类位点并集}
- 根因：{可验证机制与分类；已确认同类位点清单或「已扫无同类」}
- 测试缺口：{现有测试为何漏过、应补哪层验证；须覆盖各已确认位点}
- 线索：{`.scratch/` 最小复现路径}
- 处理：未开
"""


def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def parse_ids(specs: list[str]) -> list[int]:
    """解析 pNNN 列表，返回升序去重后的整数列表。"""
    out: list[int] = []
    for spec in specs:
        match = ID_RE.match(spec)
        if not match:
            raise IdScanError(f"非法编号：{spec!r}（期望 pNNN）")
        out.append(int(match.group(1)))
    return sorted(set(out))


def find_entry(number: int) -> tuple[str, Path]:
    """在三个状态目录中定位条目，返回 (state, path)。"""
    hits = [
        (state, path)
        for state, directory in STATE_DIRS.items()
        if directory.is_dir()
        for path in sorted(directory.glob(f"p{number:03d}_*.md"))
    ]
    if not hits:
        raise IdScanError(f"未找到 p{number:03d}")
    if len(hits) > 1:
        joined = ", ".join(_rel(path) for _, path in hits)
        raise IdScanError(f"p{number:03d} 同时存在于多处：{joined}；请先解决同号多处")
    return hits[0]


def move_entry(path: Path, target_dir: Path) -> Path:
    """git mv 到目标目录；未入库文件退化为普通移动。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / path.name
    if destination.exists():
        raise IdScanError(f"{_rel(destination)} 已存在；拒绝覆盖")
    if _git(["ls-files", "--error-unmatch", _rel(path)]).returncode == 0:
        result = _git(["mv", _rel(path), _rel(destination)])
        if result.returncode != 0:
            raise IdScanError(f"git mv 失败：{result.stderr.strip()}")
    else:
        path.rename(destination)
    return destination


def set_field(path: Path, pattern: re.Pattern, line: str) -> None:
    """替换首个匹配行；无匹配则追加到文件末尾。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, existing in enumerate(lines):
        if pattern.match(existing):
            lines[index] = line
            break
    else:
        lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def cmd_new(args: argparse.Namespace) -> None:
    body = BUG_TEMPLATE if args.kind == "bug" else ENTRY_TEMPLATE
    path = allocate(
        REPO_ROOT,
        prefix=PREFIX,
        dirs=SCAN_DIRS,
        target_dir=TODO_DIR,
        slug=args.slug,
        body=body,
    )
    print(_rel(path))


def cmd_list(args: argparse.Namespace) -> None:
    states = STATE_DIRS if args.state == "all" else {args.state: STATE_DIRS[args.state]}
    rows: list[tuple[str, str, str]] = []
    for state, directory in states.items():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if not ENTRY_FILE_RE.match(path.name):
                continue
            # 摘要取 H1 标题（# pNNN 描述）——文件首行必有，字段名变体不影响检索
            title = ""
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            rows.append((state, path.stem, title))
    if not rows:
        print("(no entries)")
        return
    for state, stem, title in sorted(rows, key=lambda row: row[1]):
        print(f"{state:<8} {stem:<40} {title}")


def _apply(args: argparse.Namespace, actions: list[tuple[Path, Path, str]]) -> None:
    """dry-run 打印或落盘执行迁移。actions 为 (源, 目标目录, 说明)。"""
    if not args.write:
        sys.stderr.write("dry-run；以下为拟定改动，加 --write 落盘。\n")
        for path, target, note in actions:
            sys.stderr.write(f"  {_rel(path)} → {_rel(target)}/  {note}\n")
        return
    for path, target, note in actions:
        destination = move_entry(path, target)
        if note.startswith("处理"):
            set_field(destination, HANDLE_RE, f"- {note}")
        elif note.startswith("暂搁"):
            set_field(destination, PARKED_RE, f"- {note}")
            set_field(destination, HANDLE_RE, "- 处理：不办")
        elif note == "revive":
            set_field(destination, HANDLE_RE, "- 处理：未开")
            lines = [
                line
                for line in destination.read_text(encoding="utf-8").splitlines()
                if not PARKED_RE.match(line)
            ]
            destination.write_text(
                "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
            )
        # git mv 已 staged；正文状态字段随后变更未入索引，直接 commit 会留下旧状态。
        # 对已跟踪文件 add，登记内容改动（未入库条目退化为普通 rename，由用户自行 add）。
        if _git(["ls-files", "--error-unmatch", _rel(destination)]).returncode == 0:
            add_result = _git(["add", _rel(destination)])
            if add_result.returncode != 0:
                raise IdScanError(f"git add 失败：{_rel(destination)}")
        sys.stderr.write(f"已迁移：{_rel(destination)}\n")


def cmd_archive(args: argparse.Namespace) -> None:
    tid = args.fix_ref.strip()
    actions: list[tuple[Path, Path, str]] = []
    for number in parse_ids(args.ids):
        state, path = find_entry(number)
        if state == "archived":
            raise IdScanError(f"p{number:03d} 已在 archive；禁止重复归档")
        if state == "parked":
            raise IdScanError(
                f"p{number:03d} 在 parked（暂搁而非闭环）；如需闭环请先 revive"
            )
        actions.append((path, ARCHIVE_DIR, f"处理：{tid}"))
    _apply(args, actions)


def cmd_park(args: argparse.Namespace) -> None:
    actions: list[tuple[Path, Path, str]] = []
    for number in parse_ids(args.ids):
        state, path = find_entry(number)
        if state != "todo":
            raise IdScanError(f"p{number:03d} 当前在 {state}；只有 todo 可暂搁")
        actions.append((path, PARKED_DIR, f"暂搁：{args.reason.strip()}"))
    _apply(args, actions)


def cmd_revive(args: argparse.Namespace) -> None:
    actions: list[tuple[Path, Path, str]] = []
    for number in parse_ids(args.ids):
        state, path = find_entry(number)
        if state != "parked":
            raise IdScanError(f"p{number:03d} 当前在 {state}；只有 parked 可复活")
        actions.append((path, TODO_DIR, "revive"))
    _apply(args, actions)


def cmd_rename(args: argparse.Namespace) -> None:
    """rename：保留编号，仅换 slug。已入库用 git mv、未入库普通改名；dry-run 默认。

    不改文档正文里的引用——slug 改动需人工核对 docs/ 与 src/ 内的 pNNN 引用。
    """
    if not re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)*", args.slug):
        sys.exit(f"slug 非法（须 snake_case）：{args.slug!r}")
    try:
        numbers = parse_ids([args.entry_id])
        # find_entry 复用状态迁移的同号多处歧义检测：多处命中时报错并列出全部候选。
        _, old_path = find_entry(numbers[0])
    except IdScanError as e:
        sys.exit(str(e))
    entry_id = f"p{numbers[0]:03d}"
    new_path = old_path.with_name(f"{entry_id}_{args.slug}.md")
    if new_path == old_path:
        sys.exit(f"新 slug 与旧 slug 相同：{args.slug!r}")
    if new_path.exists():
        sys.exit(f"目标文件已存在：{_rel(new_path)}")
    if not args.write:
        print(f"dry-run：{_rel(old_path)} → {_rel(new_path)}")
        print("（rename 不自动改正文 pNNN 引用；如需同步，请人工核对 docs/ 与 src/）")
        return
    if _git(["ls-files", "--error-unmatch", _rel(old_path)]).returncode == 0:
        result = _git(["mv", _rel(old_path), _rel(new_path)])
        if result.returncode != 0:
            sys.exit(f"git mv 失败：{result.stderr.strip()}")
    else:
        old_path.rename(new_path)
    print(f"已重命名：{_rel(new_path)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="待办条目分配与状态迁移")
    sub = parser.add_subparsers(dest="command", required=True)

    new_parser = sub.add_parser("new", help="锁内取号并按模板建条目文件")
    new_parser.add_argument("--slug", required=True, help="snake_case 主题标识")
    new_parser.add_argument(
        "--kind", choices=("entry", "bug"), default="entry", help="条目模板类型"
    )
    new_parser.set_defaults(func=cmd_new)

    list_parser = sub.add_parser("list", help="列举条目")
    list_parser.add_argument(
        "--state", choices=(*STATE_DIRS, "all"), default="todo", help="按状态过滤"
    )
    list_parser.set_defaults(func=cmd_list)

    archive_parser = sub.add_parser("archive", help="闭环：todo → archive")
    archive_parser.add_argument("ids", nargs="+", metavar="pNNN")
    archive_parser.add_argument(
        "--fix-ref", required=True, metavar="TID", help="闭环 task id，写入 - 处理"
    )
    archive_parser.add_argument("--write", action="store_true", help="落盘")
    archive_parser.set_defaults(func=cmd_archive)

    park_parser = sub.add_parser("park", help="暂搁：todo → parked")
    park_parser.add_argument("ids", nargs="+", metavar="pNNN")
    park_parser.add_argument("--reason", required=True, help="暂搁理由")
    park_parser.add_argument("--write", action="store_true", help="落盘")
    park_parser.set_defaults(func=cmd_park)

    revive_parser = sub.add_parser("revive", help="复活：parked → todo")
    revive_parser.add_argument("ids", nargs="+", metavar="pNNN")
    revive_parser.add_argument("--write", action="store_true", help="落盘")
    revive_parser.set_defaults(func=cmd_revive)

    rename_parser = sub.add_parser("rename", help="改条目 slug（保留编号）")
    rename_parser.add_argument("entry_id", metavar="pNNN", help="要改的待办编号")
    rename_parser.add_argument("--slug", required=True, help="新 snake_case slug")
    rename_parser.add_argument("--write", action="store_true", help="落盘（默认 dry-run）")
    rename_parser.set_defaults(func=cmd_rename)

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
