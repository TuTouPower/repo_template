"""assets 资产差异与同步语义回归（真实模板 hooks/ 产物）。

覆盖：链接对象独立同步（重复同步幂等、目标软链替换、源软链越界拒绝、
普通文件源对软链目标拒绝），以及执行位纳入资产差异比较后
hard_sync_status / sync_dir / sync_file 的红→绿回归、幂等与语义边界
（软链只比 readlink、目录不比 mode、只比 0o111 不比读写位）。
"""

import os
import shutil
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from template_sync import context as ctx
from template_sync.assets import (
    _file_differs,
    hard_sync_status,
    sync_dir,
    sync_file,
)

HOOKS_SRC = FACTORY_ROOT / "repo/.repo_template/hooks"
LINK_NAME = "pre-merge-commit"
LINK_TARGET = "pre-commit"
HOOK_ENTRY = "commit-msg"


def _consumer(tmp_path: Path) -> tuple[Path, ctx.SyncContext]:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    return consumer, ctx.SyncContext.for_consumer(consumer)


def _hooks_src(tmp_path: Path) -> Path:
    src = tmp_path / "hooks_src"
    shutil.copytree(HOOKS_SRC, src, symlinks=True)
    return src


def test_real_template_link_repeat_sync_is_idempotent(tmp_path):
    src = _hooks_src(tmp_path)
    consumer, sync = _consumer(tmp_path)
    dst = consumer / ".repo_template/hooks"

    first: set[Path] = set()
    sync_dir(src, dst, first, sync)
    link = dst / LINK_NAME
    assert link.is_symlink()
    assert link.readlink() == Path(LINK_TARGET)
    assert link in first

    second: set[Path] = set()
    sync_dir(src, dst, second, sync)
    assert link.is_symlink()
    assert link.readlink() == Path(LINK_TARGET)
    assert link not in second
    assert not second


def test_dst_link_with_other_target_is_replaced(tmp_path):
    src = _hooks_src(tmp_path)
    consumer, sync = _consumer(tmp_path)
    dst = consumer / ".repo_template/hooks"
    dst.mkdir(parents=True)
    (dst / LINK_NAME).symlink_to("commit-msg")

    changed: set[Path] = set()
    sync_dir(src, dst, changed, sync)
    link = dst / LINK_NAME
    assert link.is_symlink()
    assert link.readlink() == Path(LINK_TARGET)
    assert link in changed


def test_src_link_to_outside_consumer_is_rejected_without_write(tmp_path):
    src = tmp_path / "evil_src"
    src.mkdir()
    (src / "link").symlink_to("../outside")
    consumer, sync = _consumer(tmp_path)

    changed: set[Path] = set()
    with pytest.raises(ctx.SyncError):
        sync_dir(src, consumer, changed, sync)
    assert not (consumer / "link").exists()
    assert not (consumer / "link").is_symlink()
    assert not changed


def test_plain_file_src_over_link_dst_is_rejected_without_write_through(tmp_path):
    src = tmp_path / "plain_src"
    src.mkdir()
    (src / "f").write_text("template\n", encoding="utf-8")
    consumer, sync = _consumer(tmp_path)
    dst = consumer / ".repo_template/hooks"
    dst.mkdir(parents=True)
    outside = tmp_path / "outside_f"
    outside.write_text("outside\n", encoding="utf-8")
    (dst / "f").symlink_to(outside)

    changed: set[Path] = set()
    with pytest.raises(ctx.SyncError):
        sync_dir(src, dst, changed, sync)
    assert (dst / "f").is_symlink()
    assert (dst / "f").readlink() == outside
    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert not changed


def _clone_hooks_tree(root: Path) -> Path:
    """把真实模板 hooks/ 整树（含软链）复制到 root/.repo_template/hooks。"""
    hooks = root / ".repo_template/hooks"
    shutil.copytree(HOOKS_SRC, hooks, symlinks=True)
    return hooks


