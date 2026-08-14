'use strict';
/* task 看板客户端：零依赖 vanilla JS。模型由服务端注入 window.__BOARD__。 */

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

var NODE_W = 168, NODE_H = 50, GAP_X = 40, GAP_Y = 64;
var PAGE_SIZE = 50;
var THEME_KEY = 'task-board-theme';

// 类别元数据：浅/深两套配色（与 demo 的 stone 低饱和语义一致）
var CAT = {
  active:            { label: '运行中', l: { fill: '#FEF3C7', stroke: '#F59E0B', text: '#92400E' }, d: { fill: '#3D2E0A', stroke: '#D97706', text: '#FBBF24' } },
  runnable:          { label: '可运行', l: { fill: '#D1FAE5', stroke: '#10B981', text: '#065F46' }, d: { fill: '#06392B', stroke: '#10B981', text: '#6EE7B7' } },
  blocked_deps:      { label: '依赖阻塞', l: { fill: '#FFEDD5', stroke: '#F97316', text: '#9A3412' }, d: { fill: '#3E2410', stroke: '#F97316', text: '#FDBA74' } },
  blocked_conflict:  { label: '冲突阻塞', l: { fill: '#FEE2E2', stroke: '#EF4444', text: '#991B1B' }, d: { fill: '#3B1212', stroke: '#EF4444', text: '#FCA5A5' } },
  backlog:           { label: '待规划', l: { fill: '#E7E5E4', stroke: '#A8A29E', text: '#44403C' }, d: { fill: '#292524', stroke: '#78716C', text: '#D6D3D1' } },
  done:              { label: '已完成', l: { fill: '#F5F5F4', stroke: '#D6D3D1', text: '#78716C' }, d: { fill: '#1C1917', stroke: '#44403C', text: '#78716C' } },
  dropped:           { label: '已放弃', l: { fill: '#FAFAF9', stroke: '#E7E5E4', text: '#A8A29E' }, d: { fill: '#1C1917', stroke: '#292524', text: '#57534E' } },
};
var RING = { self: null, upstream: '#F59E0B', downstream: '#10B981', conflict: '#EF4444' };

var model = window.__BOARD__;
var nodes = new Map(model.nodes.map(function (n) { return [n.id, n]; }));
var index = null; // { dependenciesOf, dependentsOf, conflictsOf }
var plan = null; // 服务端 model.plan（repo_task.plan）

var state = {
  query: '',
  selectedId: null,
  hoveredId: null,
  highlightChainId: null,
  showCompleted: false,
  showConflicts: true,
  dark: (function () {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) { /* ignore */ }
    if (saved) return saved === 'dark';
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  })(),
  archiveTab: 'done',
  archivePage: 0,
  archiveOpen: false,
};

var view = { k: 1, x: 20, y: 20 };
var layout = null;
var canvasEl, dragState = null;

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

function $(id) { return document.getElementById(id); }

function el(tag, attrs, children) {
  var node = document.createElement(tag);
  if (attrs) {
    for (var k in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) node.setAttribute(k, attrs[k]);
    }
  }
  (children || []).forEach(function (c) {
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  });
  return node;
}

function truncate(s, max) {
  return s.length <= max ? s : s.slice(0, max - 1) + '…';
}

function isUnfinished(category) {
  if (window.ChainPlan) return window.ChainPlan.isUnfinished(category);
  return category !== 'done' && category !== 'dropped';
}

function recomputePlan() {
  // 算法权威在后端 repo_task.plan；注入于 model.plan。刷新页面以重算。
  plan = model.plan || { chains: [], unassigned: [], deferred: [], crossings: [] };
  state.highlightChainId = null;
}

function chainOfTask(id) {
  if (!plan || !window.ChainPlan) return null;
  return window.ChainPlan.chainOfMap(plan.chains).get(id) || null;
}

function chainById(cid) {
  if (!plan) return null;
  for (var i = 0; i < plan.chains.length; i++) {
    if (plan.chains[i].id === cid) return plan.chains[i];
  }
  return null;
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(function () { fallbackCopy(text); });
    return;
  }
  fallbackCopy(text);
}

function fallbackCopy(text) {
  var ta = document.createElement('textarea');
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (e) { /* ignore */ }
  document.body.removeChild(ta);
}

function showToast(msg) {
  var t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(function () { t.hidden = true; }, 1600);
}

function catStyle(cat) {
  var meta = CAT[cat] || CAT.backlog;
  return state.dark ? meta.d : meta.l;
}

// ---------------------------------------------------------------------------
// 关系索引与高亮（boardUtils.collectRelated / highlightRoleOf 的翻译）
// ---------------------------------------------------------------------------

function buildIndex() {
  var dependenciesOf = new Map(), dependentsOf = new Map(), conflictsOf = new Map();
  model.nodes.forEach(function (n) {
    dependenciesOf.set(n.id, n.depends_on || []);
    dependentsOf.set(n.id, []);
    conflictsOf.set(n.id, n.conflicts_with || []);
  });
  model.nodes.forEach(function (n) {
    (n.depends_on || []).forEach(function (dep) {
      if (dependentsOf.has(dep)) dependentsOf.get(dep).push(n.id);
    });
    (n.conflicts_with || []).forEach(function (c) {
      if (conflictsOf.has(c)) conflictsOf.get(c).push(n.id);
    });
  });
  index = { dependenciesOf: dependenciesOf, dependentsOf: dependentsOf, conflictsOf: conflictsOf };
}

function collectRelated(id) {
  return {
    upstream: index.dependenciesOf.get(id) || [],
    downstream: index.dependentsOf.get(id) || [],
    conflict: index.conflictsOf.get(id) || [],
  };
}

