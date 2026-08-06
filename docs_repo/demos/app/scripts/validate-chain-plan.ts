/**
 * 批次推荐(当下可执行批次)+ 模拟执行循环 校验脚本
 * 运行:npx tsx --tsconfig tsconfig.validate.json scripts/validate-chain-plan.ts
 */
import type { BoardData, TaskCategory } from '@/types/board'
import type { ChainPlan } from '@/types/chain'
import { DATASETS, getBoard, type DatasetId } from '@/lib/mockData'
import { isUnfinished } from '@/lib/chainPlan'
import { applyCompletions, applyMerge, computeBatchPlan } from '@/lib/batchPlan'

let failures = 0
function check(cond: boolean, msg: string) {
  if (cond) console.log(`  ✓ ${msg}`)
  else {
    failures += 1
    console.error(`  ✗ ${msg}`)
  }
}
function num(id: string) { return Number(id.slice(1)) }

interface Ctx {
  data: BoardData
  plan: ChainPlan
  unfinSet: Set<string>
  blockedSet: Set<string>
  upstream: Map<string, string[]>
  conflictOf: Map<string, string[]>
  conflictBlocked: Map<string, string[]>
  catOf: Map<string, TaskCategory>
}

function ctxOf(data: BoardData, plan: ChainPlan): Ctx {
  const unfinSet = new Set<string>()
  const blockedSet = new Set<string>() // 未完成 + 待合入(done 未合入也算阻塞)
  const catOf = new Map<string, TaskCategory>()
  for (const n of data.nodes) {
    catOf.set(n.id, n.category)
    if (isUnfinished(n.category)) unfinSet.add(n.id)
    if (isUnfinished(n.category) || n.category === 'done_unmerged') blockedSet.add(n.id)
  }
  const upstream = new Map<string, string[]>()
  const conflictOf = new Map<string, string[]>()
  const conflictBlocked = new Map<string, string[]>() // 冲突对手含待合入
  for (const n of data.nodes) {
    upstream.set(n.id, n.depends_on.filter((d) => blockedSet.has(d)))
    conflictOf.set(n.id, n.conflicts_with.filter((c) => unfinSet.has(c)))
    conflictBlocked.set(n.id, n.conflicts_with.filter((c) => blockedSet.has(c)))
  }
  return { data, plan, unfinSet, blockedSet, upstream, conflictOf, conflictBlocked, catOf }
}

/** 通用批次合法性检查 */
function checkBatch(ctx: Ctx, label: string) {
  const { plan, unfinSet, blockedSet, upstream, conflictOf, conflictBlocked, catOf } = ctx
  console.log(`\n[${label}]`)
  const heads = plan.chains.map((c) => c.taskIds[0])
  const headSet = new Set(heads)

  // 1. 链首合法:无阻塞依赖(未完成/待合入),无待合入冲突对手,category 可跑
  for (const h of heads) {
    check((upstream.get(h) ?? []).length === 0, `${h} 链首无阻塞依赖`)
    const unmergedConf = (conflictBlocked.get(h) ?? []).filter((p) => !unfinSet.has(p))
    check(unmergedConf.length === 0, `${h} 链首无待合入冲突对手(${unmergedConf.join(',') || '无'})`)
    check(
      ['active', 'runnable', 'blocked_conflict'].includes(catOf.get(h) as string),
      `${h} 链首 category(${catOf.get(h)})可跑`,
    )
  }
  // 1b. 待合入任务不参与任何链
  for (const c of plan.chains)
    for (const t of c.taskIds)
      check(!blockedSet.has(t) || unfinSet.has(t), `${t} 不是待合入却进链`)
  // 2. 链内非首任务:其全部未完成依赖都在本链内且位于它之前
  for (const c of plan.chains) {
    const pos = new Map(c.taskIds.map((t, i) => [t, i]))
    for (let i = 1; i < c.taskIds.length; i++) {
      const t = c.taskIds[i]
      const bad = (upstream.get(t) ?? []).filter((p) => !pos.has(p) || (pos.get(p) as number) >= i)
      check(bad.length === 0, `${c.name} 的 ${t} 的未完成依赖都在链内前方(违规:${bad.join(',') || '无'})`)
    }
  }
  // 3. 分配互斥
  const seen = new Set<string>()
  let dup = false
  for (const c of plan.chains) for (const t of c.taskIds) { if (seen.has(t)) dup = true; seen.add(t) }
  check(!dup, '没有任务同时属于两条链')
  check(!plan.deferred.some((d) => seen.has(d.taskId)), '暂缓任务不在任何链中')
  check(!plan.unassigned.some((t) => seen.has(t)), '未来批次不在任何链中')
  // 4. 覆盖:未完成 = 链内 + 暂缓 + 未来批次
  const covered = new Set([...seen, ...plan.deferred.map((d) => d.taskId), ...plan.unassigned])
  const missing = [...unfinSet].filter((id) => !covered.has(id))
  check(missing.length === 0, `未完成任务全覆盖(遗漏:${missing.join(',') || '无'})`)
  // 5. 冲突裁决:链首之间(含与运行中链首)不存在 conflict 边
  const headArr = [...headSet]
  let conflictHeads = ''
  for (let i = 0; i < headArr.length; i++)
    for (let j = i + 1; j < headArr.length; j++)
      if ((conflictOf.get(headArr[i]) ?? []).includes(headArr[j])) conflictHeads = `${headArr[i]}↔${headArr[j]}`
  check(conflictHeads === '', `链首之间无互斥(违规:${conflictHeads || '无'})`)
  // 6. 暂缓任务都与某个链首(或 active)冲突
  for (const d of plan.deferred) {
    const partners = (conflictOf.get(d.taskId) ?? []).filter((p) => headSet.has(p))
    check(partners.length > 0, `暂缓 ${d.taskId} 确有冲突对手在派发(${partners.join('、')})`)
  }
  // 7. 交叉点:每条 crossing 的被等方在链内、等待方不在同链
  const chainOf = new Map<string, string>()
  for (const c of plan.chains) for (const t of c.taskIds) chainOf.set(t, c.id)
  for (const x of plan.crossings) {
    check(chainOf.get(x.dependsOnNodeId) === x.dependsOnChainId, `交叉点 ${x.dependsOnNodeId} 方向正确`)
    check(chainOf.get(x.nodeId) !== x.dependsOnChainId, `交叉等待方 ${x.nodeId} 不在同链`)
  }
}

