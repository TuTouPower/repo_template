import type { BoardData, BoardSummary, TaskCategory, TaskStatus } from '@/types/board'
import type { Chain, ChainPlan, DeferredTask } from '@/types/chain'
import {
  CHAIN_COLORS,
  chainName,
  computeCrossings,
  isUnfinished,
} from '@/lib/chainPlan'

// ---------------------------------------------------------------------------
// 当下可执行批次推荐算法(纯前端、确定性)
//
// 语义(与用户对齐):
//   1. 只推荐"当下能跑"的链:链首 = 依赖全部满足的 active / runnable /
//      blocked_conflict 任务(backlog 不在候选内,category 语义为"暂不可跑")。
//      active = 已经在跑的链,继续展示;其余是新推荐。
//   2. 冲突裁决:候选间有 conflict 边时,迭代暂缓"剩余候选中冲突最多"的任务,
//      并列时暂缓编号最小的 —— 让本批可并行的链最多。
//      active 已在执行,天然胜出(与 active 冲突的直接暂缓)。
//   3. 链向下游延伸:每一步挑"未完成依赖已全部落在本链内"的最小 id 后继;
//      遇到三类情况停止:没有后继 / 后继还要等别的链或未来批次(汇合点) /
 //     后继因冲突被暂缓。**交叉节点执行完即停**:若刚加入链的节点被某个
//      "还依赖链外任务"的汇合点等待,链在此截止 —— 完成它后重新规划。
//   4. 每批是当前状态的快照;执行完若干链后 applyCompletions 重新计算,
//      得到下一批。
// ---------------------------------------------------------------------------

const CATEGORY_STATUS: Record<TaskCategory, TaskStatus> = {
  active: 'active',
  runnable: 'backlog',
  blocked_deps: 'blocked',
  blocked_conflict: 'blocked',
  backlog: 'backlog',
  done: 'done',
  done_unmerged: 'done',
  dropped: 'dropped',
}

/** 未完成(5 种)+ 待合入,都会阻塞下游 */
function blocksDownstream(cat: TaskCategory | undefined): boolean {
  return cat !== undefined && (isUnfinished(cat) || cat === 'done_unmerged')
}

function num(id: string): number {
  return Number(id.slice(1))
}

/** 未完成子图索引(含上游表) */
interface Subgraph {
  ids: string[]
  idSet: Set<string>
  catOf: Map<string, TaskCategory>
  /** id → 阻塞它的上游(未完成任务 + 待合入任务;已合入的 done 不算) */
  upstream: Map<string, string[]>
  /** id → 依赖它的未完成任务(下游) */
  downstream: Map<string, string[]>
  /** 阻塞上游数量(含待合入) */
  indegree: Map<string, number>
  /** 冲突邻接(对称,只含未完成节点;待合入的对手单独按"等合入"处理) */
  conflictOf: Map<string, string[]>
  /** 冲突对手为待合入的节点:等对方合入后才轮到它 */
  blockedByUnmerged: Set<string>
}

function buildSubgraph(data: BoardData): Subgraph {
  const idSet = new Set<string>()
  const catOf = new Map<string, TaskCategory>()
  const unmergedSet = new Set<string>()
  for (const n of data.nodes) {
    if (isUnfinished(n.category)) {
      idSet.add(n.id)
      catOf.set(n.id, n.category)
    } else if (n.category === 'done_unmerged') {
      unmergedSet.add(n.id)
      catOf.set(n.id, n.category)
    }
  }
  const ids = [...idSet].sort((a, b) => num(a) - num(b))

  const upstream = new Map<string, string[]>()
  const downstream = new Map<string, string[]>()
  const indegree = new Map<string, number>()
  const conflictOf = new Map<string, string[]>()
  const blockedByUnmerged = new Set<string>()
  for (const id of ids) {
    upstream.set(id, [])
    downstream.set(id, [])
    indegree.set(id, 0)
    conflictOf.set(id, [])
  }
  for (const e of data.edges) {
    if (e.type === 'dep') {
      if (!idSet.has(e.to)) continue
      if (idSet.has(e.from)) {
        downstream.get(e.from)?.push(e.to)
        upstream.get(e.to)?.push(e.from)
        indegree.set(e.to, (indegree.get(e.to) ?? 0) + 1)
      } else if (unmergedSet.has(e.from)) {
        // 待合入同样阻塞下游(未合入不能在其上继续)
        upstream.get(e.to)?.push(e.from)
        indegree.set(e.to, (indegree.get(e.to) ?? 0) + 1)
      }
    } else {
      if (idSet.has(e.from) && idSet.has(e.to)) {
        conflictOf.get(e.from)?.push(e.to)
        conflictOf.get(e.to)?.push(e.from)
      } else if (idSet.has(e.from) && unmergedSet.has(e.to)) {
        blockedByUnmerged.add(e.from)
      } else if (unmergedSet.has(e.from) && idSet.has(e.to)) {
        blockedByUnmerged.add(e.to)
      }
    }
  }
  for (const list of downstream.values()) list.sort((a, b) => num(a) - num(b))
  return { ids, idSet, catOf, upstream, downstream, indegree, conflictOf, blockedByUnmerged }
}