function relatedSets() {
  return state.hoveredId && index ? collectRelated(state.hoveredId) : null;
}

function roleOf(id, related) {
  if (!related) return null;
  if (id === state.hoveredId) return 'self';
  if (related.upstream.indexOf(id) >= 0) return 'upstream';
  if (related.downstream.indexOf(id) >= 0) return 'downstream';
  if (related.conflict.indexOf(id) >= 0) return 'conflict';
  return null;
}

function matchedIds() {
  var q = state.query.trim().toLowerCase();
  if (!q) return null;
  var set = new Set();
  model.nodes.forEach(function (n) {
    if (n.id.toLowerCase().indexOf(q) >= 0 || (n.title || '').toLowerCase().indexOf(q) >= 0) {
      set.add(n.id);
    }
  });
  return set;
}

// ---------------------------------------------------------------------------
// 分层布局（dagLayout.ts 的翻译：最长路径分层 + barycenter 层内排序）
// ---------------------------------------------------------------------------

function layoutDag(nodeIds, edges) {
  var idSet = new Set(nodeIds);
  var inEdges = new Map(), outEdges = new Map();
  nodeIds.forEach(function (id) { inEdges.set(id, []); outEdges.set(id, []); });
  edges.forEach(function (e) {
    if (!idSet.has(e.from) || !idSet.has(e.to)) return;
    outEdges.get(e.from).push(e.to);
    inEdges.get(e.to).push(e.from);
  });

  // 1. 最长路径分层（Kahn 拓扑 + 递推）
  var layer = new Map(), indeg = new Map();
  nodeIds.forEach(function (id) { indeg.set(id, inEdges.get(id).length); });
  var queue = nodeIds.filter(function (id) { return indeg.get(id) === 0; });
  queue.forEach(function (id) { layer.set(id, 0); });
  while (queue.length > 0) {
    var cur = queue.shift();
    var curLayer = layer.get(cur) || 0;
    (outEdges.get(cur) || []).forEach(function (next) {
      if ((layer.get(next) || 0) < curLayer + 1) layer.set(next, curLayer + 1);
      indeg.set(next, indeg.get(next) - 1);
      if (indeg.get(next) === 0) queue.push(next);
    });
  }
  nodeIds.forEach(function (id) { if (!layer.has(id)) layer.set(id, 0); });

  // 2. 按层分组，层内按 id 数值排序（确定性）
  var layerCount = 0;
  nodeIds.forEach(function (id) { layerCount = Math.max(layerCount, layer.get(id) + 1); });
  var layers = [];
  for (var i = 0; i < layerCount; i++) layers.push([]);
  nodeIds.forEach(function (id) { layers[layer.get(id)].push(id); });
  var num = function (id) { return Number(id.replace(/^\D+/, '')) || 0; };
  layers.forEach(function (l) { l.sort(function (a, b) { return num(a) - num(b); }); });

  // 3. barycenter 扫描：自上而下 / 自下而上交替 6 轮
  var posInLayer = new Map();
  var reindex = function () {
    layers.forEach(function (l) { l.forEach(function (id) { posInLayer.set(id, l.indexOf(id)); }); });
  };
  reindex();
  var bary = function (id, neighbors) {
    var ns = neighbors.get(id) || [];
    if (ns.length === 0) return -1;
    var sum = 0;
    ns.forEach(function (n) { sum += posInLayer.get(n) || 0; });
    return sum / ns.length;
  };
  var stableSortByBary = function (l, neighbors) {
    var decorated = l.map(function (id) { return { id: id, i: l.indexOf(id), b: bary(id, neighbors) }; });
    decorated.sort(function (x, y) {
      var bx = x.b < 0 ? x.i : x.b;
      var by = y.b < 0 ? y.i : y.b;
      return bx === by ? x.i - y.i : bx - by;
    });
    return decorated.map(function (d) { return d.id; });
  };
  for (var sweep = 0; sweep < 6; sweep++) {
    if (sweep % 2 === 0) {
      for (var li = 1; li < layerCount; li++) layers[li] = stableSortByBary(layers[li], inEdges);
    } else {
      for (var li = layerCount - 2; li >= 0; li--) layers[li] = stableSortByBary(layers[li], outEdges);
    }
    reindex();
  }

  // 4. 坐标：层内水平排开，每层相对最宽层居中
  var maxNodes = 1;
  layers.forEach(function (l) { maxNodes = Math.max(maxNodes, l.length); });
  var fullW = maxNodes * (NODE_W + GAP_X) - GAP_X;
  var positions = new Map();
  layers.forEach(function (l, li) {
    var w = l.length * (NODE_W + GAP_X) - GAP_X;
    var offset = (fullW - w) / 2;
    l.forEach(function (id, i) {
      positions.set(id, { x: offset + i * (NODE_W + GAP_X), y: li * (NODE_H + GAP_Y), layer: li });
    });
  });
  return {
    positions: positions,
    width: fullW + 40,
    height: layerCount * (NODE_H + GAP_Y) - GAP_Y + 40,
    layerCount: layerCount,
  };
}

// ---------------------------------------------------------------------------
// 渲染：顶部统计条
// ---------------------------------------------------------------------------

