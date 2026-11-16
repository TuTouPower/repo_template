#!/usr/bin/env python3
"""task CLI entrypoint for the repo_task toolchain.

Run this file directly; implementation lives in ``repo_task/`` and requires no installation.
"""

import sys

from repo_task.cli import main

if __name__ == "__main__":
    from _runtime import run
    run(main)
