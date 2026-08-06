import { memo, useEffect, useMemo, useRef, useState } from 'react'
import { Minus, Plus, Scan } from 'lucide-react'
import type { BoardData, TaskCategory } from '@/types/board'
import type { ChainPlan } from '@/types/chain'
import type { RelatedSets } from '@/lib/boardUtils'
import { highlightRoleOf } from '@/lib/boardUtils'
import { chainOfMap, isUnfinished } from '@/lib/chainPlan'
import { GAP_Y, NODE_H, NODE_W, layoutDag } from '@/lib/dagLayout'
import type { LayoutPos } from '@/lib/dagLayout'
import { Switch } from '@/components/ui/switch'

// ---------------------------------------------------------------------------
// category 配色(hex,与 v1 低饱和语义一致)
// ---------------------------------------------------------------------------

const CAT_STYLE: Record<TaskCategory, { fill: string; stroke: string; text: string }> = {
  active: { fill: '#FEF3C7', stroke: '#F59E0B', text: '#92400E' },
  runnable: { fill: '#D1FAE5', stroke: '#10B981', text: '#065F46' },
  blocked_deps: { fill: '#FFEDD5', stroke: '#F97316', text: '#9A3412' },
  blocked_conflict: { fill: '#FEE2E2', stroke: '#EF4444', text: '#991B1B' },
  backlog: { fill: '#E7E5E4', stroke: '#A8A29E', text: '#44403C' },
  done_unmerged: { fill: '#E0F2FE', stroke: '#0EA5E9', text: '#075985' },
  done: { fill: '#F5F5F4', stroke: '#D6D3D1', text: '#78716C' },
  dropped: { fill: '#FAFAF9', stroke: '#E7E5E4', text: '#A8A29E' },
}

/** 深色模式调色板 */
const CAT_STYLE_DARK: Record<TaskCategory, { fill: string; stroke: string; text: string }> = {
  active: { fill: '#3D2E0A', stroke: '#D97706', text: '#FBBF24' },
  runnable: { fill: '#06392B', stroke: '#10B981', text: '#6EE7B7' },
  blocked_deps: { fill: '#3E2410', stroke: '#F97316', text: '#FDBA74' },
  blocked_conflict: { fill: '#3B1212', stroke: '#EF4444', text: '#FCA5A5' },
  backlog: { fill: '#292524', stroke: '#78716C', text: '#D6D3D1' },
  done_unmerged: { fill: '#082A3E', stroke: '#0EA5E9', text: '#7DD3FC' },
  done: { fill: '#1C1917', stroke: '#44403C', text: '#78716C' },
  dropped: { fill: '#1C1917', stroke: '#292524', text: '#57534E' },
}

const CAT_LABEL: Record<TaskCategory, string> = {
  active: '运行中',
  runnable: '可运行',
  blocked_deps: '依赖阻塞',
  blocked_conflict: '冲突阻塞',
  backlog: '待规划',
  done_unmerged: '待合入',
  done: '已完成',
  dropped: '已放弃',
}