function renderPills() {
  var pills = $('summary-pills');
  pills.textContent = '';
  Object.keys(CAT).forEach(function (cat) {
    var count = model.summary[cat] || 0;
    var pill = el('span', { class: 'pill ' + cat });
    if (cat === 'active' && count > 0) {
      pill.appendChild(el('span', { class: 'pulse-dot' }));
    }
    pill.appendChild(el('span', null, [CAT[cat].label]));
    pill.appendChild(el('span', { class: 'count' }, [String(count)]));
    pills.appendChild(pill);
  });
  $('project-name').textContent = model.project;
  $('project-sub').textContent = '共 ' + model.nodes.length + ' 个任务';
  document.title = model.project + ' · task 看板';
}

// ---------------------------------------------------------------------------
// 渲染：DAG
// ---------------------------------------------------------------------------

function visibleNodeIds() {
  return model.nodes
    .filter(function (n) { return state.showCompleted || isUnfinished(n.category); })
    .map(function (n) { return n.id; });
}

function computeLayout() {
  var ids = visibleNodeIds();
  var idSet = new Set(ids);
  var depEdges = model.edges.filter(function (e) {
    return e.type === 'dep' && idSet.has(e.from) && idSet.has(e.to);
  });
  layout = layoutDag(ids, depEdges);
  return ids;
}

function edgePath(from, to) {
  var a = layout.positions.get(from), b = layout.positions.get(to);
  if (!a || !b) return null;
  var sx = a.x + NODE_W / 2, sy = a.y + NODE_H;
  var tx = b.x + NODE_W / 2, ty = b.y;
  if (b.layer > a.layer) {
    var my = sy + GAP_Y / 2;
    return 'M ' + sx + ' ' + sy + ' C ' + sx + ' ' + my + ', ' + tx + ' ' + my + ', ' + tx + ' ' + ty;
  }
  return 'M ' + (a.x + NODE_W) + ' ' + (a.y + NODE_H / 2) +
    ' C ' + (a.x + NODE_W + 40) + ' ' + (a.y + NODE_H / 2) +
    ', ' + (b.x + NODE_W + 40) + ' ' + (b.y + NODE_H / 2) +
    ', ' + (b.x + NODE_W) + ' ' + (b.y + NODE_H / 2);
}

var NS = 'http://www.w3.org/2000/svg';

function svgEl(name, attrs, children) {
  var node = document.createElementNS(NS, name);
  if (attrs) {
    for (var k in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) node.setAttribute(k, attrs[k]);
    }
  }
  (children || []).forEach(function (c) { node.appendChild(c); });
  return node;
}

