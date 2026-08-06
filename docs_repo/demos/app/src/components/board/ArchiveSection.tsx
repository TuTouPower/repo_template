import { useMemo, useState } from 'react'
import { Archive, ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'
import type { TaskNode } from '@/types/board'
import type { HighlightRole, RelatedSets } from '@/lib/boardUtils'
import { highlightRoleOf } from '@/lib/boardUtils'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { TaskCard } from './TaskCard'

const PAGE_SIZE = 50

interface ArchiveSectionProps {
  doneNodes: TaskNode[]
  droppedNodes: TaskNode[]
  related: RelatedSets | null
  selectedId: string | null
  /** 搜索/筛选激活时自动展开 */
  forceOpen: boolean
  filterActive: boolean
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
}

export function ArchiveSection({
  doneNodes,
  droppedNodes,
  related,
  selectedId,
  forceOpen,
  filterActive,
  onSelect,
  onHover,
}: ArchiveSectionProps) {
  const [manualOpen, setManualOpen] = useState(false)
  const [tab, setTab] = useState<'done' | 'dropped'>('done')
  const [page, setPage] = useState(0)

  // 筛选指向 done/dropped 时强制展开,其余情况由用户手动控制
  const open = manualOpen || forceOpen

  const list = tab === 'done' ? doneNodes : droppedNodes
  const pageCount = Math.max(1, Math.ceil(list.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount - 1)
  const pageItems = useMemo(
    () => list.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE),
    [list, safePage],
  )

  // 相关任务是否落在归档区(用于折叠时提示)
  const relatedInArchive = useMemo(() => {
    if (!related) return 0
    let n = 0
    for (const node of doneNodes) if (highlightRoleOf(node.id, related)) n += 1
    for (const node of droppedNodes) if (highlightRoleOf(node.id, related)) n += 1
    return n
  }, [related, doneNodes, droppedNodes])

  const roleOf = (id: string): HighlightRole => highlightRoleOf(id, related)

  return (
    <Collapsible open={open} onOpenChange={setManualOpen} className="rounded-xl border border-stone-200 bg-white dark:border-stone-700 dark:bg-stone-900">
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-stone-50 rounded-xl dark:hover:bg-stone-800 transition-colors"
        >
          <Archive className="h-4 w-4 text-stone-400 dark:text-stone-500" />
          <span className="text-sm font-medium text-stone-700 dark:text-stone-300">归档区</span>
          <span className="text-xs text-stone-400 dark:text-stone-500">已完成 / 已放弃的任务不参与主视图</span>
          {relatedInArchive > 0 && !open && (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800 dark:bg-amber-950 dark:text-amber-300">
              {relatedInArchive} 个相关任务在归档中
            </span>
          )}
          <span className="ml-auto flex items-center gap-1.5">
            <span className="rounded-full bg-stone-100 px-2.5 py-0.5 text-xs tabular-nums text-stone-500 dark:text-stone-400 dark:bg-stone-800 dark:text-stone-400">
              已完成 {doneNodes.length}
            </span>
            <span className="rounded-full bg-stone-100 px-2.5 py-0.5 text-xs tabular-nums text-stone-400 dark:bg-stone-800 dark:text-stone-500">
              已放弃 {droppedNodes.length}
            </span>
            <ChevronDown
              className={cn('h-4 w-4 text-stone-400 dark:text-stone-500 transition-transform', open && 'rotate-180')}
            />
          </span>
        </button>
      </CollapsibleTrigger>

      <CollapsibleContent>
        <div className="border-t border-stone-100 px-4 py-3 dark:border-stone-800">
          <div className="mb-3 flex items-center gap-2">
            <div className="flex rounded-lg bg-stone-100 p-0.5 dark:bg-stone-800">
              {(['done', 'dropped'] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => {
                    setTab(t)
                    setPage(0)
                  }}
                  className={cn(
                    'rounded-md px-3 py-1 text-xs transition-colors',
                    tab === t
                      ? 'bg-white font-medium text-stone-800 shadow-xs dark:bg-stone-900 dark:text-stone-100'
                      : 'text-stone-500 hover:text-stone-700 dark:text-stone-400 dark:hover:text-stone-200',
                  )}
                >
                  {t === 'done' ? `已完成 (${doneNodes.length})` : `已放弃 (${droppedNodes.length})`}
                </button>
              ))}
            </div>
            {filterActive && (
              <span className="text-xs text-stone-400 dark:text-stone-500">已按当前搜索 / 筛选条件过滤</span>
            )}
            {pageCount > 1 && (
              <div className="ml-auto flex items-center gap-1.5">
                <Button
                  variant="outline"
                  size="icon"
                  className="h-7 w-7"
                  disabled={safePage === 0}
                  onClick={() => setPage(safePage - 1)}
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </Button>
                <span className="text-xs tabular-nums text-stone-500 dark:text-stone-400">
                  {safePage + 1} / {pageCount}
                </span>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-7 w-7"
                  disabled={safePage >= pageCount - 1}
                  onClick={() => setPage(safePage + 1)}
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            )}
          </div>

          {pageItems.length === 0 ? (
            <p className="py-6 text-center text-xs text-stone-400 dark:text-stone-500">当前条件下无归档任务</p>
          ) : (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {pageItems.map((n) => (
                <TaskCard
                  key={n.id}
                  node={n}
                  compact
                  highlightRole={roleOf(n.id)}
                  dimmed={related !== null}
                  selected={selectedId === n.id}
                  onSelect={onSelect}
                  onHover={onHover}
                />
              ))}
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
