---
name: task-integrate
description: none
disable-model-invocation: true
---

# task-integrate

把已完成 task 分支合并进本地主干。本 skill 由 `task-run` 在队列链尾（或单 task 路径）调用，是主仓主干合并的唯一入口。合并时机与分支形态见 `AGENTS.md`「职责分工与合并时机」。

单 task 与链式聚合是两个独立入口：

- `integrate TID --attempt N --execution-id ID [--continue]`：只合一个 task。
- `integrate-chain TAIL_TID [--continue]`：链式 aggregate transaction，只合链尾一次。

链式聚合不复用单 task 子命令；单 task cleanup 与 integrate 始终要求完整 identity。

## 前提

- 已取得会话级合并授权。`task-run` 在整链完成后询问一次、用户同意后调用；单独调用本 skill 视为用户当次授权。
- 待合成员已在自身分支完成一个执行 commit，并写入完整 `handoff.json`。
- 待合成员已按 exact identity `(tid, attempt, execution_id)` 写入 terminal 与 report。
- 只在主仓主干调用本 skill；不 push、不动远程。

## Handoff 契约

每个成员的 `handoff.json` 必须包含且只按以下类型验证：

```json
{
  "tid": "t001",
  "attempt": 1,
  "execution_id": "0123456789abcdef0123456789abcdef",
  "status": "done",
  "branch": "t001_example",
  "base_sha": "0123456789abcdef0123456789abcdef01234567",
  "tests": "pytest -q：120 passed",
  "blackbox": "黑盒命令通过",
  "review": "第 2 轮 PASS",
  "pending": ["p047"],
  "findings": ["d012"]
}
```

`tests`、`blackbox`、`review` 都是非空字符串；`pending`、`findings` 都是字符串数组，可为空数组；全部字段必填。`attempt` 必须是非 bool 的正整数。`base_sha` 是执行 commit 前的 HEAD，必须同时等于 task front matter 的 `diff_anchor` 与 branch tip 的 first parent 完整 SHA；该机械等式保证一个 task 恰有一个执行 commit。链上后继成员的 `base_sha` 还必须等于紧邻前一成员 branch tip。

## 单 task：cleanup + integrate

### 1. Exact cleanup

```bash
scripts/repo_template/task.py cleanup-worktree {tid} \
  --attempt {N} --execution-id {EXECUTION_ID}
```

cleanup 永远要求 `--attempt` 与 `--execution-id`，并在删除 worktree 前同时通过：

1. identity 精确等于该 tid 的 current attempt；
2. executor terminal 为 `completed`；
3. 不存在未闭环或非法重叠 attempt；
4. branch tip 与 task 终态一致；
5. `handoff.json` identity、status、branch、字段类型与 `base_sha == branch tip first parent` 全部成立；
6. 登记 worktree、当前分支、路径和 tid ownership 一致，worktree clean；
7. 分支 tip 确实包含本 task 的一个执行 commit，且未混入不属于本 task 的所有权异常。

任一门禁失败即保留 worktree、分支和控制面原状，停止并报告；不得换 identity 或降级为无参数命令。

### 2. Exact single integrate

cleanup 成功后执行：

```bash
scripts/repo_template/task.py integrate {tid} \
  --attempt {N} --execution-id {EXECUTION_ID}
```

`integrate` 只处理这个 exact identity，重新验证 current、completed terminal、无 overlap、handoff、branch tip 与 worktree 已清理，再执行 `merge --no-ff`。成功后重建派生 index 并单独 commit，再写该 identity 的 `integrated`，删除已完全合入的 task 分支，最后执行合并后验证。已 exact integrated 的重入按幂等结果处理，不得把其他 attempt 视为已合入。

### 3. 单 task 冲突续跑

脚本停在冲突态并列出文件时，按双方语义解决并 `git add`，随后用原 identity 继续：

```bash
scripts/repo_template/task.py integrate {tid} \
  --attempt {N} --execution-id {EXECUTION_ID} --continue
```

`--continue` 重新验证同一 exact identity 与当前冲突事务；不得改用新 attempt/execution_id。

## 链式：integrate-chain aggregate transaction

`task-run` 对每个成员完成 exact cleanup 后，只调用一次：

```bash
scripts/repo_template/task.py integrate-chain {TAIL_TID}
```

`integrate-chain` 根据 Git ancestry 与 attempt 控制面识别从主干基点到链尾的线性成员，建立聚合事务。调用方不传单个 identity；脚本对每个成员读取并锁定其 exact `(tid, attempt, execution_id)`。

### Aggregate gate

任何 merge 前，所有成员必须整体通过：

1. **线性 ancestry**：各成员分支沿 first-parent 形成无分叉、无跳跃的链；后继确实以紧邻前一成员分支为 base，链尾包含全部前置执行 commit。
2. **exact attempt**：每个成员 identity 都是该 tid 的 current attempt，不存在未闭环或非法重叠 attempt。
3. **terminal**：每个成员 terminal=`completed`。
4. **handoff**：每个成员 handoff 的 tid/attempt/execution_id/status/branch 与控制面、refs 完全一致；三项结果为非空字符串，两项条目为字符串数组。
5. **commit/base**：每个成员 branch tip 是其执行 commit，`base_sha` 是该 tip 的 first parent 完整 SHA；相邻成员的 ancestry 与记录 base 一致。
6. **task status**：每个成员在自身 tip 中为 `done`，active/archive 形态正确。
7. **worktree**：每个成员已用其 exact identity cleanup，无残留登记或 ownership/dirty 异常。
8. **主干与分支**：主干、链成员集合和各 tip 与事务预期一致，待删分支都可由链尾覆盖。

