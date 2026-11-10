# 消费仓迁移就绪审计（2026-11-10）

## 范围与方法

- 审计目录：`/Users/karson/kar/code` 下全部消费仓。
- 排除：`repo_template`、`repo_template_wt_*`，以及审计所在 worktree。
- 判定依据：`docs_repo/plans/plan_state_ownership_and_workflow.md` 第 6 节（6.1 前置条件、6.2 迁移顺序、6.3 pilot）与 `repo/AGENTS.md`。
- 全程只读：仅使用 `git rev-parse`、`git status`、`git worktree list`、`git config --get`、`find`/文件读取，以及工厂迁移代码的只读导入；未在消费仓执行 checkout、commit、migrate、task 命令或任何写操作。
- 仓库识别口径：存在 `.repo_template/scripts/task.py`，或根 `AGENTS.md` 提及 `repo_template`。实际命中 14 个目录，其中 2 个是已登记 worktree，不是独立消费仓。

## 汇总表

| 仓库 | HEAD | 模板版本是否一致 | 工作区 | 已登记 worktree | 主要阻断项 |
|---|---|---|---|---|---|
| agent_rank | `1db997807a75` | 是（`988fd06`） | 干净 | 1 | 无 |
| capture_all | `a74792177a89` | 是（`988fd06`） | 干净 | 1 | 无 |
| CBrowser | `22cc30616488` | 是（`988fd06`） | 脏（`?? config/deploy/`） | 2 | t049 worktree 脏且 task 为 active；1 条无 NNN ADR；review 目录 6 个（>5） |
| great_websites | `cd87e2a6cd25` | 是（`988fd06`） | 干净 | 1 | `specs_index` 2 处非法 task 清单；32 份已有 front matter 缺 `status`/`tasks`；ADR `044` 重复 |
| haiwa_bot | `d45821776275` | 是（`988fd06`） | 干净 | 1 | `decisions.md` 无任何 `## NNN` 条目（当前实现报错停止） |
| kkmail | `f0e6c72b32c1` | 是（`988fd06`） | 干净 | 1 | `decisions.md` 无 `## NNN` 条目（H2 分组 + H3 日期条目，计划预期停止） |
| omni_caption | `59afe0f09211` | 是（`988fd06`） | 干净 | 1 | `specs_index` 4 处非法 task 清单 |
| omni_chat | `1e6fa9bc0900` | 是（`988fd06`） | 干净 | 1 | 无 |
| omni_panel | `7427c14b8346` | 是（`988fd06`） | 干净 | 2 | t604 worktree 脏且 task 为 active；4 个 kebab-case archive spec slug；ADR `053` 重复；601 个归档 task |
| video_transcoder | `006c129be8be` | 是（`988fd06`） | 干净 | 1 | 无 |
| zmole | `d8918f5a53ab` | 是（`988fd06`） | 干净 | 1 | `specs_index` 2 处非法 task 清单 |
| zscrcpy | `956f5eaca2e6` | 是（`988fd06`） | 干净 | 1 | 无 |

模板版本一致指 `.repo_template/sync_manifest.json.resolved_commit` 与 `.repo_template/sync_state.json.last_synced_commit` 均为 `988fd060cbc4086afd070ccb98df4ed14efe88bb`。消费仓 HEAD 是各仓自身提交，不是 `988fd06`。

## 逐仓详情

### agent_rank

- HEAD：`1db997807a75f4b38a3f9b8673c07a63904177dc`，branch `main`。
- `core.hooksPath`：`.repo_template/hooks`。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（0 个 md）；`docs/archive/reviews` 有（0 个目录）。
- Review：`docs/reviews/review_*` 0 个。
- Worktree：1 个（仅主工作区）；工作区干净。
- Task 规模：`docs/tasks` 0 个；`docs/archive/tasks` 9 个；spikes 1 个。
- ADR：`decisions.md` 2 条，全部为 `## NNN`。
- Spec：`docs/specs` 5 个，全部缺 front matter；文件名均为 snake_case。
- 阻断项：无。

### capture_all

