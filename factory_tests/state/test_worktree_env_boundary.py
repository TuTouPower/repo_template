"""集成测试：显式环境链接与交付物边界回归（Issue #91 / C91）。

覆盖：
1. 显式声明的环境软链在创建、存在、目标删除/断链时，review 指纹不发生意外漂移。
2. 当 .gitignore 仅有 node_modules/ 且未声明环境链接时，行为可预测且诊断明确；
   通过精准忽略规则或 .worktreeinclude 托管后，能顺利 review 并完成 finish。
3. 非环境的合法软链（如代码中的相对软链）正常进入 review 指纹并可正常交付。
4. finish 与 cleanup 完整闭环：无论环境链接保留还是清理，均可完成真实 finish 与 cleanup。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from test_dispatch_integration import git_repo, _git, _task_cli, _worktree
from repo_task import context as ctx
from repo_task import monitoring, worktrees
from repo_task.documents import parse_front_matter
from task_flow_support import finish_cli, prepare_finish

OWNER = "session_c91"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _task_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(repo / ".repo_template/scripts/task.py"), *args]
    return subprocess.run(
        cmd,
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _worktree(repo: Path, tid: str = "t001") -> Path:
    return repo.parent / f"{repo.name}_{tid}"


def _begin(repo: Path, tid: str = "t001", owner: str = OWNER) -> dict:
    result = _task_cli(repo, "run-begin", tid, "--owner-id", owner)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("failure", ["timeout", "oserror", "nonzero", "empty"])
def test_repo_context_rejects_worktree_local_fallback_when_git_common_dir_fails(
    git_repo, monkeypatch, failure,
):
    """linked worktree 中 git 公共目录解析失败时构造 RepoContext 必须显式报错。"""
    _begin(git_repo)
    wt = _worktree(git_repo)
    assert (wt / ".git").is_file()

    main_ctx = ctx.RepoContext.for_root(git_repo)
    wt_ctx = ctx.RepoContext.for_root(wt)
    assert wt_ctx.primary_root == git_repo.resolve()
    assert wt_ctx.ledger_path == main_ctx.ledger_path
    assert wt_ctx.audit_path == main_ctx.audit_path
    assert wt.resolve() not in wt_ctx.ledger_path.parents
    assert wt.resolve() not in wt_ctx.audit_path.parents

    def fail_git(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args[0], 30)
        if failure == "oserror":
            raise OSError("git missing")
        if failure == "nonzero":
            return subprocess.CompletedProcess(args[0], 1, "", "fatal: not a git repository")
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr("repo_task.context.subprocess.run", fail_git)
    with pytest.raises(ctx.TaskDataError, match="git 公共目录"):
        ctx.RepoContext.for_root(wt)


def test_explicit_env_symlink_fingerprint_stability(git_repo, tmp_path):
    """显式声明的环境软链在存在、目标删除/断链、恢复与重建时，review 指纹不漂移。"""
    # 1. 在主仓建立 .worktreeinclude 与 node_modules 目录
    gi = git_repo / ".gitignore"
    gi.write_text(gi.read_text(encoding="utf-8") + "node_modules/\n", encoding="utf-8")
    (git_repo / ".worktreeinclude").write_text("node_modules/\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "add worktreeinclude")

    main_nm = git_repo / "node_modules"
    main_nm.mkdir()
    (main_nm / "pkg.json").write_text('{"name": "foo"}\n', encoding="utf-8")

    # 2. 启动 task，worktree 自动软链 node_modules
    _begin(git_repo)
    wt = _worktree(git_repo)
    wt_nm = wt / "node_modules"
    assert wt_nm.is_symlink()
    assert (wt_nm / "pkg.json").is_file()

    # 准备工作区交付改动（写代码并暂存）
    (wt / "feature.py").write_text("def run(): pass\n", encoding="utf-8")
    _git(wt, "add", "feature.py")
    fm, _ = parse_front_matter(wt / "docs/tasks/t001_alpha/progress.md")
    diff_anchor = fm["diff_anchor"]

    # 3. 记录基准指纹
    fp_base = monitoring.review_scope_fingerprint(diff_anchor, "docs/tasks/t001_alpha", repo_root=wt)
    assert fp_base != ""

    # 4. 删除主仓目标目录，使 worktree 中的软链变成悬空断链（dangling symlink）
    (main_nm / "pkg.json").unlink()
    main_nm.rmdir()
    assert wt_nm.is_symlink()
    assert not wt_nm.exists()  # 断链

    fp_broken = monitoring.review_scope_fingerprint(diff_anchor, "docs/tasks/t001_alpha", repo_root=wt)
    assert fp_broken == fp_base, "软链断链不应改变 review 指纹"

    # 5. 重新建立主仓目录
    main_nm.mkdir()
    (main_nm / "pkg.json").write_text('{"name": "foo"}\n', encoding="utf-8")
    fp_recovered = monitoring.review_scope_fingerprint(diff_anchor, "docs/tasks/t001_alpha", repo_root=wt)
    assert fp_recovered == fp_base

    # 6. 删除软链本身（模拟清理），指纹仍一致
    wt_nm.unlink()
    fp_unlinked = monitoring.review_scope_fingerprint(diff_anchor, "docs/tasks/t001_alpha", repo_root=wt)
    assert fp_unlinked == fp_base

    # 7. 再次链接，指纹仍一致
    repo_ctx = ctx.RepoContext.for_root(git_repo)
    worktrees.link_local_files(wt, repo=repo_ctx)
    assert wt_nm.is_symlink()
    fp_relinked = monitoring.review_scope_fingerprint(diff_anchor, "docs/tasks/t001_alpha", repo_root=wt)
    assert fp_relinked == fp_base


def test_unignored_env_symlink_diagnostic_and_remediation_flow(git_repo, tmp_path):
    """当 .gitignore 仅有 node_modules/ 且未声明环境链接时，诊断明确；修复后可正常 review 并 finish。"""
    # 1. 仅在 .gitignore 中写 node_modules/，无 .worktreeinclude
    gi = git_repo / ".gitignore"
    gi.write_text(gi.read_text(encoding="utf-8") + "node_modules/\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "add node_modules/ ignore")

    # 2. 启动 task，并在 worktree 手工创建外部 node_modules 软链
    _begin(git_repo)
    wt = _worktree(git_repo)
    ext_dir = tmp_path / "external_node_modules"
    ext_dir.mkdir()
    (ext_dir / "lib.js").write_text("module.exports = {};\n", encoding="utf-8")

    (wt / "node_modules").symlink_to(ext_dir)

    # 3. 诊断验证：直接调用 _review_worktree 抛出 TaskDataError，明确指出尾斜杠规则缺陷
    with pytest.raises(ctx.TaskDataError) as exc_info:
        monitoring._review_worktree(wt, "t001_alpha")
    err_msg = str(exc_info.value)
    assert "未被忽略的环境软链：node_modules" in err_msg
    assert "node_modules/" in err_msg
    assert "因以 '/' 结尾仅忽略真实目录" in err_msg
    assert "在 .gitignore 或 info/exclude 补充精准规则" in err_msg

    # 4. review_scope_fingerprint 不再静默返回空串，改为 fail-closed 抛错
    fm, _ = parse_front_matter(wt / "docs/tasks/t001_alpha/progress.md")
    diff_anchor = fm["diff_anchor"]
    with pytest.raises(ctx.TaskDataError, match="未被忽略的环境软链"):
        monitoring.review_scope_fingerprint(diff_anchor, "docs/tasks/t001_alpha", repo_root=wt)

    # 5. finish 前置检查拦截，并给出精准诊断，不误导为 git add
    message_file = prepare_finish(wt, "t001", "alpha", stage=False, evidence=False)
    finish_res = finish_cli(wt, "t001", OWNER, message_file)
    assert finish_res.returncode != 0
    assert "检测到未被忽略的环境软链：" in finish_res.stderr
    assert "node_modules" in finish_res.stderr
    assert "因尾斜杠 '/' 仅忽略真实目录" in finish_res.stderr

    # 6. 修复路径：在 .gitignore 补充精准规则 /node_modules
    gi_wt = wt / ".gitignore"
    gi_wt.write_text(gi_wt.read_text(encoding="utf-8") + "/node_modules\n", encoding="utf-8")
    _git(wt, "add", ".gitignore")

    # 此时 _review_worktree 成功通过，指纹可正常计算
    tree = monitoring._review_worktree(wt, "t001_alpha")
    assert "node_modules" not in tree

    # 重新生成 review 证据并完成 finish
    message_file2 = prepare_finish(wt, "t001", "alpha", stage=True)
    finish_res2 = finish_cli(wt, "t001", OWNER, message_file2)
    assert finish_res2.returncode == 0, finish_res2.stderr + finish_res2.stdout
    committed = _git(wt, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
    assert "node_modules" not in committed


def test_unignored_env_symlink_remediation_via_worktreeinclude(git_repo):
    """当主仓通过 .worktreeinclude 声明并调用 link_local_files 托管后，自动补齐忽略规则并完成 finish。"""
    gi = git_repo / ".gitignore"
    gi.write_text(gi.read_text(encoding="utf-8") + "node_modules/\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "add node_modules/ ignore")

    main_nm = git_repo / "node_modules"
    main_nm.mkdir()
    (main_nm / "package.json").write_text("{}\n", encoding="utf-8")

    _begin(git_repo)
    wt = _worktree(git_repo)

    # 模拟开发者在 worktree 手工建立了指向主仓 node_modules 的软链
    (wt / "node_modules").symlink_to(os.path.relpath(main_nm, wt))

    # 此时未被忽略，_review_worktree 报错
    with pytest.raises(ctx.TaskDataError, match="未被忽略的环境软链：node_modules"):
        monitoring._review_worktree(wt, "t001_alpha")

    # 修复：主仓配置 .worktreeinclude 并调用 link_local_files 托管环境链接
    (git_repo / ".worktreeinclude").write_text("node_modules/\n", encoding="utf-8")
    repo_ctx = ctx.RepoContext.for_root(git_repo)
    worktrees.link_local_files(wt, repo=repo_ctx)

    # info/exclude 已自动补齐 /node_modules 托管规则
    common_dir = Path(_git(git_repo, "rev-parse", "--git-common-dir").stdout.strip())
    exclude_file = (common_dir if common_dir.is_absolute() else git_repo / common_dir) / "info/exclude"
    assert "/node_modules" in exclude_file.read_text(encoding="utf-8")

    # 此时 review 与 finish 畅通无阻
    tree = monitoring._review_worktree(wt, "t001_alpha")
    assert "node_modules" not in tree

    message_file = prepare_finish(wt, "t001", "alpha", stage=True)
    finish_res = finish_cli(wt, "t001", OWNER, message_file)
    assert finish_res.returncode == 0, finish_res.stderr + finish_res.stdout
    committed = _git(wt, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
    assert "node_modules" not in committed


def test_legitimate_code_symlinks_are_delivered(git_repo):
    """非环境的合法代码软链（如相对软链）正常进入 review 指纹并成功交付。"""
    _begin(git_repo)
    wt = _worktree(git_repo)

    # 在 worktree 中创建合法代码文件及相对软链
    src_dir = wt / "src"
    src_dir.mkdir()
    (src_dir / "target.py").write_text("VAL = 42\n", encoding="utf-8")
    (src_dir / "alias.py").symlink_to("target.py")

    fm, _ = parse_front_matter(wt / "docs/tasks/t001_alpha/progress.md")
    diff_anchor = fm["diff_anchor"]

    # 1. 未暂存时，合法软链正常计入指纹
    tree = monitoring._review_worktree(wt, "t001_alpha")
    assert "src/alias.py" in tree
    assert tree["src/alias.py"][0] == "120000"
    fp_untracked = monitoring.review_scope_fingerprint(diff_anchor, "docs/tasks/t001_alpha", repo_root=wt)
    assert fp_untracked != ""

    # 2. 暂存后，指纹保持一致
    _git(wt, "add", "src/target.py", "src/alias.py")
    fp_staged = monitoring.review_scope_fingerprint(diff_anchor, "docs/tasks/t001_alpha", repo_root=wt)
    assert fp_staged == fp_untracked, "暂存前后交付指纹必须稳定一致"

    # 3. 改变软链目标后，指纹改变（变更真实可查，未被静默忽略）
    (src_dir / "alias.py").unlink()
    (src_dir / "alias.py").symlink_to("other.py")
    fp_changed = monitoring.review_scope_fingerprint(diff_anchor, "docs/tasks/t001_alpha", repo_root=wt)
    assert fp_changed != fp_staged

    # 还原并完成 finish 交付
    (src_dir / "alias.py").unlink()
    (src_dir / "alias.py").symlink_to("target.py")
    _git(wt, "add", "src/alias.py")

    message_file = prepare_finish(wt, "t001", "alpha", stage=True)
    finish_res = finish_cli(wt, "t001", OWNER, message_file)
    assert finish_res.returncode == 0, finish_res.stderr

    # 验证 commit 中软链以 120000 模式交付
    ls_tree = _git(wt, "ls-tree", "HEAD", "src/alias.py").stdout.strip()
    assert ls_tree.startswith("120000 blob")


def test_finish_and_cleanup_lifecycle_with_retained_or_cleaned_env_links(git_repo):
    """无论环境软链保留还是在 finish 前后清理，均可完成真实 finish 与 cleanup。"""
    gi = git_repo / ".gitignore"
    gi.write_text(gi.read_text(encoding="utf-8") + "/data/\n.env.local\n", encoding="utf-8")
    (git_repo / ".worktreeinclude").write_text("/data/\n.env.local\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-m", "worktree include")
    (git_repo / "data").mkdir()
    (git_repo / "data/app.db").write_text("sqlite-data\n", encoding="utf-8")
    (git_repo / ".env.local").write_text("APP_KEY=secret\n", encoding="utf-8")

    # 启动 task
    _begin(git_repo)
    wt = _worktree(git_repo)
    assert (wt / "data").is_symlink()
    assert (wt / ".env.local").is_symlink()

    # 准备并执行 finish（保留环境软链现场）
    message_file = prepare_finish(wt, "t001", "alpha", stage=True)
    finish_res = finish_cli(wt, "t001", OWNER, message_file)
    assert finish_res.returncode == 0, finish_res.stderr + finish_res.stdout

    # 实现 commit 中不包含任何环境软链
    committed = _git(wt, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
    assert "data" not in committed and ".env.local" not in committed

    # 主仓执行 run-close 清理 worktree，主仓源数据完好无损
    closed = _task_cli(git_repo, "run-close", "t001", "--owner-id", OWNER, "--result", "done")
    assert closed.returncode == 0, closed.stderr
    assert not wt.exists()
    assert (git_repo / "data/app.db").read_text(encoding="utf-8") == "sqlite-data\n"
    assert (git_repo / ".env.local").read_text(encoding="utf-8") == "APP_KEY=secret\n"


def test_find_unignored_env_symlinks_detects_directory_only_rules(git_repo, tmp_path):
    """当未跟踪软链命中带尾斜杠的目录规则时，准确识别规则来源与内容。"""
    gi = git_repo / ".gitignore"
    gi.write_text(gi.read_text(encoding="utf-8") + "vendor/\n", encoding="utf-8")
    _git(git_repo, "add", ".gitignore")
    _git(git_repo, "commit", "-m", "vendor ignore")

    ext_dir = tmp_path / "external_vendor"
    ext_dir.mkdir()

    # 1. 外部目录软链，命中 vendor/
    vendor_link = git_repo / "vendor"
    vendor_link.symlink_to(ext_dir)

    # 2. 普通合法代码软链，指向仓内普通文件，不命中目录规则
    (git_repo / "target.py").write_text("pass\n", encoding="utf-8")
    code_link = git_repo / "link.py"
    code_link.symlink_to("target.py")

    # 3. 普通文件
    (git_repo / "normal.txt").write_text("normal\n", encoding="utf-8")

    diagnostics = worktrees.find_unignored_env_symlinks(
        git_repo,
        ["vendor", "link.py", "normal.txt"],
    )

    assert "vendor" in diagnostics
    assert ".gitignore" in diagnostics["vendor"]
    assert "vendor/" in diagnostics["vendor"]

    # 合法软链与普通文件不被误诊
    assert "link.py" not in diagnostics
    assert "normal.txt" not in diagnostics


def test_ensure_links_ignored_appends_and_is_idempotent(git_repo, tmp_path):
    """_ensure_links_ignored 在 info/exclude 写入锚定规则且幂等。"""
    repo_ctx = ctx.RepoContext.for_root(git_repo)
    gi = git_repo / ".gitignore"
    gi.write_text(gi.read_text(encoding="utf-8") + "ext_data/\n", encoding="utf-8")
    _git(git_repo, "add", ".gitignore")
    _git(git_repo, "commit", "-m", "ext_data ignore")

    ext_dir = tmp_path / "external_ext_data"
    ext_dir.mkdir()
    ext_link = git_repo / "ext_data"
    ext_link.symlink_to(ext_dir)

    worktrees._ensure_links_ignored(git_repo, ["ext_data"], repo=repo_ctx)

    common_dir = Path(_git(git_repo, "rev-parse", "--git-common-dir").stdout.strip())
    exclude = (common_dir if common_dir.is_absolute() else git_repo / common_dir) / "info/exclude"
    content = exclude.read_text(encoding="utf-8")
    assert worktrees.EXCLUDE_BLOCK_START in content
    assert worktrees.EXCLUDE_BLOCK_END in content
    assert "/ext_data" in content

    # 再次调用，不重复追加
    worktrees._ensure_links_ignored(git_repo, ["ext_data"], repo=repo_ctx)
    content2 = exclude.read_text(encoding="utf-8")
    assert content2.count("/ext_data") == 1
