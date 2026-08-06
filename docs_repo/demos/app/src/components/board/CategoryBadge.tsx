import { CATEGORY_META } from '@/lib/boardUtils'
import { cn } from '@/lib/utils'
import type { TaskCategory } from '@/types/board'

export function CategoryBadge({
  category,
  className,
}: {
  category: TaskCategory
  className?: string
}) {
  const meta = CATEGORY_META[category]
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4',
        meta.badgeClass,
        className,
      )}
    >
      {category === 'active' && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-500 opacity-60" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-amber-600" />
        </span>
      )}
      {meta.label}
    </span>
  )
}