function renderDag() {
  canvasEl.innerHTML = '';
  var ids = computeLayout();
  var related = relatedSets();
  var matched = matchedIds();
  var idSet = new Set(ids);

  var svg = svgEl('svg');
  var g = svgEl('g', { transform: 'translate(' + view.x + ',' + view.y + ') scale(' + view.k + ')' });
  svg.appendChild(g);

  var dimNode = function (id) {
    if (related) return roleOf(id, related) === null;
    if (matched) return !matched.has(id);
    return false;
  };
  var edgeDimmed = function (from, to) {
    if (related) return roleOf(from, related) === null && roleOf(to, related) === null;
    return false;
  };

  // 链索引 / 交叉点 / 暂缓
  var chainOf = plan && window.ChainPlan ? window.ChainPlan.chainOfMap(plan.chains) : new Map();
  var chainByMap = new Map();
  if (plan) plan.chains.forEach(function (c) { chainByMap.set(c.id, c); });
  var chainNodeSet = null;
  if (state.highlightChainId && chainByMap.has(state.highlightChainId)) {
    chainNodeSet = new Set(chainByMap.get(state.highlightChainId).taskIds);
  }
  var crossingNodeTip = new Map();
  var crossingEdgeKeys = new Set();
  var deferredTip = new Map();
  if (plan) {
    var nameOf = function (cid) {
      return cid === 'future' ? '未来批次' : ((chainByMap.get(cid) && chainByMap.get(cid).name) || cid);
    };
    plan.crossings.forEach(function (c) {
      crossingNodeTip.set(
        c.dependsOnNodeId,
        '交叉点:' + nameOf(c.chainId) + ' 的 ' + c.nodeId + ' 在等待本任务完成'
      );
      crossingEdgeKeys.add(c.dependsOnNodeId + '|' + c.nodeId);
    });
    plan.deferred.forEach(function (d) {
      deferredTip.set(d.taskId, d.reason);
    });
  }

  var dimByChain = function (id) {
    if (!chainNodeSet) return false;
    return !chainNodeSet.has(id);
  };

  // 边
  model.edges.forEach(function (e) {
    if (!idSet.has(e.from) || !idSet.has(e.to)) return;
    var d = edgePath(e.from, e.to);
    if (!d) return;
    if (e.type === 'conflict' && !state.showConflicts) return;
    var path = svgEl('path', { d: d, fill: 'none' });
    var isCrossing = e.type === 'dep' && crossingEdgeKeys.has(e.from + '|' + e.to);
    var dim = edgeDimmed(e.from, e.to) || (chainNodeSet && !(chainNodeSet.has(e.from) && chainNodeSet.has(e.to)));
    if (e.type === 'dep') {
      path.setAttribute('stroke', isCrossing ? '#DC2626' : (state.dark ? '#57534E' : '#C7C2BC'));
      path.setAttribute('stroke-width', isCrossing ? '2' : '1.2');
      if (isCrossing) path.setAttribute('stroke-dasharray', '5 3');
      path.setAttribute('opacity', dim && !isCrossing ? '0.25' : '0.9');
    } else {
      path.setAttribute('stroke', '#EF4444');
      path.setAttribute('stroke-width', '1.2');
      path.setAttribute('stroke-dasharray', '4 4');
      path.setAttribute('opacity', dim ? '0.3' : '0.75');
    }
    g.appendChild(path);
  });

  // 节点
  ids.forEach(function (id) {
    var n = nodes.get(id);
    var pos = layout.positions.get(id);
    if (!n || !pos) return;
    var cat = catStyle(n.category);
    var role = roleOf(id, related);
    var cid = chainOf.get(id);
    var chain = cid ? chainByMap.get(cid) : null;
    var archived = !isUnfinished(n.category);
    var chainColor = (!archived && chain) ? chain.color : null;
    var letter = (!archived && chain) ? window.ChainPlan.chainLetter(chain.name) : null;
    var stroke = (role && RING[role]) || chainColor || (deferredTip.has(id) ? '#DC2626' : cat.stroke);
    var strokeW = (role || state.selectedId === id || chainColor) ? 2.4 : 1.4;
    var tip = crossingNodeTip.get(id) || deferredTip.get(id) || (id + ' · ' + n.title);
    var nodeG = svgEl('g', { transform: 'translate(' + pos.x + ',' + pos.y + ')' });
    if (dimNode(id) || dimByChain(id)) nodeG.setAttribute('opacity', '0.22');
    nodeG.style.cursor = 'pointer';
    nodeG.addEventListener('click', function (ev) {
      ev.stopPropagation();
      openSheet(id);
    });
    nodeG.addEventListener('mouseenter', function () {
      state.hoveredId = id;
      renderDag();
      renderArchive();
      renderChainPanel();
      updateHoverLegend();
    });
    nodeG.addEventListener('mouseleave', function () {
      state.hoveredId = null;
      renderDag();
      renderArchive();
      renderChainPanel();
      updateHoverLegend();
    });
    nodeG.appendChild(svgEl('title', null, [document.createTextNode(tip)]));

    // 交叉点脉冲
    if (crossingNodeTip.has(id)) {
      var pulse = svgEl('circle', {
        cx: NODE_W - 4, cy: 4, r: 9, fill: 'none', stroke: '#DC2626', 'stroke-width': 1.6,
      });
      var animR = svgEl('animate', { attributeName: 'r', values: '7;13', dur: '1.4s', repeatCount: 'indefinite' });
      var animO = svgEl('animate', { attributeName: 'opacity', values: '0.9;0', dur: '1.4s', repeatCount: 'indefinite' });
      pulse.appendChild(animR);
      pulse.appendChild(animO);
      nodeG.appendChild(pulse);
      nodeG.appendChild(svgEl('circle', {
        cx: NODE_W - 4, cy: 4, r: 5, fill: '#DC2626',
      }));
    }

    nodeG.appendChild(svgEl('rect', {
      width: NODE_W, height: NODE_H, rx: 8,
      fill: cat.fill, stroke: stroke, 'stroke-width': strokeW,
    }));
    if (state.selectedId === id) {
      nodeG.appendChild(svgEl('rect', {
        x: -3, y: -3, width: NODE_W + 6, height: NODE_H + 6, rx: 10,
        fill: 'none', stroke: state.dark ? '#E7E5E4' : '#292524', 'stroke-width': 1.6,
      }));
    }
    var idX = 10;
    if (letter && chainColor) {
      nodeG.appendChild(svgEl('rect', {
        x: 4, y: 4, width: 16, height: 14, rx: 3, fill: chainColor,
      }));
      var letterText = svgEl('text', {
        x: 12, y: 14, 'font-size': 9, 'text-anchor': 'middle', fill: '#fff',
      });
      letterText.setAttribute('font-family', 'sans-serif');
      letterText.setAttribute('font-weight', '700');
      letterText.textContent = letter;
      nodeG.appendChild(letterText);
      idX = 26;
    }
    var idText = svgEl('text', {
      x: idX, y: 17, 'font-size': 10, fill: state.dark ? '#A8A29E' : '#78716C',
    });
    idText.setAttribute('font-family', 'monospace');
    idText.textContent = id;
    nodeG.appendChild(idText);
    var titleText = svgEl('text', { x: 10, y: 37, 'font-size': 11.5, fill: cat.text });
    titleText.textContent = truncate(n.title, letter ? 11 : 13);
    nodeG.appendChild(titleText);
    g.appendChild(nodeG);
  });

  canvasEl.appendChild(svg);
  renderLegend();
  updateDagSub(ids.length);
}

function renderLegend() {
  var legend = el('div', { class: 'graph-legend' });
  Object.keys(CAT).forEach(function (cat) {
    var c = catStyle(cat);
    legend.appendChild(el('span', null, [
      el('span', { class: 'legend-swatch', style: 'background:' + c.fill + ';border-color:' + c.stroke }),
      document.createTextNode(CAT[cat].label),
    ]));
  });
  canvasEl.appendChild(legend);
}

function updateDagSub(count) {
  $('dag-sub').textContent = state.showCompleted
    ? count + ' 节点(含归档)'
    : count + ' 个待处理任务 · 归档任务已隐藏';
}

// ---------------------------------------------------------------------------
// 渲染：推荐链面板
// ---------------------------------------------------------------------------

