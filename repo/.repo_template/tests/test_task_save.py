"""task.py front matter 读写与转义。"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pytest
from repo_task.context import TaskDataError
from repo_task.documents import (
    _quote,
    _unquote,
    dump_front_matter,
    parse_front_matter,
    write_front_matter,
)

pytestmark = pytest.mark.contract


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


# --- atomic_write_text ---

def test_atomic_write_clean_api():
    """验证 atomic_write_text 为 repo_task.documents 的公开 API，且 CLI task.py 不外漏 helper。"""
    from repo_task.documents import atomic_write_text
    import task

    assert callable(atomic_write_text)
    assert not hasattr(task, "atomic_write_text")
    assert not hasattr(task, "_atomic_write_text")


def test_atomic_write_roundtrip(tmp_path):
    from repo_task.documents import atomic_write_text

    target = tmp_path / "subdir" / "note.txt"
    atomic_write_text(target, "first content\n")
    assert target.read_text(encoding="utf-8") == "first content\n"
    assert list(target.parent.glob(".*.tmp")) == []
    assert list(target.parent.glob("*.tmp")) == []

    atomic_write_text(str(target), "updated content\n")
    assert target.read_text(encoding="utf-8") == "updated content\n"
    assert list(target.parent.glob(".*.tmp")) == []
    assert list(target.parent.glob("*.tmp")) == []


def test_atomic_write_replace_failure_preserves_target_and_cleans_tmp(tmp_path, monkeypatch):
    import os
    from repo_task.documents import atomic_write_text

    target = tmp_path / "status.txt"
    target.write_text("stable state\n", encoding="utf-8")

    def _failing_replace(src, dst):
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", _failing_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        atomic_write_text(target, "half baked state\n")

    assert target.read_text(encoding="utf-8") == "stable state\n"
    assert [p.name for p in target.parent.iterdir()] == ["status.txt"]


def test_atomic_write_fsync_failure_cleans_tmp(tmp_path, monkeypatch):
    import os
    from repo_task.documents import atomic_write_text

    target = tmp_path / "fsync_fail.txt"

    def _failing_fsync(fd):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(os, "fsync", _failing_fsync)
    with pytest.raises(OSError, match="injected fsync failure"):
        atomic_write_text(target, "never written\n")

    assert not target.exists()
    assert [p.name for p in target.parent.iterdir()] == []


def test_atomic_write_concurrent_no_collision(tmp_path):
    """P2: 并发写入使用同目录唯一临时文件，防止相互覆盖与 FileNotFoundError。"""
    from concurrent.futures import ThreadPoolExecutor
    from repo_task.documents import atomic_write_text

    target = tmp_path / "concurrent.txt"
    n_writers = 20
    contents = [f"content from writer {i}\n" for i in range(n_writers)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(atomic_write_text, target, c) for c in contents]
        for f in futures:
            f.result()

    final_content = target.read_text(encoding="utf-8")
    assert final_content in contents
    assert [p.name for p in target.parent.iterdir()] == ["concurrent.txt"]


def test_write_front_matter_many_fsync_failure_cleans_all_tmp(tmp_path, monkeypatch):
    """P3: 批量写入写入前登记清理对象，任一步失败清理所有已创建的临时文件。"""
    import os
    from repo_task.documents import write_front_matter_many

    p1 = tmp_path / "t1.md"
    p2 = tmp_path / "t2.md"
    files = [
        (p1, {"tid": "t001"}, "body 1"),
        (p2, {"tid": "t002"}, "body 2"),
    ]
    call_count = 0

    def _fsync_fail_on_second(fd):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise OSError("injected fsync failure on second file")

    monkeypatch.setattr(os, "fsync", _fsync_fail_on_second)
    with pytest.raises(OSError, match="injected fsync failure on second file"):
        write_front_matter_many(files)

    assert not p1.exists()
    assert not p2.exists()
    assert list(tmp_path.iterdir()) == []


def test_atomic_write_preserves_existing_permissions(tmp_path):
    """P2: 原子写保持已有文件权限，新文件不降为 0600。"""
    import os
    import stat
    from repo_task.documents import atomic_write_text, write_front_matter_many

    # 1. 已有 0644 文件保持 0644
    target = tmp_path / "keep_644.txt"
    target.write_text("old\n", encoding="utf-8")
    os.chmod(target, 0o644)
    atomic_write_text(target, "new\n")
    assert stat.S_IMODE(target.stat().st_mode) == 0o644

    # 2. 已有 0755 文件保持 0755
    script = tmp_path / "keep_755.sh"
    script.write_text("echo old\n", encoding="utf-8")
    os.chmod(script, 0o755)
    atomic_write_text(script, "echo new\n")
    assert stat.S_IMODE(script.stat().st_mode) == 0o755

    # 3. 新文件按 umask 创建，绝不退化为 0600
    new_file = tmp_path / "new_created.txt"
    atomic_write_text(new_file, "created\n")
    current_umask = os.umask(0)
    os.umask(current_umask)
    expected_mode = 0o666 & ~current_umask
    assert stat.S_IMODE(new_file.stat().st_mode) == expected_mode

    # 4. 批量写保持已有文件权限
    batch_file = tmp_path / "batch.md"
    batch_file.write_text("old\n", encoding="utf-8")
    os.chmod(batch_file, 0o644)
    write_front_matter_many([(batch_file, {"tid": "t001"}, "body")])
    assert stat.S_IMODE(batch_file.stat().st_mode) == 0o644


