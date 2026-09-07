---
name: task-work
description: none
---

# task-work

在已登记的 task worktree 中完成一个 task，止于一个执行 commit。主仓 attempt、cleanup 和 merge 由 `task-run` 负责。项目骨架与状态权威见 `AGENTS.md`；工具链写权和调用契约见 `.repo_template/docs/usage.md`。

## 输入与写域

必填 `tid`、正整数 `attempt`、非空 `execution_id`。当前目录、分支、worktree ownership 和 handoff identity 必须一致。

只写当前 task 的实现、测试、文档、pending/finding 和 task 文件；不写主仓控制面，不 merge、不 push、不清理自己的 worktree，不修改其它 task 状态。

## 三道硬门禁

### 项目测试

运行 `docs/blueprint/testing.md` 定义的相关测试、lint、类型和构建检查。代码或测试在通过后变化，应重跑相关检查。不得通过弱化断言、mock 掉被测逻辑或只改预期制造假绿。

### 黑盒验证

`docs/blueprint/testing.md` 的 `blackbox_verify` 章节正文不是「无」时必须执行，并触达用户或调用方可观察行为。失败后修复并重验；达到 `verify_limit` 停止并报告。项目未定义黑盒命令时明确记录“未定义”，不能伪写通过。

### 独立 review

按 `review_level` 渲染 prompt并派 reviewer。最终报告必须 PASS、finding 全部处置且 `reviewed_scope` 对应最终内容；FAIL 修复后重审，INCOMPLETE 按 checker 的 `next_action` 补报告、处置或重审。达到 `review_limit` 停止并报告。review 后改交付内容必须重新 review。

## 实施

1. 读取 spec、task、`AGENTS.md`、项目约定和现有现场，运行 preflight。`UNVERIFIED-SPIKE` 先实验并将标记改写为结论；随后必须运行 `task.py preflight {tid} --require-verified`，严格 PASS 后再实施。
2. Agent 自主决定阅读、调试和实现顺序。对可测试行为，默认先建立失败测试或复现证据；不适用时在实施笔记简述原因。
3. 实现 AC，并依次通过项目测试和黑盒门禁。
4. 更新因实现而过时的 specs、blueprint、guides、README 或接口文档。派审前用 `git ls-files --others --exclude-standard` 找出本 task 新文件，剔除无关、临时和 `.scratch/` 内容后，对明确路径执行 `git add -N -- <path...>`，使新文件进入 reviewer 可见的 diff；然后完成最终 review。
5. 收尾前检查新增调试输出、临时文件和未解释 TODO，运行项目定义的 lint/format/typecheck，并执行 `git diff --check`。检查方式按技术栈选择，不使用通用固定 grep。
6. 对实际遇到且不属于本 task 的重要问题，登记 pending 或 finding；不要求为了收尾额外全仓扫描，也不得顺手混入旁支修复。已由本 task 闭环的来源 pending 用 `pending.py archive ... --fix-ref {tid} --write` 归档；可跨 task 复用的已验证事实用 `findings.py new` 抽取。
7. 更新 task.md 的实施、验证、review 和结果摘要；遗留 finding 必须指向 `pNNN` 或 follow-up tid。再次运行 `task.py preflight {tid} --require-verified`，防收尾重新引入未知契约。
8. 写完整 `handoff.json`，其 identity、branch、base、测试、黑盒、review、AC evidence、pending 和 findings 与当前执行一致。
9. `task.py finish {tid}`，用归档路径再次检查 review 状态，然后把本 task 全部改动提交为一个执行 commit。期间不创建正式中间 commit。

## 完成

交给 task-run：tid、正整数 attempt、非空 execution_id、分支与执行 commit SHA，并附测试、黑盒、review、pending/finding 和 worktree 摘要。停止时改为报告门禁、所需输入和恢复入口。
