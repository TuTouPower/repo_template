"""export_template 归档安全边界专项测试（Issue #82）。"""

import io
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from export_template import _extract_product


def _archive(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as stream:
        for info, content in entries:
            if content is not None:
                info.size = len(content)
                stream.addfile(info, io.BytesIO(content))
            else:
                stream.addfile(info)
    buffer.seek(0)
    return tarfile.open(fileobj=buffer, mode="r")


def test_extract_rejects_duplicate_members(tmp_path):
    first = tarfile.TarInfo("same.txt")
    second = tarfile.TarInfo("same.txt")
    with _archive([(first, b"a"), (second, b"b")]) as stream:
        with pytest.raises(ValueError, match="重复归档路径"):
            _extract_product(stream, tmp_path)


def test_extract_rejects_symlink_escaping_destination(tmp_path):
    link = tarfile.TarInfo("link")
    link.type = tarfile.SYMTYPE
    link.linkname = "../outside"
    with _archive([(link, None)]) as stream:
        with pytest.raises(ValueError, match="越界软链"):
            _extract_product(stream, tmp_path)
    assert not (tmp_path.parent / "outside").exists()
