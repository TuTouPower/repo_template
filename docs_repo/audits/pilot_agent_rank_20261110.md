# agent_rank 隔离迁移演练

- 日期：2026-11-10T12:25+08:00
- 来源：`repo_template_wt_pilot_agent_rank`，分支 `pilot/agent-rank`，commit `53eabd2093187fc3318e3230b28fcc7c1ce9f93f`
- 目标：只读演练 `/Users/karson/kar/code/agent_rank` 的数据迁移，不修改原仓、不提交原仓。
- 临时副本根：`/var/folders/gy/lhhdcfps1r9426vydlnn1gnc0000gn/T/tmp.3uD6lZQ7hi`（`mktemp -d`，未放在 `~/kar/code`）

## 结论

**暂不建议进入真实迁移。** 新来源脚本 `migrate all --write` 在 `entity-storage` 成功后必然阻断 `state-root`：两步争用同一个 `.git/repo_template/sync_transaction` 路径。需先修模板并补回归用例，再重跑本演练（含回滚与再次迁移）。

除该阻断外，agent_rank 是合适的 pilot：仓小、9 个 done task、无 worktree、无 review、工作区干净，其余单项迁移均成功。

## 原仓基线（只读）

| 项 | 值 |
|---|---|
| HEAD | `1db997807a75f4b38a3f9b8673c07a63904177dc`（main） |
| core.hooksPath | `.repo_template/hooks` |
| worktree | 仅 `/Users/karson/kar/code/agent_rank` |
| git status --short | 空 |
| 工具链 | `988fd06`（`sync_manifest.resolved_commit`），本地脚本无 `migrate` 子命令 |
| entity_schema | 无 `.repo_template/entity_schema.json` |
| 本地 state | `.repo_template/sync_state.json`，last_synced `988fd06`；无 `.git/repo_template/` |
| docs/runtime | `dispatch_ledger.jsonl`、`dispatch_ledger.lock` |
| specs | `docs/specs_index.md` 5 行；`docs/specs/*.md` 5 个，无 front matter；`docs/archive/specs/` 仅 `.gitkeep` |
| reviews | `docs/reviews/`、`docs/archive/reviews/` 均空（仅 `.gitkeep`） |
| decisions | `docs/blueprint/decisions.md` 2 条；无 `docs/decisions` |
| tasks | `docs/tasks/` 空；`docs/archive/tasks/` 9 个 done task（含 task/progress/handoff/review） |
| pending | `docs/pending/todo/` 1 个、`docs/pending/parked/` 2 个 |
| spikes | `docs/spikes/s001_...` active |
| handoff | `docs/handoff.md` 与 `docs/archive/handoff.md` 同时存在 |
| 旧事务 | `.scratch/repo_sync_rollback/` 非空 |
| 其他 | `docs/tasks_index.json`、`docs/archive/tasks_index.json`；无 `tasks_audit.log` |

演练结束后复查原仓：HEAD、hooksPath、worktree 列表、`git status --short` 均与基线一致，未新增 schema 或 common state。

## dry-run

命令：

```bash
python3 /Users/karson/kar/code/repo_template_wt_pilot_agent_rank/repo/.repo_template/scripts/repo_sync.py \
  --consumer "$PILOT_ROOT/dry_run_repo" \
  --source /Users/karson/kar/code/repo_template_wt_pilot_agent_rank \
  --checkout /Users/karson/kar/code/repo_template_wt_pilot_agent_rank \
  migrate all
```

退出码 0。输出摘要：

- `legacy-state` / `workflow-schema` / `pending-ledger` / `findings-ledger` / `conventions` / `reviews` / `retired-hooks`：已是当前格式。
- `entity-storage`：17 项。9 个 task 从 `docs/archive/tasks/` 移至 `docs/tasks/`；3 个 pending 摊平到 `docs/pending/`；s001 原位校验；移除 `docs/archive/handoff.md`；删除两个 `tasks_index.json`；写 `entity_schema.json`。
- `state-root`：3 项。`docs/runtime/dispatch_ledger.jsonl`、`.repo_template/sync_state.json`、`.scratch/repo_sync_rollback` 迁入 `.git/repo_template/`。
- `agents-zones`：1 项；`specs-layout`：5 项；`adr`：2 项。

dry-run 副本保持干净：`git status --short` 为空，未生成 `.git/repo_template/` 或 `entity_schema.json`。

## 写入演练

干净副本 `write_repo` 上按计划逐项执行，每项独立进程：

| 步骤 | 结果 |
|---|---|
| `migrate entity-storage --write` | 成功，17 项 |
| `migrate state-root --write` | **失败**，见下 |
| 手工绕过后再执行 `state-root --write` | 成功，3 项 |
| `migrate specs-layout --write` | 成功，5 项 |
| `migrate reviews --write` | 无迁移项 |
| `migrate adr --write` | 成功，2 项 |
| `migrate agents-zones --write`（计划 6.2 S5 包含，本次追加） | 成功，2 项 |

### 阻断：entity-storage 与 state-root 争用 sync_transaction

`entity-storage --write` 成功后，`.git/repo_template/sync_transaction/` 保留了已 accept 的事务备份；随后 `state-root --write` 报错：