预检任一失败时，事务 **零 merge、零 integrated**；不写部分成员 integrated，不重建 index，不删任何链分支。

### 可恢复 transaction phases

aggregate gate 全通过后，脚本在 Git dir 下写 `repo-task/integrate-chain.json`。snapshot 固定主干基点、链尾、成员顺序、每成员 exact identity 与 branch tip，并以阶段推进：

```text
prepared -> merged(merge_sha) -> indexed(index_sha)
         -> awaiting_verification -> complete(transaction 删除)
```

- `prepared` 在 merge 前落盘；冲突时保留该阶段和 Git merge state。
- merge commit 一旦成功立即记录 `phase=merged` 与 `merge_sha`。即使进程在 phase 写入前中断，`--continue` 也只能在 `HEAD^1=base_head`、`HEAD^2=tail_sha` 时认领该 merge commit。
- `indexed` 记录 index 维护后的 `index_sha`；若进程在 index commit 后、phase 写入前中断，只接受 `HEAD` 为 `merge_sha` 或其紧邻 index 维护 commit。
- 成员 `integrated` 在一次 ledger 锁内完成**全量预检 + 幂等批量追加**。任一成员 identity、terminal 或既有 merge_sha 不符时整批拒绝；恢复重放不会重复事件，也不会留下成员级部分写入。
- 写完 integrated 后进入 `awaiting_verification`。此时 merge/index 已发生，但 transaction 与全部链分支必须保留。

### 合并后验证与最终 finalize

首次 `integrate-chain {TAIL_TID}` 或冲突恢复的第一次 `--continue` 最多推进到 `awaiting_verification`，不会删除分支或 transaction。随后执行 `docs/blueprint/testing.md` 声明的合并后验证：

- 验证失败：停止，保留 transaction、exact integrated 与所有链分支；不得调用最终 continue。
- 验证通过：再次执行同一命令，作为显式 finalize 确认：

```bash
scripts/repo_template/task.py integrate-chain {TAIL_TID} --continue
```

命令重验 `tail_tid`、`index_sha == HEAD`、成员 branch SHA、exact identity、handoff、worktree 与 integrated batch 后，幂等删除整条链分支并清除 transaction。分支删除中断时保留 transaction；再次执行会跳过已删分支并继续剩余成员。

### 链式冲突与收尾恢复

发生冲突时按双方语义解决并 `git add`，再以原 tail 执行 `--continue`。命令必须读取 transaction，确认 `MERGE_HEAD`、成员 snapshot 与 exact identity 均未漂移，然后完成原 merge并推进到 `awaiting_verification`。`merged`、`indexed` 阶段的基础设施失败也使用同一命令恢复；不得创建新事务掩盖旧事务。

若 transaction 文件（`.git/repo-task/integrate-chain.json`）损坏且 `--continue` 报错无法读取，且 Git 层已无活跃 merge state（`git rev-parse --verify MERGE_HEAD` 失败），可手动删除该文件以清除残留事务。删除前确认主干 HEAD 未停在未完成 merge——若有 `MERGE_HEAD`，先 `git merge --abort`。手动删除后再次从 `integrate-chain {TAIL_TID}` 开始全新事务。

## 冲突处置

| 冲突位置 | 处置 |
|----------|------|
| `docs/blueprint/` | 真语义冲突。读双方意图后合成一致表述，不叠加两段 |
| `docs/specs_index.md` | 两侧各加一行；保留双方，按 slug 排序 |
| `docs/tasks_index.json` | 派生缓存。先解决为可继续状态，成功后由脚本重建覆盖 |
| `docs/tasks/{tid}/` 与 `docs/archive/tasks/{tid}/` | task 归档是 rename；保留 archive 侧、删除 active 侧 |
| 源码 / 测试 | 读双方改动意图；无法确定时停止，报告给用户 |

条目化账本（`docs/pending/`、`docs/findings/`）为一条目一文件，正常不冲突；若出现同号不同文件，说明取号锁被绕过，停止并报告。

## 合并后验证与边界

- 执行 `docs/blueprint/testing.md` 声明的合并后动作与验证。失败时停止后续 integrate，准确报告主干已发生的 merge/index 状态，交用户裁决，不盲目重试或回退。
- `task-work` 交出汇报中提到的跨 task 影响，在合并后主干另行处置；不得混入 merge、integrated 或 index commit。
- 本 skill 不修改 task 内容、spec、代码或测试；发现门禁外内容需改动时停止并报告。
- 不启动 task、不执行 task、不写 handoff.json。

## 完成

单 task 报告：`{tid}` 的 exact identity、cleanup 结果、merge commit、integrated、index commit、分支删除或保留、合并后验证结果。

链式报告：链尾、snapshot 成员顺序与各 exact identity、aggregate gate、transaction phase、唯一 merge commit、原子批量 integrated、index commit、合并后验证、最终 continue、删除的链分支与 transaction 清理结果。未到 `complete` 时报告保留的 phase、分支与正确恢复命令。
