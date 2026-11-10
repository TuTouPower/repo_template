"""A2 复修：按消费仓同步的历史模板逐行分类 AGENTS.md 旧区。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS))

from template_sync import context as ctx
from template_sync.agents_zone_migration import migrate_agents_zones

HEADING = "## 目录与读写规则"
DEVELOP = "## 开发原则"

OLD_AGENTS = """\
模板项目介绍

## 目录与读写规则

|路径|用途|写权归属|
|---|---|---|
|`docs/specs_index.md`|当前生效 spec 清单|task 收尾时更新|
|`docs/specs/<slug>.md`|需求级 spec|旧模板写权|
|`docs/handoff.md`|项目交接记录（仅最新一节）|旧模板写权|
|`docs/pending/{todo,parked}/pNNN_{slug}.md`|待办总账|旧模板写权|
|`docs/archive/pending/pNNN_{slug}.md`|已闭环待办|旧模板写权|
|`src/` `tests/`|源码、测试|旧模板写权|

## 开发原则

- 旧模板原则一
- 旧模板原则二
"""

CURRENT_AGENTS = """\
模板项目介绍

本项目使用模板仓 repo_template。

<!-- repo-template:begin -->

## 目录与读写规则

|路径|用途|写权归属|
|---|---|---|
|`docs/specs/<slug>.md`|需求级 spec；新模板|新模板写权|
|`docs/handoff.md`|项目交接记录（仅最新一节）|旧模板写权|
|`docs/pending/pNNN_{slug}.md`|待办总账（新布局）|新模板写权|
|`src/` `tests/`|源码、测试|旧模板写权|

## 开发原则

- 旧模板原则一
- 新模板原则二

<!-- repo-template:end -->
"""

CONSUMER_AGENTS = """\
# 示例消费仓

项目专属说明。

## 目录与读写规则

消费仓表前说明。

|路径|用途|写权归属|
|---|---|---|
|`docs/specs_index.md`|当前生效 spec 清单|task 收尾时更新|
|`docs/specs/<slug>.md`|需求级 spec|旧模板写权|
|`docs/handoff.md`|消费仓自定义交接|只写本地|
|`docs/pending/{todo,parked}/pNNN_{slug}.md`|待办总账|旧模板写权|
|`docs/archive/pending/pNNN_{slug}.md`|已闭环待办|旧模板写权|
|`src/` `tests/`|源码、测试|旧模板写权|
|`custom/`|消费仓自定义目录|仅项目|

## 开发工作流

|skill|用途|
|---|---|
|task-run|执行任务|

## 开发原则

