'use strict';
/* chain_plan.js 展示辅助回归（node 直接运行）。
 * 批计划算法权威在 repo_task.plan.compute_batch_plan，见 test_plan.py。
 * 任何断言失败以非零退出码结束。
 */
require('../../repo/.repo_template/scripts/repo_task/view_static/chain_plan.js');
var ChainPlan = globalThis.ChainPlan;

var failures = [];

function check(name, actual, expected) {
  var a = JSON.stringify(actual);
  var e = JSON.stringify(expected);
  if (a !== e) {
    failures.push(name + '\n  expected: ' + e + '\n  actual:   ' + a);
  }
}

check('isUnfinished active', ChainPlan.isUnfinished('active'), true);
check('isUnfinished done', ChainPlan.isUnfinished('done'), false);
check('chainName 0', ChainPlan.chainName(0), '链 A');
check('chainName 25', ChainPlan.chainName(25), '链 Z');
check('chainName 26', ChainPlan.chainName(26), '链 27');
check('chainLetter', ChainPlan.chainLetter('链 B'), 'B');

var chains = [
  { id: 'c1', name: '链 A', taskIds: ['t001', 't002'] },
  { id: 'c2', name: '链 B', taskIds: ['t003'] },
];
var map = ChainPlan.chainOfMap(chains);
check('chainOfMap t001', map.get('t001'), 'c1');
check('chainOfMap t003', map.get('t003'), 'c2');

// A17：链停止原因只由后端计算，客户端不得保留算法
check('chainStopInfo 已移除', typeof ChainPlan.chainStopInfo, 'undefined');
check('chainText 已移除', typeof ChainPlan.chainText, 'undefined');

// 确认批计划算法已迁出本文件
check(
  'computeBatchPlan 已移除',
  typeof ChainPlan.computeBatchPlan,
  'undefined'
);

if (failures.length > 0) {
  console.error('FAILED ' + failures.length + ' case(s):');
  failures.forEach(function (f) { console.error('  - ' + f); });
  process.exit(1);
}
console.log('chain_plan display helpers: all passed');
