import { useEffect, useState, type ReactNode } from 'react'
import { Link2, Swords, ArrowDownRight, X, Waypoints } from 'lucide-react'
import type { TaskNode } from '@/types/board'
import type { ChainPlan } from '@/types/chain'
import type { BoardIndex } from '@/lib/boardUtils'
import { CATEGORY_META } from '@/lib/boardUtils'
import { fetchTaskDocs, type TaskDocs } from '@/lib/mockDocs'
import { cn } from '@/lib/utils'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Skeleton } from '@/components/ui/skeleton'
import { CategoryBadge } from './CategoryBadge'
import { MarkdownView } from './MarkdownView'

interface TaskDetailSheetProps {
  node: TaskNode | null
  index: BoardIndex | null
  /** 当前切链方案(用于展示所属链与位置) */
  plan: ChainPlan | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onJump: (id: string) => void
}

function RelationChip({
  id,
  index,
  onJump,
  tone,
}: {
  id: string
  index: BoardIndex
  onJump: (id: string) => void
  tone: 'amber' | 'red' | 'emerald'
}) {
  const target = index.nodeById.get(id)
  if (!target) return null
  const meta = CATEGORY_META[target.category]
  const toneClass = {
    amber: 'hover:border-amber-300 hover:bg-amber-50 dark:hover:border-amber-700 dark:hover:bg-amber-950',
    red: 'hover:border-red-300 hover:bg-red-50 dark:hover:border-red-700 dark:hover:bg-red-950',
    emerald: 'hover:border-emerald-300 hover:bg-emerald-50 dark:hover:border-emerald-700 dark:hover:bg-emerald-950',
  }[tone]
  return (
    <button
      type="button"
      onClick={() => onJump(id)}
      className={cn(
        'flex w-full items-center gap-2 rounded-lg border border-stone-200 bg-white px-2.5 py-1.5 text-left transition-colors dark:border-stone-700 dark:bg-stone-900',
        toneClass,
      )}
    >
      <span className="font-mono text-[11px] text-stone-400 dark:text-stone-500">{target.id}</span>
      <span className="min-w-0 flex-1 truncate text-xs text-stone-700 dark:text-stone-300">{target.title}</span>
      <span
        className={cn(
          'shrink-0 rounded-full border px-1.5 py-px text-[10px]',
          meta.badgeClass,
        )}
      >
        {meta.label}
      </span>
    </button>
  )
}

function RelationGroup({
  icon,
  title,
  ids,
  index,
  onJump,
  tone,
  emptyText,
}: {
  icon: ReactNode
  title: string
  ids: string[]
  index: BoardIndex
  onJump: (id: string) => void
  tone: 'amber' | 'red' | 'emerald'
  emptyText: string
}) {
  return (
    <div>
      <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-stone-500 dark:text-stone-400">
        {icon}
        {title}
        <span className="font-normal tabular-nums text-stone-400 dark:text-stone-500">({ids.length})</span>
      </h3>
      {ids.length === 0 ? (
        <p className="rounded-lg bg-stone-50 px-2.5 py-2 text-xs text-stone-400 dark:bg-stone-800/60 dark:text-stone-500">{emptyText}</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {ids.map((id) => (
            <RelationChip key={id} id={id} index={index} onJump={onJump} tone={tone} />
          ))}
        </div>
      )}
    </div>
  )
}