/**
 * 计算当下可执行批次。
 * 返回:chains(按链首 id 升序编号 A/B/C…)、deferred(冲突暂缓)、
 * unassigned(未来批次)、crossings(交叉点)。
 */
export function computeBatchPlan(data: BoardData): ChainPlan {
  const sub = buildSubgraph(data)

  // 1. 候选链首:未完成入度为 0(依赖全部满足)的 active/runnable/blocked_conflict
  const candidates = sub.ids.filter(
    (id) =>
      (sub.indegree.get(id) ?? 0) === 0 &&
      !sub.blockedByUnmerged.has(id) &&
      ['active', 'runnable', 'blocked_conflict'].includes(sub.catOf.get(id) as string),
  )
  const activeHeads = candidates.filter((id) => sub.catOf.get(id) === 'active')
  const activeSet = new Set(activeHeads)
  let rest = candidates.filter((id) => !activeSet.has(id))

  // 2. 冲突裁决:迭代暂缓"剩余冲突最多"者(并列暂缓 id 最小者)
  const deferredList: Array<{ taskId: string; partners: string[] }> = []
  // 与 active 冲突的直接暂缓(active 已在执行)
  for (const id of rest) {
    const partners = (sub.conflictOf.get(id) ?? []).filter((c) => activeSet.has(c))
    if (partners.length > 0) deferredList.push({ taskId: id, partners: partners.sort() })
  }
  const deferredSet = new Set(deferredList.map((d) => d.taskId))
  rest = rest.filter((id) => !deferredSet.has(id))

  for (;;) {
    const remaining = rest.filter((id) => !deferredSet.has(id))
    const degree = new Map<string, number>()
    let maxDeg = 0
    for (const id of remaining) {
      const d = (sub.conflictOf.get(id) ?? []).filter(
        (c) => remaining.includes(c),
      ).length
      degree.set(id, d)
      if (d > maxDeg) maxDeg = d
    }
    if (maxDeg === 0) break
    const victim = remaining
      .filter((id) => (degree.get(id) ?? 0) === maxDeg)
      .sort((a, b) => num(a) - num(b))[0]
    const partners = (sub.conflictOf.get(victim) ?? [])
      .filter((c) => remaining.includes(c))
      .sort((a, b) => num(a) - num(b))
    deferredList.push({ taskId: victim, partners })
    deferredSet.add(victim)
  }

  const winners = rest.filter((id) => !deferredSet.has(id))

  // 3. 建链:链首按 id 升序,依次延伸
  const heads = [...activeHeads, ...winners].sort((a, b) => num(a) - num(b))
  const assigned = new Set<string>()
  const chains: Chain[] = []

  for (const head of heads) {
    if (assigned.has(head)) continue
    const taskIds: string[] = [head]
    const chainSet = new Set<string>([head])
    assigned.add(head)

    for (;;) {
      const tail = taskIds[taskIds.length - 1]
      // 交叉即停:tail 被某个"还依赖链外任务"的汇合点等待 → 执行完 tail 重新规划
      const isCrossing = (sub.downstream.get(tail) ?? []).some(
        (y) =>
          sub.idSet.has(y) &&
          !chainSet.has(y) &&
          (sub.upstream.get(y) ?? []).some((p) => !chainSet.has(p)),
      )
      if (isCrossing) break
      // 延伸:阻塞依赖全部落在本链内的最小 id 后继
      const cands = (sub.downstream.get(tail) ?? []).filter(
        (id) =>
          !assigned.has(id) &&
          (sub.upstream.get(id) ?? []).every((p) => chainSet.has(p)),
      )
      if (cands.length === 0) break
      const next = cands.sort((a, b) => num(a) - num(b))[0]
      taskIds.push(next)
      chainSet.add(next)
      assigned.add(next)
    }

    const idx = chains.length
    chains.push({
      id: `c${idx + 1}`,
      name: chainName(idx),
      color: CHAIN_COLORS[idx % CHAIN_COLORS.length],
      taskIds,
    })
  }

  // 4. 未来批次 + 暂缓清单
  const unassigned = sub.ids.filter((id) => !assigned.has(id) && !deferredSet.has(id))
  const headSet = new Set(heads)
  const deferred: DeferredTask[] = deferredList
    .map(({ taskId, partners }) => {
      // blockedBy:最终胜出的冲突对手(进链首集合的)
      const blockedBy = partners.filter((p) => headSet.has(p) || activeSet.has(p))
      const shown = blockedBy.length > 0 ? blockedBy : partners
      return {
        taskId,
        blockedBy: shown,
        reason: `与 ${shown.join('、')} 冲突,本轮暂缓(优先让可并行的链最多)`,
      }
    })
    .sort((a, b) => num(a.taskId) - num(b.taskId))

  return {
    chains,
    unassigned,
    deferred,
    crossings: computeCrossings(chains, data),
  }
}

