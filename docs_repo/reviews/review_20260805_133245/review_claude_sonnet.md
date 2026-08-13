# 本路模型标识

Claude Sonnet

# 审阅范围

`git diff 90b2387~1..HEAD`，5 个 commit、34 个文件：

- `90b2387` feat: close attempt lifecycle and modularize task tool（核心：`scripts/repo_template/repo_task/` 拆出 13 个模块，task.py 收敛为 façade）
- `f7437a2` feat: unify attempt lifecycle with execution_id identity（exact identity `(tid, attempt, execution_id)` 三元组；ledger 重构；monitoring/control/integration 大改）
- `3fdd454` fix: address review findings（escalated 死闩、require_primary_worktree、report 语义、append_integrated 死代码等 9 项）
- `e1ea9d9` refactor: remove dead 'resource' fail class
- `d927df6` refactor: remove model ladder and circuit breaker

已全量审阅：`repo_task/{attempts,control,ledger,monitoring,integration,context,store,git_ops,scheduling,documents,lifecycle,worktrees,cli}.py`、`task.py`、4 个 SKILL.md、4 个 docs_repo/plan、test_dispatch\_{control,integration}.py、test_task\_{modularization,start_flow,archive_dir,save,scheduling,document_validation,unverified}.py。

# 高优先级

## H1. `monitoring.py:compute_reconcile_plan` 运算符优先级陷阱

- **位置**：`scripts/repo_template/repo_task/monitoring.py:569-572`
- **现象**：
    ```python
    if (
        report and report.get("status") == "blocked"
        or effective_tasks.get(tid, {}).get("status") == "blocked"
    ):
    ```
    Python 中 `and` 优先于 `or`，实际等价于 `(report and report.get("status") == "blocked") or (effective_tasks.get(...))`。当前恰好符合意图，但缺括号，后续维护改 `==` 链或加第三项即出错。
- **影响**：低（当前行为正确），但属易触雷的代码气味。
- **建议**：补括号：`if (report and report.get("status") == "blocked") or (effective_tasks.get(tid, {}).get("status") == "blocked"):`。
- **置信度**：高
- **优先级**：高（防回归）

## H2. `append_integrated_batch` 已 integrated 成员的 `merge_sha` 一致性检查在 batch 上下文下不够严格

- **位置**：`scripts/repo_template/repo_task/attempts.py:429-436`
- **现象**：当一个 member 已是 `integrated` 状态时，仅校验 `existing_sha == merge_sha` 后 `continue`；但同一 batch 中**后续 member** 的 terminal 校验仍走 `require_exact_terminal(..., allow_escalated=True)`，如果该 member 处于 escalated 状态且 terminal_status=completed，会被允许 integrated。结合 H3 的 escalate 路径，存在「escalated 但又 integrated」的状态并存可能——`project_attempts` 后写 `integrated` 覆盖 `escalated`（attempts.py:82-87 顺序：`integrated` 在 `escalated` 之后处理会覆盖 state），实际最终 state 为 `integrated`，但 `escalated` 事件字段仍保留。
- **影响**：状态机一致性弱化。escalate 本意是「转人工」，与 integrated（已并入主干）语义冲突；当前代码允许 escalate 后再 integrate，让 attempt 同时具备两份事件。
- **建议**：明确 escalate 是否为终态。若是终态，应在 `append_integrated_batch` 中拒绝 escalated 成员；若允许 escalate→integrate 转换，应在 `project_attempts` 中显式记录转换语义，并在 SKILL/AGENTS.md 文档化。
- **置信度**：中（依赖设计意图；3fdd454 commit message 表述「reconcile 仍不自动输出」暗示 escalate 是手动出口，则应禁止自动 integrate，但手动 integrate 是否允许不明）
- **优先级**：高

## H3. `cmd_integrate` 单 task 路径在 escalated 状态下的行为不对称

