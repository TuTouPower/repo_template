"""_id_scan.py 跨分支/跨 worktree 编号扫描与 pending/findings 只读 CLI。

测试用临时 git 仓库（tmp_path 下 init）模拟多分支、多 worktree 与当前工作区未提交
改动，覆盖解析规则、重复检测、坏文件报错与 CLI 行为。
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _id_scan
from _id_scan import IdScanError, scan_max_id, visible_markdown_lines
import pending as pending_mod
import findings as findings_mod

ENTRY_H3 = pending_mod.ENTRY_RE
PENDING_PATHS = pending_mod.REL_PATHS
FINDING_PATHS = findings_mod.REL_PATHS
ENTRY_H2 = findings_mod.ENTRY_RE


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs").mkdir()
    (repo / "docs" / "pending.md").write_text("# pending\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _commit(repo, rel, text, msg="update"):
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg)


# ---------- 解析规则 ----------


def test_visible_lines_strips_fence_blockquote_html_comment():
    text = """正文 p900
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
    matches = [
        m.group(1) for line in visible_markdown_lines(text) if (m := ENTRY_H3.match(line))
    ]
    assert matches == ["010", "011"]


def test_ignores_html_comments_and_invalid_fence_close():
    text = """```markdown
```not-a-close
### p900 代码示例
```
<!--
### p901 多行注释
-->
<!-- ### p902 单行注释 -->
### p010 合法
"""
    assert _id_scan._scan_source([("docs/pending.md", text)], ENTRY_H3, "x") == [10]


# ---------- 空历史与编号推进 ----------


def test_empty_history_starts_at_zero(tmp_path):
    repo = _init_repo(tmp_path)
    assert scan_max_id(repo, ENTRY_H3, PENDING_PATHS) == 0


def test_template_placeholders_do_not_consume_ids(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/pending.md", "### pNNN bug 示例\n### pNNN 普通示例\n")
    assert scan_max_id(repo, ENTRY_H3, PENDING_PATHS) == 0


def test_main_worktree_active_drives_max(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/pending.md", "### p007 bug\n### p002 普通\n")
    assert scan_max_id(repo, ENTRY_H3, PENDING_PATHS) == 7


def test_archive_in_main_worktree_counts(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/pending.md", "### p002 当前遗留\n")
    _commit(repo, "docs/archive/pending.md", "### p027 已处理\n")
    assert scan_max_id(repo, ENTRY_H3, PENDING_PATHS) == 27


def test_ids_can_exceed_999(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/pending.md", "### p999 当前\n")
    assert scan_max_id(repo, ENTRY_H3, PENDING_PATHS) == 999


# ---------- 跨分支扫描 ----------


def test_other_branch_bigger_id_is_picked_up(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/pending.md", "### p003 main\n")
    _git(repo, "branch", "feature")
    _git(repo, "checkout", "feature")
    _commit(repo, "docs/pending.md", "### p005 feature\n")
    _git(repo, "checkout", "main")
    assert scan_max_id(repo, ENTRY_H3, PENDING_PATHS) == 5


def test_detached_worktree_uncommitted_picked_up(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/pending.md", "### p003 main\n")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "--detach", str(wt))
    (wt / "docs" / "pending.md").write_text("### p009 wt-uncommitted\n", encoding="utf-8")
    assert scan_max_id(repo, ENTRY_H3, PENDING_PATHS) == 9


# ---------- 重复检测（同一来源内跨文件）----------


def test_duplicate_within_active_fails(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/pending.md", "### p003 bug\n### p003 遗留\n")
    with pytest.raises(IdScanError, match="编号重复 003"):
        scan_max_id(repo, ENTRY_H3, PENDING_PATHS)


def test_duplicate_across_active_and_archive_fails(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/pending.md", "### p010 当前\n")
    _commit(repo, "docs/archive/pending.md", "### p010 历史\n")
    with pytest.raises(IdScanError, match="编号重复 010"):
        scan_max_id(repo, ENTRY_H3, PENDING_PATHS)


def test_duplicate_across_sources_not_reported(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/pending.md", "### p010 main\n")
    _git(repo, "branch", "feature")
    _git(repo, "checkout", "feature")
    _commit(repo, "docs/pending.md", "### p010 feature\n")
    _git(repo, "checkout", "main")
    # 跨分支重复不报，取 max
    assert scan_max_id(repo, ENTRY_H3, PENDING_PATHS) == 10


# ---------- 坏文件 ----------


def test_main_worktree_active_missing_treated_as_empty(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "docs" / "pending.md").unlink()
    # 主路径不存在视为空历史，不报错
    assert scan_max_id(repo, ENTRY_H3, PENDING_PATHS) == 0


def test_main_worktree_active_invalid_utf8_fails(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "docs" / "pending.md").write_bytes(b"\xff")
    with pytest.raises(IdScanError, match="不是合法 UTF-8"):
        scan_max_id(repo, ENTRY_H3, PENDING_PATHS)


def test_other_worktree_bad_file_skipped(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/pending.md", "### p003 main\n")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "--detach", str(wt))
    (wt / "docs" / "pending.md").write_bytes(b"\xff")
    # 其他 worktree 坏文件静默跳过，不影响主 worktree 结果
    assert scan_max_id(repo, ENTRY_H3, PENDING_PATHS) == 3


def test_git_failure_wrapped(tmp_path):
    not_a_repo = tmp_path / "norepo"
    not_a_repo.mkdir()
    with pytest.raises(IdScanError):
        scan_max_id(not_a_repo, ENTRY_H3, PENDING_PATHS)


# ---------- findings ----------


def test_findings_h2_format_picked_up(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/findings.md", "## d005 发现\n")
    assert scan_max_id(repo, ENTRY_H2, FINDING_PATHS) == 5


def test_findings_h3_not_mismatched(tmp_path):
    repo = _init_repo(tmp_path)
    _commit(repo, "docs/findings.md", "### d005 H3 误配\n## d003 真 H2\n")
    assert scan_max_id(repo, ENTRY_H2, FINDING_PATHS) == 3


# ---------- CLI ----------


def test_cli_pending_prints_id(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(pending_mod, "REPO_ROOT", repo)
    pending_mod.main(["next"])
    assert capsys.readouterr().out == "p001\n"


def test_cli_findings_prints_id(tmp_path, monkeypatch, capsys):
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(findings_mod, "REPO_ROOT", repo)
    findings_mod.main(["next"])
    assert capsys.readouterr().out == "d001\n"


def test_cli_rejects_extra_argument():
    with pytest.raises(SystemExit) as exc:
        pending_mod.main(["next", "b"])
    assert exc.value.code == 2
