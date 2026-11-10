"""task.py add 模板复制校验 流程用例。

从 test_task_start_flow 拆分；fixture 与 helper 仍由 test_task_start_flow 提供。"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT
from types import SimpleNamespace

import pytest


SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
TASK_TEMPLATE_DIR = SCRIPTS_DIR.parent / "docs" / "task_template"
sys.path.insert(0, str(SCRIPTS_DIR))

from review_support import ensure_review_evidence, configure_project_verification, valid_spec
from task_flow_support import mark_done, finish_cli, prepare_finish

from repo_task import context as ctx
from repo_task import attempts, control, integration, lifecycle, store
from repo_task.monitoring import review_scope_fingerprint
from repo_task.documents import parse_front_matter, write_front_matter


from test_task_start_flow import (
    _check_ac, _finish_commit_cleanup, _git, _handoff, _identity_args, _ledger,
    _reserve, _rewind, _set_spec, _start, _task_cli, _terminal, _valid_spec,
    _valid_task_body, _worktree_path, _write_handoff, _write_worktree_include,
    git_repo,
)


def test_add_copies_validated_template(git_repo):
    result = _task_cli(
        git_repo,
        "add",
        "--title",
        "delta",
        "--slug",
        "delta",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    task_dir = git_repo / "docs/tasks/t004_delta"
    assert task_dir.is_dir()
    _, body = parse_front_matter(task_dir / "progress.md")
    assert "## 实施笔记\n\n" in body
    assert "\n无\n\n## Review 处置" in body

def test_add_rejects_invalid_template_before_copy(git_repo):
    template_task = git_repo / ".repo_template/docs/task_template/progress.md"
    template_task.write_text(
        template_task.read_text(encoding="utf-8").replace(
            "\n无\n\n## Review 处置",
            "\n## Review 处置",
            1,
        ),
        encoding="utf-8",
    )

    result = _task_cli(
        git_repo,
        "add",
        "--title",
        "delta",
        "--slug",
        "delta",
    )

    assert result.returncode != 0
    assert "模板结构校验失败" in result.stderr
    assert not (git_repo / "docs/tasks/t004_delta").exists()

def test_add_rejects_template_with_stale_front_matter_field_before_copy(git_repo):
    """A13：模板含当前契约外字段（如已删除的 schedule_status）时在建目录前失败，不静默清除。"""
    template_task = git_repo / ".repo_template/docs/task_template/progress.md"
    template_task.write_text(
        template_task.read_text(encoding="utf-8").replace(
            'conflicts_with: ""\n', 'conflicts_with: ""\nschedule_status: scheduled\n', 1
        ),
        encoding="utf-8",
    )

    result = _task_cli(git_repo, "add", "--title", "delta", "--slug", "delta")

    assert result.returncode != 0
    assert "当前契约外字段" in result.stderr
    assert "schedule_status" in result.stderr
    assert not (git_repo / "docs/tasks/t004_delta").exists()
