"""docs/specs front matter：长期需求索引的唯一真相源。"""

from __future__ import annotations

import re
from pathlib import Path

from . import context as ctx
from .documents import atomic_write_text, parse_front_matter_text

SPECS_DIR = "docs/specs"
SPEC_SLUG_RE = ctx.SLUG_RE
TASK_TOKEN_RE = re.compile(r"^(?:t[0-9]{3,}|baseline)$")
SPEC_STATUSES = ("active", "retired")


class SpecError(ctx.TaskDataError):
    pass


def _tokens(value) -> list[str]:
    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    tokens = [token.strip() for token in text.split(",") if token.strip()]
    if not tokens:
        raise SpecError("tasks 至少包含一个 tNNN 或 baseline")
    if len(tokens) != len(set(tokens)):
        raise SpecError("tasks 存在重复 token")
    for token in tokens:
        if not TASK_TOKEN_RE.fullmatch(token):
            raise SpecError(f"tasks token 非法：{token!r}")
    return tokens


def render_spec_text(slug: str, status: str, tasks: list[str], body: str) -> str:
    lines = ["---", f"slug: {slug}", f"status: {status}", f"tasks: [{', '.join(tasks)}]", "---", ""]
    return "\n".join(lines) + body.lstrip("\n")


def load_spec(path: Path, *, root: Path | None = None) -> dict:
    source = f"{root / path if root else path}"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SpecError(f"spec 无法读取：{source}（{error}）") from error
    fm, body = parse_front_matter_text(text, source=source)
    slug = fm.get("slug", "")
    if slug != path.stem:
        raise SpecError(f"{source}: slug 必须与文件名一致（{slug!r} != {path.stem!r}）")
    if not SPEC_SLUG_RE.fullmatch(slug):
        raise SpecError(f"{source}: slug 须匹配 {SPEC_SLUG_RE.pattern}")
    status = fm.get("status", "")
    if status not in SPEC_STATUSES:
        raise SpecError(f"{source}: status 只允许 active/retired（实得 {status!r}）")
    tasks = _tokens(fm.get("tasks", ""))
    title = slug
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return {
        "slug": slug,
        "path": path,
        "status": status,
        "tasks": tasks,
        "title": title,
        "body": body,
        "fm": fm,
    }


def load_specs(root: Path | str, *, include_retired: bool = True) -> list[dict]:
    root = Path(root)
    directory = root / SPECS_DIR
    if not directory.is_dir():
        return []
    specs = [load_spec(path, root=root) for path in sorted(directory.glob("*.md"))]
    seen: set[str] = set()
    for spec in specs:
        if spec["slug"] in seen:
            raise SpecError(f"spec slug 重复：{spec['slug']}")
        seen.add(spec["slug"])
    return [spec for spec in specs if include_retired or spec["status"] == "active"]


def active_specs(root: Path | str) -> list[dict]:
    return load_specs(root, include_retired=False)


def task_specs(tid: str, root: Path | str) -> list[dict]:
    return [spec for spec in active_specs(root) if tid in spec["tasks"]]


def retired_spec_paths(root: Path | str) -> set[str]:
    return {
        spec["path"].relative_to(Path(root)).as_posix()
        for spec in load_specs(root, include_retired=True)
        if spec["status"] == "retired"
    }


def validate_specs(root: Path | str) -> list[str]:
    try:
        load_specs(root)
    except SpecError as error:
        return [str(error)]
    return []


def cmd_specs_list(args) -> None:
    import json

    specs = load_specs(args.repo.root, include_retired=args.all)
    rows = [
        {
            "slug": spec["slug"],
            "path": spec["path"].relative_to(args.repo.root).as_posix(),
            "status": spec["status"],
            "tasks": spec["tasks"],
            "title": spec["title"],
        }
        for spec in specs
    ]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for row in rows:
        print(f"{row['slug']}\t{row['status']}\t{','.join(row['tasks'])}\t{row['path']}")
