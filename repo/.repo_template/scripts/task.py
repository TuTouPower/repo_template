#!/usr/bin/env python3
"""task CLI entrypoint for the repo_task toolchain.

Run this file directly; implementation lives in ``repo_task/`` and requires no installation.
"""

from _runtime import require_python, run

require_python()

import sys

from repo_task.cli import main

if __name__ == "__main__":
    run(main)
