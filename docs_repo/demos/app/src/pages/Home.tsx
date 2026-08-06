import { useEffect, useMemo, useState } from 'react'
import { Database, GitBranch, Moon, Search, Sun, X } from 'lucide-react'
import type { BoardData, TaskNode } from '@/types/board'
import type { Chain, ChainPlan } from '@/types/chain'
import type { DatasetId } from '@/lib/mockData'
import { DATASETS, DEFAULT_DATASET_ID, fetchBoard, getDatasetStats } from '@/lib/mockData'
import { buildIndex, collectRelated } from '@/lib/boardUtils'
import {
  chainLetter,
  chainOfMap,
  insertTask,
  isUnfinished,
  mergeChains,
  removeTask,
  splitChain,
  validatePlan,
} from '@/lib/chainPlan'
import { applyCompletions, applyMerge, computeBatchPlan } from '@/lib/batchPlan'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { SummaryBar } from '@/components/board/SummaryBar'
import { ArchiveSection } from '@/components/board/ArchiveSection'
import { TaskDetailSheet } from '@/components/board/TaskDetailSheet'
import { DagGraph } from '@/components/board/DagGraph'
import { ChainPanel } from '@/components/board/ChainPanel'

function matchesQuery(node: TaskNode, q: string): boolean {
  if (!q) return true
  const lower = q.toLowerCase()
  return node.id.toLowerCase().includes(lower) || node.title.toLowerCase().includes(lower)
}

/** 复制到剪贴板(带降级) */
async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
}

/** 单条链的派发文本:A: t976 t977 t978 t979 */
function chainText(chain: Chain): string {
  return `${chainLetter(chain.name)}: ${chain.taskIds.join(' ')}`
}

