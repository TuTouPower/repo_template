"""pending.py 历史编号扫描与只读 CLI。"""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pending as pending_mod
from pending import PendingDataError, next_pending_id, read_pending_ids


def _files(tmp_path, active="", archive=""):
    active_path = tmp_path / "docs" / "pending.md"
    archive_path = tmp_path / "docs" / "archive" / "pending.md"
    active_path.parent.mkdir(parents=True)
    archive_path.parent.mkdir(parents=True)
    active_path.write_text(active, encoding="utf-8")
    archive_path.write_text(archive, encoding="utf-8")
    return active_path, archive_path


def test_next_starts_at_001_for_empty_history(tmp_path):
    assert next_pending_id(_files(tmp_path)) == "p001"


def test_template_placeholders_do_not_consume_ids(tmp_path):
    paths = _files(
        tmp_path,
        active="### pNNN bug 示例\n### pNNN 遗留示例\n",
    )
    assert next_pending_id(paths) == "p001"


def test_bug_and_follow_up_sections_share_sequence(tmp_path):
    paths = _files(
        tmp_path,
        active=(
            "## 未修 bug\n### p002 当前 bug\n"
            "## 遗留待办\n### p007 当前遗留\n"
        ),
    )
    assert next_pending_id(paths) == "p008"


def test_archive_history_prevents_id_reuse(tmp_path):
    paths = _files(
        tmp_path,
        active="### p002 当前遗留\n",
        archive="### p027 已处理遗留\n",
    )
    assert next_pending_id(paths) == "p028"


def test_ids_can_exceed_999(tmp_path):
    paths = _files(
        tmp_path,
        active="### p999 当前条目\n",
        archive="### p042 历史条目\n",
    )
    assert next_pending_id(paths) == "p1000"


def test_only_visible_valid_h3_entries_count(tmp_path):
    active = """正文 p900
## p901 H2
#### p902 H4
- ### p903 列表
`### p904 行内代码`
> ### p905 引用
```markdown
### p906 fence
```
~~~
### p907 fence
~~~
### P908 大写
### p91 位数不足
### p909x 非独立编号
### b910 旧 bug 前缀
### f911 旧遗留前缀
   ### p010 合法前导空格
### p011 合法
"""
    paths = _files(tmp_path, active=active)
    assert read_pending_ids(paths[0]) == [10, 11]
    assert next_pending_id(paths) == "p012"


def test_duplicate_id_across_active_and_archive_fails(tmp_path):
    paths = _files(
        tmp_path,
        active="### p010 当前条目\n",
        archive="### p010 历史条目\n",
    )
    with pytest.raises(PendingDataError, match="pending 编号重复：p010"):
        next_pending_id(paths)


def test_duplicate_id_in_same_file_fails(tmp_path):
    paths = _files(
        tmp_path,
        active="### p003 bug\n### p003 遗留\n",
    )
    with pytest.raises(PendingDataError, match="pending 编号重复：p003"):
        next_pending_id(paths)


def test_missing_history_file_fails(tmp_path):
    active, archive = _files(tmp_path)
    archive.unlink()
    with pytest.raises(PendingDataError, match="pending 编号历史文件不存在"):
        next_pending_id((active, archive))


def test_invalid_utf8_fails(tmp_path):
    active, archive = _files(tmp_path)
    archive.write_bytes(b"\xff")
    with pytest.raises(PendingDataError, match="不是合法 UTF-8"):
        next_pending_id((active, archive))


def test_cli_prints_only_id_and_does_not_write(tmp_path, monkeypatch, capsys):
    active, archive = _files(
        tmp_path,
        active="### p003 当前遗留\n",
        archive="### p012 已处理遗留\n",
    )
    before = (active.read_bytes(), archive.read_bytes())
    monkeypatch.setattr(pending_mod, "PENDING_PATHS", (active, archive))

    pending_mod.main(["next"])

    assert capsys.readouterr().out == "p013\n"
    assert (active.read_bytes(), archive.read_bytes()) == before
    assert not (tmp_path / "docs" / "pending_index.json").exists()


def test_cli_rejects_legacy_kind_argument():
    with pytest.raises(SystemExit) as exc:
        pending_mod.main(["next", "b"])
    assert exc.value.code == 2
