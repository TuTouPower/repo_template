"""docs/specs front matter：长期需求索引的唯一真相源。"""

from __future__ import annotations

import re
from pathlib import Path

from . import context as ctx
from .documents import atomic_write_text, parse_front_matter_text
from .git_ops import _git
from .store import git_text_at_ref

SPECS_DIR = "docs/specs"
SPEC_SLUG_RE = ctx.SLUG_RE
TASK_TOKEN_RE = re.compile(r"^(?:t[0-9]{3,}|baseline)$")
SPEC_STATUSES = ("active", "retired")
SPEC_FM_KEYS = frozenset({"slug", "status", "tasks"})


class SpecError(ctx.TaskDataError):
    pass


def _parse_tasks(value: str | list[str], *, source: str) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        tokens = [token.strip() for token in text.split(",") if token.strip()]
    elif isinstance(value, list):
        tokens = list(value)
    else:
        raise SpecError(f"{source}: tasks 须为逗号分隔文本或 token 列表")
    if not tokens:
        raise SpecError(f"{source}: tasks 至少包含一个 tNNN 或 baseline")
    for token in tokens:
        if not isinstance(token, str) or not TASK_TOKEN_RE.fullmatch(token):
            raise SpecError(f"{source}: tasks token 非法：{token!r}")
    if len(tokens) != len(set(tokens)):
        raise SpecError(f"{source}: tasks 存在重复 token")
    return tokens


def build_spec(
    slug: str, status: str, tasks: str | list[str], body: str,
    *, source: str, path: Path | None = None,
) -> dict:
    """校验 spec 字段并构造记录；文本读取与迁移共用唯一语义入口。"""
    if path is not None and slug != path.stem:
        raise SpecError(f"{source}: slug 必须与文件名一致（{slug!r} != {path.stem!r}）")
    if not isinstance(slug, str) or not SPEC_SLUG_RE.fullmatch(slug):
        raise SpecError(f"{source}: slug 须匹配 {SPEC_SLUG_RE.pattern}")
    if status not in SPEC_STATUSES:
        raise SpecError(f"{source}: status 只允许 active/retired（实得 {status!r}）")
    tokens = _parse_tasks(tasks, source=source)
    title = slug
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    record = {
        "slug": slug,
        "status": status,
        "tasks": tokens,
        "title": title,
        "body": body,
    }
    if path is not None:
        record["path"] = path
    return record


def parse_spec_text(text: str, *, source: str, path: Path | None = None) -> dict:
    """解析 front matter 后由 build_spec 校验字段，语法错误统一抛 SpecError。

    front matter 只允许 slug/status/tasks 三个键；契约不限制空白与写法。
    """
    try:
        fm, body = parse_front_matter_text(text, source=source)
    except ctx.TaskDataError as error:
        raise SpecError(str(error)) from error
    extra = sorted(set(fm) - SPEC_FM_KEYS)
    if extra:
        raise SpecError(
            f"{source}: front matter 只允许 slug/status/tasks 三个键，多余字段：{', '.join(extra)}"
        )
    return build_spec(
        fm.get("slug", ""), fm.get("status", ""), fm.get("tasks", ""), body,
        source=source, path=path,
    )


def render_spec_text(slug: str, status: str, tasks: list[str], body: str) -> str:
    lines = ["---", f"slug: {slug}", f"status: {status}", f"tasks: [{', '.join(tasks)}]", "---", ""]
    return "\n".join(lines) + body.lstrip("\n")


def load_spec(path: Path, *, root: Path | None = None) -> dict:
    source = f"{root / path if root else path}"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SpecError(f"spec 无法读取：{source}（{error}）") from error
    return parse_spec_text(text, source=source, path=path)


def _unique_specs(specs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    for spec in specs:
        if spec["slug"] in seen:
            raise SpecError(f"spec slug 重复：{spec['slug']}")
        seen.add(spec["slug"])
    return specs


def load_specs(root: Path | str, *, include_retired: bool = True) -> list[dict]:
    root = Path(root)
    directory = root / SPECS_DIR
    if not directory.is_dir():
        return []
    specs = _unique_specs([load_spec(path, root=root) for path in sorted(directory.glob("*.md"))])
    return [spec for spec in specs if include_retired or spec["status"] == "active"]


def _spec_paths_at_ref(ref: str, *, root: Path | None, repo: ctx.RepoContext) -> list[str]:
    result = _git(
        [
            "ls-tree", "-r", "-z", "--name-only", "--end-of-options",
            ref, "--", SPECS_DIR,
        ],
        root=root, repo=repo,
    )
    if result.returncode != 0:
        raise SpecError(f"无法读取 {ref!r} 中 {SPECS_DIR}：{result.stderr.strip()}")
    return sorted(
        path for path in result.stdout.split("\0")
        if path.startswith(SPECS_DIR + "/") and path.endswith(".md")
    )


def load_specs_at_ref(
    ref: str, *, root: Path | None = None, repo: ctx.RepoContext,
    include_retired: bool = True,
) -> list[dict]:
    specs = _unique_specs([
        parse_spec_text(
            git_text_at_ref(ref, path, root=root, repo=repo),
            source=f"{ref}:{path}",
            path=Path(path),
        )
        for path in _spec_paths_at_ref(ref, root=root, repo=repo)
    ])
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


def retired_spec_paths_at_ref(
    ref: str, *, root: Path | None = None, repo: ctx.RepoContext,
) -> set[str]:
    return {
        spec["path"].as_posix()
        for spec in load_specs_at_ref(ref, root=root, repo=repo)
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
