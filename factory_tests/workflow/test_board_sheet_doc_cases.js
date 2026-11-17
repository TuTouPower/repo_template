'use strict';
// P3 复现：文档请求乱序时，成功与失败分支都不得覆盖当前详情区。
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const BOARD_JS = path.resolve(
  __dirname, '../../repo/.repo_template/scripts/repo_task/view_static/board.js'
);
const src = fs.readFileSync(BOARD_JS, 'utf8');

function extractFunction(text, name) {
  const start = text.indexOf('function ' + name + '(');
  assert.ok(start >= 0, 'missing ' + name);
  const open = text.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === '{') depth++;
    else if (text[i] === '}') {
      depth--;
      if (depth === 0) return text.slice(start, i + 1);
    }
  }
  throw new Error('unbalanced braces in ' + name);
}

const fnSrc = extractFunction(src, 'renderSheetDoc');

function makeEnv() {
  const vars = { selectedId: 't001', activeTab: 'task' };
  const body = {
    children: [],
    set textContent(v) { if (v === '') this.children = []; },
    get textContent() { return ''; },
    appendChild(c) { this.children.push(c); },
  };
  const pending = [];
  const sandbox = {
    nodes: new Map([['t001', { id: 't001' }], ['t002', { id: 't002' }]]),
    state: { get selectedId() { return vars.selectedId; } },
    sheetDocCache: {},
    sheetDocRequestSeq: 0,
    activeDocTab: function () { return vars.activeTab; },
    $: function () { return body; },
    el: function (tag, attrs, children) {
      return { tag: tag, attrs: attrs, text: (children || []).join('') };
    },
    model: { token: '' },
    encodeURIComponent: encodeURIComponent,
    fetch: function (url) {
      return new Promise(function (resolve, reject) {
        pending.push({ url: url, resolve: resolve, reject: reject });
      });
    },
    console: console,
  };
  vm.createContext(sandbox);
  const renderSheetDoc = vm.runInContext(fnSrc + '\nrenderSheetDoc;', sandbox);
  return { vars: vars, body: body, pending: pending, renderSheetDoc: renderSheetDoc };
}

function settle(env, index, text) {
  env.pending[index].resolve({
    ok: true,
    text: function () { return Promise.resolve(text); },
  });
}

function fail(env, index, message) {
  env.pending[index].reject(new Error(message));
}

function flush() {
  return new Promise(function (r) { setImmediate(r); });
}

function assertUntouched(env, expectedText, label) {
  assert.strictEqual(env.body.children.length, 1, label + '：详情区不得被清空');
  assert.strictEqual(env.body.children[0].text, expectedText, label + '：不得显示旧错误');
  assert.ok(
    !env.body.children.some(function (c) {
      return c.tag === 'p' && c.attrs && c.attrs.class === 'doc-error';
    }),
    label + '：不得插入 doc-error 节点'
  );
}

// 成功响应乱序：A 发起后切到 B 并再次请求，A 晚返回不得覆盖 B 正文。
async function successOutOfOrder() {
  const env = makeEnv();
  env.renderSheetDoc('task');
  assert.strictEqual(env.pending.length, 1, '任务 A 应发起一次文档请求');
  env.vars.selectedId = 't002';
  env.renderSheetDoc('task');
  assert.strictEqual(env.pending.length, 2, '任务 B 应发起一次文档请求');

  settle(env, 1, 'DOC-B');
  await flush();
  settle(env, 0, 'DOC-A');
  await flush();

  const pre = env.body.children.filter(function (c) { return c.tag === 'pre'; });
  assert.strictEqual(pre.length, 1, '详情区应只有一个 pre');
  assert.strictEqual(pre[0].text, 'DOC-B', '旧请求不得覆盖当前任务 B 的正文');
}

// 失败响应乱序：A 加载中切到 B 详情（不触发新请求，序号不变），A 随后失败。
async function failureAfterOpeningAnotherTask() {
  const env = makeEnv();
  env.renderSheetDoc('task');
  assert.strictEqual(env.pending.length, 1, 'A 应发起一次文档请求');
  env.vars.selectedId = 't002';
  env.vars.activeTab = 'detail';
  env.body.children = [{ tag: 'div', text: 'DETAIL-B' }];
  fail(env, 0, 'A 请求失败');
  await flush();
  assertUntouched(env, 'DETAIL-B', '打开另一任务详情后旧请求失败');
}

// 失败响应乱序：A 加载中切回详情（同任务、序号不变），A 随后失败。
async function failureAfterReturningToDetail() {
  const env = makeEnv();
  env.renderSheetDoc('task');
  assert.strictEqual(env.pending.length, 1, 'A 应发起一次文档请求');
  env.vars.activeTab = 'detail';
  env.body.children = [{ tag: 'div', text: 'DETAIL-A' }];
  fail(env, 0, 'A 请求失败');
  await flush();
  assertUntouched(env, 'DETAIL-A', '切回详情后旧请求失败');
}

async function main() {
  await successOutOfOrder();
  await failureAfterOpeningAnotherTask();
  await failureAfterReturningToDetail();
  console.log('board sheet doc race (success + failure): ok');
}

main().catch(function (e) { console.error(e); process.exit(1); });