function renderChainPanel() {
  var list = $('chain-list');
  if (!list || !plan) return;

  var activeCount = 0;
  plan.chains.forEach(function (c) {
    var head = nodes.get(c.taskIds[0]);
    if (head && head.category === 'active') activeCount += 1;
  });
  $('chain-sub').textContent =
    plan.chains.length + ' 条链' +
    (activeCount ? ' · ' + activeCount + ' 运行中' : '') +
    (plan.deferred.length ? ' · 暂缓 ' + plan.deferred.length : '') +
    (plan.unassigned.length ? ' · 未来 ' + plan.unassigned.length : '');

  list.textContent = '';
  if (plan.chains.length === 0) {
    list.appendChild(el('div', { class: 'chain-empty' }, [
      '当前无可并行推荐链（无可运行/冲突阻塞的链首）',
    ]));
  } else {
    plan.chains.forEach(function (chain) {
      list.appendChild(buildChainCard(chain));
    });
  }

  // 暂缓
  var dWrap = $('chain-deferred-wrap');
  var dBox = $('chain-deferred');
  if (plan.deferred.length === 0) {
    dWrap.hidden = true;
    dBox.textContent = '';
  } else {
    dWrap.hidden = false;
    dBox.textContent = '';
    plan.deferred.forEach(function (d) {
      var n = nodes.get(d.taskId);
      var row = el('button', { type: 'button', class: 'chain-deferred-item' });
      row.appendChild(el('span', { class: 'mono' }, [d.taskId]));
      row.appendChild(el('span', { class: 'title' }, [n ? n.title : '']));
      row.appendChild(el('span', { class: 'reason' }, [d.reason]));
      row.addEventListener('click', function () { openSheet(d.taskId); });
      dBox.appendChild(row);
    });
  }

  // 未来批次（折叠展示前 20 个）
  var uWrap = $('chain-unassigned-wrap');
  var uBox = $('chain-unassigned');
  if (plan.unassigned.length === 0) {
    uWrap.hidden = true;
    uBox.textContent = '';
  } else {
    uWrap.hidden = false;
    uBox.textContent = '';
    plan.unassigned.slice(0, 20).forEach(function (tid) {
      var n = nodes.get(tid);
      var row = el('button', { type: 'button', class: 'chain-unassigned-item' });
      row.appendChild(el('span', { class: 'mono' }, [tid]));
      row.appendChild(el('span', { class: 'title' }, [n ? n.title : '']));
      if (n) {
        row.appendChild(el('span', {
          class: 'category-badge',
          style: badgeStyle(n.category),
        }, [CAT[n.category] ? CAT[n.category].label : n.category]));
      }
      row.addEventListener('click', function () { openSheet(tid); });
      uBox.appendChild(row);
    });
    if (plan.unassigned.length > 20) {
      uBox.appendChild(el('div', { class: 'chain-empty' }, [
        '…另有 ' + (plan.unassigned.length - 20) + ' 个未列出',
      ]));
    }
  }
}

function buildChainCard(chain) {
  var headNode = nodes.get(chain.taskIds[0]);
  var running = headNode && headNode.category === 'active';
  var highlighted = state.highlightChainId === chain.id;

  // 本链作为交叉点被谁等待
  var waitedBy = new Map();
  var nameOf = new Map();
  plan.chains.forEach(function (c) { nameOf.set(c.id, c.name); });
  plan.crossings.forEach(function (c) {
    if (c.dependsOnChainId !== chain.id) return;
    var who = c.chainId === 'future'
      ? c.nodeId + '(未来批次)'
      : (nameOf.get(c.chainId) || c.chainId) + ' 的 ' + c.nodeId;
    if (!waitedBy.has(c.dependsOnNodeId)) waitedBy.set(c.dependsOnNodeId, []);
    waitedBy.get(c.dependsOnNodeId).push(who + ' 在等它');
  });

  var card = el('article', {
    class: 'chain-card' + (highlighted ? ' highlighted' : ''),
  });

  var head = el('header', { class: 'chain-card-head' });
  head.appendChild(el('span', {
    class: 'chain-swatch',
    style: 'background:' + chain.color,
  }));
  head.appendChild(el('h3', null, [chain.name]));
  head.appendChild(el('span', {
    class: 'chain-badge ' + (running ? 'running' : 'recommended'),
  }, [running ? '运行中' : '新推荐']));
  head.appendChild(el('span', { class: 'chain-badge muted' }, [
    chain.taskIds.length + ' 任务',
  ]));
  if (waitedBy.size > 0) {
    head.appendChild(el('span', { class: 'chain-badge cross' }, [
      waitedBy.size + ' 个交叉点',
    ]));
  }

  var actions = el('span', { class: 'chain-card-actions' });
  var copyBtn = el('button', {
    type: 'button',
    class: 'icon-btn',
    title: '复制本链 /task-run 命令（与 plan --copy 一致）',
  }, ['⎘']);
  copyBtn.addEventListener('click', function (ev) {
    ev.stopPropagation();
    // 与 task.py plan --copy 对齐：可粘贴 /task-run
    var line = '/task-run ' + chain.taskIds.join(' -> ');
    if (chain.head_kind === 'continue') line += '  # 接续 active';
    copyText(line);
    showToast('已复制 ' + chain.name);
  });
  actions.appendChild(copyBtn);
  head.appendChild(actions);

  head.addEventListener('click', function () {
    state.highlightChainId = highlighted ? null : chain.id;
    renderDag();
    renderChainPanel();
  });
  card.appendChild(head);

  var tasks = el('div', { class: 'chain-tasks' });
  // 当前任务：链内第一个非 done 的（生产侧多为 active/runnable）
  var currentId = null;
  for (var i = 0; i < chain.taskIds.length; i++) {
    var tn = nodes.get(chain.taskIds[i]);
    if (tn && isUnfinished(tn.category)) {
      currentId = tn.id;
      break;
    }
  }
  chain.taskIds.forEach(function (tid, idx) {
    var n = nodes.get(tid);
    if (!n) return;
    var row = el('button', { type: 'button', class: 'chain-task' });
    if (state.selectedId === tid) row.classList.add('selected');
    row.appendChild(el('span', { class: 'idx' }, [String(idx + 1)]));
    var cat = catStyle(n.category);
    row.appendChild(el('span', {
      class: 'dot',
      style: 'background:' + cat.stroke,
    }));
    row.appendChild(el('span', { class: 'tid' }, [tid]));
    row.appendChild(el('span', { class: 'title' }, [n.title]));
    if (currentId === tid) {
      row.appendChild(el('span', {
        class: 'tag',
        style: 'background:' + chain.color,
      }, ['当前']));
    }
    var wait = waitedBy.get(tid);
    if (wait && wait.length) {
      var cross = el('span', { class: 'tag cross', title: wait.join('\n') }, [
        '交叉×' + wait.length,
      ]);
      row.appendChild(cross);
    }
    row.addEventListener('click', function () { openSheet(tid); });
    tasks.appendChild(row);
  });
  card.appendChild(tasks);

  var stop = chain.stop_reason
    || (window.ChainPlan ? window.ChainPlan.chainStopInfo(chain, plan, model) : '');
  if (stop) {
    card.appendChild(el('footer', { class: 'chain-card-foot' }, [stop]));
  }
  return card;
}

