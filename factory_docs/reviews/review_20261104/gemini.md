# 647d7013..HEAD 综合 Diff 审阅报告（gemini）

- 审阅范围：`647d70133b63683bf0ae0ccc4a53a4db498c4178..d3dd4e0`（宏观改造全波次合并 diff）
- 审阅时间：`2026-11-04T12:00:00+08:00`

---

## 一、严重缺陷（门禁绕过 / 状态机死锁 / 同步数据不一致）

### 1. `integrate-chain` 链连续性校验退化，可放行未受审的旁路/中间 commit 或丢弃前驱成员

- **位置**：`repo/.repo_template/scripts/repo_task/integration.py:816-824`
- **现象与根因**：
    - 改前检查 `current[2]^1 == previous[2]`，在「单 task 单执行 commit」规则下等价于「后继实现 commit 的 first parent（`base_sha`）必须精确等于前驱分支 tip」。
    - 改后（D2 允许追加证据 commit）将检查替换为 `first_parent_segment(current[2], previous[2])`。但 `first_parent_segment`（`monitoring.py:68-89`）仅验证 `previous[2]` 位于 `current[2]` 的 first-parent 祖先链上，**不再验证 `previous[2]` 是否就是 `current` 实现 commit 的 first parent（即 `current` 的 `base_sha` / `diff_anchor`）**。
    - **漏洞 A（夹带未审 commit 合入主干）**：若 `previous` 分支 tip 与 `current` 的 `base_sha` 之间夹了任意单亲 commit `X`（`previous_tip <- X <- current_base(=X) <- current_tip`），`verify_integrate_ready(previous)` 与 `verify_integrate_ready(current)` 各自只检查自身 `[base_sha..tip]` 段均通过，`_collect_chain` 的 `first_parent_segment(current_tip, previous_tip)` 也通过，`_validate_merge_staged_scope` 比对 `merge_base(HEAD, tail)..tail` 同样放行——导致未被任何 task 的 `handoff.json` 与 review 证据覆盖的 commit `X` 被合入主干。
    - **漏洞 B（链头悬空或前驱追加证据 commit 后脱链）**：`_collect_chain`（`integration.py:795`）仅收集属于 `tail_sha` 祖先的本地 task 分支，且不校验链头 `candidates[0]` 的 `base_sha` 是否已包含在 `base_ref`（`HEAD`）中。若前驱 `t001` 在后继 `t002` 启动后被违规追加了证据 commit（此时 `t001` 新 tip 不再是 `t002` 的祖先），`t001` 会被 `_collect_chain` 静默排除，`candidates` 只剩 `[t002]`。此时运行 `integrate-chain t002` 会把 `t001` 的实现 commit 连同 `t002` 一起合入 `main`，但既不校验 `t001` 的门禁、也不写 `t001` 的 `integrated` 事件、更不会把 `docs/tasks/t001_*` 移入 `docs/archive/tasks/`，导致主干留下未归档的 `status: done` 活跃目录。
- **修复建议**：
    - 在 `_collect_chain` / `_preflight_chain` 中：
        1. 校验链头 `candidates[0]` 的 `base_sha`（或 `diff_anchor`）是 `base_ref` 的祖先（`_is_ancestor(head_base_sha, base_ref)`）；
        2. 校验每对相邻 `(previous, current)` 中，`current` 的 `base_sha` / `diff_anchor`（即 `execution_commits` 返回的实现 commit 的 first parent）必须精确等于 `previous[2]`。

---

### 2. `finish` 原位保留 `docs/tasks/` + 内置 `git commit` 与 `pre-commit` hook 联动，导致「`finish=OK` 却产出 `review_scope=stale` 的损坏提交」

- **位置**：
    - `repo/.repo_template/scripts/repo_task/lifecycle.py:696-762`
    - `repo/.repo_template/hooks/pre-commit:24-85`
    - `repo/.repo_template/scripts/md_format.py:28`
