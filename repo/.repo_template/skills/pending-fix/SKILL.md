---
name: pending-fix
description: 不建 task，在主仓 main 直修小而低风险的 pending 条目：逐条复现、修复、跑项目门禁、归档并提交一个修复 commit。用户要求直接修 pending 或粘贴 /pending-fix 时调用。
disable-model-invocation: true
metadata:
  opencode/autoinvoke: "false"
---

# pending-fix

在主仓 main 直修小而低风险的 `docs/pending/todo/` 条目，每条一个修复 commit。不建 task、不 start、不进 attempt 与 review 流程；超出准入的条目转回 task 流程。

## 组合契约

仅用户显式授权入口或 registry.json 已登记父流程可执行；作为子流程时由父流程直接读取本正文，原样传递参数、identity、owner_id 与授权，不扩大授权。完整规则见 `.repo_template/docs/usage.md`「skill 调用」。

## 授权与队列

- 显式 `pNNN` 列表：执行范围严格锁定为给定条目，按 pNNN 升序。
- 无参数调用：候选为 `pending.py list --state todo` 的全部条目，按 pNNN 升序；parked 不在范围内。
- 开始前列出队列（pNNN、标题、kind）并向用户确认；同意即授权队列内每条的修复 commit，不逐条询问。不 push。

## 准入

每条轮到时按当前 main 判定，前面的修复可能已改变现场：

- 问题仍存在；已不存在、已由其它条目或 effective task 覆盖时跳过并报告，不自动归档。
- 改动小且低风险：不涉及安全、鉴权、资金、并发、迁移、协议兼容，不新增依赖，不改变 `docs/specs/`、`docs/blueprint/` 描述的长期行为或接口契约。
- 根因清楚；根因未落到可验证机制的 bug 跳过，建议用户先经 `bug-to-pending` 分析，本 skill 不代做根因研究。

不满足准入时跳过该条，报告原因并建议经 `task-from-pending` 立项，继续下一条。

## 流程

主仓 `git status --short` 必须干净后才开始一条；有无关改动时停止并报告，不 stash、不覆盖。

1. 读条目、`AGENTS.md`、`docs/blueprint/conventions.md` 与相关代码；在 `.scratch/` 最小复现。
2. 可测行为先写失败测试，再修复。测试须触达生产逻辑；不弱化断言、不 mock 被测逻辑、不就地把旧测试预期改成当前输出。
3. 运行 `docs/blueprint/testing.md` 中与改动相关的 `test_cmd`；改动触及用户或调用方可观察行为且 `blackbox_verify` 不是「无」时一并执行。`verification_policy` 不是 `project_configured` 时停止。
4. 检查新增调试输出与临时文件，运行项目 lint/format，`git diff --check`，Markdown 用 `md_format.py --changed`。
5. `pending.py archive {pNNN} --resolution resolved --fix-ref main --reason "{修复要点；验证命令与结果}" --write`。
6. 只暂存本条修复、测试与 pending 迁移的明确路径，提交一个 commit：`fix: {摘要} ({pNNN})`。核对 commit 未混入无关改动。

## 停止

- 测试、黑盒或格式门禁无法闭环：停止整个队列，保留现场（未提交改动留在工作区），报告失败命令、关键输出和已做改动，不擅自回退。
- 需要密钥、账号、外部环境或产品决策：停止整个队列并说明所需输入。
- 用户指定停止。

队列结束或停止时报告：已修复条目（pNNN 与 commit SHA）、跳过条目与原因（含建议立项项）、停止点与剩余队列。

## 边界

只写修复涉及的代码、测试、文档与当前 pending 条目；不写 task 目录、tasks_index、ledger 与其它 pending。修复中发现的新问题用 `pending.py new` 登记，不混入当前 commit。
