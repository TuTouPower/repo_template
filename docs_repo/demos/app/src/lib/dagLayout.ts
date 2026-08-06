// Sugiyama-lite 分层布局:最长路径分层 + 重心法(barycenter)层内排序减少交叉。
// 纯函数、确定性,节点规模几百时瞬时完成。

export interface LayoutPos {
  x: number
  y: number
  layer: number
}

export interface DagLayout {
  positions: Map<string, LayoutPos>
  width: number
  height: number
  layerCount: number
}

export const NODE_W = 168
export const NODE_H = 50
export const GAP_X = 40
export const GAP_Y = 64

interface Edge {
  from: string
  to: string
}

/**
 * 对给定节点集合和 dep 边(方向 from → to,from 是上游)做分层布局。
 * layer:最长路径分层(入度 0 在第 0 层);层内用若干轮 barycenter 扫描排序。
 */
export function layoutDag(nodeIds: string[], edges: Edge[]): DagLayout {
  const idSet = new Set(nodeIds)
  const inEdges = new Map<string, string[]>()
  const outEdges = new Map<string, string[]>()
  for (const id of nodeIds) {
    inEdges.set(id, [])
    outEdges.set(id, [])
  }
  for (const e of edges) {
    if (!idSet.has(e.from) || !idSet.has(e.to)) continue
    outEdges.get(e.from)?.push(e.to)
    inEdges.get(e.to)?.push(e.from)
  }

  // 1. 最长路径分层(Kahn 拓扑 + 递推)
  const layer = new Map<string, number>()
  const indeg = new Map<string, number>()
  for (const id of nodeIds) indeg.set(id, inEdges.get(id)?.length ?? 0)
  const queue = nodeIds.filter((id) => (indeg.get(id) ?? 0) === 0)
  for (const id of queue) layer.set(id, 0)
  while (queue.length > 0) {
    const cur = queue.shift() as string
    const curLayer = layer.get(cur) ?? 0
    for (const next of outEdges.get(cur) ?? []) {
      if ((layer.get(next) ?? 0) < curLayer + 1) layer.set(next, curLayer + 1)
      const d = (indeg.get(next) ?? 0) - 1
      indeg.set(next, d)
      if (d === 0) queue.push(next)
    }
  }
  // 防御:环(不会出现)中的节点放到第 0 层
  for (const id of nodeIds) if (!layer.has(id)) layer.set(id, 0)

  // 2. 按层分组,初始按 id 排序(确定性)
  const layerCount = Math.max(0, ...nodeIds.map((id) => (layer.get(id) ?? 0) + 1))
  const layers: string[][] = Array.from({ length: layerCount }, () => [])
  for (const id of nodeIds) layers[layer.get(id) ?? 0].push(id)
  const num = (id: string) => Number(id.replace(/^\D+/, '')) || 0
  for (const l of layers) l.sort((a, b) => num(a) - num(b))

  // 3. barycenter 扫描:先自上而下(按上游平均位置),再自下而上,往返数轮
  const posInLayer = new Map<string, number>()
  const reindex = () => {
    layers.forEach((l, li) => l.forEach((id, i) => posInLayer.set(id, i + li * 0)))
  }
  reindex()
  const bary = (id: string, neighbors: Map<string, string[]>): number => {
    const ns = neighbors.get(id) ?? []
    if (ns.length === 0) return -1
    let sum = 0
    for (const n of ns) sum += posInLayer.get(n) ?? 0
    return sum / ns.length
  }
  const stableSortByBary = (l: string[], neighbors: Map<string, string[]>) => {
    const decorated = l.map((id, i) => ({ id, i, b: bary(id, neighbors) }))
    decorated.sort((x, y) => {
      const bx = x.b < 0 ? x.i : x.b
      const by = y.b < 0 ? y.i : y.b
      return bx === by ? x.i - y.i : bx - by
    })
    return decorated.map((d) => d.id)
  }
  for (let sweep = 0; sweep < 6; sweep++) {
    if (sweep % 2 === 0) {
      for (let li = 1; li < layerCount; li++) layers[li] = stableSortByBary(layers[li], inEdges)
    } else {
      for (let li = layerCount - 2; li >= 0; li--) {
        layers[li] = stableSortByBary(layers[li], outEdges)
      }
    }
    reindex()
  }

  // 4. 坐标:层内水平排开,每层相对最宽层水平居中
  const maxNodes = Math.max(1, ...layers.map((l) => l.length))
  const fullW = maxNodes * (NODE_W + GAP_X) - GAP_X
  const positions = new Map<string, LayoutPos>()
  layers.forEach((l, li) => {
    const w = l.length * (NODE_W + GAP_X) - GAP_X
    const offset = (fullW - w) / 2
    l.forEach((id, i) => {
      positions.set(id, {
        x: offset + i * (NODE_W + GAP_X),
        y: li * (NODE_H + GAP_Y),
        layer: li,
      })
    })
  })

  return {
    positions,
    width: fullW + NODE_W * 0 + 40,
    height: layerCount * (NODE_H + GAP_Y) - GAP_Y + 40,
    layerCount,
  }
}