- **现象与根因**：
    - 旧版 `finish` 先将任务目录移入 `docs/archive/tasks/`，再由外部执行 `git commit`。由于 `docs/archive/` 在 `md_format.py` 的 `BLACKLIST_PREFIXES = ("docs/archive/", ".scratch/")` 中，`.repo_template/hooks/pre-commit` 不会格式化已归档的 task 目录。
    - 新版（D1+C3）改为：`finish` 将目录留在 `docs/tasks/{tid}_{slug}/`（不在 `BLACKLIST_PREFIXES` 黑名单内），先在 `_finish_prechecks` 中校验工作区的 `review_scope_fingerprint`，通过后执行 `git add -A -- docs/tasks/...` 与 `git commit -F`。
    - 当消费仓按 `repo/README.md` 启用了 `core.hooksPath = .repo_template/hooks` 时，`finish` 内部的 `git commit` 会自动触发 `pre-commit`，对所有 staged `.md`（含纳入 `review_scope_fingerprint` 的 `docs/tasks/{tid}_{slug}/task.md`、`docs/specs/*.md` 等）运行 `md_kx` 并重新 `git add` 进本次实现 commit。
    - 一旦 `task.md`（例如实施者在勾选 `- [x] AC-NNN` 后未跑 `md_kx`）或任何已暂存的交付 `.md` 被 `pre-commit` 格式化改动哪怕 1 个字节：
        1. `_finish_prechecks` 在 `git commit` 之前检查的是格式化前的工作区文件，与 `review_*.md` 中的 `reviewed_scope` 一致，预检放行；
        2. `git commit` 过程中 `pre-commit` 改写了 `task.md` / 交付 `.md` 并入库，`git commit` 返回 0，`finish` 打印 `finish=OK`；
        3. 回到主仓运行 `task.py run-close {tid} --owner-id ... --result done` 时，`verify_integrate_ready` 对提交后的实现 commit 计算 `review_scope_fingerprint(..., ref=tip)`，因提交的 `.md` blob 已被 `pre-commit` 改写，当场报 `review_scope=stale` 拒绝收尾。
    - 此外，`docs/tasks/{tid}_{slug}/review_*.md` 报告在 `finish` 提交和追加证据 commit 时也会被 `pre-commit` 调用 `md_kx` 改写。
- **修复建议**：
    - 在 `_finish_prechecks` 计算 `review_scope_fingerprint` 之前，先检查将被提交的 `.md` 是否满足 `md_format.py --check`（不满足则在零写入阶段直接拒绝并提示先格式化再重审/收尾），或将 `review_*.md` / 渲染的 review prompt 纳入 `md_format.py` 豁免。

---

### 3. 新主路径 `run-close --result done` 下 review 门禁失败时，`recovery` 无法判定为 `evidence_repair`（恢复死循环）

- **位置**：
    - `repo/.repo_template/scripts/repo_task/control.py:395-404, 535-547`
    - `repo/.repo_template/scripts/repo_task/recovery.py:108-120, 149`
    - `repo/.repo_template/docs/recovery.md:32`
- **现象与根因**：
    - `cmd_run_close` 在写入 `terminal_attempt` **之前**先执行 `require_report_done_ready(tid, attempt, execution_id)`（`control.py:541`）。
    - 若分支 tip 的 review 门禁未通过（如 `review_scope=stale`），`run-close` 在写 `terminal` 前即退出并报错：
      `report=done 拒绝：...；先补审证据并追加证据 commit（见 recovery），仍卡在 committed_unreported`。
      此时 ledger 中该 attempt 的 `state` 仍是 `"running"`（尚未写 `attempt_terminal` 事件）。
    - 然而 `recovery.py:108` 进入 `evidence_repair` 分支的条件写死为：
      `if record.get("state") == "terminal" and record.get("terminal_status") == "completed":`
      （沿用了旧版手动分步先调 `attempt terminal`、后调 `attempt report` 才触发门禁的假设）。
    - 结果：在 `run-close` 主路径下被拒后运行 `task.py recovery {tid}`，因 `record["state"] == "running"`，108 行条件为 False，跳过 `evidence_repair` 直接落入 116 行返回 `phase="committed_unreported"`（action 为 `"验证最终提交和 review gate；按原 identity 补缺失的 terminal/report，不重复写已有事件"`）；按该提示重跑 `run-close` 又被 `require_report_done_ready` 拦截并指向 `recovery`，形成死循环。
    - 同时，`_evidence_repair_action`（`recovery.py:149`）与 `recovery.md:32` 的收尾指引写为「提交后 worktree 必须干净，再按原 identity ... exact cleanup」。若在 `terminal` 或 `report` 尚未写入时进入 `evidence_repair`，直接运行 `cleanup-worktree` 会因缺 `terminal=completed` 或 `report=done` 报错；应指引运行 `run-close --result done`（或补齐 `terminal`/`report` 后再 `cleanup-worktree`）。
- **修复建议**：
    - `recovery.py:108` 在已提交实现 commit 且 `record["state"] == "running"` 或 `(record["state"] == "terminal" and record["terminal_status"] == "completed")` 时均检查 `verify_integrate_ready` 的 review gate 分支，返回 `evidence_repair`；
    - 更新 `_evidence_repair_action` 与 `recovery.md:32` 的后续收尾命令说明（使用 `run-close --result done` 或补齐 `terminal`/`report` + `cleanup-worktree`）。

