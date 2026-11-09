# scratch 默认清理与在用草稿保护方案

> 编写时间：2026-11-10T02:12:40+08:00。
>
> 文档性质：已实施方案。决策状态：用户已批准并要求按本方案执行（2026-11-10）；「3 天期限不适用于未结束事务」按方案原文落地。实现状态：已实施；验证状态：工厂全量通过（1543 passed, 1 skipped，2026-11-10）。目标版本待定。
>
> 适用范围：模板产物 `repo/` 的 `repo_cleanup.py`、事务与锁原语、`pending.py`、`repo-cleanup` 与 `template-issue-report` skill，以及工厂 `issue-handler` skill。面向模板工具链维护者。裁决状态以[裁决总账](../decision_log.md)（L68）为准。

## 1. 目标与范围

`repo-cleanup` 默认清理消费仓主仓 `.scratch/`。只保护两类内容：未结束的文件事务，以及有主人且 3 天内修改过的草稿。其余内容按一次性草稿清理；需要长期保留的文件放 `local/`（L66）。

不在范围内：

- task worktree 自己的 `.scratch/`：随 `run-close` 删除 worktree 一起消失，`repo_cleanup` 只在主仓根运行。
- `artifacts/`、`data/`、`node_modules/`：仍须点名才清。
- 把事务目录移出 `.scratch/`：本方案只做清理前判定，不迁移存储位置。

## 2. 现状与差异

| 项 | 现状（当前状态） | 目标 |
|---|---|---|
| 默认类别 | `pycache / pytest / logs / os / editor`；`scratch` 须点名 | 默认含 `scratch` |
| 事务保护 | 无。点名清 scratch 会删掉 `applied` 同步事务的记录与备份，此后既不能 accept 也不能 rollback | 有未结束事务时跳过 scratch，其它类别照常清 |
| 在用草稿 | 只靠 agent 判断后传 `--keep` | 脚本按主人与修改时间机械判定，`--keep` 保留为人工补充 |
| 并发 | 不取锁 | 带 scratch 时非阻塞持有控制面锁，锁被占用立即失败 |
| 事务路径 | `.scratch/repo_sync_rollback`、`.scratch/task_graph_transaction` 以字符串分散写在 6 处 | 集中为常量 |
| issue 报告 | 无发布状态，无法区分已发布与待交接 | 本地完整版记录 issue 地址 |

主仓 `.scratch/` 中现有的工具链内容与目标处理方式：

| 内容 | 路径 | 目标处理 |
|---|---|---|
| 模板同步事务（apply、migrate、link-skills、entity-storage 共用） | `.scratch/repo_sync_rollback/` | 未结束则跳过 scratch；已结束视为无主 |
| task 图合并事务（`task.py graph merge`，`task-merge` 使用） | `.scratch/task_graph_transaction/` | 同上 |
| 同步来源缓存 | `.scratch/repo_template_sync_src/` | 无主；下次同步重新 clone，离线时失去缓存 |
| apply 临时快照 | `.scratch/repo_sync_source_*` | 无主；仅在 apply 运行期间存在，此时控制面锁被占用 |
| 状态行绑定 | `.scratch/statusline_workdir.jsonl` | 无主；下次 `run-begin --session-id` 重建 |
| purge 收尾失败残留 | `.scratch/purge_*` | 无主 |
| bug 复现、pending 修复复现 | 由条目「线索」引用的 `.scratch/...` | 主人为未关闭 pending 条目 |
| 模板 issue 本地报告 | `.scratch/repo_template_issues/` | 主人为未发布的报告 |

## 3. 判定规则

| 情况 | 处理 |
|---|---|
| 本次类别不含 `scratch` | 不取锁，行为与现状一致 |
| 控制面锁被占用（同步或 task 控制命令运行中） | 整次命令立即失败，零删除，退出码非零 |
| 存在未结束事务（`preparing / applying / applied / restore_failed`）或事务记录损坏 | 跳过 scratch，其它类别照常清；输出事务状态与处理命令 |
| pending 条目损坏，无法确定引用 | 跳过 scratch，其它类别照常清 |
| 有主人，最后修改距今不足 3 天 | 保留，标注主人 |
| 有主人，最后修改距今 3 天及以上 | 清理，标注「超期」 |
| 无主（含已结束事务的旧备份、已发布 issue 报告） | 清理 |
| `--keep` 指定路径 | 始终保留 |

3 天期限只适用于草稿类主人（pending 引用、未发布 issue 报告），不适用于未结束事务。事务是恢复状态，删除后该轮同步或图合并既不能回滚也不能确认；存在未结束事务时直接跳过 scratch。

## 4. 判定流程

仅当本次类别含 `scratch` 时执行；scan 与 apply 使用同一流程。

