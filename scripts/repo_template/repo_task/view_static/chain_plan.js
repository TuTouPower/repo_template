'use strict';
/* 当下可执行批次推荐（对齐 docs_repo/demos batchPlan + chainPlan）。
 * 纯前端、确定性；刷新即重算，不落库。
 */
(function (global) {

var CHAIN_COLORS = [
  '#0284C7', '#7C3AED', '#D97706', '#059669', '#DB2777',
  '#4F46E5', '#0D9488', '#EA580C', '#65A30D', '#9333EA',
];
var CHAIN_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
var UNFINISHED = {
  active: 1, runnable: 1, blocked_deps: 1, blocked_conflict: 1, backlog: 1,
};

function isUnfinished(cat) {
  return !!UNFINISHED[cat];
}

function num(id) {
  return Number(String(id).replace(/^[^\d]*/, '')) || 0;
}

function chainName(i) {
  return i < CHAIN_LETTERS.length ? '链 ' + CHAIN_LETTERS[i] : '链 ' + (i + 1);
}

function chainLetter(name) {
  return String(name).replace('链 ', '');
}

function chainOfMap(chains) {
  var m = new Map();
  chains.forEach(function (c) {
    c.taskIds.forEach(function (t) { m.set(t, c.id); });
  });
  return m;
}

function chainText(chain) {
  return chainLetter(chain.name) + ': ' + chain.taskIds.join(' ');
}

/** 交叉点：dep from→to，from 在链内、to 未完成且不在同链 */
function computeCrossings(chains, data) {
  var chainOf = chainOfMap(chains);
  var catOf = new Map();
  data.nodes.forEach(function (n) { catOf.set(n.id, n.category); });
  var crossings = [];
  data.edges.forEach(function (e) {
    if (e.type !== 'dep') return;
    var toCat = catOf.get(e.to);
    if (!toCat || !isUnfinished(toCat)) return;
    var fromChain = chainOf.get(e.from);
    if (!fromChain) return;
    var toChain = chainOf.get(e.to);
    if (toChain === fromChain) return;
    crossings.push({
      chainId: toChain || 'future',
      nodeId: e.to,
      dependsOnChainId: fromChain,
      dependsOnNodeId: e.from,
    });
  });
  crossings.sort(function (a, b) {
    if (a.dependsOnNodeId === b.dependsOnNodeId) {
      return a.nodeId < b.nodeId ? -1 : a.nodeId > b.nodeId ? 1 : 0;
    }
    return num(a.dependsOnNodeId) - num(b.dependsOnNodeId);
  });
  return crossings;
}

function buildSubgraph(data) {
  var idSet = new Set();
  var catOf = new Map();
  var unmergedSet = new Set();
  data.nodes.forEach(function (n) {
    if (isUnfinished(n.category)) {
      idSet.add(n.id);
      catOf.set(n.id, n.category);
    } else if (n.category === 'done_unmerged') {
      unmergedSet.add(n.id);
      catOf.set(n.id, n.category);
    }
  });
  var ids = Array.from(idSet).sort(function (a, b) { return num(a) - num(b); });

  var upstream = new Map();
  var downstream = new Map();
  var indegree = new Map();
  var conflictOf = new Map();
  var blockedByUnmerged = new Set();
  ids.forEach(function (id) {
    upstream.set(id, []);
    downstream.set(id, []);
    indegree.set(id, 0);
    conflictOf.set(id, []);
  });

  data.edges.forEach(function (e) {
    if (e.type === 'dep') {
      if (!idSet.has(e.to)) return;
      if (idSet.has(e.from)) {
        downstream.get(e.from).push(e.to);
        upstream.get(e.to).push(e.from);
        indegree.set(e.to, (indegree.get(e.to) || 0) + 1);
      } else if (unmergedSet.has(e.from)) {
        upstream.get(e.to).push(e.from);
        indegree.set(e.to, (indegree.get(e.to) || 0) + 1);
      }
    } else {
      if (idSet.has(e.from) && idSet.has(e.to)) {
        var fa = conflictOf.get(e.from);
        var tb = conflictOf.get(e.to);
        if (fa.indexOf(e.to) < 0) fa.push(e.to);
        if (tb.indexOf(e.from) < 0) tb.push(e.from);
      } else if (idSet.has(e.from) && unmergedSet.has(e.to)) {
        blockedByUnmerged.add(e.from);
      } else if (unmergedSet.has(e.from) && idSet.has(e.to)) {
        blockedByUnmerged.add(e.to);
      }
    }
  });
  downstream.forEach(function (list) {
    list.sort(function (a, b) { return num(a) - num(b); });
  });
  return {
    ids: ids, idSet: idSet, catOf: catOf, upstream: upstream,
    downstream: downstream, indegree: indegree, conflictOf: conflictOf,
    blockedByUnmerged: blockedByUnmerged,
  };
}

/**
 * 计算当下可执行批次。
 * 返回 { chains, unassigned, deferred, crossings }
 */
function computeBatchPlan(data) {
  var sub = buildSubgraph(data);

  // 1. 候选链首：入度 0 的 active/runnable/blocked_conflict
  var candidates = sub.ids.filter(function (id) {
    return (sub.indegree.get(id) || 0) === 0
      && !sub.blockedByUnmerged.has(id)
      && ['active', 'runnable', 'blocked_conflict'].indexOf(sub.catOf.get(id)) >= 0;
  });
  var activeHeads = candidates.filter(function (id) {
    return sub.catOf.get(id) === 'active';
  });
  var activeSet = new Set(activeHeads);
  var rest = candidates.filter(function (id) { return !activeSet.has(id); });

  // 2. 冲突裁决
  var deferredList = [];
  rest.forEach(function (id) {
    var partners = (sub.conflictOf.get(id) || []).filter(function (c) {
      return activeSet.has(c);
    });
    if (partners.length > 0) {
      deferredList.push({ taskId: id, partners: partners.slice().sort() });
    }
  });
  var deferredSet = new Set(deferredList.map(function (d) { return d.taskId; }));
  rest = rest.filter(function (id) { return !deferredSet.has(id); });

  for (;;) {
    var remaining = rest.filter(function (id) { return !deferredSet.has(id); });
    var degree = new Map();
    var maxDeg = 0;
    remaining.forEach(function (id) {
      var d = (sub.conflictOf.get(id) || []).filter(function (c) {
        return remaining.indexOf(c) >= 0;
      }).length;
      degree.set(id, d);
      if (d > maxDeg) maxDeg = d;
    });
    if (maxDeg === 0) break;
    var victims = remaining.filter(function (id) {
      return (degree.get(id) || 0) === maxDeg;
    }).sort(function (a, b) { return num(a) - num(b); });
    var victim = victims[0];
    var partners = (sub.conflictOf.get(victim) || [])
      .filter(function (c) { return remaining.indexOf(c) >= 0; })
      .sort(function (a, b) { return num(a) - num(b); });
    deferredList.push({ taskId: victim, partners: partners });
    deferredSet.add(victim);
  }

  var winners = rest.filter(function (id) { return !deferredSet.has(id); });

  // 3. 建链
  var heads = activeHeads.concat(winners).sort(function (a, b) {
    return num(a) - num(b);
  });
  var assigned = new Set();
  var chains = [];

  heads.forEach(function (head) {
    if (assigned.has(head)) return;
    var taskIds = [head];
    var chainSet = new Set([head]);
    assigned.add(head);

    for (;;) {
      var tail = taskIds[taskIds.length - 1];
      var isCrossing = (sub.downstream.get(tail) || []).some(function (y) {
        if (!sub.idSet.has(y) || chainSet.has(y)) return false;
        return (sub.upstream.get(y) || []).some(function (p) { return !chainSet.has(p); });
      });
      if (isCrossing) break;
      var cands = (sub.downstream.get(tail) || []).filter(function (id) {
        if (assigned.has(id)) return false;
        return (sub.upstream.get(id) || []).every(function (p) { return chainSet.has(p); });
      });
      if (cands.length === 0) break;
      cands.sort(function (a, b) { return num(a) - num(b); });
      var next = cands[0];
      taskIds.push(next);
      chainSet.add(next);
      assigned.add(next);
    }

    var idx = chains.length;
    chains.push({
      id: 'c' + (idx + 1),
      name: chainName(idx),
      color: CHAIN_COLORS[idx % CHAIN_COLORS.length],
      taskIds: taskIds,
    });
  });

  // 4. 未来批次 + 暂缓
  var unassigned = sub.ids.filter(function (id) {
    return !assigned.has(id) && !deferredSet.has(id);
  });
  var headSet = new Set(heads);
  var deferred = deferredList.map(function (item) {
    var blockedBy = item.partners.filter(function (p) {
      return headSet.has(p) || activeSet.has(p);
    });
    var shown = blockedBy.length > 0 ? blockedBy : item.partners;
    return {
      taskId: item.taskId,
      blockedBy: shown,
      reason: '与 ' + shown.join('、') + ' 冲突,本轮暂缓(优先让可并行的链最多)',
    };
  }).sort(function (a, b) { return num(a.taskId) - num(b.taskId); });

  return {
    chains: chains,
    unassigned: unassigned,
    deferred: deferred,
    crossings: computeCrossings(chains, data),
  };
}

function blocksDownstream(cat) {
  return cat !== undefined && (isUnfinished(cat) || cat === 'done_unmerged');
}

/** 链停止原因（展示在链卡底部） */
function chainStopInfo(chain, plan, data) {
  var tail = chain.taskIds[chain.taskIds.length - 1];
  if (!tail) return '';
  var catOf = new Map();
  data.nodes.forEach(function (n) { catOf.set(n.id, n.category); });
  var isUnfin = function (id) {
    var c = catOf.get(id);
    return c !== undefined && isUnfinished(c);
  };
  var chainSet = new Set(chain.taskIds);
  var chainOf = chainOfMap(plan.chains);
  var nameOf = new Map();
  plan.chains.forEach(function (c) { nameOf.set(c.id, c.name); });
  var deferredSet = new Set(plan.deferred.map(function (d) { return d.taskId; }));

  var upstream = new Map();
  data.edges.forEach(function (e) {
    if (e.type !== 'dep') return;
    if (!upstream.has(e.to)) upstream.set(e.to, []);
    upstream.get(e.to).push(e.from);
  });
  var successors = data.edges
    .filter(function (e) { return e.type === 'dep' && e.from === tail && isUnfin(e.to); })
    .map(function (e) { return e.to; });

  if (successors.length === 0) return '已到 DAG 末端';
  var parts = [];
  successors.forEach(function (s) {
    if (chainSet.has(s)) return;
    if (deferredSet.has(s)) {
      parts.push('后继 ' + s + ' 本轮冲突暂缓');
    } else if (chainOf.has(s)) {
      parts.push('后继 ' + s + ' 属于' + nameOf.get(chainOf.get(s)));
    } else {
      var others = (upstream.get(s) || []).filter(function (p) {
        return blocksDownstream(catOf.get(p)) && !chainSet.has(p);
      });
      if (others.length > 0) {
        var desc = others.map(function (p) {
          if (catOf.get(p) === 'done_unmerged') return p + ' 合入 main';
          if (chainOf.has(p)) return nameOf.get(chainOf.get(p)) + '的 ' + p;
          return p;
        }).join('、');
        parts.push('停在汇合点 ' + s + '(还需 ' + desc + ')');
      } else {
        parts.push(s + ' 下批可跑');
      }
    }
  });
  return parts.join(';');
}

global.ChainPlan = {
  CHAIN_COLORS: CHAIN_COLORS,
  isUnfinished: isUnfinished,
  chainName: chainName,
  chainLetter: chainLetter,
  chainOfMap: chainOfMap,
  chainText: chainText,
  computeCrossings: computeCrossings,
  computeBatchPlan: computeBatchPlan,
  chainStopInfo: chainStopInfo,
};

})(typeof window !== 'undefined' ? window : globalThis);