- HEAD：`a74792177a89cddcfc74cbfb4f3ab08c043d92b9`，branch `main`。
- `core.hooksPath`：`.repo_template/hooks`。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（16 个 md）；`docs/archive/reviews` 有（0 个目录）。
- Review：`docs/reviews/review_*` 4 个。
- Worktree：1 个；工作区干净。
- Task 规模：`docs/tasks` 0 个；`docs/archive/tasks` 197 个；spikes 8 个。
- ADR：`decisions.md` 26 条，全部为 `## NNN`，编号无重复。
- Spec：`docs/specs` 27 个，全部缺 front matter；文件名均为 snake_case；`specs_index` 契约校验 0 错误。
- 阻断项：无。

### CBrowser

- HEAD：`22cc30616488b58ef963d39679fbf6ba1634bc68`，branch `main`。
- `core.hooksPath`：`.repo_template/hooks`。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（1 个 md）；`docs/archive/reviews` 有（0 个目录）。
- Review：`docs/reviews/review_*` 6 个，超过保留最近 5 个的上限。
- Worktree：2 个。主工作区 `main`；`CBrowser_t049` 在分支 `t049_supervisor_restore_gateway_deadlock`，HEAD `13e6316`，该分支已并入 `main`，但 worktree 有 11 个修改 + 2 个新增文件，`progress.md` 状态为 `active`。
- 工作区：主工作区脏，未跟踪 `config/deploy/`。
- Task 规模：`docs/tasks` 1 个（t049）；`docs/archive/tasks` 48 个；spikes 7 个。
- ADR：`decisions.md` 28 个 H2，27 条为 `## NNN`；第 7 行 `## 产品历史勘误入口与 ADR 传播约定（t024）` 无编号。计划 WP3.3 将其列为停止项；当前 `adr_migration._entries` 不会主动拒绝该 H2。由于它位于首条 `## NNN` 之前，会进入前言且不生成 ADR，实际行为与计划判定不一致，迁移前应修正。
- Spec：`docs/specs` 17 个，全部缺 front matter；文件名均为 snake_case；`specs_index` 契约校验 0 错误。
- 阻断项：worktree 未收尾（t049 脏且 active）；主工作区未跟踪文件；无编号 ADR；review 数 6 需按规则裁剪到 5。

### great_websites

- HEAD：`cd87e2a6cd25bdd77c99d3731b14b67f326b840a`，branch `main`。
- `core.hooksPath`：`.repo_template/hooks`。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（2 个 md）；`docs/archive/reviews` 有（23 个目录）。
- Review：`docs/reviews/review_*` 0 个。
- Worktree：1 个；工作区干净。
- Task 规模：`docs/tasks` 0 个；`docs/archive/tasks` 470 个；spikes 0 个。
- ADR：`decisions.md` 90 条，全部为 `## NNN`；`044` 出现两次（2026-08-09 与 2026-08-06，生成文件名不冲突，但旧编号不唯一）。
- Spec：`docs/specs` 93 个，文件名均为 snake_case。61 个完全缺 front matter；32 个已有 front matter 但只含 `slug` 或 `slug`+`tid`，均缺 `status`/`tasks`。按当前 `repo_task.specs.load_spec`，已有 front matter 缺 `status`/`tasks` 会报错，不会按索引补齐，迁移会停止；这与计划 WP3.2“缺字段用索引补齐”的描述不一致，需先确认修工厂还是修消费仓。
- `specs_index` 契约校验 2 处错误：第 20 行 task 清单 `汇总（跨 task）`，第 38 行 `t008, t001(e2e), t028, t176, t183, t191, t249`。
- 阻断项：`specs_index` 非法 task 清单；32 份不完整 front matter；ADR `044` 重复；470 个归档 task 属大规模仓。

### haiwa_bot

- HEAD：`d458217762755f6ce90207509ed58a77d70bc129`，branch `main`。
- `core.hooksPath`：`.repo_template/hooks`。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（0 个 md）；`docs/archive/reviews` 有（0 个目录）。
- Review：`docs/reviews/review_*` 0 个。
- Worktree：1 个；工作区干净。
- Task 规模：`docs/tasks` 0 个；`docs/archive/tasks` 0 个；spikes 0 个。
- ADR：`decisions.md` 存在但只有模板前言，无任何 `## NNN` 条目；当前 `adr_migration._entries` 会抛“未找到任何 `## NNN` 条目”。
- Spec：`docs/specs` 0 个；`specs_index` 只有空表，契约校验 0 错误。
- 阻断项：ADR 文件无可迁移条目，需消费仓或迁移策略先处理空 ADR 文件。

