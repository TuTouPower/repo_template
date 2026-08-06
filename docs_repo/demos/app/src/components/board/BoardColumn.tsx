import type { TaskNode } from '@/types/board'
import type { HighlightRole, RelatedSets } from '@/lib/boardUtils'
import { CATEGORY_META, highlightRoleOf } from '@/lib/boardUtils'
import { cn } from '@/lib/utils'
import { TaskCard } from './TaskCard'
import type { TaskCategory } from '@/types/board'

interface BoardColumnProps {
  category: TaskCategory
  nodes: TaskNode[]
  related: RelatedSets | null
  selectedId: string | null
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
}

export function BoardColumn({
  category,
  nodes,
  related,
  selectedId,
  onSelect,
  onHover,
}: BoardColumnProps) {
  const meta = CATEGORY_META[category]
  const roleOf = (id: string): HighlightRole => highlightRoleOf(id, related)

  return (
    <section className="flex min-w-[240px] flex-col rounded-xl bg-stone-100/70 p-2.5 dark:bg-stone-800/50">
      <header className="flex items-center gap-2 px-1.5 pb-2.5">
        <span className={cn('h-2 w-2 rounded-full', meta.accentClass)} />
        <h2 className="text-sm font-semibold text-stone-800 dark:text-stone-100">{meta.label}</h2>
        <span className="text-xs text-stone-400 dark:text-stone-500">{meta.hint}</span>
        <span className="ml-auto rounded-full bg-white px-2 py-0.5 text-xs font-medium tabular-nums text-stone-600 shadow-xs dark:bg-stone-900 dark:text-stone-300">
          {nodes.length}
        </span>
      </header>
      <div className="flex max-h-[calc(100dvh-320px)] min-h-[120px] flex-col gap-2 overflow-y-auto pr-0.5">
        {nodes.length === 0 ? (
          <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-stone-200 text-xs text-stone-400 dark:border-stone-700 dark:text-stone-500 dark:text-stone-500">
            暂无任务
          </div>
        ) : (
          nodes.map((n) => (
            <TaskCard
              key={n.id}
              node={n}
              highlightRole={roleOf(n.id)}
              dimmed={related !== null}
              selected={selectedId === n.id}
              onSelect={onSelect}
              onHover={onHover}
            />
          ))
        )}
      </div>
    </section>
  )
}
