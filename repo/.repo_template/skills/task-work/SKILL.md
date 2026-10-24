---
name: task-work
description: 内部实施流程：task-run 读取正文后在登记 worktree 实施并完成 review，止于一个执行 commit。
disable-model-invocation: true
user-invocable: false
metadata:
  opencode/autoinvoke: "false"
---

# task-work

在已登记的 task worktree 中完成一个 task，止于一个执行 commit。主仓 attempt、cleanup 和 merge 由 `task-run` 负责。项目骨架与状态权威见 `AGENTS.md`；工具链写权和调用契约见 `.repo_template/docs/usage.md`。

## 组合契约

仅用户显式授权入口或 registry.json 已登记父流程可执行。本流程作为子流程时，由父流程直接读取本 canonical `SKILL.md` 并执行正文，不调用宿主原生 Skill 工具；原样传递参数、exact identity、owner_id 和写域。父流程授权不扩大，缺少所需授权时停止。调用关系以 `.repo_template/skills/registry.json` 为权威。

## 输入与写域

必填 `tid`、正整数 `attempt`、非空 `execution_id`、本会话 `owner_id`；主仓 ledger 的 claim 必须一致。当前目录、分支、worktree ownership 和 handoff identity 必须一致。

只写当前 task 的实现、测试、文档、pending/finding 和 task 文件；不写主仓控制面，不 merge、不 push、不清理自己的 worktree，不修改其它 task 状态。

## 三道硬门禁

### 项目测试

先确认 verification_policy=project_configured，verification_notes 写明适用门禁和不适用理由；模板默认或未定义策略即停止。运行 `docs/blueprint/testing.md` 定义的相关测试、lint、类型和构建检查。代码或测试在通过后变化，应重跑相关检查。不得通过弱化断言、mock 掉被测逻辑或只改预期制造假绿。

### 黑盒验证

`docs/blueprint/testing.md` 的 `blackbox_verify` 章节正文不是「无」时必须执行，并触达用户或调用方可观察行为。失败后修复并重验；达到 `verify_limit` 停止并报告。黑盒为「无」必须由项目明确选择并记录替代验收，不能继承未确认模板默认，更不能伪写通过。

### 独立 review

实施或 reviewer 发现鉴权、迁移、协议等风险扩大时，在自身 worktree 用 `task.py review-upgrade {tid} --reason ...` 将 single 升 full；不得降级，升级后旧 review 失效。按 `review_level` 渲染 prompt并派 reviewer。最终报告必须 PASS、finding 全部处置且 `reviewed_scope` 对应最终内容；FAIL 修复后重审，INCOMPLETE 按 checker 的 `next_action` 补报告、处置或重审。达到 `review_limit` 停止并报告。review 后改交付内容必须重新 review。done 后 review stale 见 recovery 证据修复（amend 同一执行 commit），不 rewind。

## 实施

1. 读取 task.md、note.md、`AGENTS.md`、项目约定和现有现场，运行 preflight 并携 `--owner-id {owner_id}`。`UNVERIFIED-SPIKE` 先实验并将标记改写为结论；一次性实验仅写 .scratch，若建立正式 spike，必须记录 sid，补全 report、finding、采纳结论、后续 tid 或无需后续理由，并按 repo-hygiene 归档；随后必须运行 `task.py preflight {tid} --require-verified --owner-id {owner_id}`，严格 PASS 后再实施。
2. Agent 自主决定阅读、调试和实现顺序。对可测试行为，默认先建立失败测试或复现证据；不适用时在实施笔记简述原因。
3. 实现 AC，并依次通过项目测试和黑盒门禁。
4. 逐项检查 finalization：累积或更新 `docs/specs/<slug>.md`，同步 `docs/specs_index.md`（在表即生效）；废弃 spec 删除索引行并归档；不适用时在 note.md 明确理由（`- specs_index: not_applicable: 具体原因`）。更新因实现而过时的 blueprint、guides、README 或接口文档。派审前用 `git ls-files --others --exclude-standard` 找出本 task 新文件，剔除无关、临时和 `.scratch/` 内容后，对明确路径执行 `git add -N -- <path...>`，使新文件进入 reviewer 可见的 diff；然后完成最终 review。
5. 收尾前检查新增调试输出、临时文件和未解释 TODO，运行项目定义的 lint/format/typecheck，并执行 `git diff --check`。检查方式按技术栈选择，不使用通用固定 grep。
6. 对实际遇到且不属于本 task 的重要问题，登记 pending 或 finding；不要求为了收尾额外全仓扫描，也不得顺手混入旁支修复。已由本 task 闭环的来源 pending 用 `pending.py archive ... --resolution resolved --fix-ref {tid} --reason 已验证闭环 --write` 归档；可跨 task 复用的已验证事实用 `findings.py new` 抽取。
7. 更新 note.md 的实施、验证、review 和结果摘要；遗留 finding 必须指向 `pNNN` 或 follow-up tid。再次运行 `task.py preflight {tid} --require-verified --owner-id {owner_id}`（在 worktree 内直接运行，不带 `--ref`），防收尾重新引入未知契约。
8. 写完整 `handoff.json`，其 identity、branch、base、测试、黑盒、review、AC evidence、pending 和 findings 与当前执行一致。
9. `task.py finish {tid}`，用归档路径再次检查 review 状态，然后把本 task 全部改动提交为一个执行 commit。期间不创建正式中间 commit。

## 完成

交给 task-run：tid、正整数 attempt、非空 execution_id、分支与执行 commit SHA，并附测试、黑盒、review、pending/finding 和 worktree 摘要。停止时改为报告门禁、所需输入和恢复入口。
