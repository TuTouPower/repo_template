#!/usr/bin/env python3
"""Check the canonical skill registry and render its public/internal interface."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- skill_registry:start -->"
END = "<!-- skill_registry:end -->"


def render_registry(root=ROOT):
    registry = json.loads((root / "skills/registry.json").read_text(encoding="utf-8"))
    files = {p.parent.name: p for p in (root / "skills").glob("*/SKILL.md")}
    if set(files) != set(registry["skills"]):
        raise ValueError("registry 与 canonical skills 清单不一致")
    lines = [START, "", "|skill|角色|", "|---|---|"]
    for name, entry in registry["skills"].items():
        if entry["role"] not in {"public", "internal"}:
            raise ValueError(f"未知 skill 角色：{name}")
        text = files[name].read_text(encoding="utf-8")
        if 'opencode/autoinvoke: "false"' not in text or "disable-model-invocation: true" not in text:
            raise ValueError(f"缺显式调用门禁：{name}")
        if entry["role"] == "internal" and "user-invocable: false" not in text:
            raise ValueError(f"内部 skill 不能成为用户入口：{name}")
        lines.append(f"|`{name}`|{'用户入口' if entry['role'] == 'public' else '内部正文流程'}|")
    lines.extend(["", "|父流程|子流程（直接读取 canonical 正文）|", "|---|---|"])
    for edge in registry["calls"]:
        parent, child = edge["parent"], edge["child"]
        if parent not in files or child not in files or child not in files[parent].read_text(encoding="utf-8"):
            raise ValueError(f"调用边无正文依据：{parent} -> {child}")
        lines.append(f"|`{parent}`|`{child}`|")
    lines.extend(["", END])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    path = ROOT / "docs/usage.md"
    text = path.read_text(encoding="utf-8")
    before, tail = text.split(START, 1)
    _, after = tail.split(END, 1)
    expected = before + render_registry() + after
    if args.write:
        path.write_text(expected, encoding="utf-8")
    elif text != expected:
        parser.exit(1, "skill 注册表文档过时；运行 skill_registry.py --write\n")


if __name__ == "__main__":
    main()
