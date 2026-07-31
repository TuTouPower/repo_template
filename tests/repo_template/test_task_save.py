"""task.py front matter 读写与转义。"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pytest
from task import (
    TaskDataError,
    _quote,
    _unquote,
    dump_front_matter,
    parse_front_matter,
    write_front_matter,
)


# --- _quote / _unquote ---

def test_quote_plain():
    assert _quote("hello") == '"hello"'


def test_quote_escapes_double_quote():
    assert _quote('a"b') == '"a\\"b"'


def test_quote_escapes_backslash():
    assert _quote("a\\b") == '"a\\\\b"'


def test_unquote_double_quoted():
    assert _unquote('"hello"') == "hello"


def test_unquote_single_quoted():
    assert _unquote("'hello'") == "hello"


def test_unquote_unwrapped():
    assert _unquote("hello") == "hello"


def test_unquote_decodes_escapes():
    assert _unquote(r'"a\"b\\c"') == 'a"b\\c'


def test_quote_unquote_roundtrip():
    for s in ["", "plain", 'with "quotes"', "back\\slash", "中文「标题」"]:
        assert _unquote(_quote(s)) == s


# --- dump / parse ---

def test_dump_quotes_all_values():
    out = dump_front_matter({"tid": "t001", "status": "backlog"})
    assert 'tid: "t001"' in out
    assert 'status: "backlog"' in out


def test_dump_preserves_key_order():
    expected_keys = [
        "tid", "slug", "title", "status", "branch", "worktree",
        "review_level", "diff_anchor", "note",
    ]
    fm = {k: "" for k in expected_keys}
    lines = dump_front_matter(fm).splitlines()
    keys = [ln.split(":")[0] for ln in lines[1:-1]]
    assert keys == expected_keys


def test_dump_parse_roundtrip(tmp_path):
    fm = {
        "tid": "t042",
        "slug": "feature_x",
        "title": '标题"含"引号',
        "status": "active",
        "note": "a; b",
    }
    body = "## 实施笔记\n\n无\n"
    p = tmp_path / "task.md"
    write_front_matter(p, fm, body)
    parsed_fm, parsed_body = parse_front_matter(p)
    for k, v in fm.items():
        assert parsed_fm[k] == v
    assert parsed_body == body


def test_parse_raises_when_no_front_matter(tmp_path):
    p = tmp_path / "task.md"
    p.write_text("正文，没有 front matter", encoding="utf-8")
    with pytest.raises(TaskDataError, match="YAML front matter"):
        parse_front_matter(p)


def test_parse_raises_when_unclosed(tmp_path):
    p = tmp_path / "task.md"
    p.write_text("---\ntid: t001\n", encoding="utf-8")
    with pytest.raises(TaskDataError, match="未闭合"):
        parse_front_matter(p)


def test_write_uses_lf_newlines(tmp_path):
    p = tmp_path / "task.md"
    write_front_matter(p, {"tid": "t001"}, "body\n")
    assert b"\r\n" not in p.read_bytes()


def test_parse_strips_inline_comment_unquoted(tmp_path):
    """照搬文档示例（值尾部行内注释）不污染值；引号内的 # 保留。"""
    p = tmp_path / "task.md"
    p.write_text(
        '---\nstatus: backlog        # backlog / active / done\ntitle: "含 # 号"\n---\nx\n',
        encoding="utf-8",
    )
    fm, _ = parse_front_matter(p)
    assert fm["status"] == "backlog"
    assert fm["title"] == "含 # 号"
