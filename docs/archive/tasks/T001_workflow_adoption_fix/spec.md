# Task spec

## 背景

`docs/reviews/review_20260720_2346` 多模型审阅发现工作流与模板存在可执行性硬伤与多处冲突。用户已批准 `adoption_decision.md` 全部采纳项（含 4 项推荐方案 A）。本 task 一次性落地，使 AGENTS / conventions / 模板 / README / tasks_index 一致可执行。

## 范围

- 按 `docs/reviews/review_20260720_2346/adoption_decision.md` 全部采纳项修改文档与模板
- 提交删除 HEAD 仍存在的 `docs/templates/task/review_prompt.md`
- 更新 `docs/blueprint/conventions.md`、`docs/blueprint/decisions.md` 中与本次决策相关的条目
- 需求级：本批为模板仓库工作流修复，不写 `docs/specs/`（无业务需求 slug）

## 非范围

- 不改 `src/`、不引入可运行业务代码
- 不改写 git 历史 commit message
- 不执行不采纳项

## 验收标准

- [ ] review target 为相对 `diff_anchor` 的 working tree + index（`git diff <diff_anchor>` 与 `git diff --cached`），全文无 `git diff <diff_anchor>...HEAD` 作为 review 证据源
- [ ] Round 1 零 finding 可直接收尾；Round 2 FAIL 为 blocked，不自动回 step 6 无限循环
- [ ] 拆分阶段要求填写 spec/plan（验收标准非空）；step 7 不写 `docs/specs/`；存在「需求完结」固化 specs 路径
- [ ] backlog 建目录规则与 `tasks_index.md` 一致；未填模板 dropped 可不归档
- [ ] 单 task step 1 创建/切换 `task_tnnn_slug` 并校验分支；`log.md` 模板含 `diff_anchor`
- [ ] 旧 `review_prompt.md` 已从树中删除；新 prompt 含零发现合法、finding 边界、`.fill()` 调查制、`read-only 边界`、项目根用 `git rev-parse`
- [ ] 严格模式含 reviewer 撤回争议路径；用户批准 exception 不改写 reviewer verdict，tasks_index / task_report 可记录
- [ ] README 与 AGENTS 语义一致（含 schemas/config、specs 固化时机）
- [ ] finding 标题分隔符统一为 ` - `；严重度以 conventions 为唯一完整定义
- [ ] `adoption.md` 标为可选；笔误/事实类有判定规则

## 依赖与约束

- 决策来源：`docs/reviews/review_20260720_2346/adoption_decision.md`
- 纯文档变更；`{test_cmd}` / `{blackbox_cmd}` 未配置时，黑盒用一致性检索代替