function truncate(s: string, max: number): string {
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`
}

// ---------------------------------------------------------------------------
// 单个节点(memo 化,千级节点下避免无效重绘)
// ---------------------------------------------------------------------------

interface GraphNodeProps {
  id: string
  title: string
  category: TaskCategory
  pos: LayoutPos
  chainColor: string | null
  chainLetter: string | null
  /** 交叉点节点(被别的链等待) */
  crossingInfo: string | null
  /** 等待别的链 */
  waitingInfo: string | null
  /** 本轮冲突暂缓(原因) */
  deferredInfo: string | null
  dimmed: boolean
  selected: boolean
  /** hover 关系高亮色(upstream/downstream/conflict/self) */
  ringColor: string | null
  dark: boolean
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
}

const GraphNode = memo(function GraphNode({
  id,
  title,
  category,
  pos,
  chainColor,
  chainLetter,
  crossingInfo,
  waitingInfo,
  deferredInfo,
  dimmed,
  selected,
  ringColor,
  dark,
  onSelect,
  onHover,
}: GraphNodeProps) {
  const cat = (dark ? CAT_STYLE_DARK : CAT_STYLE)[category]
  const stroke = ringColor ?? chainColor ?? (deferredInfo ? '#DC2626' : cat.stroke)
  const strokeW = ringColor || selected || chainColor ? 2.4 : 1.4
  const tip =
    crossingInfo ?? deferredInfo ?? waitingInfo ?? `${id} · ${title}`
  return (
    <g
      transform={`translate(${pos.x}, ${pos.y})`}
      opacity={dimmed ? 0.22 : 1}
      style={{ cursor: 'pointer' }}
      onClick={(e) => {
        e.stopPropagation()
        onSelect(id)
      }}
      onMouseEnter={() => onHover(id)}
      onMouseLeave={() => onHover(null)}
    >
      <title>{tip}</title>
      {crossingInfo && (
        <>
          <circle cx={NODE_W - 4} cy={4} r={9} fill="none" stroke="#DC2626" strokeWidth={1.6}>
            <animate attributeName="r" values="7;13" dur="1.4s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.9;0" dur="1.4s" repeatCount="indefinite" />
          </circle>
          <circle cx={NODE_W - 4} cy={4} r={7.5} fill="#DC2626" />
          <text
            x={NODE_W - 4}
            y={8}
            textAnchor="middle"
            fontSize={9}
            fontWeight={700}
            fill="#fff"
          >
            !
          </text>
        </>
      )}
      {waitingInfo && !crossingInfo && !deferredInfo && (
        <circle cx={NODE_W - 4} cy={4} r={4.5} fill="#D97706" stroke="#fff" strokeWidth={1.2} />
      )}
      {deferredInfo && !crossingInfo && (
        <>
          <rect x={NODE_W - 16} y={-2} width={14} height={12} rx={3} fill={dark ? '#1C1917' : '#fff'} stroke="#DC2626" strokeWidth={1} />
          <text x={NODE_W - 9} y={7} textAnchor="middle" fontSize={8} fontWeight={700} fill="#DC2626">
            缓
          </text>
        </>
      )}
      <rect
        width={NODE_W}
        height={NODE_H}
        rx={8}
        fill={cat.fill}
        stroke={stroke}
        strokeWidth={strokeW}
        strokeDasharray={deferredInfo ? '5 3' : undefined}
      />
      {selected && (
        <rect
          x={-3}
          y={-3}
          width={NODE_W + 6}
          height={NODE_H + 6}
          rx={10}
          fill="none"
          stroke={dark ? '#E7E5E4' : '#292524'}
          strokeWidth={1.6}
        />
      )}
      {chainLetter && chainColor && (
        <>
          <rect x={4} y={4} width={16} height={14} rx={3} fill={chainColor} />
          <text x={12} y={14.5} textAnchor="middle" fontSize={9} fontWeight={700} fill="#fff">
            {chainLetter}
          </text>
        </>
      )}
      <text x={chainLetter ? 26 : 10} y={17} fontSize={10} fontFamily="monospace" fill={dark ? '#A8A29E' : '#78716C'}>
        {id}
      </text>
      <text x={10} y={37} fontSize={11.5} fill={cat.text}>
        {truncate(title, 13)}
      </text>
    </g>
  )
})

// ---------------------------------------------------------------------------
// DAG 图主组件
// ---------------------------------------------------------------------------

interface DagGraphProps {
  data: BoardData
  plan: ChainPlan
  /** 搜索命中的任务 id(null = 无搜索) */
  matchedIds: Set<string> | null
  related: RelatedSets | null
  selectedTaskId: string | null
  highlightChainId: string | null
  showCompleted: boolean
  showConflicts: boolean
  onToggleCompleted: (v: boolean) => void
  onToggleConflicts: (v: boolean) => void
  onSelectTask: (id: string) => void
  onHoverTask: (id: string | null) => void
  /** 深色模式(切换 SVG 调色板) */
  dark: boolean
}

export function DagGraph({
  data,
  plan,
  matchedIds,
  related,
  selectedTaskId,
  highlightChainId,
  showCompleted,
  showConflicts,
  onToggleCompleted,
  onToggleConflicts,
  onSelectTask,
  onHoverTask,
  dark,
}: DagGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [view, setView] = useState({ k: 1, x: 20, y: 20 })
  const dragRef = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null)

  const chainOf = useMemo(() => chainOfMap(plan.chains), [plan.chains])
  const chainById = useMemo(() => new Map(plan.chains.map((c) => [c.id, c])), [plan.chains])

  // 交叉点节点(被等待方)与等待方节点的说明文字
  const { crossingNodeTip, waitingNodeTip, crossingEdgeKeys } = useMemo(() => {
    const crossingNodeTip = new Map<string, string>()
    const waitingNodeTip = new Map<string, string>()
    const crossingEdgeKeys = new Set<string>()
    const nameOf = (cid: string) => (cid === 'future' ? '未来批次' : (chainById.get(cid)?.name ?? cid))
    for (const c of plan.crossings) {
      crossingNodeTip.set(
        c.dependsOnNodeId,
        `交叉点:${nameOf(c.chainId)} 的 ${c.nodeId} 在等待本任务完成`,
      )
      waitingNodeTip.set(
        c.nodeId,
        `阻塞:依赖 ${nameOf(c.dependsOnChainId)} 的 ${c.dependsOnNodeId}(交叉点)`,
      )
      crossingEdgeKeys.add(`${c.dependsOnNodeId}|${c.nodeId}`)
    }
    return { crossingNodeTip, waitingNodeTip, crossingEdgeKeys }
  }, [plan.crossings, chainById])

  // 本轮暂缓任务的说明
  const deferredTip = useMemo(
    () => new Map(plan.deferred.map((d) => [d.taskId, d.reason])),
    [plan.deferred],
  )

  // 可见节点:默认渲染未完成子图 + 待合入(done 未合入);开关打开后包含 done/dropped
  const visibleIds = useMemo(
    () =>
      data.nodes
        .filter(
          (n) =>
            showCompleted || isUnfinished(n.category) || n.category === 'done_unmerged',
        )
        .map((n) => n.id),
    [data, showCompleted],
  )
  const visibleSet = useMemo(() => new Set(visibleIds), [visibleIds])

  const layout = useMemo(() => {
    const depEdges = data.edges.filter(
      (e) => e.type === 'dep' && visibleSet.has(e.from) && visibleSet.has(e.to),
    )
    return layoutDag(visibleIds, depEdges)
  }, [data, visibleIds, visibleSet])

  const renderEdges = useMemo(() => {
    const dep: Array<{ key: string; from: string; to: string; crossing: boolean }> = []
    const conflict: Array<{ key: string; from: string; to: string }> = []
    for (const e of data.edges) {
      if (!visibleSet.has(e.from) || !visibleSet.has(e.to)) continue
      if (e.type === 'dep') {
        dep.push({
          key: `${e.from}|${e.to}`,
          from: e.from,
          to: e.to,
          crossing: crossingEdgeKeys.has(`${e.from}|${e.to}`),
        })
      } else if (showConflicts) {
        conflict.push({ key: `${e.from}|${e.to}`, from: e.from, to: e.to })
      }
    }
    return { dep, conflict }
  }, [data, visibleSet, crossingEdgeKeys, showConflicts])

  const nodeById = useMemo(() => new Map(data.nodes.map((n) => [n.id, n])), [data])

  // 高亮链的节点集合
  const chainNodeSet = useMemo(() => {
    if (!highlightChainId) return null
    const c = chainById.get(highlightChainId)
    return c ? new Set(c.taskIds) : null
  }, [highlightChainId, chainById])

  const dimOf = (id: string): boolean => {
    if (related) {
      return highlightRoleOf(id, related) === null
    }
    if (chainNodeSet) return !chainNodeSet.has(id)
    if (matchedIds) return !matchedIds.has(id)
    return false
  }

  const RING: Record<string, string> = {
    self: dark ? '#E7E5E4' : '#292524',
    upstream: '#F59E0B',
    downstream: '#10B981',
    conflict: '#EF4444',
  }
  const ringOf = (id: string): string | null => {
    const role = highlightRoleOf(id, related)
    return role ? RING[role] : null
  }

  const edgePath = (from: string, to: string): string | null => {
    const a = layout.positions.get(from)
    const b = layout.positions.get(to)
    if (!a || !b) return null
    const sx = a.x + NODE_W / 2
    const sy = a.y + NODE_H
    const tx = b.x + NODE_W / 2
    const ty = b.y
    if (b.layer > a.layer) {
      const my = sy + GAP_Y / 2
      return `M ${sx} ${sy} C ${sx} ${my}, ${tx} ${my}, ${tx} ${ty}`
    }
    // 同层或跨层反向(罕见):弧线绕右侧
    return `M ${a.x + NODE_W} ${a.y + NODE_H / 2} C ${a.x + NODE_W + 40} ${a.y + NODE_H / 2}, ${b.x + NODE_W + 40} ${b.y + NODE_H / 2}, ${b.x + NODE_W} ${b.y + NODE_H / 2}`
  }

  // 缩放/平移
  const zoom = (factor: number, cx?: number, cy?: number) => {
    setView((v) => {
      const k = Math.min(3, Math.max(0.15, v.k * factor))
      const rect = containerRef.current?.getBoundingClientRect()
      const px = cx ?? (rect ? rect.width / 2 : 400)
      const py = cy ?? (rect ? rect.height / 2 : 300)
      const scale = k / v.k
      return { k, x: px - (px - v.x) * scale, y: py - (py - v.y) * scale }
    })
  }

  const fitView = () => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    const k = Math.min(1.2, Math.max(0.15, Math.min(
      (rect.width - 40) / layout.width,
      (rect.height - 40) / layout.height,
    )))
    setView({ k, x: 20, y: 20 })
  }

  // 非 passive 的 wheel 监听:阻止页面滚动,以光标为中心缩放
  const zoomRef = useRef(zoom)
  useEffect(() => {
    zoomRef.current = zoom
  })
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      zoomRef.current(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - rect.left, e.clientY - rect.top)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  // 初次渲染后自动适配视图(异步,避免级联渲染)
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      const rect = containerRef.current?.getBoundingClientRect()
      if (!rect) return
      const k = Math.min(1.2, Math.max(0.15, Math.min(
        (rect.width - 40) / layout.width,
        (rect.height - 40) / layout.height,
      )))
      setView({ k, x: 20, y: 20 })
    })
    return () => cancelAnimationFrame(raf)
  }, [layout])

  const unfinishedCount = data.nodes.filter((n) => isUnfinished(n.category)).length
  const unmergedCount = data.nodes.filter((n) => n.category === 'done_unmerged').length
  const palette = dark ? CAT_STYLE_DARK : CAT_STYLE

  return (
    <section className="flex min-w-0 flex-1 flex-col rounded-xl border border-stone-200 bg-white dark:border-stone-700 dark:bg-stone-900">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-stone-100 px-4 py-2.5 dark:border-stone-800">
        <h2 className="text-sm font-semibold text-stone-800 dark:text-stone-100">依赖 DAG</h2>
        <span className="text-xs text-stone-400 dark:text-stone-500">
          {showCompleted
            ? `${visibleIds.length} 节点(含归档)`
            : `${visibleIds.length} 个待处理任务${unmergedCount > 0 ? `(含 ${unmergedCount} 待合入)` : ''}`}
          {!showCompleted && unfinishedCount >= 0 && ' · 归档任务已隐藏'}
        </span>
        <div className="ml-auto flex items-center gap-4">
          <label className="flex items-center gap-1.5 text-xs text-stone-600 dark:text-stone-400">
            <Switch checked={showConflicts} onCheckedChange={onToggleConflicts} />
            冲突边
          </label>
          <label className="flex items-center gap-1.5 text-xs text-stone-600 dark:text-stone-400">
            <Switch checked={showCompleted} onCheckedChange={onToggleCompleted} />
            显示已完成
          </label>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => zoom(1 / 1.25)}
              className="rounded-md border border-stone-200 p-1 text-stone-500 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-400 dark:hover:bg-stone-800"
              title="缩小"
            >
              <Minus className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => zoom(1.25)}
              className="rounded-md border border-stone-200 p-1 text-stone-500 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-400 dark:hover:bg-stone-800"
              title="放大"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={fitView}
              className="rounded-md border border-stone-200 p-1 text-stone-500 hover:bg-stone-50 dark:border-stone-700 dark:text-stone-400 dark:hover:bg-stone-800"
              title="适配视图"
            >
              <Scan className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </header>

      <div
        ref={containerRef}
        className="relative h-[620px] cursor-grab overflow-hidden bg-[#FBFAF8] active:cursor-grabbing dark:bg-[#1C1B1A]"
        onPointerDown={(e) => {
          if (e.button !== 0) return
          dragRef.current = { sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y }
          ;(e.target as Element).setPointerCapture?.(e.pointerId)
        }}
        onPointerMove={(e) => {
          const d = dragRef.current
          if (!d) return
          setView((v) => ({ ...v, x: d.ox + e.clientX - d.sx, y: d.oy + e.clientY - d.sy }))
        }}
        onPointerUp={() => {
          dragRef.current = null
        }}
        onPointerLeave={() => {
          dragRef.current = null
        }}
        onClick={() => onSelectTask('')}
      >
        <svg
          width="100%"
          height="100%"
          style={{ display: 'block' }}
        >
          <g transform={`translate(${view.x}, ${view.y}) scale(${view.k})`}>
            {/* dep 边 */}
            {renderEdges.dep.map((e) => {
              const d = edgePath(e.from, e.to)
              if (!d) return null
              const dimmed =
                (chainNodeSet !== null &&
                  !(chainNodeSet.has(e.from) && chainNodeSet.has(e.to))) ||
                (related !== null &&
                  highlightRoleOf(e.from, related) === null &&
                  highlightRoleOf(e.to, related) === null)
              return (
                <path
                  key={`dep-${e.key}`}
                  d={d}
                  fill="none"
                  stroke={e.crossing ? '#DC2626' : dark ? '#57534E' : '#C7C2BC'}
                  strokeWidth={e.crossing ? 2 : 1.2}
                  strokeDasharray={e.crossing ? '5 3' : undefined}
                  opacity={dimmed && !e.crossing ? 0.25 : 0.9}
                />
              )
            })}
            {/* conflict 边(红色虚线) */}
            {renderEdges.conflict.map((e) => {
              const d = edgePath(e.from, e.to)
              if (!d) return null
              return (
                <path
                  key={`cf-${e.key}`}
                  d={d}
                  fill="none"
                  stroke="#EF4444"
                  strokeWidth={1.2}
                  strokeDasharray="4 4"
                  opacity={0.75}
                />
              )
            })}
            {/* 节点 */}
            {visibleIds.map((id) => {
              const n = nodeById.get(id)
              const pos = layout.positions.get(id)
              if (!n || !pos) return null
              const cid = chainOf.get(id)
              const chain = cid ? chainById.get(cid) : undefined
              const archived = n.category === 'done' || n.category === 'dropped'
              return (
                <GraphNode
                  key={id}
                  id={id}
                  title={n.title}
                  category={n.category}
                  pos={pos}
                  chainColor={archived ? null : (chain?.color ?? null)}
                  chainLetter={archived ? null : (chain?.name.replace('链 ', '') ?? null)}
                  crossingInfo={crossingNodeTip.get(id) ?? null}
                  waitingInfo={waitingNodeTip.get(id) ?? null}
                  deferredInfo={deferredTip.get(id) ?? null}
                  dimmed={dimOf(id)}
                  selected={selectedTaskId === id}
                  ringColor={ringOf(id)}
                  dark={dark}
                  onSelect={onSelectTask}
                  onHover={onHoverTask}
                />
              )
            })}
          </g>
        </svg>

        {/* 图例 */}
        <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-stone-200 bg-white/90 px-3 py-1.5 text-[11px] text-stone-500 shadow-xs dark:border-stone-700 dark:bg-stone-900/90 dark:text-stone-400">
          {(Object.keys(palette) as TaskCategory[]).map((c) => (
            <span key={c} className="flex items-center gap-1">
              <span
                className="h-2.5 w-2.5 rounded-sm border"
                style={{ backgroundColor: palette[c].fill, borderColor: palette[c].stroke }}
              />
              {CAT_LABEL[c]}
            </span>
          ))}
          <span className="flex items-center gap-1 text-red-600 dark:text-red-400">
            <span className="inline-block h-2.5 w-2.5 rounded-full bg-red-600 text-[8px] font-bold leading-[10px] text-white text-center">!</span>
            交叉点
          </span>
          <span className="flex items-center gap-1 text-red-600 dark:text-red-400">
            <span className="inline-block h-2.5 w-2.5 rounded-sm border border-dashed border-red-600 text-[8px] font-bold leading-[10px] text-center">缓</span>
            本轮暂缓
          </span>
        </div>
      </div>
    </section>
  )
}
