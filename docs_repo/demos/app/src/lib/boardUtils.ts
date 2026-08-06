import type { BoardData, TaskCategory, TaskNode } from '@/types/board'

// ---------------------------------------------------------------------------
// 分类元数据(中文名、说明、低饱和暖色配色)
// ---------------------------------------------------------------------------

export interface CategoryMeta {
  key: TaskCategory
  label: string
  hint: string
  /** 徽标样式 */
  badgeClass: string
  /** 列头左侧色条 / 计数点 */
  accentClass: string
  /** 卡片左侧色条 */
  cardBarClass: string
  /** 是否重点分类 */
  focus: boolean
}

export const CATEGORY_META: Record<TaskCategory, CategoryMeta> = {
  active: {
    key: 'active',
    label: '运行中',
    hint: 'Active',
    badgeClass: 'bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-800',
    accentClass: 'bg-amber-500',
    cardBarClass: 'bg-amber-400',
    focus: true,
  },
  runnable: {
    key: 'runnable',
    label: '可运行',
    hint: 'Runnable',
    badgeClass: 'bg-emerald-100 text-emerald-800 border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-800',
    accentClass: 'bg-emerald-500',
    cardBarClass: 'bg-emerald-400',
    focus: true,
  },
  blocked_deps: {
    key: 'blocked_deps',
    label: '依赖阻塞',
    hint: 'Blocked by deps',
    badgeClass: 'bg-orange-100 text-orange-800 border-orange-200 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-800',
    accentClass: 'bg-orange-500',
    cardBarClass: 'bg-orange-400',
    focus: true,
  },
  blocked_conflict: {
    key: 'blocked_conflict',
    label: '冲突阻塞',
    hint: 'Blocked by conflict',
    badgeClass: 'bg-red-100 text-red-800 border-red-200 dark:bg-red-950 dark:text-red-300 dark:border-red-800',
    accentClass: 'bg-red-500',
    cardBarClass: 'bg-red-400',
    focus: true,
  },
  backlog: {
    key: 'backlog',
    label: '待规划',
    hint: 'Backlog',
    badgeClass: 'bg-stone-200 text-stone-700 border-stone-300 dark:bg-stone-800 dark:text-stone-300 dark:border-stone-600',
    accentClass: 'bg-stone-400',
    cardBarClass: 'bg-stone-300',
    focus: true,
  },
  done_unmerged: {
    key: 'done_unmerged',
    label: '待合入',
    hint: 'Done, not merged',
    badgeClass: 'bg-sky-100 text-sky-800 border-sky-200 dark:bg-sky-950 dark:text-sky-300 dark:border-sky-800',
    accentClass: 'bg-sky-500',
    cardBarClass: 'bg-sky-400',
    focus: true,
  },
  done: {
    key: 'done',
    label: '已完成',
    hint: 'Done',
    badgeClass: 'bg-stone-100 text-stone-500 border-stone-200 dark:bg-stone-800 dark:text-stone-400 dark:border-stone-700',
    accentClass: 'bg-stone-300',
    cardBarClass: 'bg-stone-200',
    focus: false,
  },
  dropped: {
    key: 'dropped',
    label: '已放弃',
    hint: 'Dropped',
    badgeClass: 'bg-stone-100 text-stone-400 border-stone-200 dark:bg-stone-800 dark:text-stone-500 dark:border-stone-700',
    accentClass: 'bg-stone-300',
    cardBarClass: 'bg-stone-200',
    focus: false,
  },
}

export const FOCUS_CATEGORIES: TaskCategory[] = [
  'active',
  'runnable',
  'blocked_deps',
  'blocked_conflict',
  'backlog',
]

export const ALL_CATEGORIES: TaskCategory[] = [
  'active',
  'runnable',
  'blocked_deps',
  'blocked_conflict',
  'backlog',
  'done_unmerged',
  'done',
  'dropped',
]

// ---------------------------------------------------------------------------
// 图索引
// ---------------------------------------------------------------------------

export interface BoardIndex {
  nodeById: Map<string, TaskNode>
  /** id → 依赖它的任务 id 列表(下游) */
  dependentsOf: Map<string, string[]>
  /** id → 与它冲突的任务 id 列表(对称) */
  conflictsOf: Map<string, string[]>
}

export function buildIndex(data: BoardData): BoardIndex {
  const nodeById = new Map<string, TaskNode>()
  const dependentsOf = new Map<string, string[]>()
  const conflictsOf = new Map<string, string[]>()

  for (const n of data.nodes) {
    nodeById.set(n.id, n)
    dependentsOf.set(n.id, [])
    conflictsOf.set(n.id, [])
  }

  // edges 已去重且两端存在,直接从 edges 建索引
  for (const e of data.edges) {
    if (e.type === 'dep') {
      // from 是依赖项,to 是被阻塞者 → from 的下游是 to
      dependentsOf.get(e.from)?.push(e.to)
    } else {
      conflictsOf.get(e.from)?.push(e.to)
      conflictsOf.get(e.to)?.push(e.from)
    }
  }

  return { nodeById, dependentsOf, conflictsOf }
}

// ---------------------------------------------------------------------------
// 关系高亮集合
// ---------------------------------------------------------------------------

export interface RelatedSets {
  self: string
  /** 上游:它依赖的任务(传递闭包) */
  upstream: Set<string>
  /** 下游:依赖它的任务(传递闭包) */
  downstream: Set<string>
  /** 冲突对象(直接) */
  conflicts: Set<string>
}

export function collectRelated(id: string, index: BoardIndex): RelatedSets {
  const node = index.nodeById.get(id)
  const upstream = new Set<string>()
  const downstream = new Set<string>()
  const conflicts = new Set<string>(index.conflictsOf.get(id) ?? [])
  if (!node) return { self: id, upstream, downstream, conflicts }

  // 沿 depends_on 向上游 BFS
  const upQueue = [...node.depends_on]
  while (upQueue.length > 0) {
    const cur = upQueue.pop() as string
    if (cur === id || upstream.has(cur)) continue
    upstream.add(cur)
    const n = index.nodeById.get(cur)
    if (n) upQueue.push(...n.depends_on)
  }

  // 沿 dependents 向下游 BFS
  const downQueue = [...(index.dependentsOf.get(id) ?? [])]
  while (downQueue.length > 0) {
    const cur = downQueue.pop() as string
    if (cur === id || downstream.has(cur)) continue
    downstream.add(cur)
    downQueue.push(...(index.dependentsOf.get(cur) ?? []))
  }

  return { self: id, upstream, downstream, conflicts }
}

export type HighlightRole = 'self' | 'upstream' | 'downstream' | 'conflict' | null

export function highlightRoleOf(id: string, related: RelatedSets | null): HighlightRole {
  if (!related) return null
  if (id === related.self) return 'self'
  if (related.conflicts.has(id)) return 'conflict'
  if (related.upstream.has(id)) return 'upstream'
  if (related.downstream.has(id)) return 'downstream'
  return null
}