// ---------------------------------------------------------------------------
// 渲染：归档区
// ---------------------------------------------------------------------------

function archivedGroups() {
  var q = state.query.trim().toLowerCase();
  var done = [], dropped = [];
  model.nodes.forEach(function (n) {
    if (isUnfinished(n.category)) return;
    if (q) {
      var hit = n.id.toLowerCase().indexOf(q) >= 0 || (n.title || '').toLowerCase().indexOf(q) >= 0;
      if (!hit) return;
    }
    (n.category === 'done' ? done : dropped).push(n);
  });
  return { done: done, dropped: dropped };
}

function renderArchive() {
  var groups = archivedGroups();
  var list = state.archiveTab === 'done' ? groups.done : groups.dropped;
  var pageCount = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
  if (state.archivePage >= pageCount) state.archivePage = pageCount - 1;
  var page = state.archivePage;
  var items = list.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  var related = relatedSets();
  var q = state.query.trim();

  $('archive-done-count').textContent = '已完成 ' + groups.done.length;
  $('archive-dropped-count').textContent = '已放弃 ' + groups.dropped.length;
  $('tab-done-count').textContent = String(groups.done.length);
  $('tab-dropped-count').textContent = String(groups.dropped.length);

  // 搜索激活时强制展开；相关任务落在归档区时显示徽标
  var forceOpen = q !== '';
  var open = state.archiveOpen || forceOpen;
  $('archive').classList.toggle('open', open);
  $('archive-body').hidden = !open;
  $('archive-trigger').setAttribute('aria-expanded', open ? 'true' : 'false');
  $('archive-filter-hint').hidden = q === '';

  var relatedCount = 0;
  if (related) {
    groups.done.concat(groups.dropped).forEach(function (n) {
      if (roleOf(n.id, related)) relatedCount += 1;
    });
  }
  var badge = $('archive-related');
  if (relatedCount > 0 && !open) {
    badge.textContent = relatedCount + ' 个相关任务在归档中';
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }

  $('archive-pager').hidden = pageCount <= 1;
  if (pageCount > 1) {
    $('page-info').textContent = (page + 1) + ' / ' + pageCount;
    $('page-prev').disabled = page === 0;
    $('page-next').disabled = page >= pageCount - 1;
  }

  var grid = $('archive-grid');
  grid.textContent = '';
  if (items.length === 0) {
    grid.appendChild(el('div', { class: 'archive-empty' }, ['当前条件下无归档任务']));
    return;
  }
  items.forEach(function (n) {
    var card = el('button', { type: 'button', class: 'task-card' });
    if (related) {
      var role = roleOf(n.id, related);
      if (role === null) card.classList.add('dimmed');
      else if (role === 'upstream') card.classList.add('tone-amber');
      else if (role === 'downstream') card.classList.add('tone-emerald');
      else if (role === 'conflict') card.classList.add('tone-red');
    }
    if (state.selectedId === n.id) card.classList.add('selected');
    card.appendChild(el('span', { class: 'mono' }, [n.id]));
    card.appendChild(el('span', { class: 'title' }, [n.title]));
    card.appendChild(el('span', { class: 'category-badge', style: badgeStyle(n.category) }, [CAT[n.category].label]));
    card.addEventListener('click', function () { openSheet(n.id); });
    card.addEventListener('mouseenter', function () { state.hoveredId = n.id; renderDag(); renderArchive(); updateHoverLegend(); });
    card.addEventListener('mouseleave', function () { state.hoveredId = null; renderDag(); renderArchive(); updateHoverLegend(); });
    grid.appendChild(card);
  });
}

function badgeStyle(category) {
  var c = catStyle(category);
  return 'background:' + c.fill + ';border:1px solid ' + c.stroke + ';color:' + c.text;
}

// ---------------------------------------------------------------------------
// 渲染：详情侧栏
// ---------------------------------------------------------------------------

function openSheet(id) {
  state.selectedId = id;
  var cid = chainOfTask(id);
  if (cid) state.highlightChainId = cid;
  $('sheet').classList.add('open');
  $('sheet-backdrop').hidden = false;
  setSheetTab('detail');
  renderSheetDetail();
  renderDag();
  renderArchive();
  renderChainPanel();
}

function closeSheet() {
  state.selectedId = null;
  $('sheet').classList.remove('open');
  $('sheet-backdrop').hidden = true;
  renderDag();
  renderArchive();
  renderChainPanel();
}