- **位置**：`scripts/repo_template/repo_task/integration.py:233-244, 120-133`
- **现象**：`_require_execution_gate(allow_integrated=True)` 默认 `allow_escalated=True`（见 3fdd454 修复），所以单 task integrate 接受 escalated+completed。但 `_resolve_integrate_branch` 第 113-116 行仍要求 `status in ARCHIVED_STATUSES`（done/dropped）——这是 task.md 的 status，与 attempt 的 ledger state 解耦。逻辑上 OK，但导致「escalated attempt + done task.md」这种组合可被手动 integrate，而 escalate 的本意是「需用户裁决」——已经裁决 OK，但缺一道显式确认。
- **影响**：用户在 escalate 后可能误以为还需二次确认，但代码会直接放行 integrate。
- **建议**：在 SKILL.md `task-integrate` 中明确「escalated attempt 的 integrate 视作用户已裁决」；或在 CLI 增 `--force-escalated` 显式 flag。
- **置信度**：中
- **优先级**：高

# 中低优先级

## M1. `repository_fingerprint` 把所有 untracked 文件内容全量读入 SHA256

- **位置**：`scripts/repo_template/repo_task/monitoring.py:61-104`
- **现象**：untracked 列表中的常规文件用 `path.read_bytes()` 全量喂入 digest。对大仓库（node_modules 之类已被 .gitignore 排除还好），但若用户在 worktree 中临时放了大数据文件（如 dump.sql、model.bin），每次 observe 都会读全量。
- **影响**：observe 命令延迟与 IO 放大；silent 检测准确性依赖 fingerprint 变化，对大文件不利。
- **建议**：对超过阈值（如 1 MB）的 untracked 文件只哈希 `(size, mtime_ns)` 而非内容；或在 digest 中加入 size 字段，content 用前 N KB。
- **置信度**：中
- **优先级**：中

## M2. `compute_ps_rows` 对每个 running/terminal attempt 都调 `verify_integrate_ready`

- **位置**：`scripts/repo_template/repo_task/monitoring.py:368, 391`
- **现象**：`verifier=verify_integrate_ready` 默认参数，对每行 running/terminal 都跑一次 `git` + 解析 handoff.json + 解析 task.md，O(N) git 调用。`ps` 默认每 30 分钟静默检测，但 `--silent-minutes 0` 或调度循环中频繁调用会成本高。
- **影响**：ps 命令在大规模并行（10+ task）下变慢；测试中可见 test_dispatch_control 用 fake_verifier 替身。
- **建议**：保留默认真实 verifier，但加缓存层（按 branch sha 缓存 verdict），或在 SKILL 提示用户 `ps` 不宜高频轮询。
- **置信度**：中
- **优先级**：中

## M3. `ledger.py:_with_lock` 在 Windows 用 `msvcrt.locking(fh, LK_LOCK, 1)`

- **位置**：`scripts/repo_template/repo_task/ledger.py:17-22`
- **现象**：`LK_LOCK` 是阻塞锁，1 字节范围。同一进程内多次 `_with_lock` 不互斥（msvcrt.locking 是进程级，fcntl 是描述符级），多线程下不安全。但 task.py 是 CLI 单进程，OK。
- **影响**：低（项目约束为 CLI 单进程）。
- **建议**：注释说明「非线程安全；CLI 单进程独占」，防止后续嵌入多线程 daemon。
- **置信度**：高
- **优先级**：低

## M4. `lifecycle.py:cmd_rewind` 的交互式 `input()` 在 agent 上下文中行为不明

- **位置**：`scripts/repo_template/repo_task/lifecycle.py:520-525`
- **现象**：rewind 丢弃 worktree 改动时 `input()` 等待用户输入；agent 调用时 stdin 通常为空，触发 EOFError→answer=""→abort。设计上是「防 agent 误 rewind」，但 SKILL.md 没有明确说明「rewind 必须由用户在 TTY 中执行」。
- **影响**：agent 执行 rewind 总是 abort，需 `--yes` 才能跳过；SKILL 中应明确。
- **建议**：在 `task-work` / `task-integrate` SKILL 中标注 rewind 的人工触发约束。
- **置信度**：高
- **优先级**：中

## M5. `control.py:cmd_view` 错误信息包装不一致

