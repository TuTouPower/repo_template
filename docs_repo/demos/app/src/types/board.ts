// 后端数据契约类型(严格贴合接口结构)

/** 状态机原始值 */
export type TaskStatus = 'backlog' | 'active' | 'blocked' | 'done' | 'dropped'

/** 看板展示分类 */
export type TaskCategory =
  | 'active'
  | 'runnable'
  | 'blocked_deps'
  | 'blocked_conflict'
  | 'done'
  | 'done_unmerged'
  | 'dropped'
  | 'backlog'

export interface TaskNode {
  id: string
  title: string
  status: TaskStatus
  category: TaskCategory
  depends_on: string[]
  conflicts_with: string[]
  /** 后端 schedule_status（backlog 区分 scheduled / pending_clarification / 未排程；active 通常为空） */
  schedule_status?: string
}

export type EdgeType = 'dep' | 'conflict'

export interface BoardEdge {
  type: EdgeType
  from: string
  to: string
}

/** 各 category 计数 */
export type BoardSummary = Record<TaskCategory, number>

export interface BoardData {
  project: string
  summary: BoardSummary
  nodes: TaskNode[]
  edges: BoardEdge[]
}
