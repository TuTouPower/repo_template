import { useMemo, useState } from 'react'
import {
  AlertTriangle,
  CheckCheck,
  ChevronDown,
  ClipboardCopy,
  Copy,
  GitMerge,
  GitFork,
  ListPlus,
  PauseCircle,
  RefreshCw,
  RotateCcw,
  Scissors,
  Swords,
  X,
} from 'lucide-react'
import type { BoardData, TaskNode } from '@/types/board'
import type { Chain, ChainPlan, PlanValidation } from '@/types/chain'
import { CATEGORY_META } from '@/lib/boardUtils'
import { chainProgress, findCrossChainConflicts } from '@/lib/chainPlan'
import { chainStopInfo } from '@/lib/batchPlan'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

// ---------------------------------------------------------------------------
// 链内任务行
// ---------------------------------------------------------------------------

interface ChainTaskRowProps {
  node: TaskNode
  index: number
  chain: Chain
  isCurrent: boolean
  /** 本节点是交叉点(有链外任务在等它)→ 等待方描述列表 */
  waitedBy: string[]
  selected: boolean
  onSelectTask: (id: string) => void
  onRemove: (taskId: string) => void
  onSplit: (index: number) => void
}

function ChainTaskRow({
  node,
  index,
  chain,
  isCurrent,
  waitedBy,
  selected,
  onSelectTask,
  onRemove,
  onSplit,
}: ChainTaskRowProps) {
  const meta = CATEGORY_META[node.category]
  return (
    <div
      className={cn(
        'group relative flex items-center gap-2 rounded-lg border border-stone-200 bg-white dark:border-stone-700 dark:bg-stone-900 px-2 py-1.5 transition-colors hover:border-stone-300 dark:hover:border-stone-600',
        selected && 'ring-2 ring-stone-700 dark:ring-stone-300',
      )}
    >
      <button
        type="button"
        onClick={() => onSelectTask(node.id)}
        className="flex min-w-0 flex-1 items-center gap-2 text-left"
      >
        <span className="w-5 shrink-0 text-right font-mono text-[10px] text-stone-400 dark:text-stone-500">
          {index + 1}
        </span>
        <span className={cn('h-2 w-2 shrink-0 rounded-full', meta.accentClass)} />
        <span className="shrink-0 font-mono text-[11px] text-stone-400 dark:text-stone-500">{node.id}</span>
        <span className="min-w-0 flex-1 truncate text-xs text-stone-700 dark:text-stone-300">{node.title}</span>
      </button>

      {isCurrent && (
        <span
          className="shrink-0 rounded-full px-1.5 py-px text-[10px] font-medium text-white"
          style={{ backgroundColor: chain.color }}
        >
          当前
        </span>
      )}
      {waitedBy.length > 0 && (
        <span
          className="shrink-0 rounded-full bg-red-600 px-1.5 py-px text-[10px] font-medium text-white"
          title={waitedBy.join('\n')}
        >
          交叉点 ×{waitedBy.length}
        </span>
      )}

      <span
        className={cn(
          'shrink-0 rounded-full border px-1.5 py-px text-[10px]',
          meta.badgeClass,
        )}
      >
        {meta.label}
      </span>

      {/* hover 操作 */}
      <span className="absolute right-1 top-1/2 hidden -translate-y-1/2 items-center gap-0.5 rounded-md bg-white/95 dark:bg-stone-900/95 px-0.5 py-0.5 shadow-sm group-hover:flex">
        {index > 0 && (
          <button
            type="button"
            title="在此拆分为两条链"
            onClick={() => onSplit(index)}
            className="rounded p-1 text-stone-500 dark:text-stone-400 hover:bg-stone-100 dark:hover:bg-stone-800 hover:text-stone-800 dark:text-stone-100"
          >
            <Scissors className="h-3 w-3" />
          </button>
        )}
        <button
          type="button"
          title="从链中移除(回到未来批次)"
          onClick={() => onRemove(node.id)}
          className="rounded p-1 text-stone-500 dark:text-stone-400 hover:bg-red-50 hover:text-red-600 dark:text-red-400"
        >
          <X className="h-3 w-3" />
        </button>
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 链卡片
// ---------------------------------------------------------------------------

interface ChainCardProps {
  chain: Chain
  data: BoardData
  plan: ChainPlan
  issues: string[]
  highlighted: boolean
  selectedTaskId: string | null
  onSelectChain: (id: string | null) => void
  onSelectTask: (id: string) => void
  onRemoveTask: (taskId: string) => void
  onSplitChain: (chainId: string, index: number) => void
  onMergeChains: (firstId: string, secondId: string) => void
  onCopyChain: (chain: Chain) => void
  onCompleteChain: (chainId: string) => void
}

function ChainCard({
  chain,
  data,
  plan,
  issues,
  highlighted,
  selectedTaskId,
  onSelectChain,
  onSelectTask,
  onRemoveTask,
  onSplitChain,
  onMergeChains,
  onCopyChain,
  onCompleteChain,
}: ChainCardProps) {
  const nodeById = useMemo(() => new Map(data.nodes.map((n) => [n.id, n])), [data])
  const chainNameOf = useMemo(
    () => new Map(plan.chains.map((c) => [c.id, c.name])),
    [plan.chains],
  )
  const progress = chainProgress(chain, data)
  const headCat = nodeById.get(chain.taskIds[0] ?? '')?.category
  const running = headCat === 'active'
  const stopInfo = chainStopInfo(chain, plan, data)

  // 本链节点作为交叉点:waitedBy[nodeId] = 等待方描述
  const waitedBy = useMemo(() => {
    const m = new Map<string, string[]>()
    for (const c of plan.crossings) {
      if (c.dependsOnChainId !== chain.id) continue
      const who =
        c.chainId === 'future'
          ? `${c.nodeId}(未来批次)`
          : `${chainNameOf.get(c.chainId)} 的 ${c.nodeId}`
      m.set(c.dependsOnNodeId, [...(m.get(c.dependsOnNodeId) ?? []), `${who} 在等它`])
    }
    return m
  }, [plan.crossings, chain.id, chainNameOf])

  const otherChains = plan.chains.filter((c) => c.id !== chain.id)

  return (
    <article
      className={cn(
        'rounded-xl border bg-white transition-shadow dark:bg-stone-900',
        highlighted ? 'border-stone-500 shadow-md dark:border-stone-400' : 'border-stone-200 dark:border-stone-700 shadow-xs',
      )}
    >
      <header
        className="flex cursor-pointer items-center gap-2 border-b border-stone-100 px-3 py-2 dark:border-stone-800"
        onClick={() => onSelectChain(highlighted ? null : chain.id)}
        title="点击在 DAG 图中高亮本链"
      >
        <span className="h-3 w-3 rounded-sm" style={{ backgroundColor: chain.color }} />
        <h3 className="text-sm font-semibold text-stone-800 dark:text-stone-100">{chain.name}</h3>
        <span
          className={cn(
            'rounded-full px-2 py-0.5 text-[11px] font-medium',
            running ? 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300' : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300',
          )}
        >
          {running ? '运行中' : '新推荐'}
        </span>
        <span className="rounded-full bg-stone-100 dark:bg-stone-800 px-2 py-0.5 text-[11px] tabular-nums text-stone-600 dark:text-stone-400">
          {progress.total} 任务
        </span>
        {waitedBy.size > 0 && (
          <span className="rounded-full bg-red-100 dark:bg-red-950 px-2 py-0.5 text-[11px] text-red-700 dark:text-red-300">
            {waitedBy.size} 个交叉点
          </span>
        )}
        <span
          className="ml-auto flex items-center gap-1"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            title="复制本链(粘贴到终端派发)"
            onClick={() => onCopyChain(chain)}
            className="rounded-md border border-stone-200 dark:border-stone-700 p-1.5 text-stone-500 dark:text-stone-400 hover:bg-stone-50 dark:hover:bg-stone-800 hover:text-stone-800 dark:text-stone-100"
          >
            <Copy className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            title="模拟执行完成:本链全部任务标记为待合入(合入 main 后解锁下一批)"
            onClick={() => onCompleteChain(chain.id)}
            className="rounded-md border border-emerald-200 bg-emerald-50 p-1.5 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-300 dark:hover:bg-emerald-900"
          >
            <CheckCheck className="h-3.5 w-3.5" />
          </button>
          {otherChains.length > 0 && (
            <>
              <GitMerge className="ml-1 h-3.5 w-3.5 text-stone-400 dark:text-stone-500" />
              <Select onValueChange={(v) => onMergeChains(chain.id, v)}>
                <SelectTrigger className="h-7 w-[96px] border-stone-200 text-[11px] dark:border-stone-700 dark:bg-stone-900 dark:text-stone-300">
                  <SelectValue placeholder="合并到链尾" />
                </SelectTrigger>
                <SelectContent>
                  {otherChains.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name}({c.taskIds.length} 任务)
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </>
          )}
        </span>
      </header>

      {/* 合法性警告 */}
      {issues.length > 0 && (
        <div className="flex flex-col gap-0.5 border-b border-red-100 bg-red-50 px-3 py-1.5 text-[11px] text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {issues.slice(0, 3).map((msg) => (
            <p key={msg} className="flex items-center gap-1">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              {msg}
            </p>
          ))}
          {issues.length > 3 && <p>…共 {issues.length} 处警告</p>}
        </div>
      )}

      <div className="flex flex-col gap-1.5 p-2.5">
        {chain.taskIds.map((tid, i) => {
          const node = nodeById.get(tid)
          if (!node) return null
          return (
            <ChainTaskRow
              key={tid}
              node={node}
              index={i}
              chain={chain}
              isCurrent={progress.currentTaskId === tid}
              waitedBy={waitedBy.get(tid) ?? []}
              selected={selectedTaskId === tid}
              onSelectTask={onSelectTask}
              onRemove={onRemoveTask}
              onSplit={(idx) => onSplitChain(chain.id, idx)}
            />
          )
        })}
        {chain.taskIds.length === 0 && (
          <p className="py-3 text-center text-xs text-stone-400 dark:text-stone-500">空链(任务已全部移除)</p>
        )}
      </div>

      {/* 停止原因 */}
      {stopInfo && (
        <footer className="flex items-start gap-1.5 border-t border-stone-100 px-3 py-1.5 text-[11px] text-stone-500 dark:border-stone-800 dark:text-stone-400">
          <GitFork className="mt-px h-3 w-3 shrink-0 text-stone-400 dark:text-stone-500" />
          <span>{stopInfo}</span>
        </footer>
      )}
    </article>
  )
}