/** 链的停止原因(展示在链卡底部) */
export function chainStopInfo(chain: Chain, plan: ChainPlan, data: BoardData): string {
  const tail = chain.taskIds[chain.taskIds.length - 1]
  if (!tail) return ''
  const catOf = new Map(data.nodes.map((n) => [n.id, n.category]))
  const isUnfin = (id: string) => {
    const c = catOf.get(id)
    return c !== undefined && isUnfinished(c)
  }
  const blocks = (id: string) => blocksDownstream(catOf.get(id))
  const chainSet = new Set(chain.taskIds)
  const chainOf = new Map<string, string>()
  for (const c of plan.chains) for (const t of c.taskIds) chainOf.set(t, c.id)
  const nameOf = new Map(plan.chains.map((c) => [c.id, c.name]))
  const deferredSet = new Set(plan.deferred.map((d) => d.taskId))

  const upstream = new Map<string, string[]>()
  for (const e of data.edges) {
    if (e.type !== 'dep') continue
    const list = upstream.get(e.to)
    if (list) list.push(e.from)
    else upstream.set(e.to, [e.from])
  }
  const successors = data.edges
    .filter((e) => e.type === 'dep' && e.from === tail && isUnfin(e.to))
    .map((e) => e.to)

  if (successors.length === 0) return '已到 DAG 末端'
  const parts: string[] = []
  for (const s of successors) {
    if (chainSet.has(s)) continue
    if (deferredSet.has(s)) {
      parts.push(`后继 ${s} 本轮冲突暂缓`)
    } else if (chainOf.has(s)) {
      parts.push(`后继 ${s} 属于${nameOf.get(chainOf.get(s) as string)}`)
    } else {
      const others = (upstream.get(s) ?? []).filter((p) => blocks(p) && !chainSet.has(p))
      if (others.length > 0) {
        const desc = others
          .map((p) => {
            if (catOf.get(p) === 'done_unmerged') return `${p} 合入 main`
            if (chainOf.has(p)) return `${nameOf.get(chainOf.get(p) as string)}的 ${p}`
            return p
          })
          .join('、')
        parts.push(`停在汇合点 ${s}(还需 ${desc})`)
      } else {
        parts.push(`${s} 下批可跑`)
      }
    }
  }
  return parts.join(';')
}

// ---------------------------------------------------------------------------
// 模拟执行循环(对齐真实节奏:agent 执行完 → 任务"待合入" → 合入 main → 重新计划)
// ---------------------------------------------------------------------------

/**
 * 模拟完成:把一组任务标记为 done_unmerged(执行完但还没合入 main)。
 * 未合入不会解锁任何下游 —— 下游要等合入后才能在其上继续。
 */
export function applyCompletions(data: BoardData, completedIds: string[]): BoardData {
  const done = new Set(completedIds)
  const nodes = data.nodes.map((n) =>
    done.has(n.id)
      ? { ...n, status: 'done' as TaskStatus, category: 'done_unmerged' as TaskCategory }
      : { ...n },
  )
  return { project: data.project, summary: recount(nodes), nodes, edges: data.edges }
}

/**
 * 模拟合入 main:全部 done_unmerged → done,并按契约语义重算剩余任务的
 * category(依赖全部满足 → runnable;冲突对手全部了结 → runnable)。
 */
export function applyMerge(data: BoardData): BoardData {
  const nodes = data.nodes.map((n) =>
    n.category === 'done_unmerged'
      ? { ...n, status: 'done' as TaskStatus, category: 'done' as TaskCategory }
      : { ...n },
  )
  const catOf = new Map(nodes.map((n) => [n.id, n.category]))
  const blocks = (id: string) => blocksDownstream(catOf.get(id))
  const unfinished = (id: string) => {
    const c = catOf.get(id)
    return c !== undefined && isUnfinished(c)
  }
  for (const n of nodes) {
    if (!isUnfinished(n.category)) continue
    const depBlocked = n.depends_on.some(blocks)
    const confBlocked = n.conflicts_with.some(unfinished)
    let cat = n.category
    if (!depBlocked) {
      if (cat === 'blocked_deps') cat = confBlocked ? 'blocked_conflict' : 'runnable'
      else if (cat === 'blocked_conflict' && !confBlocked) cat = 'runnable'
    }
    if (cat !== n.category) {
      n.category = cat
      n.status = CATEGORY_STATUS[cat]
    }
  }
  return { project: data.project, summary: recount(nodes), nodes, edges: data.edges }
}

function recount(nodes: BoardData['nodes']): BoardSummary {
  const ALL: TaskCategory[] = [
    'active', 'runnable', 'blocked_deps', 'blocked_conflict', 'backlog',
    'done_unmerged', 'done', 'dropped',
  ]
  const summary = {} as BoardSummary
  for (const c of ALL) summary[c] = 0
  for (const n of nodes) summary[n.category] += 1
  return summary
}
