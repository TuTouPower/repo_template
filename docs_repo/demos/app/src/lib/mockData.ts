import type {
  BoardData,
  BoardEdge,
  BoardSummary,
  TaskCategory,
  TaskNode,
  TaskStatus,
} from '@/types/board'

// ---------------------------------------------------------------------------
// 固定 seed 的伪随机生成器(mulberry32),保证每次刷新数据完全一致
// ---------------------------------------------------------------------------

function mulberry32(seed: number) {
  let a = seed >>> 0
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// ---------------------------------------------------------------------------
// 中文工程任务标题词库
// ---------------------------------------------------------------------------

const VERBS = [
  '实现', '重构', '修复', '优化', '接入', '拆分', '迁移', '调研',
  '设计', '联调', '压测', '补全', '清理', '升级', '改造', '验证',
]

const MODULES = [
  'task view 看板', '调度引擎', '依赖解析器', '冲突检测器', '执行器',
  '状态机', '归档服务', '任务队列', '并发控制器', '事件总线',
  '结果缓存', '重试策略', '日志聚合', '指标上报', '配置中心',
  '权限校验', '快照存储', '增量同步', '超时治理', '熔断器',
]

const OBJECTS = [
  '核心链路', '边界条件', '异常恢复', '单元测试', '集成测试',
  '性能基准', '内存占用', '竞态条件', '降级方案', '灰度发布',
  '接口兼容', '数据迁移', '文档与示例', '告警规则', '回归用例',
  '热更新', '批处理', '流式输出', '死锁排查', '幂等性',
]

function makeTitle(rng: () => number, used: Set<string>): string {
  for (let i = 0; i < 40; i++) {
    const v = VERBS[Math.floor(rng() * VERBS.length)]
    const m = MODULES[Math.floor(rng() * MODULES.length)]
    const o = OBJECTS[Math.floor(rng() * OBJECTS.length)]
    const title = rng() < 0.55 ? `${v}${m}的${o}` : `${v}${m}${o}`
    if (!used.has(title)) {
      used.add(title)
      return title
    }
  }
  // 极端情况下加后缀去重
  const fallback = `${VERBS[Math.floor(rng() * VERBS.length)]}${MODULES[Math.floor(rng() * MODULES.length)]}专项`
  let n = 2
  let title = `${fallback}(${n})`
  while (used.has(title)) {
    n += 1
    title = `${fallback}(${n})`
  }
  used.add(title)
  return title
}

// ---------------------------------------------------------------------------
// category → status 映射
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

// ---------------------------------------------------------------------------
// 场景参数:两套示例数据集共用同一套生成器,仅参数不同
// ---------------------------------------------------------------------------

/**
 * 段间交叉设计:[dependent段, dependent位置, 目标段, 目标位置]
 * 语义:dependent 段中段的某个任务额外依赖目标段中段的某个任务,
 * 切链后形成典型的"甲链等乙链"交叉点。
 */
type DesignedCrossing = [number, number, number, number]

interface ScenarioConfig {
  /** 总任务数 */
  total: number
  /** 未完成区(重点区)大小 */
  focusTotal: number
  /** 历史区 dropped 占比 */
  droppedRatio: number
  /** 主干长链各段长度(含段首) */
  mainSegments: number[]
  /** 迷你链各段长度(含段首) */
  miniSegments: number[]
  /** 独立 runnable(各带 1 个 blocked_deps 下游) */
  standaloneRunnable: number
  /** blocked_conflict 数量(两两配对) */
  conflictCount: number
  /** active 数量(取主干链前 N 条的段首) */
  activeCount: number
  /** 显式设计的段间交叉 */
  designedCrossings: DesignedCrossing[]
  /** 额外的随机段间汇入/分叉边数量 */
  extraCrossEdges: number
  /** runnable ↔ blocked_conflict 冲突边数量 */
  runnableConflictEdges: number
  /** 历史区尾部翻转为 done_unmerged(已完成未合入)的节点数 */
  unmergedCount: number
}

export type DatasetId = 'large' | 'small'

export interface DatasetMeta {
  id: DatasetId
  /** 展示名称 */
  name: string
  /** 一句话描述 */
  description: string
  /** 固定 seed(两套必须不同) */
  seed: number
  scenario: ScenarioConfig
}

export const DATASETS: DatasetMeta[] = [
  {
    id: 'large',
    name: '大型示例',
    description: '未完成任务 ~180 个,多条主干长链 + 大量交叉,适合演示复杂切链',
    seed: 20240817,
    scenario: {
      // 共 1200 个任务,done + dropped ≈ 85%
      // 未完成区被构造成一个多层 DAG:
      //   · 6 条主干长链(10-14 个任务的串行段)+ 4 条迷你链 + 4 个独立可运行任务
      //   · 段间汇入 / 分叉边,以及 5 处显式设计的"链间交叉"(甲链中段依赖乙链中段)
      //   · 16 个 blocked_conflict 组成 8 对互斥,另有几对 runnable 冲突
      total: 1200,
      focusTotal: 180,
      droppedRatio: 0.08,
      mainSegments: [13, 12, 14, 11, 12, 10],
      miniSegments: [5, 4, 6, 5],
      standaloneRunnable: 4,
      conflictCount: 16,
      activeCount: 4,
      designedCrossings: [
        [2, 8, 0, 6],
        [3, 5, 1, 4],
        [4, 6, 2, 5],
        [5, 5, 3, 7],
        [1, 9, 4, 3],
      ],
      extraCrossEdges: 10,
      runnableConflictEdges: 4,
      unmergedCount: 5,
    },
  },
  {
    id: 'small',
    name: '小型示例',
    description: '未完成任务 25 个,几条短链 + 2 处交叉 + 少量冲突,结构精炼便于讲解',
    seed: 20250211,
    scenario: {
      // 共 1000 个任务,done + dropped ≈ 97.5%
      // 未完成区(25 个)结构:
      //   · 3 条主干链(6/5/4)+ 1 条迷你链(3)+ 1 个独立可运行任务(带 1 下游)
      //   · 2 处显式设计的链间交叉 + 少量随机汇入边
      //   · 4 个 blocked_conflict 组成 2 对互斥,另有 runnable 冲突与跨链段首冲突
      total: 1000,
      focusTotal: 25,
      droppedRatio: 0.08,
      mainSegments: [6, 5, 4],
      miniSegments: [3],
      standaloneRunnable: 1,
      conflictCount: 4,
      activeCount: 2,
      designedCrossings: [
        [1, 2, 0, 3],
        [2, 1, 1, 2],
      ],
      extraCrossEdges: 2,
      runnableConflictEdges: 2,
      unmergedCount: 3,
    },
  },
]

export const DEFAULT_DATASET_ID: DatasetId = 'large'

export function getDatasetMeta(id: DatasetId): DatasetMeta {
  const meta = DATASETS.find((d) => d.id === id)
  if (!meta) throw new Error(`未知数据集:${id}`)
  return meta
}

function taskId(n: number): string {
  return `t${String(n).padStart(3, '0')}`
}

// ---------------------------------------------------------------------------
// 生成看板数据(按场景参数确定性生成)
// ---------------------------------------------------------------------------

function generateBoard(meta: DatasetMeta): BoardData {
  const rng = mulberry32(meta.seed)
  const cfg = meta.scenario
  const TOTAL = cfg.total
  const FOCUS_TOTAL = cfg.focusTotal
  const HISTORY_TOTAL = TOTAL - FOCUS_TOTAL
  const usedTitles = new Set<string>()

  // 1. 确定每个节点的 category:前面为历史区(done/dropped);
  //    后面 FOCUS_TOTAL 个未完成节点的 category 由结构角色决定(见下方注释),
  //    先占位,生成依赖结构时回填。
  const categories = new Array<TaskCategory>(TOTAL)
  for (let i = 0; i < HISTORY_TOTAL; i++) {
    categories[i] = rng() < cfg.droppedRatio ? 'dropped' : 'done'
  }

  const idAt = (idx: number) => taskId(idx + 1)

  // ------------------------------------------------------------------
  // 2. 规划未完成区结构:segments = 主干长链 + 迷你链 + 独立对
  //    segIds[s] = 该段节点在 nodes 中的下标(按依赖顺序排列)
  // ------------------------------------------------------------------
  const segDefs = [...cfg.mainSegments, ...cfg.miniSegments]
  const segIdx: number[][] = []
  let cursor = HISTORY_TOTAL
  for (const len of segDefs) {
    const seg: number[] = []
    for (let k = 0; k < len; k++) seg.push(cursor++)
    segIdx.push(seg)
  }
  // 独立 runnable + 各自 1 个 blocked_deps 下游
  const standaloneIdx: Array<[number, number]> = []
  for (let k = 0; k < cfg.standaloneRunnable; k++) {
    standaloneIdx.push([cursor++, cursor++])
  }
  const conflictStart = cursor
  cursor += cfg.conflictCount
  const backlogStart = cursor
  const backlogCount = TOTAL - backlogStart

  // 回填 category:
  //   · 主干链前 activeCount 条的段首为 active,其余段首为 runnable
  //   · 段内其余节点为 blocked_deps(每个都有未完成的前驱依赖)
  //   · 独立对:runnable + blocked_deps
  //   · 之后是 blocked_conflict × N、backlog × 剩余
  for (let s = 0; s < segIdx.length; s++) {
    categories[segIdx[s][0]] = s < cfg.activeCount ? 'active' : 'runnable'
    for (let k = 1; k < segIdx[s].length; k++) categories[segIdx[s][k]] = 'blocked_deps'
  }
  for (const [r, b] of standaloneIdx) {
    categories[r] = 'runnable'
    categories[b] = 'blocked_deps'
  }
  for (let i = 0; i < cfg.conflictCount; i++) {
    // 互斥对按序号分胜负（对齐生产冲突裁决：小号不被大号压 → 可排 runnable，
    // 大号被压 → blocked_conflict，等对手完成释放）。
    categories[conflictStart + i] = i % 2 === 0 ? 'runnable' : 'blocked_conflict'
  }
  for (let i = 0; i < backlogCount; i++) categories[backlogStart + i] = 'backlog'

  // 3. 生成节点骨架
  const nodes: TaskNode[] = categories.map((cat, i) => ({
    id: idAt(i),
    title: makeTitle(rng, usedTitles),
    status: CATEGORY_STATUS[cat],
    category: cat,
    depends_on: [],
    conflicts_with: [],
    // schedule_status 对齐生产调度：除未排程 backlog 外，已规划节点
    // （active/runnable/blocked_deps/blocked_conflict）都视为 scheduled——
    // 解锁（applyMerge）后它们变成 runnable 时须保留 scheduled 才能入候选，
    // 否则批次推荐停滞。
    schedule_status: cat === 'backlog' ? '' : 'scheduled',
  }))

  // 4. 依赖关系 —— 历史区:偏向链接前一个 done 节点,形成长主链
  const doneEarlier: string[] = []
  for (let i = 0; i < HISTORY_TOTAL; i++) {
    const node = nodes[i]
    if (node.category === 'done') {
      // 主链:约 55% 概率依赖前一个 done
      const depCount = doneEarlier.length === 0 ? 0 : rng() < 0.2 ? 0 : rng() < 0.6 ? 1 : 2
      for (let k = 0; k < depCount; k++) {
        if (doneEarlier.length === 0) break
        const pick =
          k === 0 && rng() < 0.55
            ? doneEarlier[doneEarlier.length - 1]
            : doneEarlier[Math.floor(rng() * doneEarlier.length)]
        if (pick !== node.id && !node.depends_on.includes(pick)) {
          node.depends_on.push(pick)
        }
      }
      doneEarlier.push(node.id)
    } else {
      // dropped:少量历史依赖
      if (doneEarlier.length > 0 && rng() < 0.5) {
        node.depends_on.push(doneEarlier[Math.floor(rng() * doneEarlier.length)])
      }
    }
  }

  const pickDoneDep = (exclude: string): string => {
    for (let i = 0; i < 20; i++) {
      // 偏向历史区尾部的 done(更接近"当前工作")
      const base = Math.floor(HISTORY_TOTAL * 0.6)
      const idx = base + Math.floor(rng() * (HISTORY_TOTAL - base))
      if (categories[idx] === 'done' && idAt(idx) !== exclude) return idAt(idx)
    }
    return doneEarlier[Math.floor(rng() * doneEarlier.length)]
  }

  const addDep = (fromIdx: number, toIdx: number) => {
    const id = idAt(fromIdx)
    const node = nodes[toIdx]
    if (fromIdx !== toIdx && !node.depends_on.includes(id)) node.depends_on.push(id)
  }

  // 5. 未完成区依赖(所有边都从"后生成的节点"指向"先生成的节点",保证 DAG):
  //    段内:每个节点依赖前一个节点 → 自然的串行长链
  //    段首(active/runnable):仅依赖历史 done,保证"当前可跑/正在跑"
  for (const seg of segIdx) {
    for (let k = 0; k < seg.length; k++) {
      if (k === 0) {
        const n = 1 + Math.floor(rng() * 2)
        for (let d = 0; d < n; d++) {
          const doneId = pickDoneDep(idAt(seg[0]))
          const doneIdx = Number(doneId.slice(1)) - 1
          addDep(doneIdx, seg[0])
        }
      } else {
        addDep(seg[k - 1], seg[k])
        // 35% 概率再挂一个历史 done 依赖(丰富汇入)
        if (rng() < 0.35) {
          const doneId = pickDoneDep(idAt(seg[k]))
          addDep(Number(doneId.slice(1)) - 1, seg[k])
        }
      }
    }
  }
  //    独立对
  for (const [r, b] of standaloneIdx) {
    const n = 1 + Math.floor(rng() * 2)
    for (let d = 0; d < n; d++) {
      const doneId = pickDoneDep(idAt(r))
      addDep(Number(doneId.slice(1)) - 1, r)
    }
    addDep(r, b)
    if (rng() < 0.5) {
      const doneId = pickDoneDep(idAt(b))
      addDep(Number(doneId.slice(1)) - 1, b)
    }
  }

  //    显式设计的段间交叉(甲链中段依赖乙链中段)
  for (const [depSeg, depPos, tgtSeg, tgtPos] of cfg.designedCrossings) {
    addDep(segIdx[tgtSeg][tgtPos], segIdx[depSeg][depPos])
  }

  //    随机段间汇入/分叉边:从某段内 blocked_deps 节点指向另一段中更早的节点
  const segOf = new Map<number, number>()
  segIdx.forEach((seg, s) => seg.forEach((idx) => segOf.set(idx, s)))
  const innerNodes = segIdx.flatMap((seg) => seg.slice(1)) // 可挂额外依赖的 blocked_deps
  let extraAdded = 0
  for (let attempt = 0; attempt < 200 && extraAdded < cfg.extraCrossEdges; attempt++) {
    const to = innerNodes[Math.floor(rng() * innerNodes.length)]
    // 目标:更早生成、属于不同段的未完成节点
    const candidates: number[] = []
    for (const seg of segIdx) {
      for (const idx of seg) {
        if (idx < to && segOf.get(idx) !== segOf.get(to)) candidates.push(idx)
      }
    }
    if (candidates.length === 0) continue
    const from = candidates[Math.floor(rng() * candidates.length)]
    const before = nodes[to].depends_on.length
    addDep(from, to)
    if (nodes[to].depends_on.length > before) extraAdded += 1
  }

  //    blocked_conflict:依赖只指向 done,冲突关系在下一步建立
  for (let i = 0; i < cfg.conflictCount; i++) {
    if (rng() < 0.5) {
      const idx = conflictStart + i
      const doneId = pickDoneDep(idAt(idx))
      addDep(Number(doneId.slice(1)) - 1, idx)
    }
  }

  //    backlog:暂不可跑也未阻塞,优先串到更早的 backlog 上(形成待规划链),
  //    否则依赖历史 done
  for (let i = 0; i < backlogCount; i++) {
    const idx = backlogStart + i
    const n = 1 + Math.floor(rng() * 2) // 1–2
    for (let k = 0; k < n; k++) {
      if (i > 0 && rng() < 0.65) {
        addDep(backlogStart + Math.floor(rng() * i), idx)
      } else {
        const doneId = pickDoneDep(idAt(idx))
        addDep(Number(doneId.slice(1)) - 1, idx)
      }
    }
  }

  // 6. 冲突关系:blocked_conflict 两两配对(互斥),
  //    再加若干条 runnable ↔ blocked_conflict 冲突边,
  //    以及 1 对不同长链段首之间的冲突(演示跨链冲突提示)
  const pair = (a: TaskNode, b: TaskNode) => {
    if (!a.conflicts_with.includes(b.id)) a.conflicts_with.push(b.id)
    if (!b.conflicts_with.includes(a.id)) b.conflicts_with.push(a.id)
  }
  // 互斥对：conflictStart+i 区，i 偶数=runnable（可排）、i 奇数=blocked_conflict（被压）。
  // 每对（偶数, 奇数）配对，避免 bc↔bc 死锁。
  for (let i = 0; i < cfg.conflictCount; i += 2) {
    pair(nodes[conflictStart + i], nodes[conflictStart + i + 1])
  }
  const conflictNodes = nodes.filter((n) => n.category === 'blocked_conflict')
  const runnableNodes = nodes.filter((n) => n.category === 'runnable')
  for (let k = 0; k < cfg.runnableConflictEdges && k < runnableNodes.length; k++) {
    pair(runnableNodes[k], conflictNodes[(k * 2 + 1) % conflictNodes.length])
  }
  if (segIdx.length > cfg.activeCount + 1) {
    pair(nodes[segIdx[cfg.activeCount][0]], nodes[segIdx[cfg.activeCount + 1][0]])
  }

  // 6.5 待合入(done_unmerged):从已完成的历史区挑"没有被未完成区依赖"的
  //     末尾 K 个节点翻转;再让个别 backlog 节点显式依赖它们,
  //     演示"已完成但未合入 → 下游仍被阻塞"的语义
  const focusDependents = new Set<string>()
  for (let i = HISTORY_TOTAL; i < TOTAL; i++) {
    for (const d of nodes[i].depends_on) focusDependents.add(d)
  }
  const flippable: number[] = []
  for (let i = HISTORY_TOTAL - 1; i >= 0 && flippable.length < cfg.unmergedCount; i--) {
    if (nodes[i].category === 'done' && !focusDependents.has(nodes[i].id)) {
      flippable.push(i)
    }
  }
  for (const i of flippable) nodes[i].category = 'done_unmerged'
  // 显式阻塞演示:未完成区 backlog 节点依赖刚翻转的待合入节点(下标更大,DAG 成立)
  if (flippable.length > 0 && backlogCount > 0) {
    addDep(flippable[flippable.length - 1], backlogStart)
    if (flippable.length > 1 && backlogCount > 1) addDep(flippable[0], backlogStart + 1)
  }

  // 7. edges:由 nodes 推导,去重,两端节点都必须存在
  const idSet = new Set(nodes.map((n) => n.id))
  const edgeSet = new Set<string>()
  const edges: BoardEdge[] = []
  for (const n of nodes) {
    for (const d of n.depends_on) {
      if (!idSet.has(d)) continue
      const key = `dep|${d}|${n.id}`
      if (edgeSet.has(key)) continue
      edgeSet.add(key)
      edges.push({ type: 'dep', from: d, to: n.id })
    }
    for (const c of n.conflicts_with) {
      if (!idSet.has(c)) continue
      const [a, b] = n.id < c ? [n.id, c] : [c, n.id]
      const key = `conflict|${a}|${b}`
      if (edgeSet.has(key)) continue
      edgeSet.add(key)
      edges.push({ type: 'conflict', from: a, to: b })
    }
  }
  edges.sort((a, b) =>
    a.type === b.type
      ? a.from === b.from
        ? a.to.localeCompare(b.to)
        : a.from.localeCompare(b.from)
      : a.type.localeCompare(b.type),
  )

  // 8. summary 由 nodes 统计得出
  const summary = {} as BoardSummary
  const ALL_CATEGORIES: TaskCategory[] = [
    'active', 'runnable', 'blocked_deps', 'blocked_conflict', 'backlog',
    'done_unmerged', 'done', 'dropped',
  ]
  for (const c of ALL_CATEGORIES) summary[c] = 0
  for (const n of nodes) summary[n.category] += 1

  return { project: 'repo_template', summary, nodes, edges }
}

// 模块级缓存:每个数据集各自缓存,保证同一会话内多次调用返回一致数据
const cache = new Map<DatasetId, BoardData>()

export function getBoard(datasetId: DatasetId = DEFAULT_DATASET_ID): BoardData {
  let data = cache.get(datasetId)
  if (!data) {
    data = generateBoard(getDatasetMeta(datasetId))
    cache.set(datasetId, data)
  }
  return data
}

/** 数据集的未完成统计(供 UI 标签/校验使用,同步确定性计算) */
export function getDatasetStats(datasetId: DatasetId): { total: number; unfinished: number } {
  const data = getBoard(datasetId)
  return {
    total: data.nodes.length,
    unfinished:
      data.nodes.length - data.summary.done - data.summary.dropped - data.summary.done_unmerged,
  }
}

/**
 * 模拟异步取数。与真实后端调用方式一致,
 * 日后替换为 `fetch(`/api/board?dataset=${datasetId}`).then(r => r.json())` 即可。
 */
export function fetchBoard(datasetId: DatasetId = DEFAULT_DATASET_ID): Promise<BoardData> {
  return new Promise((resolve) => {
    setTimeout(() => {
      const data = getBoard(datasetId)
      resolve({
        project: data.project,
        summary: { ...data.summary },
        nodes: data.nodes.map((n) => ({
          ...n,
          depends_on: [...n.depends_on],
          conflicts_with: [...n.conflicts_with],
        })),
        edges: data.edges.map((e) => ({ ...e })),
      })
    }, 350)
  })
}
