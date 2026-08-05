# Worker 静默监控 + `task.py` 模块化实施计划

目标：直接在当前 `main` 工作区完成两项工作：

1. 将“20 分钟无 commit 自动重派”替换为“每 5 分钟观察仓库状态指纹，连续 30 分钟无变化只告警用户”；worker 不主动写 heartbeat/progress，告警后不取消、不重派。
2. 将 3433 行 `scripts/repo_template/task.py` 拆成正式的 `repo_task` 包；保留 `python3 scripts/repo_template/task.py ...` 命令兼容，`task.py` 收缩为薄 façade/入口。

执行位置：用户已明确要求直接修改当前 `main`；不创建 task/worktree，不执行 git commit、merge 或 push。

## 1. 先锁定兼容基线

- 运行当前 `tests/repo_template`，记录拆分前基线。
- 盘点所有 `import task`、`from task import ...`、对 `task_mod` 的 monkeypatch 以及临时仓库中“只复制 task.py”的 fixture。
- 保持以下外部契约不变：
  - `python3 scripts/repo_template/task.py <command>`；
  - 当前 CLI 子命令、参数、退出码和关键错误文本；
  - task 状态机、worktree/merge/index 语义；
  - `task.py` 对现有测试和潜在脚本使用者的常用函数 re-export。

## 2. 建立包化结构

新增目录：

```text
scripts/repo_template/repo_task/
├── __init__.py
├── context.py
├── git_ops.py
├── documents.py
├── store.py
├── ledger.py
├── scheduling.py
├── monitoring.py
├── worktrees.py
├── lifecycle.py
├── integration.py
├── control.py
└── cli.py
```

依赖方向固定为：

```text
cli/control/lifecycle/integration
        ↓
monitoring/scheduling/worktrees
        ↓
ledger/store/documents/git_ops
        ↓
context
```

约束：

- 子模块禁止 import `task.py`，避免循环依赖。
- 所有仓库路径和常量只在 `context.py` 有一个权威来源；其他模块使用 `import repo_task.context as ctx` 动态读取，不复制 `REPO_ROOT` 等全局。
- 直接执行 `task.py` 时，脚本目录天然在 `sys.path`，由 façade 导入 `repo_task.cli`；不要求安装 package。
- `task.py` 最终只保留 CLI 说明、兼容 re-export、`main()` 调用和异常出口，目标控制在约 100–250 行。

## 3. 各模块职责与迁移范围

### `context.py`

- 路径：`REPO_ROOT`、task/archive/index/runtime/ledger 路径。
- 状态枚举、正则、文档模板常量、`TaskDataError`。
- 测试统一 patch 这里的路径；不再 patch façade 上的静态副本。

### `git_ops.py`

- `_git`、branch/HEAD/merge/worktree 查询。
- 主仓与 task worktree 门禁。
- ref 内容读取、dirty/冲突检测。
- 新增二进制安全的 Git 调用辅助，供仓库状态指纹使用。

### `documents.py`

- front matter 编解码和写入。
- tid 列表解析。
- Markdown 标题、UNVERIFIED、spec/task 文档门禁。

### `store.py`

- task 扫描、索引派生、ref 快照、task lookup。
- 主干 → 未合并 branch → 已登记 worktree 的有效状态覆盖。
- `load_task`、`load_task_at_ref`、状态检查、note 更新。

### `ledger.py`

- JSONL 文件锁、append/read、坏行跳过、attempt 编号。
- dispatch/report/failed/escalated/breaker/integrated/start 以及新 observation 事件的基础持久化。
- 删除原来“为了单文件复制而内联锁实现”的说明；工具链以后按目录整体复制。

### `scheduling.py`

- 依赖环检测和 `compute_schedule()`。
- `view` 与 `reconcile` 共用同一调度图实现。

### `monitoring.py`

- 仓库状态指纹、`observe`、silence 判定。
- in-flight attempt、refs/handoff 完成验证。
- `ps` 行计算、retry/breaker/escalate 规则和 reconcile 纯算法。
- 这是 worker 静默监控唯一实现落点；不在 skill、ledger、CLI 中复制算法。

