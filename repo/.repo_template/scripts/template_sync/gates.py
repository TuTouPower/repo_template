"""同步后的契约测试门禁执行。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from repo_task.locks import validation_environment

from . import context as ctx


def _run_tests(consumer: Path) -> bool:
    try:
        r = subprocess.run(
            list(ctx.CONSUMER_TEST_COMMAND), cwd=str(consumer),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=ctx.CONSUMER_TEST_TIMEOUT, env=validation_environment(consumer),
        )
    except subprocess.TimeoutExpired:
        print(f"ERROR: 契约测试超时（超过 {ctx.CONSUMER_TEST_TIMEOUT}s）", file=sys.stderr)
        return False
    if r.returncode != 0:
        print(r.stdout[-2000:] if r.stdout else "", file=sys.stderr)
        print(r.stderr[-2000:] if r.stderr else "", file=sys.stderr)
        return False
    return True
