---
name: task-work
description: 内部实施流程：task-run 读取正文后在登记 worktree 实施并完成 review，止于一个实现 commit。
disable-model-invocation: true
user-invocable: false
metadata:
  opencode/autoinvoke: "false"
---

# task-work

在已登记的 task worktree 中完成一个 task，止于 `finish` 提交的一个实现 commit。主仓 run-begin、run-close 和 merge 由 `task-run` 负责。项目骨架与状态权威见 `AGENTS.md`；工具链写权和调用契约见 `.repo_template/docs/usage.md`。

## 组合契约

仅用户显式授权入口或 registry.json 已登记父流程可执行；作为子流程时由父流程直接读取本正文，原样传递 tid、owner_id、写域与授权，不扩大授权。完整规则见 `.repo_template/docs/usage.md`「skill 调用」。

## 输入与写域

必填 `tid` 与本会话 `owner_id`；主仓 ledger 的 claim 必须一致（`run-begin` 已领取）。attempt 与 execution_id 由脚本从 `docs/runtime/execution_context.json` 解析，不手动传递。当前目录、分支和 worktree ownership 必须一致。

只写当前 task 的实现、测试、文档、pending/finding 和 task 文件（`handoff.json` 由 `finish` 生成，不手写）；不写主仓控制面，不 merge、不 push、不清理自己的 worktree，不修改其它 task 状态。

## 三道硬门禁

### 项目测试

先确认 verification_policy=project_configured，verification_notes 写明适用门禁和不适用理由；模板默认或未定义策略即停止。运行 `docs/blueprint/testing.md` 定义的相关测试、lint、类型和构建检查。代码或测试在通过后变化，应重跑相关检查。不得通过弱化断言、mock 掉被测逻辑或只改预期制造假绿。

### 黑盒验证

`docs/blueprint/testing.md` 的 `blackbox_verify` 章节正文不是「无」时必须执行，并触达用户或调用方可观察行为。失败后修复并重验；达到 `verify_limit` 停止并报告。黑盒为「无」必须由项目明确选择并记录替代验收，不能继承未确认模板默认，更不能伪写通过。

### 独立 review

实施或 reviewer 发现鉴权、迁移、协议等风险扩大时，在自身 worktree 用 `task.py review-upgrade {tid} --reason ...` 将 single 升 full；不得降级，升级后旧 review 失效。按 `review_level` 渲染 prompt并派 reviewer（reviewer 负责审查代码质量并独立复核实施 Agent 的 AC 打勾真实性）。最终报告必须 PASS、finding 全部处置且 `reviewed_scope` 对应最终内容；FAIL 修复后重审，INCOMPLETE 按 checker 的 `next_action` 补报告、处置或重审。review 判 FAIL 且随后要修改交付内容时，先把 `task.md` 全部 AC 撤回为 `- [ ]`，修复后重跑测试与黑盒，逐条重新打勾，再派下一轮 review；只补报告或处置表、不改交付内容的 INCOMPLETE 不撤回。reviewer 不改 `task.md`。达到 `review_limit` 停止并报告。review 后改交付内容必须重新 review。done 后 review stale 按 recovery 的 review 修复处理，禁止 rewind、reserve。

## 实施

1. 读取 task.md、progress.md、`AGENTS.md`、项目约定和现有现场，运行 `task.py preflight {tid} --owner-id {owner_id}`。`UNVERIFIED-SPIKE` 先实验并将标记改写为结论；一次性实验仅写 .scratch，若建立正式 spike，必须记录 sid，补全 report、finding、采纳结论、后续 tid 或无需后续理由，并按 repo-hygiene 归档；改写后加 `--require-verified` 再跑 preflight，严格 PASS 后再实施。无 spike 时 `finish` 内含同一严格 preflight，不必提前重复。
2. Agent 自主决定阅读、调试和实现顺序。对可测试行为，默认先建立失败测试或复现证据；不适用时在实施笔记简述原因。
3. 实现 AC，依次通过项目测试和黑盒门禁；验证通过后在 `task.md` 验收标准中就地将对应条目勾选为 `- [x]`。
4. 逐项检查 finalization：累积或更新 `docs/specs/<slug>.md`，同步 `docs/specs_index.md`（在表即生效；格式按 `.repo_template/docs/usage.md` 的 specs_index 契约）；废弃 spec 删除索引行并归档；不适用时在 progress.md 明确理由（`- specs_index: not_applicable: 具体原因`）。更新因实现而过时的 blueprint、guides、README 或接口文档。派审前用 `git ls-files --others --exclude-standard` 找出本 task 新文件，剔除无关、临时和 `.scratch/` 内容后，对明确路径执行 `git add -N -- <path...>`，使新文件进入 reviewer 可见的 diff；然后完成最终 review。
5. 收尾前检查新增调试输出、临时文件和未解释 TODO，运行项目定义的 lint/format/typecheck，并执行 `git diff --check`。检查方式按技术栈选择，不使用通用固定 grep。
6. 对实际遇到且不属于本 task 的重要问题，登记 pending 或 finding；不要求为了收尾额外全仓扫描，也不得顺手混入旁支修复。已由本 task 闭环的来源 pending 用 `pending.py archive ... --resolution resolved --fix-ref {tid} --reason 已验证闭环 --write` 归档；可跨 task 复用的已验证事实用 `findings.py new` 抽取。
7. 更新 progress.md 的实施、review 结果和结果摘要；遗留 finding 必须指向 `pNNN` 或 follow-up tid。在「收尾报告 / 验收与验证」填写 `- 测试：`、`- 黑盒：`、`- review：` 三行实际验证摘要，不得为空或「待执行时填写」；`finish` 据此生成 `handoff.json`。
8. 暂存全部交付文件：`git add` 实现、测试、文档、pending/finding、specs 等本 task 改动（`git add -N` 不算暂存）；task 目录由 `finish` 自行处理。无关或临时文件删除或移入 `.scratch/`。把实现 commit message 写入仓外或 `.scratch/` 的文件。
9. `task.py finish {tid} --owner-id {owner_id} --message-file {path}` 完成校验、原位写 done、生成回执并提交实现 commit；归档由 `integrate` 完成。前置检查失败不写入，提交失败则回滚 finish 自身写入，修复后重跑；中断或回滚失败按 `recovery.md`「finish 回滚语义」处理。期间不手写 handoff、不手动 `git commit`、不创建正式中间 commit。

## 完成

交给 task-run：tid、分支与最终 commit SHA，附测试、黑盒、review、pending/finding 和 worktree 摘要。已提交后的 review 修复按 `recovery.md` 处理；停止时报告门禁、所需输入和恢复入口。