var sheetDocCache = {};

function setSheetTab(tab) {
  var tabs = document.querySelectorAll('.tab-item');
  for (var i = 0; i < tabs.length; i++) {
    tabs[i].classList.toggle('active', tabs[i].getAttribute('data-doc') === tab);
  }
  if (tab === 'detail') {
    renderSheetDetail();
  } else {
    renderSheetDoc(tab);
  }
}

function renderSheetDetail() {
  var n = nodes.get(state.selectedId);
  var body = $('sheet-body');
  if (!n) return;
  $('sheet-id').textContent = n.id;
  var badge = $('sheet-badge');
  badge.textContent = CAT[n.category].label;
  badge.setAttribute('style', badgeStyle(n.category));
  $('sheet-title').textContent = n.title;
  $('sheet-meta').textContent = '';
  $('sheet-meta').appendChild(el('span', null, ['状态机状态:']));
  $('sheet-meta').appendChild(el('code', null, [n.status]));
  $('sheet-meta').appendChild(el('span', null, ['· 分类:']));
  $('sheet-meta').appendChild(el('code', null, [n.category]));

  body.textContent = '';
  var related = collectRelated(n.id);
  body.appendChild(relationGroup('依赖(阻塞它的上游)', related.upstream, 'tone-amber', '没有依赖,随时可以开始'));
  body.appendChild(relationGroup('被依赖(它阻塞的下游)', related.downstream, 'tone-emerald', '没有任务依赖它'));
  body.appendChild(relationGroup('冲突任务(互斥)', related.conflict, 'tone-red', '没有冲突任务'));
  body.appendChild(el('p', { class: 'sheet-hint' }, ['点击关系条目可跳转到对应任务']));
}

function relationGroup(title, ids, tone, emptyText) {
  var group = el('div', { class: 'relation-group' });
  var h = el('h3', null, [document.createTextNode(title)]);
  h.appendChild(el('span', { class: 'n' }, ['(' + ids.length + ')']));
  group.appendChild(h);
  if (ids.length === 0) {
    group.appendChild(el('div', { class: 'empty' }, [emptyText]));
    return group;
  }
  var list = el('div', { class: 'relation-list' });
  ids.forEach(function (id) {
    var target = nodes.get(id);
    if (!target) return;
    var chip = el('button', { type: 'button', class: 'relation-chip ' + tone });
    chip.appendChild(el('span', { class: 'mono' }, [id]));
    chip.appendChild(el('span', { class: 'title' }, [target.title]));
    chip.appendChild(el('span', { class: 'category-badge', style: badgeStyle(target.category) }, [CAT[target.category].label]));
    chip.addEventListener('click', function () { openSheet(id); });
    list.appendChild(chip);
  });
  group.appendChild(list);
  return group;
}

function renderSheetDoc(doc) {
  var n = nodes.get(state.selectedId);
  var body = $('sheet-body');
  if (!n) return;
  body.textContent = '';
  var cacheKey = n.id + '/' + doc;
  if (sheetDocCache[cacheKey]) {
    body.appendChild(el('pre', null, [sheetDocCache[cacheKey]]));
    return;
  }
  body.appendChild(el('p', { class: 'doc-loading' }, ['加载 ' + doc + '.md …']));
  fetch('/task-doc?tid=' + encodeURIComponent(n.id) + '&doc=' + encodeURIComponent(doc))
    .then(function (resp) {
      if (!resp.ok) return resp.text().then(function (t) { throw new Error(t); });
      return resp.text();
    })
    .then(function (text) {
      sheetDocCache[cacheKey] = text;
      body.textContent = '';
      body.appendChild(el('pre', null, [text]));
    })
    .catch(function (err) {
      body.textContent = '';
      body.appendChild(el('p', { class: 'doc-error' }, ['加载失败:' + err.message]));
    });
}

// ---------------------------------------------------------------------------
// 交互：缩放 / 平移 / 主题 / 搜索 / 归档 / 刷新
// ---------------------------------------------------------------------------

function zoom(factor, cx, cy) {
  var rect = canvasEl.getBoundingClientRect();
  var px = cx != null ? cx : rect.width / 2;
  var py = cy != null ? cy : rect.height / 2;
  var k = Math.min(3, Math.max(0.15, view.k * factor));
  var scale = k / view.k;
  view = { k: k, x: px - (px - view.x) * scale, y: py - (py - view.y) * scale };
  renderDag();
}

function fitView() {
  var rect = canvasEl.getBoundingClientRect();
  if (!rect.width || !layout) return;
  var k = Math.min(1.2, Math.max(0.15, Math.min((rect.width - 40) / layout.width, (rect.height - 40) / layout.height)));
  view = { k: k, x: 20, y: 20 };
  renderDag();
}

function initTheme() {
  document.documentElement.classList.toggle('dark', state.dark);
}

function toggleTheme() {
  state.dark = !state.dark;
  document.documentElement.classList.toggle('dark', state.dark);
  try { localStorage.setItem(THEME_KEY, state.dark ? 'dark' : 'light'); } catch (e) { /* ignore */ }
  renderPills();
  renderDag();
  renderArchive();
  renderChainPanel();
  if (state.selectedId) renderSheetDetail();
}

function updateHoverLegend() {
  var legend = $('hover-legend');
  if (state.hoveredId) {
    legend.hidden = false;
  } else {
    legend.hidden = true;
  }
}