---

### 4. `template_sync` 强制迁移任务文档后，未重建或更新消费仓 `docs/tasks_index.json` 与 `docs/archive/tasks_index.json`

- **位置**：
    - `repo/.repo_template/scripts/template_sync/workflow_migration.py:166-167, 440-487`
    - `repo/.repo_template/scripts/template_sync/apply.py:70-71, 124-126`
- **现象与根因**：
    - `force_migrate_workflow_tasks` 将消费仓 `docs/tasks/` 与 `docs/archive/tasks/` 下的 `spec.md`/`task.md` 或 `note.md` 迁移为 `progress.md`，并将旧 `status: "blocked"` 改为 `status: "active"`。
    - 消费仓的 `docs/tasks_index.json` 与 `docs/archive/tasks_index.json` 不在 `HARD_SYNC_FILES` 中，而 `apply` 既未将这两个路径纳入 `FileTransaction` 的 `paths`（`apply.py:124`），也未在迁移后重建索引。
    - 导致消费仓同步完成后，两个 `tasks_index.json` 的 `"authority"` 字段仍停留在 `"docs/.../note.md front matter"`（或 `"task.md front matter"`），且原 `blocked` 任务的索引记录仍为 `"status": "blocked"`，与迁移后的 `progress.md` 不一致。
- **修复建议**：
    - 将 `docs/tasks_index.json` 与 `docs/archive/tasks_index.json` 纳入 `apply.py` 的事务保护路径；当 `force_migrate_workflow_tasks` 存在实际迁移写入（或索引 `authority` 为旧文件名）时，按消费仓目录重建这两个索引文件并加入 `changed`。

---

## 二、中度缺陷 / 行为与诊断缺口

### 5. `task.py goal <tids...>` 显式冻结队列未校验依赖顺序，可冻结出 `plan --serial` 立即拒绝的非法队列

- **位置**：`repo/.repo_template/scripts/repo_task/goal.py:60-82`
- **现象与根因**：
    - `plan.py` 的 `compute_queue_topology`（478-486 行）在 `plan --serial` 中强制校验队列内依赖必须排在依赖者之前（违反时抛 `invalid_queue: 依赖 {src} 必须排在依赖者 {dst} 之前`）。
    - 但 `goal.py` 的 `_compute_queue` 在接收显式 `args.tids` 时，仅检查了 tid 格式、重复、存在性与非终态，**未调用 `build_execution_plan(mode="serial", queue=queue)` 校验依赖顺序和图合法性**。
    - 若 `t002` 依赖 `t001`，运行 `task.py goal t002 t001` 会成功冻结队列，但随后按 `task-run` 第一步运行 `task.py plan --serial --json t002 t001` 时立即报 `plan=FAIL：invalid_queue` 失败。
    - 此外，`goal.py:76` 的报错文案 `f"{tid} 已归档（{status}）；done/dropped 永不入队"` 仍沿用旧口径把所有 `done` 称为「已归档」（D1 后未集成的 `done` 位于 `docs/tasks/`，尚未归档；对比 `attempts.py:278` 已改为「已是终态」）。
- **修复建议**：
    - `_compute_queue` 在显式 `args.tids` 分支中也通过 `build_execution_plan(mode="serial", queue=queue)` 校验队列拓扑；将 76 行文案中的「已归档」改为「已是终态」。

---

### 6. `finish` 前置检查在 review 门禁失败时漏输出 `ac_review` 与 `ac_review_detail`

- **位置**：`repo/.repo_template/scripts/repo_task/lifecycle.py:696-705`
- **现象与根因**：
    - `monitoring.py:554-560`（`verify_integrate_ready`）在 review 门禁未通过时输出了 `ac_review` 与 `ac_review_detail`。
    - 但 `lifecycle.py:696-705`（`_finish_prechecks`）在 `review["overall"] != "PASS"` 时仅输出了 `overall`、`review_scope`、`missing_disposition`、`next_action`。
    - 当因报告含 `TICK-VOID` 导致 `overall=FAIL` 时，`finish` 仅输出 `overall=FAIL review_scope=ok missing_disposition=- next_action=fix_or_block`，看不到是 `ac_review=void` 及具体哪份报告、哪个 AC 触发了失败。
- **修复建议**：
    - 在 `lifecycle.py:697-703` 的报错字符串中补齐 `ac_review={review['ac_review']} ac_review_detail={review['ac_review_detail'] or '-'}`。

---

### 7. `finish` 跳过了对归档目标目录 `docs/archive/tasks/{tid}_{slug}` 是否已占用的前置检查

