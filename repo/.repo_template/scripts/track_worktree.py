#!/usr/bin/env python3
"""状态行 worktree 绑定 CLI（薄层；实现见 repo_task.statusline）。

绑定由核心命令在拿到 identity 后显式建立：`run-begin --session-id` 写入，`run-close --session-id` 解除。
本脚本不解析终端命令文本，也不依赖宿主私有字段。

用法：
  python3 .repo_template/scripts/track_worktree.py bind --session <host-session> --owner-id <owner> --tid t113
  python3 .repo_template/scripts/track_worktree.py render --session <host-session>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from repo_task.statusline import (  # noqa: E402,F401  (CLI 出口与测试复用)
    append_record,
    bind,
    current_worktree,
    main,
    resolve_worktree,
    unbind,
)

if __name__ == "__main__":
    raise SystemExit(main())
