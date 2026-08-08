'use strict';
/* eslint-disable @typescript-eslint/no-require-imports */
/* chain_plan.js computeBatchPlan 行为级回归用例（node 直接运行）。
 * 覆盖看板推荐链与后端 compute_schedule 规则对齐的关键场景：
 * 链首只取 active/runnable（=后端 selected），后继须已排程且不跨冲突。
 * 任何断言失败以非零退出码结束。
 */
require('../../scripts/repo_template/repo_task/view_static/chain_plan.js');
var ChainPlan = globalThis.ChainPlan;

var failures = [];

function check(name, actual, expected) {
  var a = JSON.stringify(actual);
  var e = JSON.stringify(expected);
  if (a !== e) {
    failures.push(name + '\n  expected: ' + e + '\n  actual:   ' + a);
  }
}

function node(id, category, scheduleStatus) {
  return { id: id, title: id, category: category, schedule_status: scheduleStatus || '' };
}

function dep(from, to) { return { type: 'dep', from: from, to: to }; }
function conflict(a, b) { return { type: 'conflict', from: a, to: b }; }

function planOf(nodes, edges) {
  var plan = ChainPlan.computeBatchPlan({ nodes: nodes, edges: edges });
  return {
    chains: plan.chains.map(function (c) { return c.taskIds; }),
    unassigned: plan.unassigned.slice().sort(),
    deferred: plan.deferred.map(function (d) { return d.taskId; }).sort(),
  };
}

// P1-2：blocked_conflict 不作链首（后端序号优先级压住 t002，前端不重推）
check('blocked_conflict 不进链首', planOf(
  [node('t001', 'blocked_deps', 'scheduled'),
    node('t002', 'blocked_conflict', 'scheduled'),
    node('t003', 'backlog', 'scheduled')],
  [dep('t003', 't001'), conflict('t001', 't002')]
), { chains: [], unassigned: ['t001', 't002', 't003'], deferred: [] });

// P1-1：未排程后继不进链
check('未排程后继不进链', planOf(
  [node('t001', 'runnable', 'scheduled'), node('t002', 'backlog', '')],
  [dep('t001', 't002')]
), { chains: [['t001']], unassigned: ['t002'], deferred: [] });

// P1-1：待澄清后继不进链
check('待澄清后继不进链', planOf(
  [node('t001', 'runnable', 'scheduled'),
    node('t002', 'backlog', 'pending_clarification')],
  [dep('t001', 't002')]
), { chains: [['t001']], unassigned: ['t002'], deferred: [] });

// P1-1：与 active 冲突的后继不进链
check('与运行中冲突的后继不进链', planOf(
  [node('t001', 'runnable', 'scheduled'),
    node('t002', 'backlog', 'scheduled'),
    node('t003', 'active', '')],
  [dep('t001', 't002'), conflict('t002', 't003')]
), { chains: [['t001'], ['t003']], unassigned: ['t002'], deferred: [] });

// 链内冲突由串行顺序消化，正常吸纳
check('链内冲突串行消化', planOf(
  [node('t001', 'runnable', 'scheduled'), node('t002', 'backlog', 'scheduled')],
  [dep('t001', 't002'), conflict('t001', 't002')]
), { chains: [['t001', 't002']], unassigned: [], deferred: [] });

// 与其他链首（本轮确定并行）冲突的后继不进链
check('与其他链首冲突的后继不进链', planOf(
  [node('t001', 'runnable', 'scheduled'),
    node('t002', 'backlog', 'scheduled'),
    node('t003', 'runnable', 'scheduled')],
  [dep('t001', 't002'), conflict('t002', 't003')]
), { chains: [['t001'], ['t003']], unassigned: ['t002'], deferred: [] });

// 健康依赖链照常整链推荐
check('健康依赖链整链推荐', planOf(
  [node('t001', 'runnable', 'scheduled'),
    node('t002', 'blocked_deps', 'scheduled'),
    node('t003', 'blocked_deps', 'scheduled')],
  [dep('t001', 't002'), dep('t002', 't003')]
), { chains: [['t001', 't002', 't003']], unassigned: [], deferred: [] });

if (failures.length > 0) {
  console.error('FAILED ' + failures.length + ' case(s):');
  failures.forEach(function (f) { console.error('  - ' + f); });
  process.exit(1);
}
console.log('chain_plan cases: all passed');
