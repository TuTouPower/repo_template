# 实施审阅意见（基线 1b090f6）

审阅对象：`docs_repo/plans/plan_state_ownership_and_workflow.md` 的工厂实施（`074de1e..1b090f6`）。
证据来自读代码与对本机 12 个消费仓的只读核对。全量 `1529 passed, 1 skipped` 已通过，以下问题都在现有 fixture 之外。
编号用于处置回复，请逐条给出：采纳 / 拒绝 / 需用户决策，附理由；结论前先自行核对代码与证据。

## A 阻断级

### A1 新增四个迁移无事务保护
- `template_sync/migrate.py` 的 `_STEPS` 中 `agents-zones`、`specs-layout`、`reviews`、`adr` 直接写文件、`rmtree`、`unlink`，没有走 `_transactional` / `FileTransaction`。
- `sync.md:101` 声称“每项拥有独立文件事务，失败只恢复当前项”，与实现不符。
- 可触发场景：omni_panel `docs/specs/` 有 4 个 kebab-case 文件（`ai-cli-token-stats.md`、`connector-cpa.md`、`platform-services.md`、`ui-views.md`），且不在 `specs_index.md` 中，索引解析通过；`migrate_specs_layout` 随后写全部 front matter、移回 archive spec、删除索引，最后 `load_specs` 才因 slug 非法失败，留下半迁移状态。
- 建议：四项纳入文件事务；specs-layout 在任何写入前先校验全部文件名与索引。

### A2 AGENTS 分区迁移丢内容并保留过时规则
`agents_zone_migration.py` 只用新模板路径集合分类。对照 `988fd06` 模板（消费仓当前版本）逐行比对：
- 旧模板有、新模板已删的行（`docs/specs_index.md`、`docs/pending/{todo,parked}/pNNN_{slug}.md`、`docs/archive/pending/pNNN_{slug}.md`）被当成项目行保留到项目区，12 个仓都会留下与新布局矛盾的规则。pilot 记为“消费仓自行更新”，实为分类错误。
- 消费仓改写过的模板行被丢弃且不报告：kkmail（`src/`、`scripts/`、`docs/reviews/review_*/`）、capture_all 11 行、omni_panel 10 行、agent_rank 4 行（`.repo_template/`、`artifacts/`、`docs/handoff.md`、`scripts/`）。
- 该节内表格以外文字被丢弃：capture_all、omni_caption 在该节内各有 17 行 `## 开发工作流`（含 skill 表）。
- 建议：以旧模板行为基准分类。与旧模板一致的行替换；旧模板有但被改写的行、未知行、表外文字保留到项目区并在报告中列出，供 agent 复核。

### A3 ADR 迁移静默丢弃无编号条目
- CBrowser `docs/blueprint/decisions.md` 第 7 行 `## 产品历史勘误入口与 ADR 传播约定（t024）` 是真实规则，位于第一条 `## NNN` 之前，`_entries` 把它当前言丢弃，随后 `decisions.md` 被删除。
- 编号条目之后出现的无编号 `##` 会被并进上一条 ADR，不报错。
- 计划要求：围栏外不匹配 `## NNN` 的 H2 一律停止；前言列入报告。

### A4 残留 ledger 锁会被提交
- `state-root` 不处理 `docs/runtime/dispatch_ledger.lock`（pilot 两次观察到残留）。
- 新模板 `.gitignore` 托管块删除了 `docs/runtime/` 规则，迁移后该锁成为未跟踪文件，工作区变脏；按计划用 `git add -A` 暂存会把它提交入库。
- 建议：迁移清理已知锁文件；`docs/runtime/` 有其它未知残留时停止并报告。

## B 中等

- B1 `state_root_migration.py` 回滚缺陷：复制阶段失败时源仍在，恢复执行 `copytree(backup, source)` 抛 `FileExistsError`，误报“恢复失败”；失败时正在复制的那一项不在 `moved` 中，残缺目标不清理，重跑会被“目标已有文件”拒绝。迁移期间未持控制面锁。
- B2 pilot 遗留阻断未修：旧 `.scratch/repo_sync_rollback` 为 `applied` 时迁移失败，而新 `transaction status` 看不到旧目录。需要迁移预检识别它，并提示用消费仓旧脚本 accept 或 rollback。
- B3 `healthcheck.py`：未设 `core.hooksPath` 时直接把 `_check_hook_snippet()` 的返回值加入问题列表，空字符串也算问题，导致误报失败；此时应提示运行 `install-hooks`，而不是 husky 片段。
- B4 `privacy-audit` 的排除列表（done/dropped task、retired spec）按当前工作区计算，不是按 `--ref` 的树计算；审计历史版本或其它分支时两者不一致。
- B5 `docs_repo/decision_log.md` 新增的 L64–L69 与原有 L64–L69 重复，应改为 L72–L77。
- B6 铺开流程两种说法：计划 6.2 是 entity-storage 单独 `--no-verify` 提交、state-root 单独进程；产品文档与 pilot 实际是 `migrate all --write` → apply → 单个 commit。pilot 证明后者可行，且整体在新 hook 下提交，不存在旧 hook 问题。建议统一为后者并改写计划 6.2。pilot 仍未做“迁移 → 逆序回滚 → 再迁移”，计划 6.3 验收未达成。

## C 低

- C1 reviews 迁移 dry-run 未列出将被删除的 `_meta/` 与其它未跟踪内容（计划要求列出）；`rmtree` 会删掉已跟踪 review 目录内未被忽略的未跟踪文件；多个无日期目录时只报第一个。
- C2 `RepoContext.state_dir` / `ledger_path` 等属性每次访问都调 git 子进程，没有缓存；状态行每次渲染调 3 次。
- C3 `install-hooks` 输出的片段缺 lefthook commit-msg；未安装成功时也返回 0，bootstrap 仍提示完成。
- C4 `state_root_migration._sources` 在 `docs/runtime/` 找 statusline 文件，但旧版 statusline 在 `.scratch/` 下，该分支无效。
- C5 文案：`AGENTS.md` 写 `decisions/<日期>_<slug>.md`，计划定为 `YYYYMMDD_<slug>.md`；`progress.md` 豁免关键字仍为 `specs_index:`，名称已过时。

## 已确认无问题（不需处置）

隐私比较器重命名识别与 `-z` 解析；review hook（新增 HEAD 中不存在的顶层目录才检查、按 index 计数、合并跳过、引用保护）；pre-commit `AM` 过滤；`preflight --creation` 的“关联 spec”校验；task-integrate 公开、task-combine 改名及 registry、软链、OpenCode 命令同步；迁移前事务沿用旧 `.scratch`（`791dcbb`）。
