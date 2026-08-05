# Attempt 生命周期闭环

本文描述已实施的统一 attempt 生命周期。串行 `task-run` 与并行 `task-dispatch` 共用同一控制面；两者只在 branch topology、executor 与合并时机上不同。

## Exact identity 与 provenance

每次执行的 exact identity 是：

```text
(tid, attempt, execution_id)
```

- `tid` 标识 task。
- `attempt` 是同一 tid 的递增执行轮次。
- `execution_id` 由 reserve 原子生成，是本次执行 provenance；所有迟到通知、observation、terminal、report、cleanup 与 integrate 都按它精确归属。
- `executor` 只有 `inline` / `agent`。`task-run` 使用 inline，`task-dispatch` 使用 agent。
- `host_worker_id` 只保存 agent 宿主任务句柄，供 coordinator 查询宿主状态；它不参与 identity，不代替 `execution_id`，也不进入 worker handoff。

## 生命周期命令

生命周期只通过 `task.py attempt` 子命令写入：

```bash
python3 scripts/repo_template/task.py attempt reserve TID --executor inline|agent [--model MODEL]
python3 scripts/repo_template/task.py attempt bind TID --attempt N --execution-id ID [--host-worker-id HOST_ID]
python3 scripts/repo_template/task.py attempt terminal TID --attempt N --execution-id ID --status completed|failed|stopped
python3 scripts/repo_template/task.py attempt report TID --attempt N --execution-id ID --status done|blocked|failed [--sha SHA] [--class infra|task|contract] [--reason REASON]
python3 scripts/repo_template/task.py attempt escalate TID --attempt N --execution-id ID --reason REASON
python3 scripts/repo_template/task.py attempt silent-alert TID --attempt N --execution-id ID --fingerprint SHA256
```

### Reserve 与 running

`attempt reserve` 在文件锁内完成 current 检查、attempt 递增、`execution_id` 生成和 JSON 输出，是 identity 的唯一来源。

- inline reserve 直接进入 `running`。
- agent reserve 进入 `reserved`；Agent 启动后，coordinator 以 reserve 返回的 identity 调用 `attempt bind`，可附 `host_worker_id`，随后进入 `running`。
- 当前 attempt 为 `reserved` / `running` 时禁止 reserve。
- terminal `failed` / `stopped` 或 exact report=`failed` 才可机械 retry；completed attempt 不可被新 reserve 顶掉，必须先 integrate 或显式 escalate。已 integrated attempt 不可重新 reserve。
- attempt 号必须是非 bool 的正整数；不存在串行例外、nullable identity 或“无控制面”兼容路径。

### Terminal 与 report

terminal 与 report 表达两层不同事实：

- `terminal completed|failed|stopped` 是 executor/宿主终态。
- `report done|blocked|failed` 是 task 业务结果。

coordinator 总是先写 terminal，再以同一 exact identity 写 report。terminal completed 不自动表示业务 done；业务 blocked 可以对应正常返回的 terminal completed。失败分类与 reason 写在 report，不用宿主句柄推断。

`escalate` 将已 terminal 的 exact attempt 转入人工处置；`silent-alert` 只记录某个 running identity 的某个 fingerprint 已告警，不改变 terminal/report，也不允许取消或重派。

## Attempt 控制面

`docs/runtime/dispatch_ledger.jsonl` 是全部执行拓扑共用、仅主仓存在、gitignored、append-only 的 attempt 控制面。名称保留路径兼容，不代表它只服务并行 dispatch。

写入边界：

- 生命周期：`task.py attempt reserve/bind/terminal/report/escalate/silent-alert`。
- 仓库观察：`task.py observe` 写 `observation`。
- 合并完成：`task.py integrate` / `integrate-chain` 写 exact `integrated`。
- 运维备注：`task.py ledger record` 只允许 `note` / `breaker`。
- 查询：`task.py ledger tail` 只读。

`ledger record` 的公共写面固定为上述运维备注，不参与 attempt 状态投影。

current attempt 的所有推导都以完整 identity 为准。旧 attempt 的迟到 terminal/report/escalate/integrated 不得结束、锁住或覆盖新 attempt；未 terminal 时出现新 identity 属于 overlap 异常，不能静默吞掉任一执行。

## Observation 精确归属

观察命令必须提供完整 identity：

