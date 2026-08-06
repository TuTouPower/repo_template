// 链式任务规划(v2)类型定义

/** 一条链:若干未完成任务按依赖顺序严格串行 */
export interface Chain {
  /** 内部稳定 id,如 'c1' */
  id: string
  /** 展示名,如 '链 A' */
  name: string
  /** 链色(hex) */
  color: string
  /** 按执行顺序排列的任务 id(链是 DAG 中的一条路径) */
  taskIds: string[]
}

/**
 * 交叉点(链间依赖 / 汇合点):
 * 等待方任务 nodeId 依赖被等方任务 dependsOnNodeId。
 * chainId 为等待方所属链;'future' 表示等待方还没进本批(未来批次的汇合点)。
 * dependsOnNodeId 即"交叉点节点":它完成后就可以重新规划。
 */
export interface Crossing {
  chainId: string
  nodeId: string
  dependsOnChainId: string
  dependsOnNodeId: string
}

/** 本轮因冲突暂缓的任务 */
export interface DeferredTask {
  taskId: string
  /** 人类可读的暂缓原因,如 "与 t991 冲突,本轮暂缓(它堵住的任务更少)" */
  reason: string
  /** 本轮胜出、导致它被暂缓的冲突对手 */
  blockedBy: string[]
}

/** 当下可执行批次方案(全部前端计算,不持久化) */
export interface ChainPlan {
  chains: Chain[]
  /** 未进本批的未完成任务(未来批次 / 手动移除出链的任务) */
  unassigned: string[]
  /** 本轮因冲突暂缓的任务 */
  deferred: DeferredTask[]
  crossings: Crossing[]
}

/** 跨链冲突对(同一冲突对被分进了不同链,需要提示) */
export interface CrossChainConflict {
  aId: string
  aChainId: string
  bId: string
  bChainId: string
}

/** 校验结果 */
export interface PlanValidation {
  /** chainId → 该链的合法性警告(链内拓扑不连通等) */
  chainIssues: Record<string, string[]>
  /** 全局问题(重复分配等) */
  globalIssues: string[]
}
