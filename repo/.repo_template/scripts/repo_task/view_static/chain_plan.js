'use strict';
/* 看板展示辅助（算法权威在后端 repo_task.plan.compute_batch_plan）。
 * 生产路径使用服务端注入的 model.plan；本文件不再实现批计划算法，
 * 避免与 Python 双实现漂移。
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

  function blocksDownstream(cat) {
    return cat !== undefined && isUnfinished(cat);
  }

  /** 链停止原因（人类可读）；服务端已写入 stop_reason 时看板优先用服务端值。 */
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
    var deferredSet = new Set((plan.deferred || []).map(function (d) { return d.taskId; }));

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
    chainStopInfo: chainStopInfo,
  };

})(typeof window !== 'undefined' ? window : globalThis);