### kkmail

- HEAD：`f0e6c72b32c1beea4e3614cb72ff608f4ef8ac19`，branch `main`。
- `core.hooksPath`：`.repo_template/hooks`。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（0 个 md）；`docs/archive/reviews` 有（13 个目录）。
- Review：`docs/reviews/review_*` 1 个。
- Worktree：1 个；工作区干净。
- Task 规模：`docs/tasks` 0 个；`docs/archive/tasks` 75 个；spikes 0 个。
- ADR：`decisions.md` 无 `## NNN` 条目；只有 `## 一、`/`## 二、` 分组和 `### YYYY-MM-DD:` 条目，计划 WP3.3 已列为此类漂移，预期停止。
- Spec：`docs/specs` 6 个，全部缺 front matter；文件名均为 snake_case；`specs_index` 契约校验 0 错误。
- 阻断项：ADR 全部无编号，需先补 NNN 或人工制定迁移映射。

### omni_caption

- HEAD：`59afe0f0921137bcfcce2c1cc0d3a8eac396d617`，branch `main`。
- `core.hooksPath`：`.repo_template/hooks`。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（0 个 md）；`docs/archive/reviews` 有（0 个目录）。
- Review：`docs/reviews/review_*` 2 个。
- Worktree：1 个；工作区干净。
- Task 规模：`docs/tasks` 0 个；`docs/archive/tasks` 80 个；spikes 30 个。
- ADR：`decisions.md` 13 条，全部为 `## NNN`，编号无重复。
- Spec：`docs/specs` 34 个，全部缺 front matter；文件名均为 snake_case。
- `specs_index` 契约校验 4 处错误，均为 task 清单使用 `/` 分隔：第 33 行 `t033/t040`、第 34 行 `t034/t044`、第 37 行 `t036/t041/t080`、第 38 行 `t037/t042`。
- 阻断项：`specs_index` 非法 task 清单。

### omni_chat

- HEAD：`1e6fa9bc09007a7bc7b3e08338804ea191414abb`，branch `main`。
- `core.hooksPath`：`.repo_template/hooks`。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（1 个 md）；`docs/archive/reviews` 有（2 个目录）。
- Review：`docs/reviews/review_*` 1 个。
- Worktree：1 个；工作区干净。
- Task 规模：`docs/tasks` 0 个；`docs/archive/tasks` 12 个；spikes 3 个。
- ADR：`decisions.md` 8 条，全部为 `## NNN`，编号无重复。
- Spec：`docs/specs` 23 个，全部缺 front matter；文件名均为 snake_case；`specs_index` 契约校验 0 错误。
- 阻断项：无。

### omni_panel

- HEAD：`7427c14b8346ad2965fdd09858a8b12e72fc2424`，branch `main`。
- `core.hooksPath`：`.husky/_`（计划中已注明该仓走 husky）。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（6 个 md）；`docs/archive/reviews` 有（25 个目录）。
- Review：`docs/reviews/review_*` 2 个。
- Worktree：2 个。主工作区 `main`；`omni_panel_t604` 在分支 `t604_repo_panel_headless_e2e`，HEAD 与 `main` 相同（`7427c14b`），worktree 有 7 个修改 + 4 个新增文件，`progress.md` 状态为 `active`。
- 工作区：主工作区干净。
- Task 规模：`docs/tasks` 1 个（t604）；`docs/archive/tasks` 601 个（计划写的 594 已过时）；spikes 41 个。
- ADR：`decisions.md` 62 条，全部为 `## NNN`；`053` 出现两次（2026-10-01 与 2026-10-13，生成文件名不冲突，但旧编号不唯一）。
- Spec：`docs/specs` 90 个，全部缺 front matter，文件名均为 snake_case；`specs_index` 契约校验 0 错误。
- Archive spec 漂移：`docs/archive/specs` 中 4 个 kebab-case slug：`ai-cli-token-stats.md`、`connector-cpa.md`、`platform-services.md`、`ui-views.md`。与计划“4 个 kebab-case spec slug”一致，需先改为 snake_case 并修引用。
- 阻断项：worktree 未收尾（t604 脏且 active）；4 个 kebab-case archive spec；ADR `053` 重复；601 个归档 task 属最大规模仓。

