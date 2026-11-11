"""assets._sync_link：链接对象独立同步语义回归（真实模板 hooks/ 软链）。

覆盖：重复同步幂等、目标软链替换、源软链越界拒绝、普通文件源对软链目标拒绝。
"""

import shutil
import sys
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from template_sync import context as ctx
from template_sync.assets import sync_dir

HOOKS_SRC = FACTORY_ROOT / "repo/.repo_template/hooks"
LINK_NAME = "pre-merge-commit"
LINK_TARGET = "pre-commit"


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
