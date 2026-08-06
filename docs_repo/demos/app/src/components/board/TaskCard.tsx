import { memo } from 'react'
import { Link2, Swords } from 'lucide-react'
import type { TaskNode } from '@/types/board'
import type { HighlightRole } from '@/lib/boardUtils'
import { CATEGORY_META } from '@/lib/boardUtils'
import { cn } from '@/lib/utils'
import { CategoryBadge } from './CategoryBadge'

interface TaskCardProps {
  node: TaskNode
  highlightRole: HighlightRole
  /** 是否有任意高亮在进行(用于弱化无关卡片) */
  dimmed: boolean
  selected: boolean
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
  /** 归档区紧凑模式 */
  compact?: boolean
}

const ROLE_RING: Record<Exclude<HighlightRole, null>, string> = {
  self: 'ring-2 ring-stone-700 border-stone-700 dark:ring-stone-300 dark:border-stone-300',
  upstream: 'ring-2 ring-amber-400/80 border-amber-300',
  downstream: 'ring-2 ring-emerald-400/80 border-emerald-300',
  conflict: 'ring-2 ring-red-400/80 border-red-300',
}

function formatIdList(ids: string[], max = 3): string {
  if (ids.length <= max) return ids.join('、')
  return `${ids.slice(0, max).join('、')} 等 ${ids.length} 个`
}

export const TaskCard = memo(function TaskCard({
  node,
  highlightRole,
  dimmed,
  selected,
  onSelect,
  onHover,
  compact,
}: TaskCardProps) {
  const meta = CATEGORY_META[node.category]
  const archived = node.category === 'done' || node.category === 'dropped'

  return (
    <button
      type="button"
      onClick={() => onSelect(node.id)}
      onMouseEnter={() => onHover(node.id)}
      onMouseLeave={() => onHover(null)}
      className={cn(
        'group relative w-full rounded-lg border border-stone-200 bg-white text-left shadow-xs dark:border-stone-700 dark:bg-stone-900 transition-all duration-150',
        'hover:border-stone-300 hover:shadow-sm dark:hover:border-stone-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-stone-500',
        compact ? 'px-3 py-2' : 'px-3 py-2.5',
        highlightRole ? ROLE_RING[highlightRole] : selected && 'ring-2 ring-stone-700 border-stone-700 dark:ring-stone-300 dark:border-stone-300',
        dimmed && !highlightRole && !selected && 'opacity-35',
        archived && 'bg-stone-50 dark:bg-stone-800/60',
      )}
    >
      {/* 左侧分类色条 */}
      <span
        className={cn(
          'absolute left-0 top-2 bottom-2 w-0.5 rounded-full',
          meta.cardBarClass,
        )}
      />
      <div className="flex items-center justify-between gap-2 pl-1.5">
        <span className="font-mono text-[11px] text-stone-400 dark:text-stone-500">{node.id}</span>
        <CategoryBadge category={node.category} />
      </div>
      <p
        className={cn(
          'mt-1 pl-1.5 text-[13px] leading-5 text-stone-800 dark:text-stone-100',
          archived && 'text-stone-400 line-through decoration-stone-300 dark:text-stone-500 dark:decoration-stone-600',
        )}
      >
        {node.title}
      </p>

      {/* 阻塞/冲突提示行 */}
      {!archived && node.category === 'blocked_deps' && node.depends_on.length > 0 && (
        <p className="mt-1 flex items-center gap-1 pl-1.5 text-[11px] text-orange-700/80 dark:text-orange-400/80">
          <Link2 className="h-3 w-3 shrink-0" />
          <span className="truncate">被 {formatIdList(node.depends_on)} 阻塞</span>
        </p>
      )}
      {!archived && node.conflicts_with.length > 0 && (
        <p className="mt-1 flex items-center gap-1 pl-1.5 text-[11px] text-red-700/80 dark:text-red-400/80">
          <Swords className="h-3 w-3 shrink-0" />
          <span className="truncate">与 {formatIdList(node.conflicts_with)} 冲突</span>
        </p>
      )}
    </button>
  )
})
