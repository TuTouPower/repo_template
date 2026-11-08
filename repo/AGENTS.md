{一句话介绍：这个项目是什么、给谁用。} 这是代码仓库的仓库模板，用户创建代码库时复制这个作为起点。

本项目使用模板仓 repo_template。此声明必须保留，消费仓不得删除。

## 目录与读写规则

|路径|用途|写权归属|
|---|---|---|
|`docs/specs_index.md`|当前生效 spec 清单（在表即生效）|task 收尾时更新；废弃删除行|
|`docs/specs/<slug>.md`|需求级 spec（按已完成 task 累积）|task 收尾时累积更新；废弃移入`docs/archive/specs/`|
|`docs/tasks/{tid}_{slug}/`|task 工作区兼**状态权威**（backlog 起即存在）；含执行回执 `handoff.json`|`task.md` / `progress.md` 正文由实现侧写；执行回执由 `finish` 生成，不手写；`progress.md` front matter 只经 `.repo_template/scripts/task.py`；reviewer 写 `review_code.md` / `review_test.md`（`single` 级写 `review_general.md`）；done 由 `integrate` 在 merge commit 中移入 `docs/archive/tasks/{tid}_{slug}/`，`drop` 由脚本移入|
|`docs/handoff.md`|项目交接记录（仅最新一节）|记录须含 branch 与交出时 head_commit；过时段落迁`docs/archive/handoff.md`|
|`docs/pending/pNNN_{slug}.md`|待办总账（一条目一文件，统一`pNNN`，原位表达 `todo / parked / closed`，不进 Archive）|条目创建与状态维护只经`.repo_template/scripts/pending.py`；skill 流程见`.repo_template/docs/usage.md`「skill 调用」|
|`docs/findings/dNNN_{slug}.md`|已验证的技术发现（一条目一文件，跨 task 复用，`dNNN`）|条目创建只经`.repo_template/scripts/findings.py`；只新增与就地修订，不迁 archive；spike 收尾或日常验证出的事实写入|
|`docs/blueprint/`|当前长期真相：架构、领域、约定、决策、测试|finalization 时更新；写代码或文档前读`conventions.md`，改跨模块行为前读 `architecture.md`，历史取舍读 `decisions.md`。门禁命令在 `testing.md` 的 `## doctor_cmd` / `## test_cmd` / `## blackbox_verify` 章节。`architecture.md` / `domain.md` 是模板仓占位符，**消费项目复制后自行填充**，未填充前不视为权威。`conventions.md` / `testing.md` 是模板默认，复制后按技术栈修改，不是空壳|
|`docs/reviews/review_*/`|多路 review 会话产物（my-review 等外部评审生成）|报告`review_*.md` 入库；`_meta/` 过程文件已 gitignore；确认过时由 `repo-hygiene` 迁 `docs/archive/reviews/`|
|`docs/spikes/{sid}_{slug}/`|技术 spike（`report.md` 必需；有实验代码建 `code/`）|目录创建只经`.repo_template/scripts/spikes.py new`；流程见 `task-work`“实施”中的未知契约处理；结论入 `docs/findings/`；完结原位标 completed 或 superseded，不再迁入 archive|
|`docs/guides/`|给人看的使用指南|给人读，不写 agent 行为规则|
|`docs/archive/`|完结或终止的历史|日常只追加；归档 task 的格式迁移仅由模板同步执行，见[同步规程](.repo_template/docs/sync.md#workflow-schema-%E5%BC%BA%E5%88%B6%E8%BF%81%E7%A7%BB)|
|`schemas/`|跨服务接口契约|改契约走 task 流程|
|`config/`|配置（默认 + 环境覆盖 +`.env.example`）|仅`.env.example` 入库；真值写本地 `.env`|
|`src/` `tests/` `assets/`|源码、测试、静态源|仅在 task 执行期按 spec 修改，或经授权由 `pending-fix` 在 main 直修；debug 复现不得写入|
|`scripts/`|用户项目脚本|仅在 task 执行期按 spec 修改，或经授权由 `pending-fix` 在 main 直修；debug 复现不得写入|
|`.repo_template/`|模板工具链（skills、scripts、docs、hooks）|仅模板演进时修改；细目与写权见`.repo_template/docs/usage.md`|
|`artifacts/` `data/` `.scratch/`|产物、运行数据、一次性草稿|运行与草稿；debug 复现和临时实验只写`.scratch/`（已 gitignore）；需保留的 spike 验证材料写 `docs/spikes/{sid}_{slug}/code/`|

## 开发原则

- specs driven：需求拆分为可独立验证的 task，填写 `task.md`（契约区行为 AC 须非空）；版本号、底层库选型、目录结构不写进行为 AC，需要长期约束的写 `docs/blueprint/decisions.md`。
- TDD：可测部分先红后绿；测试须触达生产逻辑。实现变更让旧测试语义失效时，新增覆盖新语义的测试；旧测试原样保留或整体删除并写明理由，**禁止就地把旧测试的预期改成当前实现的输出**。
- 用户未明确要求修改，且当前任务不在获准写入的 skill 流程中时，禁止修改未被 gitignore 的代码文件。
- task 状态以 `task.py effective-status` 为准；进行中 task 的状态在其合并前不进主干，细则见 `.repo_template/docs/usage.md`「task 状态读取」。
- 每个 task 恰好一个实现 commit；review 失效的修复见 `.repo_template/docs/recovery.md`；创建期和状态维护 commit 分开；派生 index 与 task 目录的归档移动在集成时进入同一个 merge commit。每个 commit 必须独立可验证，有工程意义。
- 发现 commit 混入不属于当前工作的改动时，立即停止工作并向用户汇报；未经用户确认，不继续提交、合并或修正。
- 使用 `.repo_template` 提供的工具链、skills、hooks、模板文件时发现缺陷，不静默处理、不自行绕过或修改，报告用户决定。
- task 状态：`backlog` / `active` / `done` / `dropped`。
- 开发工作流的设计见 `.repo_template/docs/architecture.md`，使用见 `.repo_template/docs/usage.md`，多会话恢复见 `.repo_template/docs/recovery.md`，模板同步与升级见 `.repo_template/docs/sync.md`。