// ---------------------------------------------------------------------------
// 1. 两套数据集:契约结构 + 批次合法性
// ---------------------------------------------------------------------------
for (const meta of DATASETS) {
  const data = getBoard(meta.id)
  // 契约:edges 两端存在、去重;summary 一致
  const idSet = new Set(data.nodes.map((n) => n.id))
  const edgeKeys = new Set<string>()
  let edgeOk = true
  for (const e of data.edges) {
    if (!idSet.has(e.from) || !idSet.has(e.to)) edgeOk = false
    const k = `${e.type}|${e.from}|${e.to}`
    if (edgeKeys.has(k)) edgeOk = false
    edgeKeys.add(k)
  }
  check(edgeOk, `${meta.id} 契约:edges 两端存在且去重`)
  const sum: Record<string, number> = {}
  for (const n of data.nodes) sum[n.category] = (sum[n.category] ?? 0) + 1
  check(
    Object.entries(data.summary).every(([k, v]) => sum[k] === v),
    `${meta.id} 契约:summary 与 nodes 一致`,
  )
  check(
    (data.summary.done_unmerged ?? 0) > 0,
    `${meta.id} 含待合入(done_unmerged)样例(${data.summary.done_unmerged ?? 0} 个)`,
  )
  checkBatch(ctxOf(data, computeBatchPlan(data)), `${meta.id} 批次#1 合法性`)
}

// ---------------------------------------------------------------------------
// 2. 小型示例:与用户示例对齐的确定性快照
// ---------------------------------------------------------------------------
console.log('\n[small 批次#1 快照]')
{
  const data = getBoard('small')
  const plan = computeBatchPlan(data)
  const inChain = new Set(plan.chains.flatMap((c) => c.taskIds))
  const unmergedIds = data.nodes.filter((n) => n.category === 'done_unmerged').map((n) => n.id)
  check(
    unmergedIds.every((id) => !inChain.has(id)),
    `待合入任务不在任何链中(${unmergedIds.join(' ')})`,
  )
  const heads = plan.chains.map((c) => c.taskIds[0]).sort((a, b) => num(a) - num(b))
  check(
    JSON.stringify(heads) === JSON.stringify(['t976', 't982', 't991', 't994', 't997', 't998']),
    `链首 = [t976 t982 t991 t994 t997 t998](实际:${heads.join(' ')})`,
  )
  const chainA = plan.chains.find((c) => c.taskIds[0] === 't976')
  check(
    JSON.stringify(chainA?.taskIds) === JSON.stringify(['t976', 't977', 't978', 't979']),
    `链A = t976 t977 t978 t979(实际:${chainA?.taskIds.join(' ')})`,
  )
  const chainB = plan.chains.find((c) => c.taskIds[0] === 't982')
  check(
    JSON.stringify(chainB?.taskIds) === JSON.stringify(['t982', 't983']),
    `链B = t982 t983(停在汇合点 t984)`,
  )
  const defIds = plan.deferred.map((d) => d.taskId)
  check(
    JSON.stringify(defIds) === JSON.stringify(['t987', 't996', 't999']),
    `暂缓 = [t987 t996 t999](实际:${defIds.join(' ')})`,
  )
}