def _exec_bit_damaged(tmp_path: Path):
    """真实模板产物同字节：product/consumer 各复制 HOOKS_SRC 整树，
    consumer 的 commit-msg 仅被 chmod 0644，字节与源完全相同。"""
    product = tmp_path / "product"
    consumer = tmp_path / "consumer"
    product.mkdir()
    consumer.mkdir()
    src_hooks = _clone_hooks_tree(product)
    _clone_hooks_tree(consumer)
    hook = consumer / ".repo_template/hooks" / HOOK_ENTRY
    hook.chmod(0o644)
    sync = ctx.SyncContext.for_consumer(consumer)
    return product, sync, src_hooks, hook


def test_exec_bit_loss_is_a_diff_and_is_repaired_by_sync_dir(tmp_path):
    product, sync, src_hooks, hook = _exec_bit_damaged(tmp_path)
    src_hook = src_hooks / HOOK_ENTRY

    assert src_hook.read_bytes() == hook.read_bytes()
    assert src_hook.stat().st_mode & 0o777 == 0o755
    assert hook.stat().st_mode & 0o777 == 0o644
    assert _file_differs(src_hook, hook) is True

    states = {item["rel"]: item["state"] for item in hard_sync_status(product, sync)}
    assert states[".repo_template/"] == "write"

    first: set[Path] = set()
    sync_dir(src_hooks, hook.parent, first, sync)
    assert hook.stat().st_mode & 0o777 == 0o755
    assert os.access(hook, os.X_OK)
    assert hook in first

    second: set[Path] = set()
    sync_dir(src_hooks, hook.parent, second, sync)
    assert not second
    states_after = {item["rel"]: item["state"] for item in hard_sync_status(product, sync)}
    assert states_after[".repo_template/"] == "same"


def test_exec_bit_loss_is_repaired_by_sync_file(tmp_path):
    product = tmp_path / "product"
    consumer = tmp_path / "consumer"
    product.mkdir()
    consumer.mkdir()
    src = product / HOOK_ENTRY
    dst = consumer / HOOK_ENTRY
    shutil.copy2(HOOKS_SRC / HOOK_ENTRY, src)
    shutil.copy2(HOOKS_SRC / HOOK_ENTRY, dst)
    dst.chmod(0o644)
    sync = ctx.SyncContext.for_consumer(consumer)

    assert src.read_bytes() == dst.read_bytes()
    assert src.stat().st_mode & 0o777 == 0o755
    assert _file_differs(src, dst) is True

    first: set[Path] = set()
    sync_file(src, dst, first, sync)
    assert dst.stat().st_mode & 0o777 == 0o755
    assert os.access(dst, os.X_OK)
    assert dst in first

    second: set[Path] = set()
    sync_file(src, dst, second, sync)
    assert not second


@pytest.mark.parametrize(
    "src_mode, dst_mode, differs",
    [
        (0o755, 0o644, True),
        (0o700, 0o755, True),
        (0o755, 0o777, False),
        (0o644, 0o664, False),
        (0o755, 0o755, False),
    ],
)
def test_file_differs_only_compares_exec_bits(tmp_path, src_mode, dst_mode, differs):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_bytes(b"same content\n")
    b.write_bytes(b"same content\n")
    a.chmod(src_mode)
    b.chmod(dst_mode)
    assert _file_differs(a, b) is differs


def test_symlink_only_compares_readlink_and_directory_ignores_mode(tmp_path):
    product = tmp_path / "product"
    consumer = tmp_path / "consumer"
    product.mkdir()
    consumer.mkdir()
    src_dir = _clone_hooks_tree(product)
    dst_dir = _clone_hooks_tree(consumer)

    src_link = src_dir / LINK_NAME
    dst_link = dst_dir / LINK_NAME
    assert src_link.is_symlink() and dst_link.is_symlink()
    assert _file_differs(src_link, dst_link) is False
    dst_link.unlink()
    dst_link.symlink_to("commit-msg")
    assert _file_differs(src_link, dst_link) is True
    dst_link.unlink()
    dst_link.symlink_to(LINK_TARGET)

    src_dir.chmod(0o755)
    dst_dir.chmod(0o700)
    sync = ctx.SyncContext.for_consumer(consumer)
    states = {item["rel"]: item["state"] for item in hard_sync_status(product, sync)}
    assert states[".repo_template/"] == "same"