### video_transcoder

- HEAD：`006c129be8be7207db6034b966724fb021066c13`，branch `main`。
- `core.hooksPath`：`.repo_template/hooks`。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（0 个 md）；`docs/archive/reviews` 有（0 个目录）。
- Review：`docs/reviews/review_*` 0 个。
- Worktree：1 个；工作区干净。
- Task 规模：`docs/tasks` 0 个；`docs/archive/tasks` 0 个；spikes 0 个。
- ADR：`decisions.md` 4 条，全部为 `## NNN`，编号无重复。
- Spec：`docs/specs` 0 个；`specs_index` 只有空表，契约校验 0 错误。
- 阻断项：无。

### zmole

- HEAD：`d8918f5a53ab592abdb5288b9a347629a3ac2cbe`，branch `main`。
- `core.hooksPath`：`.repo_template/hooks`。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（0 个 md）；`docs/archive/reviews` 有（0 个目录）。
- Review：`docs/reviews/review_*` 2 个。
- Worktree：1 个；工作区干净。
- Task 规模：`docs/tasks` 0 个；`docs/archive/tasks` 58 个；spikes 1 个。
- ADR：`decisions.md` 15 条，全部为 `## NNN`，编号无重复。
- Spec：`docs/specs` 14 个，全部缺 front matter；文件名均为 snake_case。
- `specs_index` 契约校验 2 处错误：第 7 行 `t001, t003, t014→t015, t035, t044`，第 8 行 `t002, t025, t027, adoption-review`。
- 阻断项：`specs_index` 非法 task 清单。

### zscrcpy

- HEAD：`956f5eaca2e6717d8dbd471edc4ada618316d3a2`，branch `main`。
- `core.hooksPath`：`.repo_template/hooks`。
- 迁移基线：`entity_schema.json` 无；`docs/runtime` 有；`.repo_template/sync_state.json` 有；`docs/specs_index.md` 有。
- Archive：`docs/archive/specs` 有（0 个 md）；`docs/archive/reviews` 有（0 个目录）。
- Review：`docs/reviews/review_*` 1 个。
- Worktree：1 个；工作区干净。
- Task 规模：`docs/tasks` 0 个；`docs/archive/tasks` 10 个；spikes 0 个。
- ADR：`decisions.md` 6 条，全部为 `## NNN`，编号无重复。
- Spec：`docs/specs` 10 个，全部缺 front matter；文件名均为 snake_case；`specs_index` 契约校验 0 错误。
- 阻断项：无。

## 对计划“12 个仓全部 988fd06”的现状纠偏

1. **消费仓数量仍是 12 个，但目录扫描会得到 14 个命中。** `CBrowser_t049` 是 `CBrowser` 的已登记 worktree，`omni_panel_t604` 是 `omni_panel` 的已登记 worktree；两者不是独立消费仓。计划中的“12 个仓”成立。
2. **“全部 988fd06”只对模板版本成立，不是消费仓 HEAD。** 12 个仓的 `sync_manifest.resolved_commit` 与 `sync_state.last_synced_commit` 全部为 `988fd060cbc4086afd070ccb98df4ed14efe88bb`；各仓 HEAD 是自身提交，见汇总表。没有仓的 HEAD 是 `988fd06`。
3. **“无 entity_schema.json”仍成立。** 12 个仓均无 `.repo_template/entity_schema.json`。
4. **“无已登记 worktree”不成立。** `CBrowser` 与 `omni_panel` 各有 1 个已登记 task worktree，且对应 task 均为 `active`、worktree 均有未提交修改。计划 6.1 还写到 omni_panel 的 t600 worktree；当前实际是 `t604_repo_panel_headless_e2e`。
5. **“工作区干净”不成立。** `CBrowser` 主工作区有未跟踪 `config/deploy/`；两个 task worktree 也分别有未提交修改。
6. **没有 done 未合并分支。** 12 个仓的 `git branch --no-merged main` 均为 0。
7. **所有仓都未开始迁移。** 12 个仓均同时存在 `docs/runtime` 与 `.repo_template/sync_state.json`，且均无 `entity_schema.json`，符合迁移前 `988fd06` 布局。
8. **计划中的已知漂移需要扩充。** 除计划的 omni_panel kebab-case、kkmail/CBrowser 无编号 ADR 外，本次只读审计新增：
   - `great_websites`、`omni_caption`、`zmole` 的 `specs_index` task 清单不符合严格 schema，会在 specs 迁移前停止。
   - `great_websites` 32 份已有 front matter 缺 `status`/`tasks`，按当前实现会停止；计划 WP3.2 描述为按索引补齐。
   - `haiwa_bot` 的 `decisions.md` 无任何 `## NNN` 条目，当前 ADR 迁移实现会报错停止；计划未列该仓。
   - `great_websites` ADR `044`、`omni_panel` ADR `053` 各重复一次。生成文件名带日期，当前不冲突，但“NNN 唯一”的前提不成立。
   - `CBrowser` 的 review 顶层目录为 6 个，超过保留 5 个的上限；其余仓均不超过 5。
