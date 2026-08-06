import { LayoutGrid } from 'lucide-react'
import type { BoardSummary } from '@/types/board'
import { ALL_CATEGORIES, CATEGORY_META } from '@/lib/boardUtils'
import { cn } from '@/lib/utils'

export function SummaryBar({
  project,
  summary,
  total,
}: {
  project: string
  summary: BoardSummary
  total: number
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-stone-800 text-stone-50 dark:bg-stone-100 dark:text-stone-900">
          <LayoutGrid className="h-4 w-4" />
        </div>
        <div>
          <h1 className="text-base font-semibold leading-5 text-stone-900 dark:text-stone-100">{project}</h1>
          <p className="text-xs text-stone-400 dark:text-stone-500">共 {total} 个任务</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {ALL_CATEGORIES.map((cat) => {
          const meta = CATEGORY_META[cat]
          const count = summary[cat] ?? 0
          return (
            <span
              key={cat}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs',
                meta.badgeClass,
                !meta.focus && 'opacity-70',
              )}
              title={`${meta.label} (${meta.hint})`}
            >
              {cat === 'active' && (
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-500 opacity-60" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-amber-600" />
                </span>
              )}
              {meta.label}
              <span className="font-semibold tabular-nums">{count}</span>
            </span>
          )
        })}
      </div>
    </div>
  )
}
