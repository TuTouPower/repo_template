#!/usr/bin/env python3
"""Export only a committed product tree; never copy a developer worktree."""

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


_NON_DISTRIBUTABLE_DIRS = {".git", ".commandcode", "__pycache__", ".pytest_cache"}
_NON_DISTRIBUTABLE_NAMES = {"credentials.json", ".netrc"}
_NON_DISTRIBUTABLE_SUFFIXES = (
    ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".secret",
)
_NON_DISTRIBUTABLE_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa")


def is_non_distributable(name: str) -> bool:
    """产物树内不可分发的路径：VCS/缓存、.env、密钥与凭据文件。"""
    path = PurePosixPath(name)
    for part in path.parts:
        if part in _NON_DISTRIBUTABLE_DIRS:
            return True
        if (part == ".env" or part.startswith(".env.")) and part != ".env.example":
            return True
        if part in _NON_DISTRIBUTABLE_NAMES:
            return True
        if part.endswith(_NON_DISTRIBUTABLE_SUFFIXES):
            return True
        if part.startswith(_NON_DISTRIBUTABLE_PREFIXES):
            return True
    posix = path.as_posix()
    return posix.startswith(".claude/state/")


def _extract_product(stream, destination):
    """Extract the Git archive subset safely using only Python 3.9 APIs.

    Validate every member first, write regular files/directories, then create
    symlinks. No write ever traverses an archive-provided symlink.
    """
    members = stream.getmembers()
    entries = {}
    for item in members:
        path = PurePosixPath(item.name)
        if path.is_absolute() or ".." in path.parts or "\\" in item.name or not path.parts:
            raise ValueError(f"非法归档路径：{item.name}")
        if ":" in item.name or not (item.isdir() or item.isfile() or item.issym()):
            raise ValueError(f"不支持的归档条目：{item.name}")
        key = path.as_posix()
        if key in entries:
            raise ValueError(f"重复归档路径：{item.name}")
        entries[key] = item
    for name in entries:
        for parent in Path(name).parents:
            if parent.as_posix() in entries and not entries[parent.as_posix()].isdir():
                raise ValueError(f"归档父路径不是目录：{name}")
    for name, item in entries.items():
        target = destination / name
        if item.issym():
            continue
        if item.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with stream.extractfile(item) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o755 if item.mode & 0o111 else 0o644)
    for name, item in entries.items():
        if item.issym():
            link = PurePosixPath(item.linkname)
            if link.is_absolute() or "\\" in item.linkname or ":" in item.linkname:
                raise ValueError(f"非法归档软链：{name}")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(item.linkname)
    for name, item in entries.items():
        if item.issym():
            path = destination / name
            try:
                valid = path.resolve().is_relative_to(destination.resolve()) and path.exists()
            except (OSError, RuntimeError) as error:
                raise ValueError(f"无效软链：{name}") from error
            if not valid:
                raise ValueError(f"无效或越界软链：{name}")


def export_template(source: Path, target: Path, ref: str = "HEAD") -> None:
    source, target = source.resolve(), target.absolute()
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise ValueError("目标必须不存在或为空目录；不覆盖已有项目")
    if ref.startswith("-"):
        raise ValueError("ref 非法")
    def git(*args):
        return subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
    # git archive applies the caller's subdirectory prefix even for a tree-ish.
    # Always archive from the repository root, including product paths in factories.
    source = Path(git("rev-parse", "--show-toplevel").stdout.decode().strip())
    commit = git("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.decode().strip()
    product = subprocess.run(["git", "-C", str(source), "cat-file", "-e",
                              f"{commit}:.repo_template/scripts/task.py"], capture_output=True)
    tree = commit if product.returncode == 0 else f"{commit}:repo"
    archive = git("archive", "--format=tar", tree).stdout
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".template_export_", dir=target.parent))
    try:
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            for item in stream.getmembers():
                if is_non_distributable(item.name):
                    raise ValueError(f"提交含不可分发文件或本机状态：{item.name}")
            _extract_product(stream, staging)
        if target.exists():
            target.rmdir()  # Concurrent additions cause a safe failure.
        os.rename(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def bootstrap(source: Path, target: Path, ref: str) -> bool:
    export_template(source, target, ref)
    try:
        if not (target / ".git").exists():
            subprocess.run(["git", "-C", str(target), "init", "-b", "main"], check=True)
        script = target / ".repo_template/scripts/repo_sync.py"
        # install-hooks 的退出码即安装结果的唯一判据：0 已装、2 未安装需人工
        # 接入（记未完成并继续 init）、其它一律抛错停止。
        result = subprocess.run([sys.executable, str(script), "install-hooks"], cwd=target)
        if result.returncode == 0:
            hooks_installed = True
        elif result.returncode == 2:
            hooks_installed = False
        else:
            raise ValueError(
                f"install-hooks 失败（退出码 {result.returncode}）；已完成导出/初始化部分，"
                "请检查现场后人工完成剩余步骤，或删除目标目录重来"
            )
        subprocess.run(
            [sys.executable, str(script), "init", "--source", str(source.resolve())],
            cwd=target, check=True,
        )
    except subprocess.CalledProcessError as error:
        raise ValueError(
            f"bootstrap 在步骤中失败（{error}）；已完成导出/初始化部分，"
            "请检查现场后人工完成剩余步骤，或删除目标目录重来"
        ) from error
    return hooks_installed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--bootstrap", action="store_true", help="导出后 git init、安装 hooks、登记来源")
    args = parser.parse_args()
    hooks_installed = True
    try:
        if args.bootstrap:
            hooks_installed = bootstrap(args.source, args.target, args.ref)
        else:
            export_template(args.source, args.target, args.ref)
    except (OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError) as error:
        parser.exit(1, f"导出失败：{error}\n")
    print(f"已从 Git commit 导出：{args.target}")
    if args.bootstrap:
        if hooks_installed:
            print("bootstrap 完成；下一步填写 docs/blueprint/testing.md 并运行 repo_sync.py healthcheck")
        else:
            print(
                "bootstrap 未完成：hooks 未接入（已导出、git init 并登记来源）；"
                "请按上方片段人工接入 hooks，再填写 docs/blueprint/testing.md "
                "并运行 repo_sync.py healthcheck"
            )


if __name__ == "__main__":
    main()