function renderFooter() {
  var dep = 0, conflict = 0;
  model.edges.forEach(function (e) {
    if (e.type === 'dep') dep += 1; else conflict += 1;
  });
  var footer = $('footer');
  footer.textContent = '';
  footer.appendChild(el('span', null, [
    '关系图:' + model.nodes.length + ' 节点 · ' + dep + ' 条依赖边 · ' + conflict + ' 条冲突边' +
    (plan ? ' · 推荐 ' + plan.chains.length + ' 条链' : ''),
  ]));
  var right = el('span', { class: 'right' }, [
    '只读看板;链推荐后端计算;刷新或「重新计算」更新',
  ]);
  footer.appendChild(right);
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

function bindEvents() {
  $('reload-btn').addEventListener('click', function () { location.reload(); });
  $('theme-toggle').addEventListener('click', toggleTheme);

  $('chain-recalc').addEventListener('click', function () {
    // 状态可能已在仓库侧变化；整页重载以拉取后端最新 plan
    location.reload();
  });
  $('chain-copy-all').addEventListener('click', function () {
    if (!plan || !plan.chains.length) {
      showToast('当前无推荐链');
      return;
    }
    // 与 task.py plan --copy 对齐
    copyText(plan.chains.map(function (c) {
      var line = '/task-run ' + c.taskIds.join(' -> ');
      if (c.head_kind === 'continue') line += '  # 接续 active';
      return line;
    }).join('\n'));
    showToast('已复制整批 ' + plan.chains.length + ' 条链');
  });

  var input = $('search-input');
  input.addEventListener('input', function () {
    state.query = input.value;
    state.archivePage = 0;
    $('search-clear').hidden = input.value === '';
    $('search-hint').hidden = input.value.trim() === '';
    if (input.value.trim()) {
      $('search-hint').textContent = '命中 ' + (matchedIds() ? matchedIds().size : 0) + ' 个任务(图中未命中的已弱化,归档区按条件过滤)';
    }
    renderDag();
    renderArchive();
    renderChainPanel();
  });
  $('search-clear').addEventListener('click', function () {
    input.value = '';
    state.query = '';
    state.archivePage = 0;
    $('search-clear').hidden = true;
    $('search-hint').hidden = true;
    renderDag();
    renderArchive();
    renderChainPanel();
  });

  $('toggle-conflicts').addEventListener('change', function (e) {
    state.showConflicts = e.target.checked;
    renderDag();
  });
  $('toggle-completed').addEventListener('change', function (e) {
    state.showCompleted = e.target.checked;
    state.archivePage = 0;
    renderDag();
    renderArchive();
  });

  $('zoom-in').addEventListener('click', function () { zoom(1.25); });
  $('zoom-out').addEventListener('click', function () { zoom(1 / 1.25); });
  $('zoom-fit').addEventListener('click', fitView);

  canvasEl.addEventListener('wheel', function (e) {
    e.preventDefault();
    var rect = canvasEl.getBoundingClientRect();
    zoom(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - rect.left, e.clientY - rect.top);
  }, { passive: false });

  canvasEl.addEventListener('pointerdown', function (e) {
    if (e.button !== 0) return;
    dragState = { sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y };
    canvasEl.classList.add('dragging');
    canvasEl.setPointerCapture && canvasEl.setPointerCapture(e.pointerId);
  });
  canvasEl.addEventListener('pointermove', function (e) {
    if (!dragState) return;
    view = { k: view.k, x: dragState.ox + e.clientX - dragState.sx, y: dragState.oy + e.clientY - dragState.sy };
    renderDag();
  });
  canvasEl.addEventListener('pointerup', function () { dragState = null; canvasEl.classList.remove('dragging'); });
  canvasEl.addEventListener('pointerleave', function () { dragState = null; canvasEl.classList.remove('dragging'); });
  canvasEl.addEventListener('click', function (e) {
    if (e.target === canvasEl || (e.target.tagName && e.target.tagName.toLowerCase() === 'svg')) {
      state.selectedId = null;
      state.highlightChainId = null;
      $('sheet').classList.remove('open');
      $('sheet-backdrop').hidden = true;
      renderDag();
      renderChainPanel();
    }
  });

  $('archive-trigger').addEventListener('click', function () {
    state.archiveOpen = !state.archiveOpen;
    renderArchive();
  });
  var segItems = document.querySelectorAll('.seg-item');
  for (var i = 0; i < segItems.length; i++) {
    segItems[i].addEventListener('click', function () {
      state.archiveTab = this.getAttribute('data-tab');
      state.archivePage = 0;
      for (var j = 0; j < segItems.length; j++) segItems[j].classList.toggle('active', segItems[j] === this);
      renderArchive();
    });
  }
  $('page-prev').addEventListener('click', function () { if (state.archivePage > 0) { state.archivePage -= 1; renderArchive(); } });
  $('page-next').addEventListener('click', function () { state.archivePage += 1; renderArchive(); });

  $('sheet-close').addEventListener('click', closeSheet);
  $('sheet-backdrop').addEventListener('click', closeSheet);
  var tabs = document.querySelectorAll('.tab-item');
  for (var i = 0; i < tabs.length; i++) {
    tabs[i].addEventListener('click', function () { setSheetTab(this.getAttribute('data-doc')); });
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      if (!$('sheet-backdrop').hidden) closeSheet();
      else if (state.selectedId) closeSheet();
    }
  });
}

function init() {
  canvasEl = $('dag-canvas');
  initTheme();
  buildIndex();
  recomputePlan();
  renderPills();
  renderDag();
  renderChainPanel();
  renderArchive();
  renderFooter();
  bindEvents();
  requestAnimationFrame(fitView);
}

document.addEventListener('DOMContentLoaded', init);