1. 以非阻塞方式获取控制面锁 `CONTROL_LOCK`。失败则输出「控制面忙：同步或 task 控制命令正在运行，稍后重试」并整次退出；成功则持有到 apply 删除结束。
2. 调用 `open_transactions()`。任一事务未结束或记录损坏，即从本次类别移除 `scratch` 并记录跳过原因。
3. 识别主人（第 5 节），得到「受保护候选路径 → 原因」。
4. 对每个有主人的路径，取其自身及全部子项的最大 `lstat` 修改时间，不跟随软链。目录自身的修改时间不反映内部文件编辑，不能单独使用。距今不足 `OWNED_GRACE_SECONDS` 的加入受保护集合，其余标为超期可清。
5. 受保护集合并入现有 keep 判定，复用 `_kept` 与 `_dir_holds_keep`，保证父目录不会因清理子项被整树删除。
6. apply 重新执行第 2–5 步。scan 与 apply 之间如有文件跨过 3 天边界，候选数量变化，现有 `--expected-count` 拒绝执行。

## 5. 主人识别

| 主人 | 数据来源 | 受保护路径 |
|---|---|---|
| 同步、图事务 | `file_transaction.TRANSACTION_DIRS` | 不参与第 4 步：未结束则跳过 scratch，已结束视为无主 |
| 未关闭 pending 条目（`todo / parked`） | `pending.py` 新增 `open_scratch_references(repo)` | 条目正文中出现的 `.scratch/...` 文件或目录 |
| 未发布模板 issue 报告 | 本地完整版的 `issue` 字段 | 该报告及同名 `.issue.md` |

pending 引用提取规则：

- 用现有 `parse_pending_file` 读取 `docs/pending/*.md`，只取 `todo` 与 `parked` 条目。
- 在正文中按正则提取以 `.scratch/` 开头的路径，去除尾随反引号、引号与中英文标点。
- 含 `..`、绝对路径或目标不存在的引用忽略。
- 条目关闭后引用自动失效；`pending-fix` 修复并关闭条目后，复现文件变为可清。

## 6. 事务与锁原语

`repo_task/file_transaction.py`：

- 新增 `SYNC_TRANSACTION_DIR = ".scratch/repo_sync_rollback"`、`GRAPH_TRANSACTION_DIR = ".scratch/task_graph_transaction"` 与 `TRANSACTION_DIRS`。
- 新增 `OPEN_STATUSES = {"preparing", "applying", "applied", "restore_failed"}`；`begin()` 中现有同一集合改为引用它，使「能否开始新事务」与「能否清理」共用一个定义。
- 新增 `open_transactions(root) -> list[tuple[str, str]]`，返回未结束事务的目录与状态；记录无法解析时状态为 `corrupt`。

以下调用方改为引用常量，提示文案中的路径同步改为引用常量：

- `template_sync/apply.py`
- `template_sync/transaction.py`
- `template_sync/migrate.py`
- `template_sync/skills_links.py`
- `template_sync/entity_storage_migration.py`
- `repo_task/graph.py`（2 处）

`repo_task/locks.py`：

- `file_lock` 与 `git_common_lock` 增加参数 `blocking=True`。
- `blocking=False` 时，POSIX 使用 `fcntl` 的 `LOCK_NB`，Windows 使用 `msvcrt.LK_NBLCK`；获取失败抛 `LockBusyError`。
- 现有调用方不传参数，行为不变。

## 7. 模板 issue 发布标记

本地完整版报告在第 4 步创建时加入 front matter：

```markdown
---
issue: ""
---

# 模板仓问题报告
```

- 只加在本地完整版。脱敏版 `.issue.md` 是发布正文，保持现状。
- 第 6 步发布成功后，agent 将字段回写为 `issue: "https://github.com/<owner>/<repo>/issues/<编号>"`。超时后查询到已创建的 issue 同样回写。
- 发布失败或目标无法确认时字段保持为空，并提示用户：未发布报告 3 天未修改会被 `repo-cleanup` 清理，需长期保留移到 `local/`。

cleanup 判定：

- 合法地址：`^https://github\.com/[^/\s]+/[^/\s]+/issues/[1-9][0-9]*$`。
- `<时间戳>.md` 与 `<时间戳>.issue.md` 作为一个单元判定：
  - 地址合法 → 已发布 → 无主 → 清理；
  - 地址为空、缺失、格式非法或 front matter 解析失败 → 未发布 → 有主人，按两份文件的最大修改时间适用 3 天期限。
- 只有 `.issue.md`、没有本地完整版 → 无主 → 清理。

工厂 `.claude/skills/issue-handler/SKILL.md` 的本地回退扫描跳过已有合法地址的报告，只处理未发布报告；扫描片段同步修改。

## 8. 输出格式