### `worktrees.py`

- start base 解析、worktree 创建/回滚/删除。
- 本地 `.env` 链接与清理。

### `lifecycle.py`

- `add/edit/preflight/block/resume/finish/drop/rewind/purge/list/show`。
- task 文档和状态变更，不承担 merge/integrate。

### `integration.py`

- `start`、`cleanup-worktree`、`integrate`、chain merge、index commit、分支删除。
- 这是组合 Git/worktree/task store/ledger 的唯一工作流层。

### `control.py`

- `view`、`observe`、`ledger record/tail`、`ps`、`reconcile` 的命令适配和输出格式。
- 核心算法留在 scheduling/monitoring/ledger，不放在 command adapter。

### `cli.py`

- argparse parser 和 command 路由。
- 保持当前命令路径和参数兼容；新增 `observe`，将 `--stall-minutes` 替换为 `--silent-minutes`。

### `task.py`

- 导入 `repo_task.cli.main`。
- 为兼容现有测试/脚本 re-export 当前被外部使用的解析、扫描、调度和 command 函数；canonical owner 仍是包内模块。
- 不保留任何业务逻辑副本。

## 4. 实现仓库状态指纹

在 `monitoring.py` 实现稳定 SHA-256：

```text
HEAD commit
+ staged binary diff
+ unstaged tracked binary diff
+ 非 ignored untracked 文件的排序路径、类型/模式和内容
```

规则：

- 不使用 mtime。
- ignored/cache/log/build 文件不参与。
- 二进制文件按 bytes 哈希。
- 删除、重命名、chmod 由 binary diff 表达。
- 符号链接只哈希 link target，不跟随到仓库外。
- 账本只保存 fingerprint，不保存文件内容。

新增命令：

```bash
python3 scripts/repo_template/task.py observe TID --attempt N [--json]
```

行为：

- coordinator 调用，worker 不调用。
- 校验 attempt 已 dispatch、worktree 存在且归属该 tid。
- 首次观察或 fingerprint 变化时追加 `observation`；未变化不追加。
- 输出 fingerprint、最后变化时间和静默时长。

扩展 dispatch：

```bash
ledger record --event dispatch ... --worker-id <宿主后台任务 ID>
```

worker-id 只供 coordinator 查询宿主运行状态；`task.py` 不猜测 agent 是否存活。

## 5. 将 stalled 自动重派改为 silent 告警

- 删除 `observe_last_activity()` 基于 dispatch/HEAD commit 时间自动判 stalled 的控制路径。
- 删除“无仓库变化 → resource failure → redispatch”。显式 `failed --class resource` 仍保留原重试策略。
- observation 作为当前 attempt 的唯一仓库活动基线。

`ps`：

- 无 observation：`dispatched(未观察)`；
- 30 分钟内：`progressing`；
- 超过 30 分钟：`silent?`；
- `last_activity` 显示最后一次 fingerprint 变化时间。

`reconcile`：

- refs ready、contract、blocked、显式 failed 仍按原顺序处理。
- 当前 in-flight attempt 的 observation 超过 `--silent-minutes`（默认 30）时输出：

```text
ALERT-SILENT t272 attempt=1 — 连续 34 分钟无仓库可见变化，worker 可能出现问题
```

- `alert-silent` 不转换成 failed/escalated，不生成 redispatch，attempt 继续占槽。
- 同 fingerprint 已记录 `silent_alerted` 后不重复告警；出现新 observation 后重新计时。
- 一旦本轮存在 `alert-silent`，不再补位新的 dispatch，coordinator 报告用户并停止自动调度。

CLI：

- `ps/reconcile --silent-minutes N`，默认 30。
- 移除旧 `--stall-minutes` 文案和默认 20 分钟语义。

## 6. 更新 coordinator skill

修改 `.agents/skills/task-dispatch/SKILL.md`：

