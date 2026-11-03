#!/usr/bin/env python3
"""repo_sync.py — repo-template-sync 机械化执行器。

把 repo-template-sync skill 中可机械化对齐的部分下沉到脚本执行：硬同步清单
树对树覆盖、skill 整目录覆盖、软链建修、.gitignore / .prettierignore / MCP 机械合并、
sync_state.json 字段级原子更新、差异评估与修改路径清单输出。

非机械化裁定（AGENTS.md 等共享文稿的语义合并）由脚本给出
分类与差异摘要，agent 审查后通过 `--decision UNIT:DISPOSITION` 决策，apply
按决策写盘；未提供决策的裁定单元跳过并报告（ask_user 语义）。

用法：
  python3 .repo_template/scripts/repo_sync.py init --source <path|url>
  python3 .repo_template/scripts/repo_sync.py status
  python3 .repo_template/scripts/repo_sync.py plan
  python3 .repo_template/scripts/repo_sync.py apply [--decision AGENTS.md:update]... [--skip-tests]
  python3 .repo_template/scripts/repo_sync.py prompt list
  python3 .repo_template/scripts/repo_sync.py prompt add --text "..." [--tags a,b]
  python3 .repo_template/scripts/repo_sync.py prompt revoke --id N
  python3 .repo_template/scripts/repo_sync.py link-skills
  python3 .repo_template/scripts/repo_sync.py install-hooks [--force | --statusline]

本脚本处理模板工具链与模板侧资产，并在 apply 时强制迁移 active/backlog task 的 workflow schema；不碰业务代码。禁止自动 commit——写盘与 state 更新完成后由 agent 走审批门禁。
"""

import sys
from pathlib import Path

# 消费仓从来源 checkout 直接运行本入口：保证同目录的 template_sync / repo_task 可导入。
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from template_sync.cli import main  # noqa: E402

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main()
