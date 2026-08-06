// ---------------------------------------------------------------------------
// 任务文档 mock:spec.md(需求规格)与 task.md(执行任务单)
// 前端样式与示例内容先行 —— 正式内容由后端按 task 提供,届时把
// fetchTaskDocs 替换为真实接口调用即可(返回结构不变)。
// ---------------------------------------------------------------------------

export interface TaskDocs {
  /** spec.md:需求规格说明书 */
  specMd: string
  /** task.md:给 coding agent 的执行任务单 */
  taskMd: string
}

function hash(id: string): number {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0
  return Math.abs(h)
}

const MODULES = ['调度引擎', 'task view 看板', '依赖解析器', '执行器', '状态机', '事件总线']
const FILES = [
  ['src/engine/scheduler.ts', 'src/engine/dag.ts', 'tests/scheduler.test.ts'],
  ['src/board/view.tsx', 'src/board/state.ts', 'tests/view.test.tsx'],
  ['src/core/machine.ts', 'src/core/types.ts', 'tests/machine.test.ts'],
  ['src/queue/runner.ts', 'src/queue/retry.ts', 'tests/runner.test.ts'],
]
const ACCEPTS = [
  '重复执行 100 次结果一致(幂等)',
  '并发 8 路时不出现竞态(压测脚本通过)',
  '异常注入后能在 3s 内恢复到一致状态',
  '边界输入(空图/单节点/自环拒绝)行为符合约定',
]

export function fetchTaskDocs(taskId: string, title: string): Promise<TaskDocs> {
  const h = hash(taskId)
  const mod = MODULES[h % MODULES.length]
  const files = FILES[h % FILES.length]
  const accept = [ACCEPTS[h % ACCEPTS.length], ACCEPTS[(h + 1) % ACCEPTS.length]]
  const specMd = `# ${title} — 需求规格

> 文档类型:\`spec.md\` · 任务:\`${taskId}\` · 模块:${mod}
> (示例内容,正式版由后端提供)

## 背景

${mod}当前在链式执行场景下缺少统一约定,多个 coding agent 并行时行为不一致。
本任务在不破坏既有接口的前提下补齐该能力。

## 目标

- 明确${mod}在串行链与并行链下的语义边界
- 对外暴露的行为可观测、可回归
- 与既有 \`done / dropped\` 归档逻辑保持兼容

## 验收标准

1. ${accept[0]}
2. ${accept[1]}
3. 新增单元测试覆盖核心路径,覆盖率不低于 80%

## 非目标

- 不引入新的持久化存储
- 不改动与本任务存在 \`conflict\` 关系的其他任务范围
- 性能优化不在本次范围内(单独立项)

## 接口约定

\`\`\`ts
// 供下游任务消费的稳定接口(签名冻结)
export interface ChainHandle {
  readonly id: string
  next(): TaskNode | null
  complete(id: string): void
}
\`\`\`
`

  const taskMd = `# ${title} — 执行任务单

> 文档类型:\`task.md\` · 任务:\`${taskId}\`
> (示例内容,正式版由后端提供)

## 执行步骤

1. 阅读 \`spec.md\`,确认验收标准与接口约定
2. 在 ${files[0]} 中实现核心逻辑
3. 同步更新 ${files[1]} 的调用方
4. 编写/更新测试:${files[2]}
5. 本地跑通 \`npm test\` 与 \`npm run build\`

## 涉及文件

| 文件 | 改动类型 |
| --- | --- |
| \`${files[0]}\` | 主要实现 |
| \`${files[1]}\` | 适配修改 |
| \`${files[2]}\` | 测试 |

## 约束

- **不要**触碰与本任务冲突的其他任务的代码路径
- 分支基于最新 \`main\`,完成后标记为 *待合入*
- 提交信息格式:\`feat(${taskId}): <一句话说明>\`

## 状态记录

- [x] 任务单生成
- [ ] 实现完成
- [ ] 测试通过
- [ ] 合入 main
`

  return new Promise((resolve) => {
    setTimeout(() => resolve({ specMd, taskMd }), 250)
  })
}