9. **规模数据需要更新。** omni_panel 当前 `docs/archive/tasks` 601 个、`docs/tasks` 1 个（t604），合计 602 个 task 目录；计划写的 594 已过时。
10. **hook 现状与计划一致。** 11 个仓 `core.hooksPath=.repo_template/hooks`；`omni_panel` 为 `.husky/_`。因此 entity-storage 迁移 commit 仍应按计划用 `--no-verify` 并做 rename 复核。

## 建议的迁移分批顺序

以下顺序只依据本次只读事实（阻断项、归档 task 规模、spec/ADR/review 数量），不包含耗时或风险评估推测。按计划 6.3，同一时间只迁移一个仓。

| 批次 | 仓库 | 依据 |
|---|---|---|
| 0. 前置清零 | CBrowser、omni_panel | 两个已登记 worktree 必须完成或 rewind；CBrowser 主工作区未跟踪 `config/deploy/` 需处置；CBrowser 6 个 review 需按规则裁剪到 5 个并要求用户确认删除。 |
| 0. 文档漂移修复 | great_websites、omni_caption、zmole、kkmail、haiwa_bot、CBrowser、omni_panel | 先修 `specs_index`、不完整 front matter、无编号 ADR、kebab-case slug；修复后干跑确认阻断项清零。 |
| 1. pilot 复制 | agent_rank | 9 个归档 task、5 个 spec、2 条 ADR、0 个 review、无漂移、工作区干净。按计划先复制到系统临时目录做迁移、回滚、再迁移。 |
| 2. 无内容漂移、可先迁 | video_transcoder、zscrcpy、omni_chat、capture_all | 四仓 `specs_index` 均 0 错误，ADR 全为 `## NNN` 且无重复，工作区干净，无 worktree。规模递增：0/0、10/10、12/23、197/27（归档 task/spec）。 |
| 3. 修完文档后迁 | haiwa_bot、zmole、omni_caption、kkmail | haiwa_bot 最小（0 task、0 spec）但需处理空 ADR 文件；zmole 58 个归档 task、2 处 index 错误；omni_caption 80 个归档 task、4 处 index 错误；kkmail 75 个归档 task、需重写无编号 ADR。 |
| 4. 工作区与历史文稿修完后迁 | CBrowser、great_websites | CBrowser 需先清 worktree、未跟踪文件、无编号 ADR、review 裁剪；great_websites 需先修 2 处 index、32 份不完整 front matter、ADR 重复，且 470 个归档 task 属大规模。 |
| 5. 最后迁 | omni_panel | 601 个归档 task + 1 个 active task、90 个 spec、6 个 archive spec（含 4 个 kebab-case）、62 条 ADR（`053` 重复）、41 个 spike、t604 worktree。规模与漂移均最多，适合最后处理。 |

## 未执行事项

- 未运行任何消费仓的 `task.py`、`repo_sync.py` 或测试命令。
- 未执行迁移 dry-run；本文的阻断判断来自文件结构、`specs_index` 解析器、ADR 解析器与 `load_spec` 的只读调用。
- 未修改任何消费仓文件、index、分支或 worktree。