export default function Home() {
  const [datasetId, setDatasetId] = useState<DatasetId>(DEFAULT_DATASET_ID)
  const [data, setData] = useState<BoardData | null>(null)
  const [plan, setPlan] = useState<ChainPlan | null>(null)
  const [dirty, setDirty] = useState(false)
  const [batchNo, setBatchNo] = useState(1)
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [highlightChainId, setHighlightChainId] = useState<string | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [showCompleted, setShowCompleted] = useState(false)
  const [showConflicts, setShowConflicts] = useState(true)
  const [copiedTip, setCopiedTip] = useState<string | null>(null)
  const [dark, setDark] = useState<boolean>(() => {
    const saved = localStorage.getItem('task-board-theme')
    if (saved) return saved === 'dark'
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  })

  // 深色模式:class 策略,挂到 <html> 上并持久化
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    localStorage.setItem('task-board-theme', dark ? 'dark' : 'light')
  }, [dark])

  // 各数据集的未完成统计(用于切换控件标签,同步确定性计算)
  const datasetStats = useMemo(
    () => DATASETS.map((meta) => ({ meta, stats: getDatasetStats(meta.id) })),
    [],
  )

  // 像调真实后端一样按数据集异步取数,取到后立即计算当下可执行批次。
  // 切换数据集时:重置选中/高亮/手动调整/批次号等视图状态,重新进入 loading 态。
  useEffect(() => {
    let cancelled = false
    setData(null)
    setPlan(null)
    setDirty(false)
    setBatchNo(1)
    setQuery('')
    setSelectedId(null)
    setHoveredId(null)
    setHighlightChainId(null)
    setSheetOpen(false)
    fetchBoard(datasetId).then((d) => {
      if (cancelled) return
      setData(d)
      setPlan(computeBatchPlan(d))
    })
    return () => {
      cancelled = true
    }
  }, [datasetId])

  const index = useMemo(() => (data ? buildIndex(data) : null), [data])
  const validation = useMemo(
    () => (plan && data ? validatePlan(plan, data) : { chainIssues: {}, globalIssues: [] }),
    [plan, data],
  )
  const chainOf = useMemo(() => (plan ? chainOfMap(plan.chains) : null), [plan])

  // 搜索命中集合(null = 未搜索)
  const q = query.trim()
  const matchedIds = useMemo(() => {
    if (!data || !q) return null
    return new Set(data.nodes.filter((n) => matchesQuery(n, q)).map((n) => n.id))
  }, [data, q])

  // 归档区(按搜索过滤)
  const archivedGroups = useMemo(() => {
    const done: TaskNode[] = []
    const dropped: TaskNode[] = []
    if (!data) return { done, dropped }
    for (const n of data.nodes) {
      if (!matchesQuery(n, q)) continue
      if (n.category === 'done') done.push(n)
      else if (n.category === 'dropped') dropped.push(n)
    }
    return { done, dropped }
  }, [data, q])

  // hover 关系高亮(上游/下游/冲突)
  const related = useMemo(
    () => (hoveredId && index ? collectRelated(hoveredId, index) : null),
    [hoveredId, index],
  )

  const selectedNode = selectedId && index ? (index.nodeById.get(selectedId) ?? null) : null

  const handleSelectTask = (id: string) => {
    if (!id) {
      setSelectedId(null)
      setSheetOpen(false)
      return
    }
    setSelectedId(id)
    setSheetOpen(true)
    const cid = chainOf?.get(id)
    if (cid) setHighlightChainId(cid)
  }

  const handleRecalc = () => {
    if (!data) return
    if (dirty && !window.confirm('已手动调整过切链,重新计算将丢弃调整并恢复自动推荐。继续?')) {
      return
    }
    setPlan(computeBatchPlan(data))
    setDirty(false)
    setHighlightChainId(null)
  }

  /** 模拟执行:某条链全部完成 → 标记为"待合入"(未合入不解锁下游) */
  const handleCompleteChain = (chainId: string) => {
    if (!data || !plan) return
    const chain = plan.chains.find((c) => c.id === chainId)
    if (!chain) return
    const next = applyCompletions(data, chain.taskIds)
    setData(next)
    setPlan(computeBatchPlan(next))
    setDirty(false)
    setHighlightChainId(null)
  }

  /** 模拟合入 main:待合入 → done,解锁下游,重算得到下一批 */
  const handleMerge = () => {
    if (!data) return
    const next = applyMerge(data)
    setData(next)
    setPlan(computeBatchPlan(next))
    setBatchNo((n) => n + 1)
    setDirty(false)
    setHighlightChainId(null)
  }

  /** 重置为原始数据(丢弃模拟执行进度) */
  const handleResetData = () => {
    fetchBoard(datasetId).then((d) => {
      setData(d)
      setPlan(computeBatchPlan(d))
      setBatchNo(1)
      setDirty(false)
      setHighlightChainId(null)
    })
  }

  const flashCopied = (tip: string) => {
    setCopiedTip(tip)
    window.setTimeout(() => setCopiedTip(null), 1600)
  }

  const handleCopyChain = (chain: Chain) => {
    copyText(chainText(chain))
    flashCopied(`已复制 ${chain.name}`)
  }

  const handleCopyAll = () => {
    if (!plan) return
    copyText(plan.chains.map(chainText).join('\n'))
    flashCopied(`已复制整批 ${plan.chains.length} 条链`)
  }

  const mutatePlan = (next: ChainPlan) => {
    setPlan(next)
    setDirty(true)
  }

  const depEdgeCount = data?.edges.filter((e) => e.type === 'dep').length ?? 0
  const conflictEdgeCount = data?.edges.filter((e) => e.type === 'conflict').length ?? 0
  const unfinishedCount = data?.nodes.filter((n) => isUnfinished(n.category)).length ?? 0
  const unmergedCount = data?.summary.done_unmerged ?? 0

  return (
    <div className="min-h-[100dvh] bg-[#F7F6F3] text-stone-900 dark:bg-[#171614] dark:text-stone-100">
      <div className="mx-auto flex max-w-[1680px] flex-col gap-4 px-5 py-6 lg:px-8">
        {/* 顶行 summary 条 + 数据集切换 */}
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
          {data ? (
            <SummaryBar project={data.project} summary={data.summary} total={data.nodes.length} />
          ) : (
            <div className="flex items-center gap-4">
              <Skeleton className="h-8 w-8 rounded-lg" />
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-6 w-96 rounded-full" />
            </div>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => setDark((v) => !v)}
              title={dark ? '切换为浅色模式' : '切换为深色模式'}
              className="rounded-lg border border-stone-200 bg-white p-2 text-stone-500 hover:bg-stone-50 hover:text-stone-800 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-400 dark:hover:bg-stone-800 dark:hover:text-stone-200"
            >
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <Database className="h-3.5 w-3.5 text-stone-400" />
            <Tabs value={datasetId} onValueChange={(v) => setDatasetId(v as DatasetId)}>
              <TabsList className="h-9">
                {datasetStats.map(({ meta, stats }) => (
                  <TabsTrigger
                    key={meta.id}
                    value={meta.id}
                    title={meta.description}
                    className="text-xs"
                  >
                    {meta.name} · {stats.unfinished} 待规划
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          </div>
        </div>

        {/* 工具条:搜索 */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative w-72 max-w-full">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-stone-400" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="按 id / 标题搜索任务…"
              className="h-9 border-stone-200 bg-white pl-8 pr-8 text-sm dark:border-stone-700 dark:bg-stone-900"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-0.5 text-stone-400 hover:bg-stone-100 hover:text-stone-600 dark:text-stone-500 dark:hover:bg-stone-800 dark:hover:text-stone-300"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          {matchedIds && (
            <span className="text-xs text-stone-500 dark:text-stone-400">
              命中 {matchedIds.size} 个任务(图中未命中的已弱化,归档区按条件过滤)
            </span>
          )}
          {related && (
            <div className="ml-auto flex items-center gap-3 text-[11px] text-stone-500 dark:text-stone-400">
              <span className="flex items-center gap-1">
                <span className="h-2.5 w-2.5 rounded-sm border-2 border-amber-400" /> 上游依赖
              </span>
              <span className="flex items-center gap-1">
                <span className="h-2.5 w-2.5 rounded-sm border-2 border-emerald-400" /> 下游被依赖
              </span>
              <span className="flex items-center gap-1">
                <span className="h-2.5 w-2.5 rounded-sm border-2 border-red-400" /> 冲突任务
              </span>
            </div>
          )}
        </div>

        {/* 主视图:左 DAG 图 + 右链列表 */}
        {data && plan ? (
          <main className="flex flex-col gap-4 lg:flex-row">
            <DagGraph
              data={data}
              plan={plan}
              dark={dark}
              matchedIds={matchedIds}
              related={related}
              selectedTaskId={selectedId}
              highlightChainId={highlightChainId}
              showCompleted={showCompleted}
              showConflicts={showConflicts}
              onToggleCompleted={setShowCompleted}
              onToggleConflicts={setShowConflicts}
              onSelectTask={handleSelectTask}
              onHoverTask={setHoveredId}
            />
            <ChainPanel
              plan={plan}
              data={data}
              validation={validation}
              dirty={dirty}
              batchNo={batchNo}
              highlightChainId={highlightChainId}
              selectedTaskId={selectedId}
              onRecalc={handleRecalc}
              onResetData={handleResetData}
              unmergedCount={unmergedCount}
              onMerge={handleMerge}
              onSelectChain={setHighlightChainId}
              onSelectTask={handleSelectTask}
              onRemoveTask={(tid) => mutatePlan(removeTask(plan, data, tid))}
              onInsertTask={(cid, tid, at) => mutatePlan(insertTask(plan, data, cid, tid, at))}
              onSplitChain={(cid, at) => mutatePlan(splitChain(plan, data, cid, at))}
              onMergeChains={(a, b) => mutatePlan(mergeChains(plan, data, a, b))}
              onCopyChain={handleCopyChain}
              onCopyAll={handleCopyAll}
              onCompleteChain={handleCompleteChain}
            />
          </main>
        ) : (
          <main className="flex flex-col gap-4 lg:flex-row">
            <Skeleton className="h-[680px] flex-1 rounded-xl" />
            <Skeleton className="h-[680px] w-full rounded-xl lg:w-[400px]" />
          </main>
        )}

        {/* 归档区:done / dropped,默认折叠 */}
        {data && (
          <ArchiveSection
            key={datasetId}
            doneNodes={archivedGroups.done}
            droppedNodes={archivedGroups.dropped}
            related={related}
            selectedId={selectedId}
            forceOpen={false}
            filterActive={q !== ''}
            onSelect={handleSelectTask}
            onHover={setHoveredId}
          />
        )}

        {/* 底栏:图统计 */}
        {data && (
          <footer className="flex items-center gap-2 text-[11px] text-stone-400 dark:text-stone-500">
            <GitBranch className="h-3 w-3" />
            <span>
              关系图:{data.nodes.length} 节点 · {depEdgeCount} 条依赖边 · {conflictEdgeCount}{' '}
              条冲突边 · 未完成 {unfinishedCount} 个
            </span>
            <span className="ml-auto">
              批次推荐为前端实时计算,刷新即重算;数据为本地 mock(
              {DATASETS.find((d) => d.id === datasetId)?.name} · 固定 seed) · 批次 #{batchNo}
            </span>
          </footer>
        )}
      </div>

      <TaskDetailSheet
        node={selectedNode}
        index={index}
        plan={plan}
        open={sheetOpen && selectedNode !== null}
        onOpenChange={(open) => {
          setSheetOpen(open)
          if (!open) setSelectedId(null)
        }}
        onJump={handleSelectTask}
      />

      {/* 复制成功提示 */}
      {copiedTip && (
        <div className="fixed bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-full bg-stone-900 px-4 py-2 text-xs text-stone-50 shadow-lg">
          {copiedTip}
        </div>
      )}
    </div>
  )
}
