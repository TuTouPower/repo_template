#!/usr/bin/env python3
"""task.py - docs/tasks_index.json 的唯一操作入口。

禁止 agent 直接编辑 JSON。所有状态流转通过本脚本。
脚本失败必须停下提示用户，禁止 agent 不告知用户就手工修 JSON。

数据：
  docs/tasks_index.json          活跃 task（backlog/active/blocked）
  docs/archive/tasks_index.json  归档 task（done/dropped）
  docs/tasks/{tid}_{slug}/       活跃 task 工作区
  docs/archive/tasks/{tid}_{slug}/  finish/drop 时随条目一并归档

命令：
  task.py add --title TITLE --slug SLUG [--note NOTE]
  task.py edit TID [--title TITLE] [--note NOTE | --note-append NOTE]
  task.py start TID
  task.py block TID --reason blackbox|review
  task.py resume TID
  task.py finish TID              # done + 索引归档 + 目录归档
  task.py drop TID --reason TEXT  # dropped + 索引归档 + 目录归档
  task.py rewind TID [--to backlog|active] --reason TEXT
  task.py purge TID --reason TEXT
  task.py list [--status STATUS]
  task.py show TID
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_PATH = REPO_ROOT / "docs/tasks_index.json"
ARCHIVE_PATH = REPO_ROOT / "docs/archive/tasks_index.json"
AUDIT_PATH = REPO_ROOT / "docs/archive/tasks_audit.log"
TASKS_DIR = REPO_ROOT / "docs" / "tasks"
ARCHIVE_TASKS_DIR = REPO_ROOT / "docs" / "archive" / "tasks"

VALID_STATUSES = ("backlog", "active", "blocked", "done", "dropped")
# 仅 active 文件内可 rewind 的状态及其顺序（防 forward）
STATUS_ORDER = ("backlog", "active", "blocked")
DEFAULT_REWIND = {"active": "backlog", "blocked": "active"}  # 撤一步映射
DEFAULT_BRANCH = "main"
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TID_RE = re.compile(r"^t([0-9]+)$")
TZ_CN = timezone(timedelta(hours=8))


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
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _git(args: list, *, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True, text=True, check=check,
    )


def _get_head_short() -> str:
    try:
        r = _git(["rev-parse", "--short", "HEAD"])
    except (FileNotFoundError, OSError):
        return "unknown"
    if r.returncode != 0:
        return "unknown"
    return r.stdout.strip() or "unknown"


def has_unmerged_commits(branch: str) -> bool:
    """branch 上是否有未合并进 DEFAULT_BRANCH 的 commit；分支/默认分支不存在或 git 不可用返回 False。"""
    if not branch:
        return False
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{DEFAULT_BRANCH}"]).returncode != 0:
        print(f"WARNING: default branch '{DEFAULT_BRANCH}' not found; skip unmerged check", file=sys.stderr)
        return False
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode != 0:
        print(f"WARNING: branch '{branch}' not found; skip unmerged check", file=sys.stderr)
        return False
    r = _git(["rev-list", "--count", f"{DEFAULT_BRANCH}..{branch}"])
    if r.returncode != 0:
        return False
    try:
        return int(r.stdout.strip()) > 0
    except ValueError:
        return False


def append_audit(action: str, *, tid: str, fr: str, to: str, reason: str,
                 slug: str = "", title: str = "") -> None:
    """append 一行审计到 docs/archive/tasks_audit.log（ISO8601 时区，| 分隔，便于 grep）。"""
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(TZ_CN).isoformat(timespec="seconds")
    head = _get_head_short()
    parts = [ts, action, f"tid={tid}", f"from={fr}", f"to={to}", f"head={head}"]
    if slug:
        parts.append(f"slug={slug}")
    if title:
        parts.append(f"title={title}")
    parts.append(f"reason={reason}")
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(" | ".join(parts) + "\n")


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


def cmd_edit(args):
    if args.title is None and args.note is None and args.note_append is None:
        sys.exit("nothing to edit; pass --title and/or --note / --note-append")
    if args.note is not None and args.note_append is not None:
        sys.exit("--note and --note-append are mutually exclusive")
    data = load(ACTIVE_PATH)
    t = find(data["tasks"], args.tid)
    if not t:
        sys.exit(f"{args.tid} not found in active tasks (archived tasks are immutable)")
    changed = []
    if args.title is not None:
        title = args.title.strip()
        if not title:
            sys.exit("title must not be empty")
        changed.append(f"title={title!r}")
        t["title"] = title
    if args.note is not None:
        t["note"] = args.note
        changed.append(f"note={args.note!r}")
    if args.note_append is not None:
        if not args.note_append.strip():
            sys.exit("--note-append must not be empty")
        t["note"] = f"{t['note']}; {args.note_append}" if t.get("note") else args.note_append
        changed.append(f"note+={args.note_append!r}")
    save(ACTIVE_PATH, data)
    print(f"{args.tid} updated: {', '.join(changed)}")


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


def task_dir_paths(task: dict) -> tuple[Path, Path]:
    """返回 (活跃目录, 归档目录) docs/tasks/{tid}_{slug} 与 docs/archive/tasks/{tid}_{slug}。"""
    name = f"{task['tid']}_{task['slug']}"
    return TASKS_DIR / name, ARCHIVE_TASKS_DIR / name


def _move_index_to_archive(data: dict, tid: str) -> dict:
    """从 active JSON 移除并写入 archive JSON；调用前 task 状态须已更新。"""
    arc = load(ARCHIVE_PATH)
    t = find(data["tasks"], tid)
    if not t:
        sys.exit(f"{tid} not found in active tasks")
    data["tasks"] = [x for x in data["tasks"] if x["tid"] != tid]
    if find(arc["tasks"], tid):
        sys.exit(f"{tid} already exists in archive (数据冲突，请提示用户)")
    arc["tasks"].append(t)
    save(ARCHIVE_PATH, arc)
    save(ACTIVE_PATH, data)
    return t


def archive_task_directory(task: dict) -> str:
    """将 task 工作区移入 docs/archive/tasks/。

    - 源不存在：警告并跳过（例如 backlog 从未建目录）
    - 目标已存在：失败退出（调用方应在改 JSON 前预检）
    不做 git commit，由 tasks-run Step 8（执行期 commit）一并提交。
    """
    src, dst = task_dir_paths(task)
    if not src.is_dir():
        rel = src.relative_to(REPO_ROOT) if src.is_relative_to(REPO_ROOT) else src
        msg = f"task directory missing: {rel} (skipped)"
        print(f"WARNING: {msg}", file=sys.stderr)
        return msg
    if dst.exists():
        rel = dst.relative_to(REPO_ROOT) if dst.is_relative_to(REPO_ROOT) else dst
        sys.exit(f"archive task directory already exists: {rel} (数据冲突，请提示用户)")
    ARCHIVE_TASKS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    rel_dst = dst.relative_to(REPO_ROOT) if dst.is_relative_to(REPO_ROOT) else dst
    return f"moved task directory -> {rel_dst}"


def _preflight_archive_dir(task: dict) -> None:
    """改 JSON 前检查归档目录目标不冲突。"""
    src, dst = task_dir_paths(task)
    if src.is_dir() and dst.exists():
        rel = dst.relative_to(REPO_ROOT) if dst.is_relative_to(REPO_ROOT) else dst
        sys.exit(f"archive task directory already exists: {rel} (数据冲突，请提示用户)")


def cmd_finish(args):
    data = load(ACTIVE_PATH)
    t = find(data["tasks"], args.tid)
    if not t:
        sys.exit(f"{args.tid} not found in active tasks")
    require_status(t, "active")
    _preflight_archive_dir(t)
    t["status"] = "done"
    _move_index_to_archive(data, args.tid)
    dir_msg = archive_task_directory(t)
    print(f"{args.tid} status=done (archived); {dir_msg}")


def cmd_drop(args):
    data = load(ACTIVE_PATH)
    t = find(data["tasks"], args.tid)
    if not t:
        sys.exit(f"{args.tid} not found in active tasks")
    _preflight_archive_dir(t)
    t["status"] = "dropped"
    note = f"dropped: {args.reason}"
    t["note"] = f"{t['note']}; {note}" if t.get("note") else note
    _move_index_to_archive(data, args.tid)
    dir_msg = archive_task_directory(t)
    print(f"{args.tid} status=dropped (archived); {dir_msg}")


def cmd_rewind(args):
    data = load(ACTIVE_PATH)
    t = find(data["tasks"], args.tid)
    if not t:
        sys.exit(
            f"{args.tid} not found in active tasks; rewind only operates on active file "
            f"(done/dropped are in archive and cannot be rewound; to discard use drop, "
            f"to fully remove use purge)"
        )
    current = t["status"]
    if current not in STATUS_ORDER:
        sys.exit(f"{args.tid} status={current}; rewind only handles {STATUS_ORDER}")
    target = args.to or DEFAULT_REWIND.get(current)
    if target is None:
        sys.exit(f"{args.tid} is already backlog (rewind target); nothing to rewind")
    if target not in STATUS_ORDER:
        sys.exit(f"invalid --to {target!r}; must be one of {STATUS_ORDER}")
    # 方向校验：防 forward 与同态
    if STATUS_ORDER.index(target) >= STATUS_ORDER.index(current):
        sys.exit(
            f"rewind is backward only: {current} -> {target} is not a rewind "
            f"(use start/block to move forward)"
        )
    # branch 未合并检测（仅目标 backlog 且 branch 非空）
    if target == "backlog" and t.get("branch"):
        if has_unmerged_commits(t["branch"]):
            print(
                f"WARNING: branch {t['branch']!r} has unmerged commits on {DEFAULT_BRANCH}; "
                f"rewind will orphan them. Continue? (y/N)",
                file=sys.stderr,
            )
            try:
                answer = input()
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                sys.exit("rewind aborted by user")
    t["status"] = target
    if target == "backlog":
        t["branch"] = ""
    note = f"rewound: {current} -> {target}; {args.reason}"
    t["note"] = f"{t['note']}; {note}" if t.get("note") else note
    save(ACTIVE_PATH, data)
    append_audit("rewind", tid=args.tid, fr=current, to=target, reason=args.reason)
    print(f"{args.tid} status={target} (rewound from {current})")


def cmd_purge(args):
    data = load(ACTIVE_PATH)
    t = find(data["tasks"], args.tid)
    if not t:
        sys.exit(f"{args.tid} not found in active tasks")
    require_status(t, "backlog")
    # 校验 task 目录不存在（已开干的不能 purge）
    task_dirs = [d for d in TASKS_DIR.glob(f"{args.tid}_*") if d.is_dir()]
    if task_dirs:
        sys.exit(
            f"{args.tid} has task directory {task_dirs[0].name}; "
            f"purge is for tasks that never started (use drop to archive)"
        )
    # 校验 branch（backlog 理论为空，防御性检查）
    branch = t.get("branch", "")
    if branch:
        if has_unmerged_commits(branch):
            sys.exit(f"{args.tid} branch {branch!r} has unmerged commits; purge refused (use drop)")
        print(
            f"WARNING: {args.tid} is backlog but has branch {branch!r} (data inconsistency); proceeding",
            file=sys.stderr,
        )
    # title 含 | 会破坏审计行格式
    if "|" in t["title"]:
        sys.exit(f"{args.tid} title contains '|' which breaks audit log format; rename before purge")
    # 先 append 审计（删除前留快照），再从 active 删除；不进 archive
    append_audit(
        "purge",
        tid=t["tid"], fr="backlog", to="deleted", reason=args.reason,
        slug=t["slug"], title=t["title"],
    )
    data["tasks"] = [x for x in data["tasks"] if x["tid"] != args.tid]
    save(ACTIVE_PATH, data)
    print(f"{args.tid} purged (tid released; audit in {AUDIT_PATH.relative_to(REPO_ROOT)})")


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

    e = sub.add_parser("edit", help="修改活跃 task 的 title / note（不动 slug/branch/status）")
    e.add_argument("tid")
    e.add_argument("--title")
    e.add_argument("--note", help="覆盖 note（传空串则清空）")
    e.add_argument("--note-append", help="在现有 note 后追加")
    e.set_defaults(func=cmd_edit)

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

    f = sub.add_parser("finish", help="active -> done；JSON 与 task 目录一并归档")
    f.add_argument("tid")
    f.set_defaults(func=cmd_finish)

    d = sub.add_parser("drop", help="任意状态 -> dropped；JSON 与 task 目录一并归档")
    d.add_argument("tid")
    d.add_argument("--reason", required=True)
    d.set_defaults(func=cmd_drop)

    rw = sub.add_parser("rewind", help="状态撤回（active->backlog / blocked->active；默认撤一步）")
    rw.add_argument("tid")
    rw.add_argument("--to", choices=("backlog", "active"))
    rw.add_argument("--reason", required=True)
    rw.set_defaults(func=cmd_rewind)

    pg = sub.add_parser("purge", help="误建彻底删除（仅 backlog 无 task 目录；审计留快照）")
    pg.add_argument("tid")
    pg.add_argument("--reason", required=True)
    pg.set_defaults(func=cmd_purge)

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