- 旧模板原则一
- 消费仓改写原则
- 旧模板原则二
"""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def _source_with_history(
    tmp_path: Path, *, old_agents: str = OLD_AGENTS, current_agents: str = CURRENT_AGENTS
) -> tuple[Path, str]:
    src = tmp_path / "source"
    (src / ".repo_template/scripts").mkdir(parents=True)
    (src / ".repo_template/scripts/task.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    _git(src, "init", "-b", "main")
    _git(src, "config", "user.email", "test@example.com")
    _git(src, "config", "user.name", "test")
    (src / "AGENTS.md").write_text(old_agents, encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "historical template")
    old_commit = _git(src, "rev-parse", "HEAD").stdout.strip()
    (src / "AGENTS.md").write_text(current_agents, encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-m", "current template")
    return src, old_commit


def _consumer(
    tmp_path: Path,
    *,
    agents: str = CONSUMER_AGENTS,
    manifest: dict | None = None,
    last_synced_commit: str | None = None,
) -> Path:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    _git(consumer, "init", "-b", "main")
    _git(consumer, "config", "user.email", "test@example.com")
    _git(consumer, "config", "user.name", "test")
    (consumer / ".repo_template").mkdir()
    (consumer / "AGENTS.md").write_text(agents, encoding="utf-8")
    if manifest is not None:
        (consumer / ".repo_template/sync_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
    sync = ctx.SyncContext.for_consumer(consumer)
    if last_synced_commit is not None:
        sync.state_path.parent.mkdir(parents=True, exist_ok=True)
        sync.state_path.write_text(
            json.dumps({"last_synced_commit": last_synced_commit}), encoding="utf-8"
        )
    return consumer


def _project_zone(text: str) -> str:
    return text.split(ctx.AGENTS_TEMPLATE_BEGIN, 1)[0]


def test_classifies_against_historical_template(tmp_path: Path):
    src, old_commit = _source_with_history(tmp_path)
    consumer = _consumer(
        tmp_path, manifest={"version": 1, "resolved_commit": old_commit}
    )
    sync = ctx.SyncContext.for_consumer(consumer)

    reports = migrate_agents_zones(sync, src, write=True)

    text = (consumer / "AGENTS.md").read_text(encoding="utf-8")
    project = _project_zone(text)
    joined = "\n".join(reports)

    # 旧模板独有且未改写的行直接删除，不进项目区。
    assert "`docs/specs_index.md`" not in text
    assert "`docs/pending/{todo,parked}/pNNN_{slug}.md`" not in text
    assert "`docs/archive/pending/pNNN_{slug}.md`" not in text
    assert "旧模板原则二" not in text

    # 改写过的模板行保留并报告。
    assert "|`docs/handoff.md`|消费仓自定义交接|只写本地|" in project
    assert "docs/handoff.md" in joined and "改写" in joined

    # 未知行保留并报告。
    assert "|`custom/`|消费仓自定义目录|仅项目|" in project
    assert "custom/" in joined and "未知" in joined

    # 该节表外段落与表逐行保留并报告。
    assert "消费仓表前说明。" in project
    assert "## 开发工作流" in project
    assert "|task-run|执行任务|" in project
    assert "消费仓表前说明。" in joined and "## 开发工作流" in joined and "表外" in joined

    # 改写过的开发原则保留并报告。
    assert "消费仓改写原则" in project
    assert "消费仓改写原则" in joined

    # 当前模板行替换旧模板行。
    assert "|`docs/specs/<slug>.md`|需求级 spec；新模板|新模板写权|" in text
    assert "|`docs/specs/<slug>.md`|需求级 spec|旧模板写权|" not in text
    assert "|`docs/pending/pNNN_{slug}.md`|待办总账（新布局）|新模板写权|" in text
    assert text.count("旧模板原则一") == 1
    assert ctx.AGENTS_TEMPLATE_BEGIN in text and ctx.AGENTS_TEMPLATE_END in text


def test_state_commit_fallback_when_manifest_has_no_resolved_commit(tmp_path: Path):
    src, old_commit = _source_with_history(tmp_path)
    consumer = _consumer(
        tmp_path,
        manifest={"version": 1},
        last_synced_commit=old_commit,
    )
    sync = ctx.SyncContext.for_consumer(consumer)

    migrate_agents_zones(sync, src, write=True)

    text = (consumer / "AGENTS.md").read_text(encoding="utf-8")
    assert "|`docs/pending/pNNN_{slug}.md`|待办总账（新布局）|新模板写权|" in text
    assert "`docs/specs_index.md`" not in text


def test_missing_commit_and_state_stops_without_write(tmp_path: Path):
    src, _old_commit = _source_with_history(tmp_path)
    consumer = _consumer(tmp_path, manifest={"version": 1})
    sync = ctx.SyncContext.for_consumer(consumer)
    before = (consumer / "AGENTS.md").read_text(encoding="utf-8")

    with pytest.raises(ctx.SyncError, match="resolved_commit|last_synced_commit"):
        migrate_agents_zones(sync, src, write=True)

    assert (consumer / "AGENTS.md").read_text(encoding="utf-8") == before


def test_source_without_commit_stops_without_write(tmp_path: Path):
    src, _old_commit = _source_with_history(tmp_path)
    consumer = _consumer(
        tmp_path, manifest={"version": 1, "resolved_commit": "0" * 40}
    )
    sync = ctx.SyncContext.for_consumer(consumer)
    before = (consumer / "AGENTS.md").read_text(encoding="utf-8")

    with pytest.raises(ctx.SyncError, match="AGENTS.md|同步提交"):
        migrate_agents_zones(sync, src, write=True)

    assert (consumer / "AGENTS.md").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("missing_heading", [HEADING, DEVELOP])
def test_historical_template_without_legacy_sections_stops(
    tmp_path: Path, missing_heading: str
):
    incomplete = "\n".join(
        line for line in OLD_AGENTS.splitlines() if line != missing_heading
    ) + "\n"
    src, old_commit = _source_with_history(tmp_path, old_agents=incomplete)
    consumer = _consumer(
        tmp_path, manifest={"version": 1, "resolved_commit": old_commit}
    )
    sync = ctx.SyncContext.for_consumer(consumer)
    before = (consumer / "AGENTS.md").read_text(encoding="utf-8")

    with pytest.raises(ctx.SyncError, match="缺旧版"):
        migrate_agents_zones(sync, src, write=True)

    assert (consumer / "AGENTS.md").read_text(encoding="utf-8") == before
