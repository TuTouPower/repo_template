import type { BoardData, TaskCategory } from '@/types/board'
import type {
  Chain,
  ChainPlan,
  CrossChainConflict,
  Crossing,
  PlanValidation,
} from '@/types/chain'

// ---------------------------------------------------------------------------
// 链式规划公共工具(批次语义 v3)
//
// 本文件只放公共件:category 判断、链色、交叉点检测、方案校验与手动微调操作。
// 「当下可执行批次」的推荐算法见 batchPlan.ts。
//
// 交叉点(v3 语义):dep 边 from → to,from 在某条链内、to 不在同一条链
// (to 属于别的链,或还没进本批的未来批次汇合点),则 from 是交叉点节点。
// ---------------------------------------------------------------------------

export const UNFINISHED_CATEGORIES: TaskCategory[] = [
  'active',
  'runnable',
  'blocked_deps',
  'blocked_conflict',
  'backlog',
]

const UNFINISHED_SET = new Set<TaskCategory>(UNFINISHED_CATEGORIES)

export function isUnfinished(category: TaskCategory): boolean {
  return UNFINISHED_SET.has(category)
}

/** 链色调色板(与 category 的浅填充区分开,用作描边/徽标) */
export const CHAIN_COLORS = [
  '#0284C7', // sky-600
  '#7C3AED', // violet-600
  '#D97706', // amber-600
  '#059669', // emerald-600
  '#DB2777', // pink-600
  '#4F46E5', // indigo-600
  '#0D9488', // teal-600
  '#EA580C', // orange-600
  '#65A30D', // lime-600
  '#9333EA', // purple-600
]

const CHAIN_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

export function chainName(i: number): string {
  return i < CHAIN_LETTERS.length ? `链 ${CHAIN_LETTERS[i]}` : `链 ${i + 1}`
}

export function chainLetter(name: string): string {
  return name.replace('链 ', '')
}

/** taskId → 所属 chainId */
export function chainOfMap(chains: Chain[]): Map<string, string> {
  const m = new Map<string, string>()
  for (const c of chains) for (const t of c.taskIds) m.set(t, c.id)
  return m
}

/**
 * 交叉点检测(v3):
 * 遍历 dep 边 from → to,若 from 在某条链内、to 是未完成任务且不在同一条链,
 * 则 from 为交叉点节点。to 可能属于别的链,也可能还没进本批(chainId='future',
 * 即未来批次的汇合点)。
 */
export function computeCrossings(chains: Chain[], data: BoardData): Crossing[] {
  const chainOf = chainOfMap(chains)
  const catOf = new Map(data.nodes.map((n) => [n.id, n.category]))
  const crossings: Crossing[] = []
  for (const e of data.edges) {
    if (e.type !== 'dep') continue
    const toCat = catOf.get(e.to)
    if (!toCat || !isUnfinished(toCat)) continue // to 已完成 → 不存在等待
    const fromChain = chainOf.get(e.from)
    if (!fromChain) continue // from 不在本批任何链上
    const toChain = chainOf.get(e.to)
    if (toChain === fromChain) continue
    crossings.push({
      chainId: toChain ?? 'future',
      nodeId: e.to,
      dependsOnChainId: fromChain,
      dependsOnNodeId: e.from,
    })
  }
  crossings.sort((a, b) =>
    a.dependsOnNodeId === b.dependsOnNodeId
      ? a.nodeId.localeCompare(b.nodeId)
      : Number(a.dependsOnNodeId.slice(1)) - Number(b.dependsOnNodeId.slice(1)),
  )
  return crossings
}

/** 编辑后统一收尾:重算交叉点(deferred/unassigned 原样保留) */
export function finalizePlan(plan: ChainPlan, data: BoardData): ChainPlan {
  return { ...plan, crossings: computeCrossings(plan.chains, data) }
}

/** 跨链冲突对:同一冲突对被分进了不同链(手动调整后可能出现,需要提示) */
export function findCrossChainConflicts(
  chains: Chain[],
  data: BoardData,
): CrossChainConflict[] {
  const chainOf = chainOfMap(chains)
  const out: CrossChainConflict[] = []
  for (const e of data.edges) {
    if (e.type !== 'conflict') continue
    const aChain = chainOf.get(e.from)
    const bChain = chainOf.get(e.to)
    if (!aChain || !bChain || aChain === bChain) continue
    out.push({ aId: e.from, aChainId: aChain, bId: e.to, bChainId: bChain })
  }
  return out
}

// ---------------------------------------------------------------------------
// 合法性校验
// ---------------------------------------------------------------------------

/** a 是否能沿 dep 边到达 b(路径可经过任意节点) */
function reachable(data: BoardData, a: string, b: string): boolean {
  const downstream = new Map<string, string[]>()
  for (const e of data.edges) {
    if (e.type !== 'dep') continue
    const list = downstream.get(e.from)
    if (list) list.push(e.to)
    else downstream.set(e.from, [e.to])
  }
  const seen = new Set<string>([a])
  const stack = [a]
  while (stack.length > 0) {
    const cur = stack.pop() as string
    for (const next of downstream.get(cur) ?? []) {
      if (next === b) return true
      if (!seen.has(next)) {
        seen.add(next)
        stack.push(next)
      }
    }
  }
  return false
}

/**
 * 校验切链方案:
 *  · 每个任务至多属于一条链;
 *  · 链内相邻任务之间必须存在 dep 路径(拓扑连通);
 * 不合法只报告,不阻止(用户对手动调整负责)。
 */