// ---------------------------------------------------------------------------
// 3. 模拟执行循环:完成 t991/t997 → 待合入(不解锁)→ 合入 main → 批次#2
// ---------------------------------------------------------------------------
console.log('\n[small 模拟循环:完成 991/997 → 待合入 → 合入 → 批次#2]')
{
  let data = getBoard('small')
  let plan = computeBatchPlan(data)
  const initialUnmerged = data.summary.done_unmerged ?? 0
  const done = plan.chains
    .filter((c) => ['t991', 't997'].includes(c.taskIds[0]))
    .flatMap((c) => c.taskIds)

  // 3a. 完成 → done_unmerged:不解锁任何下游/冲突
  data = applyCompletions(data, done)
  const catOf = new Map(data.nodes.map((n) => [n.id, n.category]))
  check(
    done.every((id) => catOf.get(id) === 'done_unmerged'),
    `完成的 ${done.join(' ')} 进入待合入`,
  )
  check(
    (data.summary.done_unmerged ?? 0) === initialUnmerged + done.length,
    `待合入计数 ${initialUnmerged} + ${done.length} = ${data.summary.done_unmerged}`,
  )
  plan = computeBatchPlan(data)
  const preHeads = plan.chains.map((c) => c.taskIds[0])
  check(!preHeads.includes('t987'), `合入前不推荐 t987(链首:${preHeads.join(' ')})`)
  check(!preHeads.includes('t996') && !preHeads.includes('t999'), '合入前不推荐 t996/t999')
  check(preHeads.includes('t976') && preHeads.includes('t982'), '合入前仍包含运行中的 链A/链B')
  checkBatch(ctxOf(data, plan), 'small 合入前(完成未合入)合法性')

  // 3b. 合入 main → done:解锁下游/冲突,重算得批次#2
  data = applyMerge(data)
  check((data.summary.done_unmerged ?? 0) === 0, '合入后待合入清零')
  const catOf2 = new Map(data.nodes.map((n) => [n.id, n.category]))
  check(done.every((id) => catOf2.get(id) === 'done'), '合入后完成项变为 done')
  plan = computeBatchPlan(data)
  const heads = plan.chains.map((c) => c.taskIds[0]).sort((a, b) => num(a) - num(b))
  check(heads.includes('t987'), `批次#2 推荐了刚解锁的 t987(链首:${heads.join(' ')})`)
  check(heads.includes('t976') && heads.includes('t982'), '批次#2 仍包含运行中的 链A/链B')
  check(heads.includes('t996') && heads.includes('t999'), '批次#2 解锁了 t996/t999')
  check(
    JSON.stringify(plan.deferred.map((d) => d.taskId)) === JSON.stringify(['t998']),
    `批次#2 暂缓 = [t998](实际:${plan.deferred.map((d) => d.taskId).join(' ')})`,
  )
  checkBatch(ctxOf(data, plan), 'small 批次#2(合入后)合法性')
}

// ---------------------------------------------------------------------------
// 4. 大型示例:多轮模拟直至收敛(每批完成全部链 → 合入 → 下一批)
// ---------------------------------------------------------------------------
console.log('\n[large 收敛性:连续模拟 12 批(每批完成后合入)]')
{
  let data = getBoard('large' as DatasetId)
  let remaining = data.nodes.filter((n) => isUnfinished(n.category)).length
  let batches = 0
  for (let i = 0; i < 12 && remaining > 0; i++) {
    const plan = computeBatchPlan(data)
    const done = plan.chains.flatMap((c) => c.taskIds)
    check(done.length > 0 || plan.deferred.length === 0, `批次#${i + 1} 有 ${done.length} 个任务可完成`)
    if (done.length === 0) {
      // 无可执行:剩余应全为 backlog(待规划,不参与批次推荐)
      const remCats = new Set(
        data.nodes.filter((n) => isUnfinished(n.category)).map((n) => n.category),
      )
      check(
        remCats.size === 1 && remCats.has('backlog'),
        `批次#${i + 1} 停滞时剩余全为 backlog(实际:${[...remCats].join(',')})`,
      )
      break
    }
    data = applyCompletions(data, done)
    check(
      (data.summary.done_unmerged ?? 0) > 0,
      `批次#${i + 1} 完成后存在待合入(${data.summary.done_unmerged})`,
    )
    data = applyMerge(data)
    batches += 1
    const now = data.nodes.filter((n) => isUnfinished(n.category)).length
    check(now < remaining, `批次#${i + 1} 后剩余未完成 ${now}(< ${remaining})`)
    remaining = now
  }
  console.log(`  · ${batches} 批后剩余未完成 ${remaining} 个`)
}

console.log(failures === 0 ? '\nALL CHECKS PASSED' : `\n${failures} CHECK(S) FAILED`)
process.exit(failures === 0 ? 0 : 1)