```bash
python3 scripts/repo_template/task.py observe TID --attempt N --execution-id ID [--json]
```

`observation` 精确绑定 `(tid, attempt, execution_id)`。命令只接受 current、已 bind 的 `executor=agent` running identity，并验证 worktree、branch 与 tid ownership；首次或 fingerprint 变化时追加 observation，未变化时只返回诊断信息。inline attempt 不 observe、不参与 silent hold。

## Handoff 契约

worker 只在自身分支写 `handoff.json`，不写 attempt 控制面。字段全部必填：

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

- `attempt` 是非 bool 的正整数，`execution_id` 是非空字符串，二者必须与调用 task-work 的 identity 一致。
- `tests`、`blackbox`、`review` 都是非空字符串。
- `pending`、`findings` 都是字符串数组；没有条目时写 `[]`。
- `base_sha` 是执行 commit 前 HEAD 的完整 SHA，必须同时等于 task front matter `diff_anchor` 与 branch tip first parent；链上后继还须等于紧邻 predecessor tip。
- 所有字段逐项必填。

## Cleanup 与单 task integrate

所有 topology 的 cleanup 都使用同一命令：

```bash
python3 scripts/repo_template/task.py cleanup-worktree TID --attempt N --execution-id ID
```

它同时校验 exact current identity、completed terminal、无 overlap、branch tip、完整 handoff、base_sha first parent、worktree clean 与 ownership。任一失败时不删除 worktree。

单 task 合并只使用：

```bash
python3 scripts/repo_template/task.py integrate TID --attempt N --execution-id ID [--continue]
```

`integrate` 只处理一个 exact identity。成功后合并该分支、重建 index、写 exact integrated、删除已合入分支；冲突解决并 `git add` 后，以原 identity 加 `--continue` 续跑。

## 链式 aggregate transaction

链式合并使用独立入口：

```bash
python3 scripts/repo_template/task.py integrate-chain TAIL_TID [--continue]
```

`integrate-chain` 根据 Git first-parent ancestry 识别线性链，并在任何 merge 前对每个成员执行 aggregate gate：exact current attempt、completed terminal、handoff、task status、worktree cleanup、base_sha/branch tip 与成员 ancestry 全部一致。

门禁通过后在 Git dir 下写 `repo-task/integrate-chain.json` snapshot，固定主干基点、链尾、成员顺序、各成员 exact identity 与 tip。transaction 依次经过 `prepared → merged(merge_sha) → indexed(index_sha) → awaiting_verification → complete`。

只 merge 链尾一次。index 完成后，所有成员 integrated 在一次 ledger 锁内先整体预检，再幂等批量追加；任一成员失败时整批不写。merge/index/integrated 完成后停在 `awaiting_verification`，transaction 与全部链分支保留。coordinator 完成外部合并后验证后，再执行同一 `integrate-chain TAIL_TID --continue` 删除分支并清除 transaction。

预检失败时零 merge、零 integrated。冲突或 merge 后收尾失败时保留 phase、`merge_sha`、`index_sha` 与成员 snapshot；`--continue` 只能恢复同一事务。验证失败不得执行最终 continue。

## 两种 topology

### task-run：链式 inline

每个 task 固定执行：

```text
start
→ attempt reserve --executor inline
→ task-work(tid, attempt, execution_id)
→ attempt terminal
→ attempt report
→ cleanup-worktree exact identity
```

每个 task 一个执行 commit；中间只 cleanup，后继 `start --base` 指向前一 task 分支。全链完成后只进行一次链尾 merge。合并授权可在启动时取得，也可在首次 `integrate-chain` 前只询问一次；已有授权不重复询问。`awaiting_verification` 后验证通过再用 `--continue` 完成分支与 transaction 清理。

### task-dispatch：扇出 agent

每个初始 task 固定执行：

```text
start
→ attempt reserve --executor agent
→ Agent prompt 携带 tid/attempt/execution_id
→ Agent 启动后 bind host_worker_id
→ 查询宿主终态
→ attempt terminal exact identity
→ attempt report exact identity
→ cleanup-worktree exact identity
→ integrate exact identity
```

silent 只告警并暂停自动调度，不取消 Agent、不写失败、不重派。新 attempt 只能在 current attempt terminal 后 reserve。