```text
错误: 目标 common 状态目录已有文件，拒绝覆盖：
- <copy>/.git/repo_template/sync_transaction
```

根因：

- `template_sync/entity_storage_migration.py:1016` 使用 `state_transaction()`；
- `repo_task/file_transaction.py:59` 将该事务备份放到 Git common dir 的 `sync_transaction/`；
- `template_sync/state_root_migration.py:27` 把旧 `.scratch/repo_sync_rollback` 也映射到同一 `sync_transaction`；
- `state_root_migration.py:136-143` 对非空目标直接拒绝。

该问题不是 dry-run 特有的：干净副本上执行 `migrate all --write`，在 `entity-storage: 落盘完成 17 项` 后同样以同一错误退出 1，已写入的 entity-storage 变更保留。事后单独对 post-entity-storage 副本跑 `state-root` dry-run 也失败。

临时副本中的绕过（仅用于继续演练，不改产品代码）：

```bash
mv .git/repo_template/sync_transaction \
   .git/repo_template/sync_transaction.entity-storage-backup
```

保留 entity-storage 回滚备份后，`state-root --write` 成功。

### 迁移结果摘要

- `docs/tasks/` 9 个 task；`docs/pending/` 3 个文件；`docs/spikes/s001...` 增加 front matter。
- `.repo_template/entity_schema.json` 写入 `{"version": 1}`。
- `docs/specs/` 5 个 spec 写入 front matter；`docs/specs_index.md` 删除。
- `docs/blueprint/decisions/` 生成 2 个 ADR，`decisions.md` 删除。
- `AGENTS.md` 拆出模板区标记 `<!-- repo-template:begin/end -->`。
- 最终 staged diff 统计：40 个 `R100`（9 个 task 目录的纯移动）、3 个 pending `R083/R087/R090`（工具预期重写）、3 A / 9 D / 7 M。未发现 task 正文被格式化改写。

## 迁移后验证

| 检查 | 结果 |
|---|---|
| `repo_sync.py status` | 退出 0；迁移待办为「无」。仍有 `.repo_template/` hard sync 差异与 `AGENTS.md`/`.gitignore` 裁定差异，属未 apply 的预期状态 |
| 本地旧 `task.py list` | 9 个 done，与迁移前一致 |
| 本地旧 `task.py effective-status` | 9 个 done，来源 main |
| 新来源 `task.py list/effective-status/view` | 通过临时 harness 在副本内运行，9 个 done；view 为 done=9 |
| contract 测试 | `pytest .repo_template/tests -q -m contract -p no:cacheprovider`：257 passed, 1 skipped，退出 0 |
| `healthcheck` | 失败：`task 模板缺“关联 spec”行`。原因是工具链仍是 `988fd06`，未 apply 新模板；迁移完成、apply 后需复验 |
| 回滚 / 再次迁移 | 未执行，见「未覆盖」 |

说明：系统 `python3` 无 pytest 模块，测试使用 PATH 上的 uv 工具 `pytest 9.2.0`；副本内 `pytest` 套件仍是消费仓 `988fd06` 版本，不是新来源契约套件。新来源 `task.py` CLI 按脚本所在工作区定位仓库，不能直接对 `--consumer` 副本运行；验证时把新 `scripts/` 临时复制到副本 `.repo_template/.pilot_scripts/`，运行后删除。

## 残留与需处理项

1. **阻断项**：修 `state-root` 与 `entity-storage` 的 `sync_transaction` 冲突，并补「fresh `988fd06` fixture → entity-storage → state-root」回归用例；修好前不得对真实消费仓执行 `migrate all --write`。
2. `docs/runtime/dispatch_ledger.lock` 未被 state-root 迁移，`docs/runtime/` 目录残留。应确认该锁是否仍需迁移或清理。
3. `docs/archive/reviews/.gitkeep` 与 `docs/archive/specs/.gitkeep` 使 `docs/archive/` 未完全清空；需确认消费仓是否接受占位目录残留。
4. `AGENTS.md` 项目区仍保留迁移前路径：`docs/specs_index.md`、`docs/pending/{todo,parked}/`、`docs/archive/pending/`。agents-zones 只拆区，不改项目区，需消费仓自行更新。
5. 未执行 `repo_sync.py apply`，因此工具链、任务模板、hooks、skills 仍是旧版；`healthcheck` 失败属预期，真实迁移提交前必须完成 apply 并复验。
6. 未做回滚与再次迁移。计划 6.3 要求 pilot 实际验证「迁移 → 逆序全量回滚 → 旧工具链读取状态一致」，本次只覆盖迁移侧。

## 是否建议进入真实迁移

**否，当前先修模板阻断。** 修复后建议：

1. 重跑本演练，覆盖回滚与再次迁移；
2. 真实迁移时先备份，再按 entity-storage → 单独提交 → state-root → S5/S6 顺序执行；
3. 迁移 commit 用 `--no-verify` 并按计划核对重命名摘要；
4. apply 新工具链后跑新 contract、`healthcheck`、`task.py list/view/effective-status`，再判定是否铺开其它仓。