```text
## repo-cleanup 预览（未删除）
类别：pycache, pytest, logs, os, editor, scratch
| 路径 | 类别 | 说明 |
| ./.scratch/exp/tmp.py | scratch | 无主 |
| ./.scratch/repo_sync_rollback | scratch | 事务已结束（accepted） |
| ./.scratch/repro_p012 | scratch | 主人 p012（todo），最后修改 5 天前，超期 |
保留（在用）：
- .scratch/repro_p014 — 主人 p014（todo），最后修改 1 天前
- .scratch/repo_template_issues/20261110-101500.md — 未发布 issue，最后修改 0 天前
```

跳过 scratch 时：

```text
scratch 已跳过：同步事务 applied（.scratch/repo_sync_rollback）；
先 `repo_sync.py transaction accept` 或 `transaction rollback`；其它类别照常处理
```

图事务未结束时，处理命令为 `task.py graph-transaction status` 与 `task.py graph-transaction rollback`。

## 9. 改动清单

| 文件 | 改动 |
|---|---|
| `repo/.repo_template/scripts/repo_cleanup.py` | `DEFAULT_CATEGORIES` 加入 `scratch` 并从点名类别移除；新增 `OWNED_GRACE_SECONDS = 3 * 86400`；实现第 4 节流程；输出逐项标注原因；更新模块说明 |
| `repo/.repo_template/scripts/repo_task/file_transaction.py` | 第 6 节常量与 `open_transactions` |
| `repo/.repo_template/scripts/repo_task/locks.py` | 非阻塞取锁与 `LockBusyError` |
| `repo/.repo_template/scripts/pending.py` | 新增 `open_scratch_references` |
| 第 6 节列出的 6 处事务调用方 | 改为引用常量 |
| `repo/.repo_template/skills/repo-cleanup/SKILL.md` | description 改为默认含 scratch；流程补充主人保护、3 天期限、跳过与立即失败的处理 |
| `repo/.repo_template/skills/template-issue-report/SKILL.md` | 发布标记字段、回写步骤、保留期提示 |
| `.claude/skills/issue-handler/SKILL.md` | 本地回退扫描跳过已发布报告 |
| `repo/AGENTS.md` | `.scratch/` 行改为：默认可清；无主随时清，有主超过 3 天未修改也清；长期保留放 `local/` |
| `repo/.repo_template/docs/sync.md`、`repo/.repo_template/docs/recovery.md` | 说明 apply 之后、accept 之前 cleanup 跳过 scratch |
| `docs_repo/decision_log.md` | L68 实施后更新状态 |

## 10. 验收

实施后由工厂全量门禁验证：

```bash
pytest repo/.repo_template/tests factory_tests -q -n auto -p no:cacheprovider --basetemp "$(mktemp -d)"
```

契约测试 `repo/.repo_template/tests/test_repo_cleanup.py`：

1. 默认类别清理无主 scratch 文件。
2. 4 种未结束状态与事务记录损坏均跳过 scratch，其它类别照常清，输出处理命令。
3. `accepted`、`rolled_back` 事务目录被清理；图事务适用同一规则。
4. pending 引用：
   - `todo` 条目引用且文件新 → 保留；
   - `os.utime` 改为 4 天前 → 清理；
   - 条目已关闭 → 清理；
   - 目录自身修改时间旧、内部文件新 → 保留。
5. pending 条目损坏 → 跳过 scratch。
6. issue 报告：
   - 未发布且新 → 两份都保留；
   - 未发布且旧 → 清理；
   - 已发布 → 清理；
   - 只有 `.issue.md` → 清理；
   - front matter 损坏 → 按未发布处理。
7. `--keep` 与 `--expected-count` 仍生效。

现有 `test_default_categories_do_not_enter_unnamed_dirs` 中「默认不进入 `.scratch`」的断言与目标契约冲突。按仓库测试规则删除该部分断言并写明替换依据，另以新测试覆盖目标行为，不就地改写旧断言的预期。

工厂测试：子进程持有控制面锁时，带 scratch 的 cleanup 立即失败且零删除；不带 scratch 时照常执行。

## 11. 风险与结论

风险：

- 修改时间可被 `touch` 或编辑器刷新；延长保留即修改文件，属于规则本身。
- 发布失败的 issue 报告 3 天后被清理，本地交接回退随之失效；skill 提前提示用户。
- 只有写成 `.scratch/...` 形式的复现引用能被识别。
- 控制面锁由 task 控制命令与同步共用；任何控制面命令运行期间，带 scratch 的清理都会立即失败。

结论：

- 3 天期限只用于草稿类主人，不用于未结束事务（第 3 节）。用户已确认按本方案执行：事务保留「未结束即跳过 scratch」，不适用期限规则。
