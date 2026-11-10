# 实施审阅处置（074de1e..1b090f6）

评估对象：`.scratch/review_impl_20261110.md`。本文件只做逐条核对与结论，不修改代码。消费仓仅只读核对。

## 结论总表

| 编号 | 结论 | 理由 | 涉及文件 |
|---|---|---|---|
| A1 | 采纳 | `agents-zones`、`specs-layout`、`reviews`、`adr` 在 `_STEPS` 中确实直接写盘，未复用 `_transactional`；specs 还是先写后校验 slug，失败会留下半迁移现场。 | `repo/.repo_template/scripts/template_sync/migrate.py`、`specs_migration.py`、`agents_zone_migration.py`、`reviews_migration.py`、`adr_migration.py` |
| A2 | 采纳 | `agents_zone_migration` 只按新模板路径集合分类，旧模板已删路径（`docs/specs_index.md`、`docs/pending/{todo,parked}/`、`docs/archive/pending/`）会被误留为项目行；表外文字也被直接丢弃，确实会制造过时规则并丢内容。 | `repo/.repo_template/scripts/template_sync/agents_zone_migration.py`、`git show 988fd06:repo/AGENTS.md` |
| A3 | 采纳 | `adr_migration._entries` 把第一个 `## NNN` 前的无编号 H2 放入未使用的 `preface` 后丢弃；编号条目之后的未知 H2 并入上一条 ADR，均未按要求停止。CBrowser 的 t024 条目可复现。 | `repo/.repo_template/scripts/template_sync/adr_migration.py`、`/Users/karson/kar/code/CBrowser/docs/blueprint/decisions.md` |
| A4 | 采纳 | `state_root_migration._sources` 只搬已知数据文件，不处理 `docs/runtime/dispatch_ledger.lock`；新 `.gitignore` 又删除 `docs/runtime/` 规则，残留锁会变未跟踪文件。 | `repo/.repo_template/scripts/template_sync/state_root_migration.py`、`repo/.gitignore` |
| B1 | 采纳 | 复制阶段失败时 `moved` 中的源文件尚未删除，回滚先恢复再 `copytree(backup, source)` 会因源仍存在抛 `FileExistsError`；当前失败项也不在 `moved`，残缺目标不会清理。 | `repo/.repo_template/scripts/template_sync/state_root_migration.py` |
| B2 | 采纳 | 旧 `.scratch/repo_sync_rollback` 存在且为 `applied` 时，新 `migration_transaction()` 会复用它并在 `begin` 失败；新 `transaction status` 只看 common dir，确实看不到旧事务。 | `repo/.repo_template/scripts/template_sync/common.py`、`entity_storage_migration.py`、`transaction.py` |
| B3 | 采纳 | 未设置 `core.hooksPath` 时 `run_healthcheck` 直接追加 `_check_hook_snippet()`，成功时返回空字符串也被当成问题，导致误报；此时应提示 `install-hooks`。 | `repo/.repo_template/scripts/template_sync/healthcheck.py` |
| B4 | 采纳 | `task.py privacy-audit` 的排除项来自当前工作区 `scan_tasks` / `retired_spec_paths`，不是 `--ref` 对应 tree；审计历史或其它分支时范围会不一致。 | `repo/.repo_template/scripts/repo_task/cli.py`、`privacy.py`、`repo_task/specs.py` |
| B5 | 采纳 | `decision_log.md` 已有 L64–L69，新增方案条目又复用同号，编号重复；应改为 L72–L77。 | `docs_repo/decision_log.md` |
| B6 | 已关闭 | 用户裁决：工厂只保证 `migrate all → apply` 路径正确，不规定消费仓铺开流程；本轮删除计划中的消费仓铺开内容。 | `docs_repo/plans/plan_state_ownership_and_workflow.md`、`docs_repo/audits/pilot_agent_rank_20261110.md` |
| C1 | 采纳 | `reviews` 迁移的 dry-run 只列目录，不列将被删的 `_meta/` 和未跟踪文件；`rmtree` 会连带删除已跟踪 review 目录内未被忽略的未跟踪文件。 | `repo/.repo_template/scripts/template_sync/reviews_migration.py`、`docs_repo/plans/plan_state_ownership_and_workflow.md` 4.4 |
| C2 | 采纳 | `RepoContext` 的 `state_dir`/`ledger_path`/`statusline_path` 等属性每次访问都重新执行 git；状态行路径解析会重复触发。 | `repo/.repo_template/scripts/repo_task/context.py`、`statusline.py` |
| C3 | 采纳 | `install-hooks` 的 lefthook 片段缺 `commit-msg`；已有其它 `hooksPath` 时返回 0 且 bootstrap 仍打印“完成”，用户会误以为 hooks 已安装。 | `repo/.repo_template/scripts/template_sync/hooks.py`、`export_template.py` |
| C4 | 采纳 | 旧版状态行文件在 `.scratch/statusline_workdir.jsonl`，`state_root_migration._sources` 只在 `docs/runtime/` 找，迁移分支无效。 | `repo/.repo_template/scripts/template_sync/state_root_migration.py`、`repo_task/statusline.py` |
| C5a | 采纳 | `AGENTS.md` 写 `decisions/<日期>_<slug>.md`，与计划/ADR 实现的 `YYYYMMDD_<slug>.md` 不一致；统一为明确格式。 | `repo/AGENTS.md`、`docs_repo/plans/plan_state_ownership_and_workflow.md` 3.4/4.3 |
| C5b | 拒绝 | `progress.md` 的 `- specs_index: not_applicable:` 是计划明确保留的豁免关键字；改名会破坏既有 task 与 finish 契约，收益只是字面整洁，应保留并在文档说明其历史含义。 | `repo/.repo_template/scripts/repo_task/finalization.py`、`repo/.repo_template/docs/task_template/progress.md`、计划 3.2 |


## 说明

- 本次评估未修改生产代码，未写入任何消费仓；仅新增 `review.md` 与 `adoption.md`。
- 审阅方标记为“已确认无问题”的项不再重复处置，未发现需要推翻的结论。