export function validatePlan(plan: ChainPlan, data: BoardData): PlanValidation {
  const chainIssues: Record<string, string[]> = {}
  const globalIssues: string[] = []

  const seen = new Map<string, string>()
  for (const c of plan.chains) {
    for (const t of c.taskIds) {
      const prev = seen.get(t)
      if (prev) globalIssues.push(`任务 ${t} 同时出现在 ${prev} 和 ${c.id}`)
      else seen.set(t, c.id)
    }
  }
  for (const t of plan.unassigned) {
    if (seen.has(t)) globalIssues.push(`任务 ${t} 既在未来批次又在 ${seen.get(t)}`)
  }
  for (const d of plan.deferred) {
    if (seen.has(d.taskId)) globalIssues.push(`暂缓任务 ${d.taskId} 同时出现在 ${seen.get(d.taskId)}`)
  }

  for (const c of plan.chains) {
    const issues: string[] = []
    for (let i = 0; i + 1 < c.taskIds.length; i++) {
      const a = c.taskIds[i]
      const b = c.taskIds[i + 1]
      if (!reachable(data, a, b)) {
        issues.push(`${a} → ${b} 之间没有依赖路径,链在此断开`)
      }
    }
    chainIssues[c.id] = issues
  }
  return { chainIssues, globalIssues }
}

// ---------------------------------------------------------------------------
// 手动微调操作(纯函数,返回带新交叉点的方案)
// ---------------------------------------------------------------------------

function findChain(chains: Chain[], chainId: string): Chain | undefined {
  return chains.find((c) => c.id === chainId)
}

function sortIds(ids: string[]): string[] {
  return [...ids].sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)))
}

/** 把任务从所在链中移除(回到未来批次) */
export function removeTask(plan: ChainPlan, data: BoardData, taskId: string): ChainPlan {
  const chains = plan.chains.map((c) =>
    c.taskIds.includes(taskId) ? { ...c, taskIds: c.taskIds.filter((t) => t !== taskId) } : c,
  )
  const unassigned = plan.unassigned.includes(taskId)
    ? plan.unassigned
    : sortIds([...plan.unassigned, taskId])
  return finalizePlan({ ...plan, chains, unassigned }, data)
}

/** 把任务加入某条链的指定位置(越界则接到链尾);若已在别处(含暂缓区)先移除 */
export function insertTask(
  plan: ChainPlan,
  data: BoardData,
  chainId: string,
  taskId: string,
  index: number,
): ChainPlan {
  const strippedChains = plan.chains.map((c) =>
    c.taskIds.includes(taskId) ? { ...c, taskIds: c.taskIds.filter((t) => t !== taskId) } : c,
  )
  const strippedUnassigned = plan.unassigned.filter((t) => t !== taskId)
  const strippedDeferred = plan.deferred.filter((d) => d.taskId !== taskId)
  const chains = strippedChains.map((c) => {
    if (c.id !== chainId) return c
    const at = Math.max(0, Math.min(index, c.taskIds.length))
    const taskIds = [...c.taskIds.slice(0, at), taskId, ...c.taskIds.slice(at)]
    return { ...c, taskIds }
  })
  return finalizePlan(
    { ...plan, chains, unassigned: strippedUnassigned, deferred: strippedDeferred },
    data,
  )
}

/** 在 index 处拆分一条链:[0, index) 留在原链,[index, …) 成为新链 */
export function splitChain(
  plan: ChainPlan,
  data: BoardData,
  chainId: string,
  index: number,
): ChainPlan {
  const target = findChain(plan.chains, chainId)
  if (!target || index <= 0 || index >= target.taskIds.length) return plan

  const usedNames = new Set(plan.chains.map((c) => c.name))
  let newIdx = plan.chains.length
  while (usedNames.has(chainName(newIdx))) newIdx += 1
  const usedColors = new Set(plan.chains.map((c) => c.color))
  const color =
    CHAIN_COLORS.find((c) => !usedColors.has(c)) ??
    CHAIN_COLORS[newIdx % CHAIN_COLORS.length]

  const newChain: Chain = {
    id: `c${Date.now().toString(36)}${newIdx}`,
    name: chainName(newIdx),
    color,
    taskIds: target.taskIds.slice(index),
  }
  const chains: Chain[] = []
  for (const c of plan.chains) {
    if (c.id === chainId) {
      chains.push({ ...c, taskIds: c.taskIds.slice(0, index) })
      chains.push(newChain)
    } else {
      chains.push(c)
    }
  }
  return finalizePlan({ ...plan, chains }, data)
}

/** 合并两条链:把 second 拼到 first 的链尾,删除 second */
export function mergeChains(
  plan: ChainPlan,
  data: BoardData,
  firstChainId: string,
  secondChainId: string,
): ChainPlan {
  if (firstChainId === secondChainId) return plan
  const first = findChain(plan.chains, firstChainId)
  const second = findChain(plan.chains, secondChainId)
  if (!first || !second) return plan
  const merged: Chain = { ...first, taskIds: [...first.taskIds, ...second.taskIds] }
  const chains = plan.chains
    .filter((c) => c.id !== firstChainId && c.id !== secondChainId)
    .concat(merged)
    .sort((a, b) => a.name.localeCompare(b.name))
  return finalizePlan({ ...plan, chains }, data)
}

/** 链进度:链首即"当前任务";ready = 链内 active/runnable 计数 */
export function chainProgress(chain: Chain, data: BoardData): {
  ready: number
  total: number
  currentTaskId: string | null
} {
  const catOf = new Map(data.nodes.map((n) => [n.id, n.category]))
  let ready = 0
  for (const t of chain.taskIds) {
    const cat = catOf.get(t)
    if (cat === 'active' || cat === 'runnable') ready += 1
  }
  return {
    ready,
    total: chain.taskIds.length,
    currentTaskId: chain.taskIds.length > 0 ? chain.taskIds[0] : null,
  }
}
