"""export_template 归档安全边界专项测试（Issue #82）。"""

import io
import subprocess
import sys
import tarfile
from pathlib import Path

from factory_paths import FACTORY_ROOT

import pytest

SCRIPTS_DIR = FACTORY_ROOT / "repo/.repo_template/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import export_template
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


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _fake_repo_sync(target: Path, *, install_hooks: bool) -> None:
    script = target / ".repo_template/scripts/repo_sync.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    body = "import subprocess, sys\n"
    if install_hooks:
        body += (
            "if sys.argv[1] == 'install-hooks':\n"
            "    subprocess.run("
            "['git', 'config', 'core.hooksPath', '.repo_template/hooks'], check=True)\n"
        )
    else:
        # 未安装按新契约退出码 2（需人工接入），而非静默 0。
        body += (
            "if sys.argv[1] == 'install-hooks':\n"
            "    sys.exit(2)\n"
        )
    script.write_text(body, encoding="utf-8")


def _run_bootstrap_main(tmp_path: Path, target: Path, monkeypatch) -> None:
    monkeypatch.setattr(export_template, "export_template", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "export_template.py",
            "--source", str(tmp_path / "source"),
            "--target", str(target),
            "--bootstrap",
        ],
    )
    export_template.main()


def test_bootstrap_does_not_claim_completion_when_hooks_not_installed(
    tmp_path, monkeypatch, capsys
):
    target = tmp_path / "target"
    target.mkdir()
    _git(target, "init", "-b", "main")
    _git(target, "config", "core.hooksPath", ".git/hooks")
    _fake_repo_sync(target, install_hooks=False)

    _run_bootstrap_main(tmp_path, target, monkeypatch)

    out = capsys.readouterr().out
    assert "bootstrap 完成" not in out
    assert "hooks 未接入" in out


def test_bootstrap_claims_completion_when_hooks_installed(tmp_path, monkeypatch, capsys):
    target = tmp_path / "target"
    target.mkdir()
    _git(target, "init", "-b", "main")
    _fake_repo_sync(target, install_hooks=True)

    _run_bootstrap_main(tmp_path, target, monkeypatch)

    assert "bootstrap 完成" in capsys.readouterr().out
