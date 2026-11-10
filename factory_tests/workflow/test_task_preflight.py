"""task.py preflight 门禁与 policy 解析 流程用例。

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


def test_preflight_reads_active_state_only_inside_task_worktree(git_repo):
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    primary = _task_cli(git_repo, "preflight", "t001")
    task_worktree = _task_cli(worktree, "preflight", "t001")

    assert primary.returncode != 0
    assert "status=backlog" in primary.stdout
    assert "status=backlog" not in task_worktree.stdout

def test_preflight_can_check_backlog_from_main_and_chain_ref(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    _set_spec(git_repo, "t002", "beta", "外部行为：已核实")

    main_backlog = _task_cli(git_repo, "preflight", "t001", "--allow-backlog")
    assert main_backlog.returncode == 0, main_backlog.stdout + main_backlog.stderr
    assert "preflight=PASS" in main_backlog.stdout

    _start(git_repo)
    _, branch, _ = _finish_commit_cleanup(git_repo, "t001", "alpha")
    chain_backlog = _task_cli(
        git_repo,
        "preflight",
        "t002",
        "--allow-backlog",
        "--ref",
        branch,
    )

    assert chain_backlog.returncode == 0, chain_backlog.stdout + chain_backlog.stderr
    assert f"source_ref: {branch}" in chain_backlog.stdout
    assert "未检查 task worktree 与当前脏改动" in chain_backlog.stdout
    assert "preflight=PASS" in chain_backlog.stdout

def test_preflight_active_worktree_allows_implementation_files_without_warning(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    (worktree / "src").mkdir(exist_ok=True)
    (worktree / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (worktree / "tests").mkdir(exist_ok=True)
    (worktree / "tests" / "test_app.py").write_text("assert True\n", encoding="utf-8")
    (worktree / "docs" / "blueprint").mkdir(parents=True, exist_ok=True)
    (worktree / "docs" / "blueprint" / "testing.md").write_text(
        "## verification_policy\nproject_configured\n## verification_notes\n测试配置已明确\n## doctor_cmd\n```bash\npytest\n```\n## test_cmd\n```bash\npytest\n```\n## blackbox_verify\n```bash\npytest\n```\n",
        encoding="utf-8",
    )

    result = _task_cli(worktree, "preflight", "t001")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preflight=PASS" in result.stdout
    assert "与本 task 无关" not in result.stdout
    assert "未提交改动" not in result.stdout
    assert "混入其他 task 目录改动" not in result.stdout

def test_preflight_active_worktree_warns_on_cross_task_directory_change(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    other_spec = worktree / "docs" / "tasks" / "t002_beta" / "task.md"
    other_spec.write_text("modified in t001 worktree\n", encoding="utf-8")

    result = _task_cli(worktree, "preflight", "t001")
    assert "工作区混入其他 task 目录改动" in result.stdout
    assert "docs/tasks/t002_beta/task.md" in result.stdout

def test_preflight_backlog_warns_on_uncommitted_files(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    (git_repo / "dirty_code.py").write_text("x = 1\n", encoding="utf-8")

    result = _task_cli(git_repo, "preflight", "t001", "--allow-backlog")
    assert "工作区有 1 项未提交改动：dirty_code.py" in result.stdout

def test_preflight_ref_diagnostics_when_active_in_worktree(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    _start(git_repo)

    result = _task_cli(git_repo, "preflight", "t001", "--ref", "t001_alpha")
    assert result.returncode != 0
    assert "status=backlog" in result.stdout
    assert "快照滞后" in result.stdout
    assert "--ref 仅读取 Git 提交快照" in result.stdout

def test_start_rejects_missing_scaffold_before_mutation(git_repo):
    spec = git_repo / "docs/tasks/t001_alpha/task.md"
    # 按原始行扫描删除第一个 `<!-- 规范 -->` 块（含空行），
    # 不硬编码块格式，模板空行变化不影响本测试。
    lines = spec.read_text(encoding="utf-8").splitlines()
    out, in_block, skipped = [], False, False
    for line in lines:
        if not skipped and line.strip() == "<!-- 规范（门禁必留，不得删除） -->":
            in_block, skipped = True, True
            continue
        if in_block:
            if line.strip() == "<!-- /规范 -->":
                in_block = False
            continue
        out.append(line)
    spec.write_text("\n".join(out), encoding="utf-8")
    _git(git_repo, "add", str(spec.relative_to(git_repo)))
    _git(git_repo, "commit", "-m", "break task scaffold")
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(SystemExit, match="规范块"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()

def test_backlog_preflight_rejects_empty_implementation_notes(git_repo):
    task_md = git_repo / "docs/tasks/t001_alpha/progress.md"
    text = task_md.read_text(encoding="utf-8").replace(
        "\n无\n\n## Review 处置",
        "\n## Review 处置",
        1,
    )
    task_md.write_text(text, encoding="utf-8")

    result = _task_cli(git_repo, "preflight", "t001", "--allow-backlog")

    assert result.returncode != 0
    assert "实施笔记为空" in result.stdout

def test_start_rejects_blocking_unknown_contract_before_mutation(git_repo):
    _set_spec(git_repo, "t001", "alpha", "用户账号：UNVERIFIED-BLOCKING，需用户核实")
    initial_head = _git(git_repo, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(SystemExit, match="UNVERIFIED-BLOCKING"):
        _start(git_repo)

    assert _git(git_repo, "rev-parse", "HEAD").stdout.strip() == initial_head
    assert _git(git_repo, "status", "--porcelain").stdout.strip() == ""
    assert not _worktree_path(git_repo).exists()

def test_start_rejects_ambiguous_unverified_marker(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：UNVERIFIED，待决定")

    with pytest.raises(SystemExit, match="裸 UNVERIFIED"):
        _start(git_repo)

    assert not _worktree_path(git_repo).exists()

def test_spike_requires_strict_preflight_before_implementation(git_repo):
    _set_spec(git_repo, "t001", "alpha", "平台行为：UNVERIFIED-SPIKE，执行期实验")
    _start(git_repo)
    worktree = _worktree_path(git_repo)

    default = _task_cli(worktree, "preflight", "t001")
    assert default.returncode == 0, default.stderr
    assert "WARN" in default.stdout
    assert "只能先完成实验并回填结论" in default.stdout

    strict = _task_cli(worktree, "preflight", "t001", "--require-verified")
    assert strict.returncode != 0
    assert "UNVERIFIED-SPIKE" in strict.stdout

    spec = worktree / "docs/tasks/t001_alpha/task.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "平台行为：UNVERIFIED-SPIKE，执行期实验",
            "平台行为：已通过本地兼容实验核实",
        ),
        encoding="utf-8",
    )
    verified = _task_cli(worktree, "preflight", "t001", "--require-verified")
    assert verified.returncode == 0, verified.stderr
    assert "preflight=PASS" in verified.stdout

def test_preflight_rejects_blocking_marker_added_after_start(git_repo):
    _set_spec(git_repo, "t001", "alpha", "外部行为：已核实")
    _start(git_repo)
    worktree = _worktree_path(git_repo)
    spec = worktree / "docs/tasks/t001_alpha/task.md"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "外部行为：已核实",
            "用户账号：UNVERIFIED-BLOCKING，需用户核实",
        ),
        encoding="utf-8",
    )

    result = _task_cli(worktree, "preflight", "t001")

    assert result.returncode != 0
    assert "UNVERIFIED-BLOCKING" in result.stdout

def test_preflight_ignores_placeholder_explanations_when_sections_are_filled(git_repo):
    testing = git_repo / "docs/blueprint/testing.md"
    testing.parent.mkdir(parents=True, exist_ok=True)
    testing.write_text(
        """# 测试