// ---------------------------------------------------------------------------
// 插入控件(暂缓区 / 未来批次共用)
// ---------------------------------------------------------------------------

function InsertControls({
  plan,
  taskId,
  onInsert,
}: {
  plan: ChainPlan
  taskId: string
  onInsert: (chainId: string, taskId: string, index: number) => void
}) {
  const [target, setTarget] = useState(plan.chains[0]?.id ?? '')
  const [pos, setPos] = useState('')
  if (plan.chains.length === 0) return null
  return (
    <div className="mt-1.5 flex items-center gap-1.5">
      <Select value={target} onValueChange={setTarget}>
        <SelectTrigger className="h-7 flex-1 border-stone-200 dark:border-stone-700 text-[11px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {plan.chains.map((c) => (
            <SelectItem key={c.id} value={c.id}>
              {c.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <input
        type="number"
        min={1}
        placeholder="位置"
        title="插入位置(从 1 开始,留空接到链尾)"
        value={pos}
        onChange={(e) => setPos(e.target.value)}
        className="h-7 w-14 rounded-md border border-stone-200 dark:border-stone-700 px-1.5 text-[11px] outline-none focus:border-stone-400"
      />
      <Button
        size="sm"
        variant="outline"
        className="h-7 px-2 text-[11px]"
        disabled={!target}
        onClick={() => {
          const chain = plan.chains.find((c) => c.id === target)
          const at = pos.trim() === '' ? (chain?.taskIds.length ?? 0) : Math.max(0, Number(pos) - 1)
          onInsert(target, taskId, at)
        }}
      >
        加入
      </Button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 链面板(右侧栏)
// ---------------------------------------------------------------------------

interface ChainPanelProps {
  plan: ChainPlan
  data: BoardData
  validation: PlanValidation
  dirty: boolean
  batchNo: number
  highlightChainId: string | null
  selectedTaskId: string | null
  onRecalc: () => void
  onResetData: () => void
  onSelectChain: (id: string | null) => void
  onSelectTask: (id: string) => void
  onRemoveTask: (taskId: string) => void
  onInsertTask: (chainId: string, taskId: string, index: number) => void
  onSplitChain: (chainId: string, index: number) => void
  onMergeChains: (firstId: string, secondId: string) => void
  onCopyChain: (chain: Chain) => void
  onCopyAll: () => void
  onCompleteChain: (chainId: string) => void
  /** 待合入(done 未合入 main)任务数 */
  unmergedCount: number
  /** 将待合入任务合入 main,进入下一批次 */
  onMerge: () => void
}

export function ChainPanel({
  plan,
  data,
  validation,
  dirty,
  batchNo,
  highlightChainId,
  selectedTaskId,
  onRecalc,
  onResetData,
  onSelectChain,
  onSelectTask,
  onRemoveTask,
  onInsertTask,
  onSplitChain,
  onMergeChains,
  onCopyChain,
  onCopyAll,
  onCompleteChain,
  unmergedCount,
  onMerge,
}: ChainPanelProps) {
  const [futureOpen, setFutureOpen] = useState(false)
  const nodeById = useMemo(() => new Map(data.nodes.map((n) => [n.id, n])), [data])
  const crossConflicts = useMemo(
    () => findCrossChainConflicts(plan.chains, data),
    [plan.chains, data],
  )
  const chainNameOf = useMemo(
    () => new Map(plan.chains.map((c) => [c.id, c.name])),
    [plan.chains],
  )
  const crossingNodes = useMemo(
    () => new Set(plan.crossings.map((c) => c.dependsOnNodeId)),
    [plan.crossings],
  )
  const runningCount = useMemo(() => {
    let n = 0
    for (const c of plan.chains) {
      if (nodeById.get(c.taskIds[0] ?? '')?.category === 'active') n += 1
    }
    return n
  }, [plan.chains, nodeById])

  return (
    <aside className="flex w-full shrink-0 flex-col gap-3 lg:w-[420px]">
      {/* 操作条 */}
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-stone-200 bg-white dark:border-stone-700 dark:bg-stone-900 px-3 py-2.5">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-stone-800 dark:text-stone-100">当下可执行批次</h2>
          <span className="rounded-full bg-stone-800 px-2 py-0.5 text-[11px] font-medium text-stone-50 dark:bg-stone-100 dark:text-stone-900 tabular-nums">
            批次 #{batchNo}
          </span>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            onClick={onCopyAll}
            className="h-8 gap-1.5 text-xs"
            title="复制整批推荐(粘贴到终端派发)"
          >
            <ClipboardCopy className="h-3.5 w-3.5" />
            复制整批
          </Button>
          <Button
            size="sm"
            onClick={onRecalc}
            className="h-8 gap-1.5 bg-stone-800 text-stone-50 hover:bg-stone-700 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-stone-200"
            title="按当前数据重新计算批次"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            重新计算
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onMerge}
            disabled={unmergedCount === 0}
            className="h-8 gap-1.5 border-sky-300 text-xs text-sky-700 hover:bg-sky-50 disabled:opacity-40 dark:border-sky-800 dark:text-sky-300 dark:hover:bg-sky-950"
            title={unmergedCount > 0 ? `将 ${unmergedCount} 个已完成任务合入 main,解锁下游并进入下一批次` : '没有待合入的任务'}
          >
            <GitMerge className="h-3.5 w-3.5" />
            合入 main{unmergedCount > 0 ? ` (${unmergedCount})` : ''}
          </Button>
          <button
            type="button"
            onClick={onResetData}
            title="重置为原始数据(丢弃模拟执行进度)"
            className="rounded-md border border-stone-200 dark:border-stone-700 p-1.5 text-stone-500 dark:text-stone-400 hover:bg-stone-50 dark:hover:bg-stone-800 hover:text-stone-800 dark:text-stone-100"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        </div>
        <div className="flex w-full items-center gap-2 text-[11px] text-stone-500 dark:text-stone-400">
          <span className="rounded-full bg-stone-100 dark:bg-stone-800 px-2 py-0.5 tabular-nums">
            {plan.chains.length} 条链({runningCount} 运行中 / {plan.chains.length - runningCount} 新推荐)
          </span>
          {plan.deferred.length > 0 && (
            <span className="rounded-full bg-red-100 dark:bg-red-950 px-2 py-0.5 tabular-nums text-red-700 dark:text-red-300">
              {plan.deferred.length} 暂缓
            </span>
          )}
          <span className="rounded-full bg-stone-100 dark:bg-stone-800 px-2 py-0.5 tabular-nums">
            {plan.unassigned.length} 未来批次
          </span>
          {unmergedCount > 0 && (
            <span className="rounded-full bg-sky-100 px-2 py-0.5 tabular-nums text-sky-700 dark:bg-sky-950 dark:text-sky-300">
              {unmergedCount} 待合入
            </span>
          )}
          <span
            className={cn(
              'rounded-full px-2 py-0.5 tabular-nums',
              crossingNodes.size > 0 ? 'bg-red-100 dark:bg-red-950 text-red-700 dark:text-red-300' : 'bg-stone-100 dark:bg-stone-800',
            )}
          >
            {crossingNodes.size} 交叉点
          </span>
        </div>
      </div>

      {dirty && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950 px-3 py-1.5 text-[11px] text-amber-800 dark:text-amber-300">
          已手动调整,「重新计算」将恢复自动推荐
        </p>
      )}

      {/* 全局警告 */}
      {validation.globalIssues.length > 0 && (
        <div className="rounded-lg border border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950 px-3 py-1.5 text-[11px] text-red-700 dark:text-red-300">
          {validation.globalIssues.map((msg) => (
            <p key={msg} className="flex items-center gap-1">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              {msg}
            </p>
          ))}
        </div>
      )}

      {/* 跨链冲突提示(手动调整造成时) */}
      {crossConflicts.length > 0 && (
        <div className="rounded-xl border border-red-200 bg-white dark:border-red-800 dark:bg-stone-900 px-3 py-2">
          <h3 className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-red-700 dark:text-red-300">
            <Swords className="h-3.5 w-3.5" />
            跨链冲突({crossConflicts.length} 对)— 互斥任务分属不同链,请勿并行派发
          </h3>
          <div className="flex flex-col gap-0.5 text-[11px] text-stone-600 dark:text-stone-400">
            {crossConflicts.slice(0, 6).map((c) => (
              <span key={`${c.aId}|${c.bId}`}>
                {c.aId}({chainNameOf.get(c.aChainId)}) ↔ {c.bId}({chainNameOf.get(c.bChainId)})
              </span>
            ))}
            {crossConflicts.length > 6 && <span>…共 {crossConflicts.length} 对</span>}
          </div>
        </div>
      )}

      <div className="flex max-h-[calc(100dvh-360px)] min-h-[200px] flex-col gap-3 overflow-y-auto pr-0.5">
        {/* 链卡片 */}
        {plan.chains.map((chain) => (
          <ChainCard
            key={chain.id}
            chain={chain}
            data={data}
            plan={plan}
            issues={validation.chainIssues[chain.id] ?? []}
            highlighted={highlightChainId === chain.id}
            selectedTaskId={selectedTaskId}
            onSelectChain={onSelectChain}
            onSelectTask={onSelectTask}
            onRemoveTask={onRemoveTask}
            onSplitChain={onSplitChain}
            onMergeChains={onMergeChains}
            onCopyChain={onCopyChain}
            onCompleteChain={onCompleteChain}
          />
        ))}
        {plan.chains.length === 0 && (
          <p className="rounded-xl border border-dashed border-stone-300 bg-stone-50 dark:border-stone-600 dark:bg-stone-900 px-3 py-6 text-center text-xs text-stone-500 dark:text-stone-400">
            当前没有可执行的任务 —— 全部任务要么已完成,要么还在等待依赖
          </p>
        )}

        {/* 暂缓区(冲突裁决) */}
        {plan.deferred.length > 0 && (
          <div className="rounded-xl border border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-950/60 p-2.5">
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-red-700 dark:text-red-300">
              <PauseCircle className="h-3.5 w-3.5" />
              本轮暂缓({plan.deferred.length})— 冲突裁决:让可并行的链最多
            </h3>
            <div className="flex flex-col gap-1.5">
              {plan.deferred.map((d) => {
                const node = nodeById.get(d.taskId)
                if (!node) return null
                return (
                  <div
                    key={d.taskId}
                    className="rounded-lg border border-red-200 bg-white dark:border-red-800 dark:bg-stone-900 px-2 py-1.5"
                  >
                    <button
                      type="button"
                      onClick={() => onSelectTask(d.taskId)}
                      className="flex w-full items-center gap-2 text-left"
                    >
                      <span className="shrink-0 font-mono text-[11px] text-stone-400 dark:text-stone-500">
                        {d.taskId}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-xs text-stone-700 dark:text-stone-300">
                        {node.title}
                      </span>
                      <span className="shrink-0 rounded-full bg-red-100 dark:bg-red-950 px-1.5 py-px text-[10px] text-red-700 dark:text-red-300">
                        暂缓
                      </span>
                    </button>
                    <p className="mt-1 text-[11px] text-stone-500 dark:text-stone-400">{d.reason}</p>
                    <InsertControls plan={plan} taskId={d.taskId} onInsert={onInsertTask} />
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* 未来批次 */}
        {plan.unassigned.length > 0 && (
          <div className="rounded-xl border border-dashed border-stone-300 bg-stone-50 dark:border-stone-600 dark:bg-stone-900 p-2.5">
            <button
              type="button"
              onClick={() => setFutureOpen((v) => !v)}
              className="flex w-full items-center gap-1.5 text-xs font-semibold text-stone-600 dark:text-stone-400"
            >
              <ListPlus className="h-3.5 w-3.5" />
              未来批次({plan.unassigned.length})— 依赖未满足,暂不可跑
              <ChevronDown
                className={cn('ml-auto h-3.5 w-3.5 transition-transform', futureOpen && 'rotate-180')}
              />
            </button>
            {futureOpen && (
              <div className="mt-2 flex flex-col gap-1.5">
                {plan.unassigned.map((tid) => {
                  const node = nodeById.get(tid)
                  if (!node) return null
                  return (
                    <div key={tid} className="rounded-lg border border-stone-200 bg-white dark:border-stone-700 dark:bg-stone-900 px-2 py-1.5">
                      <button
                        type="button"
                        onClick={() => onSelectTask(tid)}
                        className="flex w-full items-center gap-2 text-left"
                      >
                        <span className="shrink-0 font-mono text-[11px] text-stone-400 dark:text-stone-500">{tid}</span>
                        <span className="min-w-0 flex-1 truncate text-xs text-stone-700 dark:text-stone-300">
                          {node.title}
                        </span>
                        <span
                          className={cn(
                            'shrink-0 rounded-full border px-1.5 py-px text-[10px]',
                            CATEGORY_META[node.category].badgeClass,
                          )}
                        >
                          {CATEGORY_META[node.category].label}
                        </span>
                      </button>
                      <InsertControls plan={plan} taskId={tid} onInsert={onInsertTask} />
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}