- **位置**：`scripts/repo_template/repo_task/control.py:82-86`
- **现象**：`if not message.startswith(("invalid_graph:", "invalid_done:"))` 才加前缀，但 `compute_schedule` 抛的所有 TaskDataError 都已自带 `invalid_graph:` 前缀（见 scheduling.py 多处）。这条分支判断冗余但无害。
- **影响**：极低。
- **建议**：删除冗余分支或加注释说明保留意图。
- **置信度**：高
- **优先级**：低

## M6. `store.py:discover_effective_tasks` 对 worktree 中 task.md 解析失败时 raise，整个 ps/reconcile 崩

- **位置**：`scripts/repo_template/repo_task/store.py:271-275`
- **现象**：遍历登记 worktree 时，`scan_tasks_in_worktree(path)` 会读 worktree 内的 task.md；任一 worktree 的 task.md 损坏（如 agent 写一半崩溃）则整个 `ps`/`reconcile`/`view` 全部失败，无法用脚本观察其余 task 状态去救援。
- **影响**：单点故障扩大；故障恢复路径依赖手工改 task.md。
- **建议**：在 ps/reconcile 路径上加 try/except，损坏的 worktree 单独标记并继续；或 `discover_effective_tasks` 增加 `strict=False` 参数。
- **置信度**：中
- **优先级**：中

## M7. `attempts.py:overlapping_attempts` 对「reserved→又 reserved」标记后，原 attempt 若一直未 terminal 会永久阻塞该 tid

- **位置**：`scripts/repo_template/repo_task/attempts.py:121-139`
- **现象**：检测逻辑只把 `attempt_reserved` 时仍 open 的标识为 invalid；invalid 集合永久累积。如果某 attempt reserve 后 agent 崩溃未 terminal，理论上 reserve 会被 `OPEN_STATES` 检查拦下（attempts.py:199-203），不会真的发生重叠。但若手动构造（直接改 ledger 文件）导致重叠，invalid 集合无法清除，需人工删 ledger 行。
- **影响**：低（正常路径不会触发；人工删 ledger 是已知逃生口）。
- **建议**：在 `task-dispatch` SKILL 的「故障恢复」章节列出该逃生口。
- **置信度**：中
- **优先级**：低

## L1. `documents.py:parse_front_matter_text` 行内注释剥离不严谨

- **位置**：`scripts/repo_template/repo_task/documents.py:55-58`
- **现象**：`val.split(" #", 1)[0].rstrip()`——若值本身含 ` #`（如 title="foo # bar"），会被截断。已加引号则不进入该分支。
- **影响**：低（约定 front matter 字符串值都加引号；只 title/note 可能裸值含 `#`）。
- **建议**：注释说明仅对未加引号值生效，建议所有含特殊字符的值都加引号。
- **置信度**：高
- **优先级**：低

## L2. `worktrees.py:link_local_env` glob 模式 `*/.env` 只覆盖一级嵌套

- **位置**：`scripts/repo_template/repo_task/worktrees.py:22`
- **现象**：`sorted(ctx.REPO_ROOT.glob(".env")) + sorted(ctx.REPO_ROOT.glob("*/.env"))`，只匹配根与一级子目录。若项目结构是 `apps/api/.env`，不会被软链。
- **影响**：中（monorepo 场景下 worktree 缺 .env，agent 跑测试失败）。
- **建议**：用 `rglob(".env")` 或显式 `**/.env`，并跳过 `.scratch/`、`node_modules/` 等 gitignore 路径。
- **置信度**：中
- **优先级**：中

## L3. `test_task_modularization.py` 第 53 行硬编码行数区间 100~250

- **位置**：`tests/repo_template/test_task_modularization.py:53`
- **现象**：`assert 100 <= len(task.py splitlines()) <= 250`。task.py 当前作为 façade，行数会随子命令新增缓慢增长；上限 250 在加几个子命令后会破。
- **影响**：未来加子命令时测试无意义失败。
- **建议**：放宽到如 `<= 400`，或改为「task.py 不含业务逻辑，只做 import/re-export/argparse 路由」的结构断言。
- **置信度**：高
- **优先级**：低