export function TaskDetailSheet({
  node,
  index,
  plan,
  open,
  onOpenChange,
  onJump,
}: TaskDetailSheetProps) {
  const dependents = node && index ? (index.dependentsOf.get(node.id) ?? []) : []

  // 所属链与链内位置
  const chainInfo = (() => {
    if (!node || !plan) return null
    for (const c of plan.chains) {
      const at = c.taskIds.indexOf(node.id)
      if (at >= 0) return { chain: c, position: at + 1, total: c.taskIds.length }
    }
    return null
  })()
  const isUnassigned = !!node && !!plan && !chainInfo && plan.unassigned.includes(node.id)
  const deferredInfo = node && plan ? plan.deferred.find((d) => d.taskId === node.id) : undefined
  // 交叉点角色
  const crossingTips: string[] = []
  if (node && plan) {
    const nameOf = new Map(plan.chains.map((c) => [c.id, c.name]))
    const label = (cid: string) => (cid === 'future' ? '未来批次' : (nameOf.get(cid) ?? cid))
    for (const c of plan.crossings) {
      if (c.dependsOnNodeId === node.id) {
        crossingTips.push(`交叉点:${label(c.chainId)} 的 ${c.nodeId} 在等待本任务`)
      }
      if (c.nodeId === node.id) {
        crossingTips.push(
          `本任务依赖 ${label(c.dependsOnChainId)} 的 ${c.dependsOnNodeId}(交叉点)`,
        )
      }
    }
  }

  // 任务文档(spec.md / task.md):选中任务或切到文档页时按需拉取(模拟后端)
  const [docs, setDocs] = useState<TaskDocs | null>(null)
  const [docsLoading, setDocsLoading] = useState(false)
  useEffect(() => {
    setDocs(null)
    if (!node) return
    let cancelled = false
    setDocsLoading(true)
    fetchTaskDocs(node.id, node.title).then((d) => {
      if (cancelled) return
      setDocs(d)
      setDocsLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [node?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const docsViewer = (markdown: string | undefined) =>
    docsLoading || !markdown ? (
      <div className="flex flex-col gap-2.5 pt-1">
        <Skeleton className="h-5 w-3/4" />
        <Skeleton className="h-3.5 w-full" />
        <Skeleton className="h-3.5 w-11/12" />
        <Skeleton className="h-3.5 w-4/5" />
        <Skeleton className="h-24 w-full rounded-lg" />
      </div>
    ) : (
      <MarkdownView markdown={markdown} />
    )

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[420px] overflow-y-auto sm:max-w-[420px]">
        {node && index ? (
          <>
            <SheetHeader>
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-stone-400 dark:text-stone-500">{node.id}</span>
                <CategoryBadge category={node.category} />
              </div>
              <SheetTitle className="text-left text-base leading-6">{node.title}</SheetTitle>
              <SheetDescription className="text-left">
                状态机状态:<code className="rounded bg-stone-100 px-1 py-0.5 font-mono text-[11px] dark:bg-stone-800">{node.status}</code>
                <span className="mx-1.5 text-stone-300 dark:text-stone-600">·</span>
                分类:<code className="rounded bg-stone-100 px-1 py-0.5 font-mono text-[11px] dark:bg-stone-800">{node.category}</code>
              </SheetDescription>
            </SheetHeader>

            <Tabs defaultValue="detail" className="mt-4">
              <TabsList className="h-9 w-full">
                <TabsTrigger value="detail" className="flex-1 text-xs">详情</TabsTrigger>
                <TabsTrigger value="spec" className="flex-1 font-mono text-xs">spec.md</TabsTrigger>
                <TabsTrigger value="task" className="flex-1 font-mono text-xs">task.md</TabsTrigger>
              </TabsList>
              <TabsContent value="detail" className="mt-4">
            <div className="flex flex-col gap-5">
              {(chainInfo || isUnassigned || deferredInfo) && (
                <div>
                  <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-stone-500 dark:text-stone-400">
                    <Waypoints className="h-3.5 w-3.5 text-sky-700 dark:text-sky-400" />
                    所属链
                  </h3>
                  {chainInfo ? (
                    <div className="flex items-center gap-2 rounded-lg border border-stone-200 bg-white px-2.5 py-2 dark:border-stone-700 dark:bg-stone-900">
                      <span
                        className="h-3 w-3 rounded-sm"
                        style={{ backgroundColor: chainInfo.chain.color }}
                      />
                      <span className="text-xs font-medium text-stone-800 dark:text-stone-100">
                        {chainInfo.chain.name}
                      </span>
                      <span className="text-xs tabular-nums text-stone-500 dark:text-stone-400">
                        第 {chainInfo.position} / {chainInfo.total} 个
                      </span>
                    </div>
                  ) : deferredInfo ? (
                    <p className="rounded-lg border border-red-200 bg-red-50 px-2.5 py-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
                      本轮暂缓:{deferredInfo.reason}
                    </p>
                  ) : (
                    <p className="rounded-lg bg-stone-50 px-2.5 py-2 text-xs text-stone-400 dark:bg-stone-800/60 dark:text-stone-500">
                      未来批次(依赖未满足,暂不可跑)
                    </p>
                  )}
                  {crossingTips.length > 0 && (
                    <div className="mt-1.5 flex flex-col gap-1">
                      {crossingTips.map((tip) => (
                        <p
                          key={tip}
                          className="rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-[11px] text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300"
                        >
                          {tip}
                        </p>
                      ))}
                    </div>
                  )}
                </div>
              )}
              <RelationGroup
                icon={<Link2 className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />}
                title="依赖(阻塞它的上游)"
                ids={node.depends_on}
                index={index}
                onJump={onJump}
                tone="amber"
                emptyText="没有依赖,随时可以开始"
              />
              <RelationGroup
                icon={<ArrowDownRight className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />}
                title="被依赖(它阻塞的下游)"
                ids={dependents}
                index={index}
                onJump={onJump}
                tone="emerald"
                emptyText="没有任务依赖它"
              />
              <RelationGroup
                icon={<Swords className="h-3.5 w-3.5 text-red-600 dark:text-red-400" />}
                title="冲突任务(互斥)"
                ids={node.conflicts_with}
                index={index}
                onJump={onJump}
                tone="red"
                emptyText="没有冲突任务"
              />
            </div>

            <p className="mt-6 flex items-center gap-1 text-[11px] text-stone-400 dark:text-stone-500">
              <X className="h-3 w-3" />
              点击关系条目可跳转到对应任务
            </p>
              </TabsContent>
              <TabsContent value="spec" className="mt-4">
                {docsViewer(docs?.specMd)}
              </TabsContent>
              <TabsContent value="task" className="mt-4">
                {docsViewer(docs?.taskMd)}
              </TabsContent>
            </Tabs>
          </>
        ) : (
          <SheetHeader>
            <SheetTitle>未选择任务</SheetTitle>
            <SheetDescription>点击看板中的任务卡片查看详情</SheetDescription>
          </SheetHeader>
        )}
      </SheetContent>
    </Sheet>
  )
}
