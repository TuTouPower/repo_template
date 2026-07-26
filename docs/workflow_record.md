 责任在我。问题分三层：

  1. 分支不等于独立 worktree

  Git 有两种隔离：

  ┌────────┬─────────────────────────┬───────────────────────────┐
  │  概念  │        隔离内容         │         本次情况          │
  ├────────┼─────────────────────────┼───────────────────────────┤
  │ branch │ 提交历史、HEAD 指向     │ 创建了 t071_rate_limit_tr │
  │        │                         │ usted_proxies             │
  ├────────┼─────────────────────────┼───────────────────────────┤
  │ worktr │ 工作目录、未提交文件、  │ 未创建，仍使用仓库根目录  │
  │ ee     │ 暂存区                  │                           │
  └────────┴─────────────────────────┴───────────────────────────┘

  我执行过：

  git switch -c t071_rate_limit_trusted_proxies
  scripts/task.py start t071

  这只把仓库根目录 /home/karon/karson_ubuntu/omni_media 从 main 切到
  t071。所有 t071
  测试、源码、任务文档改动仍放在这个共享目录里，尚未提交。

  项目流程要求单 task 最终只做一个
  commit。为遵守这个规则，我没有把半成品提交；但因此未提交内容必须依赖
  worktree 隔离保护。这里没有做。

  2. 实际发生过程

  已确认事实：

  1. t071 分支创建成功，task.py start t071 把 t071 标为 active。
  2. 在共享目录写入过 t071 内容：
    - tests/unit/t071_rate_limit_hardening.test.ts
    - src/lib/rate_limit/config.ts
    - src/lib/rate_limit/fixed_window.ts
    - src/instrumentation.ts
    - src/instrumentation.node.ts
    - scripts/smoke_rate_limit_startup.ts
    - package.json
    - t071 task.md 的 diff_anchor
  3. 这些改动没有提交。
  4. 随后同一个仓库根目录被切回 main，当前 main 存在 t102 合并冲突。
  5. reflog 记录了这次共享工作区状态切换：

  checkout: moving from t071_rate_limit_trusted_proxies to main
  reset: moving to HEAD
  6. 当前目录位于 main；t071
  新建文件已不存在，未提交改动也不在工作区。

  reflog 无法识别执行者，只能证明同一工作区发生过 checkout 与
  reset。不是 main 的提交进入 t071，也不是 Git 自动把 main 内容合并到
  t071；而是共享目录被别的操作切换和重置后，t071 未提交文件失去保护。

  严格说，单独执行普通 git switch main
  通常会因覆盖未提交内容而拒绝。但后续
  reset/清理类操作可清掉工作区改动。当前未保留完整命令历史，不能断言
  reset 的具体参数；结果已确认：未提交 t071 改动消失。

  3. 为什么这属于我的问题

  当时已有迹象表明仓库不是独占状态：

  - 初始工作区已有 t110 未提交文档；
  - task 索引和 task 目录持续可能被其他任务修改；
  - 项目工作流会改同一份 docs/tasks_index.json。

  这种状态下，不能只创建 branch。应在开始 t071 前创建独立
  worktree，例如：

  主 worktree:
    /home/karon/karson_ubuntu/omni_media
    → main / 其他任务

  t071 worktree:
    /home/karon/karson_ubuntu/omni_media/.claude/worktrees/t071_rate_l
  imit_trusted_proxies
    → t071_rate_limit_trusted_proxies

  之后即使主目录被切到 main、处理 t102、reset 或合并，t071
  目录及其未提交文件仍留在独立 worktree 内。

  我错误地把“已创建 t071
  分支”当成足够隔离，没有把“同一物理工作目录会被其他 Git
  操作影响”纳入执行前检查。正确做法是先隔离
  worktree，再开始测试和代码修改。

  4. 影响范围

  - t071 分支：仍存在，但没有 t071 提交，指向创建时基础提交 faa1ecf。
  - t071 状态：启动时已写为 active；当前 docs/tasks_index.json 存在
  t102 冲突，不能安全确认或改写。
  - t071 未提交实现：已丢失，需在隔离 worktree 中重建。
  - t102 冲突：未修改。
  - main / t110 / t102 文件：不应触碰。

  5. 后续防护

  恢复时应按以下顺序：

  1. 不处理、不覆盖当前 main 的 t102 冲突。
  2. 基于既有 t071_rate_limit_trusted_proxies 分支创建独立 worktree。
  3. 只在该 worktree 内恢复 t071 测试和实现。
  4. t071 完成验证、review、归档后，按项目规则形成唯一 task commit。
  5. 主 worktree 与 t071 worktree 分开运行；不在主目录执行 t071 的 git
  switch、reset、build 产物清理或任务状态操作。