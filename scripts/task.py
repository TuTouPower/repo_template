#!/usr/bin/env python3
"""task.py - docs/tasks_index.json 的唯一操作入口。

禁止 agent 直接编辑 JSON。所有状态流转通过本脚本。
脚本失败必须停下提示用户，禁止 agent 不告知用户就手工修 JSON。

数据：
  docs/tasks_index.json          活跃 task（backlog/active/blocked）
  docs/archive/tasks_index.json  归档 task（done/dropped）

命令：
  task.py add --title TITLE --slug SLUG [--note NOTE]
  task.py start TID
  task.py block TID --reason blackbox|review
  task.py resume TID
  task.py finish TID
  task.py drop TID --reason TEXT
  task.py list [--status STATUS]
  task.py show TID
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_PATH = REPO_ROOT / "docs/tasks_index.json"
ARCHIVE_PATH = REPO_ROOT / "docs/archive/tasks_index.json"

VALID_STATUSES = ("backlog", "active", "blocked", "done", "dropped")
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TID_RE = re.compile(r"^t([0-9]+)$")


def load(path: Path) -> dict:
    if not path.exists():
        return {"tasks": []}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {"tasks": []}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        sys.exit(f"JSON parse error in {path}: {e}\n请把错误提示给用户，不要手工修 JSON。")
    if "tasks" not in data or not isinstance(data["tasks"], list):
        sys.exit(f"{path}: missing 'tasks' list\n请把错误提示给用户，不要手工修 JSON。")
    return data


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find(tasks: list, tid: str):
    for t in tasks:
        if t["tid"] == tid:
            return t
    return None


def max_tid_num(active: list, archive: list) -> int:
    mx = 0
    for t in active + archive:
        m = TID_RE.match(t["tid"])
        if m:
            mx = max(mx, int(m.group(1)))
    return mx


def require_status(t: dict, *allowed: str) -> None:
    if t["status"] not in allowed:
        sys.exit(f"{t['tid']} status is {t['status']}, expected one of {allowed}")


def cmd_add(args):
    if not SLUG_RE.match(args.slug):
        sys.exit(f"slug must match {SLUG_RE.pattern} (got {args.slug!r})")
    if not args.title.strip():
        sys.exit("title must not be empty")
    data = load(ACTIVE_PATH)
    arc = load(ARCHIVE_PATH)
    for t in data["tasks"] + arc["tasks"]:
        if t["slug"] == args.slug:
            sys.exit(f"slug already exists: {args.slug}")
    n = max_tid_num(data["tasks"], arc["tasks"]) + 1
    tid = f"t{n:03d}"
    task = {
        "tid": tid,
        "title": args.title.strip(),
        "slug": args.slug,
        "status": "backlog",
        "branch": "",
        "note": args.note or "",
    }
    data["tasks"].append(task)
    save(ACTIVE_PATH, data)
    print(f"added {tid} '{task['title']}' status=backlog")


def cmd_start(args):
    data = load(ACTIVE_PATH)
    t = find(data["tasks"], args.tid)
    if not t:
        sys.exit(f"{args.tid} not found in active tasks")
    require_status(t, "backlog")
    t["status"] = "active"
    t["branch"] = f"{t['tid']}_{t['slug']}"
    save(ACTIVE_PATH, data)
    print(f"{args.tid} status=active branch={t['branch']}")


def cmd_block(args):
    data = load(ACTIVE_PATH)
    t = find(data["tasks"], args.tid)
    if not t:
        sys.exit(f"{args.tid} not found in active tasks")
    require_status(t, "active")
    t["status"] = "blocked"
    note = f"blocked: {args.reason}"
    t["note"] = f"{t['note']}; {note}" if t.get("note") else note
    save(ACTIVE_PATH, data)
    print(f"{args.tid} status=blocked reason={args.reason}")


def cmd_resume(args):
    data = load(ACTIVE_PATH)
    t = find(data["tasks"], args.tid)
    if not t:
        sys.exit(f"{args.tid} not found in active tasks")
    require_status(t, "blocked")
    t["status"] = "active"
    save(ACTIVE_PATH, data)
    print(f"{args.tid} status=active (resumed)")


def _move_to_archive(data: dict, tid: str) -> dict:
    arc = load(ARCHIVE_PATH)
    t = find(data["tasks"], tid)
    if not t:
        sys.exit(f"{tid} not found in active tasks")
    data["tasks"] = [x for x in data["tasks"] if x["tid"] != tid]
    # archive 不允许同 tid 重复
    if find(arc["tasks"], tid):
        sys.exit(f"{tid} already exists in archive (数据冲突，请提示用户)")
    arc["tasks"].append(t)
    save(ARCHIVE_PATH, arc)
    return t


def cmd_finish(args):
    data = load(ACTIVE_PATH)
    t = find(data["tasks"], args.tid)
    if not t:
        sys.exit(f"{args.tid} not found in active tasks")
    require_status(t, "active")
    t["status"] = "done"
    save(ACTIVE_PATH, data)
    _move_to_archive(data, args.tid)
    save(ACTIVE_PATH, data)
    print(f"{args.tid} status=done (archived)")


def cmd_drop(args):
    data = load(ACTIVE_PATH)
    t = find(data["tasks"], args.tid)
    if not t:
        sys.exit(f"{args.tid} not found in active tasks")
    t["status"] = "dropped"
    note = f"dropped: {args.reason}"
    t["note"] = f"{t['note']}; {note}" if t.get("note") else note
    save(ACTIVE_PATH, data)
    _move_to_archive(data, args.tid)
    save(ACTIVE_PATH, data)
    print(f"{args.tid} status=dropped (archived)")


def cmd_list(args):
    if args.status and args.status not in VALID_STATUSES:
        sys.exit(f"invalid status {args.status!r}; valid: {VALID_STATUSES}")
    rows = []
    if args.status in (None, "backlog", "active", "blocked"):
        data = load(ACTIVE_PATH)
        rows.extend(t for t in data["tasks"] if not args.status or t["status"] == args.status)
    if args.status in (None, "done", "dropped"):
        arc = load(ARCHIVE_PATH)
        rows.extend(t for t in arc["tasks"] if not args.status or t["status"] == args.status)
    if not rows:
        print("(no tasks)")
        return
    print("| tid    | title                              | status   | branch                        | note |")
    print("|--------|------------------------------------|----------|-------------------------------|------|")
    for t in rows:
        title = t["title"][:34]
        branch = t.get("branch", "")[:29]
        note = t.get("note", "")[:40]
        print(f"| {t['tid']:<6} | {title:<34} | {t['status']:<8} | {branch:<29} | {note} |")


def cmd_show(args):
    data = load(ACTIVE_PATH)
    t = find(data["tasks"], args.tid)
    if not t:
        arc = load(ARCHIVE_PATH)
        t = find(arc["tasks"], args.tid)
    if not t:
        sys.exit(f"{args.tid} not found in active or archive")
    width = max(len(k) for k in t)
    for k, v in t.items():
        print(f"{k.ljust(width)}: {v}")


def main():
    p = argparse.ArgumentParser(
        description="docs/tasks_index.json 操作入口（唯一允许的修改方式）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新增 backlog task；自动分配 max(tid)+1")
    a.add_argument("--title", required=True)
    a.add_argument("--slug", required=True)
    a.add_argument("--note", default="")
    a.set_defaults(func=cmd_add)

    s = sub.add_parser("start", help="backlog -> active；branch 自动构造为 {tid}_{slug}")
    s.add_argument("tid")
    s.set_defaults(func=cmd_start)

    b = sub.add_parser("block", help="active -> blocked")
    b.add_argument("tid")
    b.add_argument("--reason", required=True, choices=("blackbox", "review"))
    b.set_defaults(func=cmd_block)

    r = sub.add_parser("resume", help="blocked -> active（用户加轮后）")
    r.add_argument("tid")
    r.set_defaults(func=cmd_resume)

    f = sub.add_parser("finish", help="active -> done；移入 archive")
    f.add_argument("tid")
    f.set_defaults(func=cmd_finish)

    d = sub.add_parser("drop", help="任意状态 -> dropped；移入 archive")
    d.add_argument("tid")
    d.add_argument("--reason", required=True)
    d.set_defaults(func=cmd_drop)

    l = sub.add_parser("list", help="列出 task（默认全部，含归档）")
    l.add_argument("--status", choices=VALID_STATUSES)
    l.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="显示单条 task 详情")
    sh.add_argument("tid")
    sh.set_defaults(func=cmd_show)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
