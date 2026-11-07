'use strict';
/* 看板展示辅助（算法权威在后端 repo_task.plan.compute_batch_plan）。
 * 生产路径使用服务端注入的 model.plan；本文件只保留纯展示函数，
 * 不再实现批计划或链停止原因推导，避免与 Python 双实现漂移。
 */
(function (global) {

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

  global.ChainPlan = {
    isUnfinished: isUnfinished,
    chainName: chainName,
    chainLetter: chainLetter,
    chainOfMap: chainOfMap,
  };

})(typeof window !== 'undefined' ? window : globalThis);