- cron 从每 10 分钟改为每 5 分钟。
- 每次唤醒：
  1. 根据 dispatch 保存的 worker-id 查询宿主后台任务状态；
  2. 对仍为 running 的 attempt 执行 `observe`；
  3. 再运行 reconcile。
- worker 不写 heartbeat/progress。
- `alert-silent` 时报告：tid、attempt、静默时长、worker 状态、最后变化时间、HEAD、worktree、dirty 摘要。
- 报告后暂停/注销 cron，不取消、不重派、不启动同 tid 新 attempt，等待用户。
- report/failed/escalated/silent 事件全部显式携带 attempt，避免迟到通知默认绑定最新 attempt。

## 7. 文档同步

- 新建 `docs_repo/plan_worker_silence_monitoring.md`，作为静默监控权威设计。
- 更新 `docs_repo/plan_dispatch_control_plane.md`，删除 stalled 自动重派、20 分钟、10 分钟 cron 的旧语义并链接新设计。
- 更新 `AGENTS.md`：
  - 增加 `task.py observe` 示例；
  - `scripts/repo_template/task.py` 是 façade，`repo_task/` 是实现包；
  - 模板复制和维护必须保留整个工具链。
- 更新 `README.md` 中工具链复制/入口说明；不再暗示 `task.py` 可作为单文件独立复制。
- 扫描注释、docstring、测试名称，清除旧 `stalled → 自动 redispatch` 和“单文件可复制”表述。

## 8. 测试迁移与新增测试

### 拆分兼容

- 现有测试改为从 canonical module 导入纯函数；保留 façade re-export 兼容测试。
- 所有路径 monkeypatch 改到 `repo_task.context`。
- monkeypatch 函数改到其 canonical owner，避免 patch façade 别名不生效。
- `test_dispatch_integration.py`、`test_task_start_flow.py` 的临时仓 fixture 从只复制 `task.py` 改为复制：

```text
task.py
repo_task/**
```

- 新增 direct CLI 回归：仓库外 cwd 调用、复制后的工具链调用、`--help`、主要子命令 parser。

### 指纹和 observe

- clean commit、staged、unstaged、删除、untracked、二进制、符号链接都会在应变化时改变 fingerprint。
- ignored 文件和仅 mtime 变化不改变 fingerprint。
- `observe` 首次/变化时记账，未变化不追加。
- attempt 不存在、worktree 缺失/不匹配时拒绝。

### silent/reconcile

- 30 分钟内 progressing，超过阈值 silent。
- silent 输出 `alert-silent`、继续占槽、不 redispatch。
- alert 时不补位新 dispatch。
- 同 fingerprint 已 alerted 后不重复；新 fingerprint 重新计时。
- 显式 resource failed 仍按原策略。
- worker-id 正确记录和展示。

## 9. 实施顺序

1. 跑全量基线并锁定 CLI/import/monkeypatch 清单。
2. 创建 `repo_task` 包和 `context/git_ops/documents` 底层模块。
3. 迁移 `store/ledger/scheduling`，每层迁移后跑相关测试。
4. 在 `monitoring` 中实现 fingerprint、observe、silent reconcile 新语义。
5. 迁移 `worktrees/lifecycle/integration/control/cli`。
6. 收缩 `task.py` 为 façade并修复测试工具链复制。
7. 更新 skill、设计文档、AGENTS、README。
8. 运行定向和全量验证，清理旧语义残留。

## 10. 验证

- `python3 scripts/repo_template/task.py --help`
- 从仓库外 cwd 调用同一命令。
- `pytest -q tests/repo_template/test_dispatch_control.py tests/repo_template/test_dispatch_integration.py`
- `pytest -q tests/repo_template/test_task_start_flow.py tests/repo_template/test_task_document_validation.py tests/repo_template/test_task_scheduling.py`
- `pytest -q tests/repo_template`
- `git diff --check`
- 搜索确认不再存在旧 stalled 自动重派、默认 20 分钟、cron 10 分钟和“task.py 单文件复制”权威表述。
- `git status --short` 确认只包含包化、静默监控、测试和文档相关改动。