## L4. `scheduling.py:_dependency_cycle` 用递归 DFS，深层依赖链可能爆栈

- **位置**：`scripts/repo_template/repo_task/scheduling.py:20-46`
- **现象**：`visit()` 递归无尾递归优化；Python 默认栈深 1000，task 数量大、依赖链深时可能 RecursionError。
- **影响**：极低（task 实际数量 < 100，依赖链短）。
- **建议**：保持递归（可读性优先），仅在 SKILL 注明 task 数量上限。
- **置信度**：高
- **优先级**：低

# 改进建议

## 全局

1. **`repo_task/` 模块化设计扎实**：13 个文件职责清晰，attempts.py（domain）+ ledger.py（storage）+ monitoring.py（observation）分层得当，test 用依赖注入（observer/verifier/mode_probe 默认参数）做替换，是良好实践。
2. **exact identity `(tid, attempt, execution_id)` 的引入解决了原 attempt 复用混乱**，`_require_exact_current` 在每个 mutation 前做 identity 校验，配合 ledger 锁，正确性高。
3. **3fdd454 的 9 项修复质量高**，特别是 escalated 死闩与 `append_integrated` 死代码——后者若保留会导致 silent corruption。
4. **建议补充**：
    - 增加 `attempt doctor` 子命令，自动检测并报告：a) overlapping_attempts 中的 tid；b) 残留 worktree 但无 active task 的情况；c) ledger 行解析失败的行号。当前这些只能靠 ps/reconcile 间接观察。
    - SKILL.md 中补一张「attempt state 机状态转移图」（reserved→running→terminal→integrated/escalated；reserved→running→terminal→escalated→integrated 这种 H2 关注的边），让 reviewer/agent 一致理解。
    - `repo_task/__init__.py` 当前只导出 5 个名字（test 验证），考虑显式 `__all__` 防止意外 re-export。

## 测试

1. `test_dispatch_control.py`（1478 行）+ `test_dispatch_integration.py`（750 行）覆盖了 attempt 全生命周期、链式/扇出拓扑、并发 reserve/binding、escalated 路径、silent_alert、batch integrated 等关键路径，质量高。
2. 建议补：a) `compute_reconcile_plan` 在运算符优先级边界（H1）的回归用例；b) `append_integrated_batch` 中混合 escalated+completed 成员的用例（H2）；c) `discover_effective_tasks` 在 worktree task.md 损坏时的行为（M6）。

# 不确定项

1. **H2/H3 escalate→integrate 是否为合法路径**：commit message 模糊，需项目维护者澄清 escalate 是终态还是中间态。当前代码允许，但语义上 escalate 的「需用户裁决」与 integrate 的「已并入主干」存在张力。
2. **`monitoring.py:dispatch_mode` 的 `restart` vs `resume` 决策**：当前靠 worktree 是否存在 + 是否有未合并 commit 判断；若用户手动 `git worktree remove` 后又想 resume，会被判 restart，丢失 resume 上下文。是否设计如此？
3. **`integration.py:_collect_chain` 的线性 ancestry 验证**：用两两 `merge-base --is-ancestor` 配对检查 + first-parent 连续性，但要求 chain 中所有 task 分支 tip 都互为祖先关系——若两条独立 task 分支同时未合并但都属于 chain 候选（通过 `--is-ancestor sha tail_sha` 都为真），会因无祖先关系 raise。这种「Y 型」未合并分支组合在实际并行 task 中常见，是否应更友好地降级为只取链尾单独 integrate？
4. **`ledger.py` 行级解析失败仅 warning 跳过**：`_read_unlocked` 第 53-58 行对解析失败的行只 stderr 警告并跳过，不抛错。这会让 project_attempts 静默丢失事件，可能在 `require_exact_terminal` 中产生错误「未 reserve」结论。是否应在 strict 模式（如 reconcile）下 fail-fast？

—— 审阅完成。5 个 commit 的核心风险集中在 escalate 与 integrate 的状态机边界（H2/H3），其余多为代码气味与故障恢复路径的鲁棒性。