- **位置**：`repo/.repo_template/scripts/repo_task/lifecycle.py:536-579`
- **现象与根因**：
    - `_close_task` 中 `archive = status != "done"`，`dst = _require_archive_target_free(fm) if archive else None`。
    - 改后 `finish`（`status == "done"`）不再调用 `_require_archive_target_free(fm)`。若 `docs/archive/tasks/{tid}_{slug}` 已存在异常残留目录（如空目录或非 task 残留文件，未被 `scan_tasks` 检出），`finish` 会成功提交，直到 `integrate` 阶段才在 `_stage_archive_moves` 报数据冲突。
- **修复建议**：
    - 在 `_check_done_ready`（或 `_close_task` 中无论 `archive` 与否）保留 `_require_archive_target_free(fm)` 检查。

---

### 8. `factory_tests/test_repo_sync.py` 残留一处直接改写进程级 `subprocess.run` 的测试

- **位置**：`factory_tests/test_repo_sync.py:1845-1856`（`test_git_timeout_is_reported_as_sync_error`）
- **现象与根因**：
    - WP11（`9769df2`）将测试中直接改写全局模块属性的替身统一改为 `monkeypatch` 隔离到被测模块以支持 xdist 并行，但此处仍保留了 `old = subprocess.run; subprocess.run = timeout`。
- **修复建议**：
    - 改为接收 `monkeypatch` 参数并使用 `monkeypatch.setattr("template_sync.common.subprocess", ...)`。

---

## 三、文档、注释与用词不精准

### 9. `task-preflight/SKILL.md` 残留第一代文件名术语「读取 spec、task」

- **位置**：`repo/.repo_template/skills/task-preflight/SKILL.md:22`
- **原文**：`4. 读取 spec、task 和 .env.example，只列 Agent 无法替代的缺口...`
- **问题**：「spec、task」是 `35d298b` 改名前（`spec.md` + `task.md`）的旧称，现行应为「读取 `task.md`、`progress.md` 和 `.env.example`」（与 `task-work/SKILL.md:40` 的「读取 task.md、progress.md」一致）。

---

### 10. `.agents/skills/issue-handler/SKILL.md` 验证命令与耗时描述未随 WP11 同步

- **位置**：`.agents/skills/issue-handler/SKILL.md:36-40`
- **原文**：
    ```bash
    pytest repo/.repo_template/tests factory_tests -q -p no:cacheprovider --basetemp "$(mktemp -d)"
    ```
    及「全量耗时 10 分钟以上」。
- **问题**：工厂根 `AGENTS.md:17` 已在 WP11 后更新为带 `-n auto` 的并行命令，此处漏加 `-n auto`。

---

### 11. `test_check_review_status.py` 注释与 `4438f85` 修复后的实现事实相反

- **位置**：`repo/.repo_template/tests/test_check_review_status.py:857-858`
- **原文**：`"""目录里没有 task.md（integrate 门禁的临时目录）无法比对 AC 集合，但 VOID 与缺小节仍拦截。"""`
- **问题**：commit `4438f85` 已在 `monitoring.py:544` 的 `verify_integrate_ready` 临时目录中写入了 `task.md`（且缺 `task.md` 直接报错），「integrate 门禁的临时目录没有 task.md」已不再成立。

---

### 12. `docs_repo/decision_log.md` 编号断号（缺 L49 与 D-10 说明）及导言引用不全

- **位置**：`docs_repo/decision_log.md:7, 50-65`
- **问题**：
    - 第 7 行写「追加宏观方案 D-01–D-14 对应的 L40–L52（D-11 撤销）」，但表格从 `L48`（对应 D-09）直接跳到 `L50`（对应 D-12），缺少 `L49`，且未像 D-11 一样说明 D-10（dogfood 消费仓，于 `d43deba` 移除）已撤销/移除。
    - 第 7 行末尾写「现行行为以 `repo/AGENTS.md`、`repo/.repo_template/docs/usage.md`、`repo/.repo_template/docs/architecture.md` 和 `task.py --help` 为准」，在 `usage.md` 拆分为 `usage.md` / `recovery.md` / `sync.md` 后未补列 `recovery.md` 与 `sync.md`。

---

### 13. `docs_repo/plans/plan_macro_overhaul_2026_11.md` 内部残留未同步描述

- **位置**：`docs_repo/plans/plan_macro_overhaul_2026_11.md:122, 222`
- **问题**：
    - 第 122 行（C3）：命令签名写为 `task.py finish {tid} --message-file <path>`，实际实现还必填 `--owner-id <id>`。
    - 第 222 行（G1）：写为 `factory_tests/conftest.py 保留 xdist 守卫`，但同文 H3（第 237 行）及实际 `factory_tests/conftest.py` 已移除 xdist 守卫。