`{doctor_cmd}` / `{test_cmd}` / `{blackbox_verify}` 的说明。

## verification_policy

project_configured

## verification_notes

该测试显式选择已填命令。

## doctor_cmd

```bash
python3 --version
```

## test_cmd

```bash
pytest -q
```

## blackbox_verify

```bash
python3 --version
```
""",
        encoding="utf-8",
    )
    result = _task_cli(git_repo, "preflight", "t001", "--allow-backlog")
    assert result.returncode == 0, result.stderr
    assert "testing.md 仍有未填占位符" not in result.stdout

def test_missing_testing_sections_parser_matrix():
    """testing.md 章节解析矩阵：fence、缩进代码、小节、占位回显、空章节。"""
    from repo_task.lifecycle import _missing_testing_sections

    fence_cmd = "```bash\n# 注释在 fence 内不算标题\npytest -q\n```\n"
    matrix = {
        "模板默认": (
            "# 测试\n\n`{doctor_cmd}` 说明。\n\n## doctor_cmd\n\n"
            + fence_cmd + "\n## test_cmd\n\n```bash\npytest -q\n```\n\n## blackbox_verify\n\n无\n",
            [],
        ),
        "缩进代码块首行井号": (
            "## doctor_cmd\n\n    # 环境检查\n    pytest --collect-only\n\n"
            "## test_cmd\n\n    pytest -q\n\n## blackbox_verify\n\n无\n",
            [],
        ),
        "章节内小节": (
            "## doctor_cmd\n\n### 前置\npytest --collect-only\n\n"
            "## test_cmd\n\npytest -q\n\n## blackbox_verify\n\n无\n",
            [],
        ),
        "单行引用其他占位符": (
            "## doctor_cmd\n\npytest\n\n## test_cmd\n\npytest -q\n\n"
            "## blackbox_verify\n\n同 {test_cmd}\n",
            [],
        ),
        "缺全部章节": ("# 测试\n\n说明，无章节。\n",
                     ["{doctor_cmd}", "{test_cmd}", "{blackbox_verify}"]),
        "空章节": ("## doctor_cmd\n\n## test_cmd\n\n```bash\npytest -q\n```\n\n"
                 "## blackbox_verify\n\n无\n",
                 ["{doctor_cmd}"]),
        "占位回显待填": ("## doctor_cmd\n\n`{doctor_cmd}` 待填\n\n## test_cmd\n\npytest\n",
                     ["{doctor_cmd}", "{blackbox_verify}"]),
        "大写反引号标题": ("## `DOCTOR_CMD`\n\npytest\n",
                       ["{test_cmd}", "{blackbox_verify}"]),
        "四反引号内三反引号与伪标题": (
            "## doctor_cmd\n\n````bash\necho ```\n## fake\n````\n\n"
            "## test_cmd\n\n```bash\npytest -q\n```\n\n## blackbox_verify\n\n无\n",
            [],
        ),
    }
    for name, (text, expected) in matrix.items():
        assert _missing_testing_sections(text) == expected, name

def test_parse_policy_respects_fence_length_and_extracts_bash_blocks():
    """testing.md 章节解析与 documents 口径一致：闭栏须同字符且不短于开栏（Issue #78）。"""
    from repo_task.validation import parse_policy

    text = """# 测试

## verification_policy

project_configured

## verification_notes

全部门禁可执行。

## doctor_cmd

````bash
echo ```
echo doctor
````

## test_cmd

```bash
pytest -q
```

## blackbox_verify

无
"""
    commands = parse_policy(text)
    assert "echo ```" in commands["doctor_cmd"]
    assert commands["test_cmd"] == "pytest -q"
    assert commands["blackbox_verify"] == ""
