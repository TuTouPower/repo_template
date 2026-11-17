'use strict';
// P3-2 复现：快速切换任务后，旧的文档请求晚返回不得覆盖当前详情区。
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

let selectedId = 't001';
let activeTab = 'task';
const body = {
  children: [],
  set textContent(v) { if (v === '') this.children = []; },
  get textContent() { return ''; },
  appendChild(c) { this.children.push(c); },
};
const pending = [];

const sandbox = {
  nodes: new Map([['t001', { id: 't001' }], ['t002', { id: 't002' }]]),
  state: { get selectedId() { return selectedId; } },
  sheetDocCache: {},
  sheetDocRequestSeq: 0,
  activeDocTab: function () { return activeTab; },
  $: function () { return body; },
  el: function (tag, attrs, children) {
    return { tag: tag, text: (children || []).join('') };
  },
  model: { token: '' },
  encodeURIComponent: encodeURIComponent,
  fetch: function (url) {
    return new Promise(function (resolve) {
      pending.push({ url: url, resolve: resolve });
    });
  },
  console: console,
};
vm.createContext(sandbox);
const renderSheetDoc = vm.runInContext(fnSrc + '\nrenderSheetDoc;', sandbox);

function settle(index, text) {
  pending[index].resolve({
    ok: true,
    text: function () { return Promise.resolve(text); },
  });
}

async function main() {
  renderSheetDoc('task');
  assert.strictEqual(pending.length, 1, '任务 A 应发起一次文档请求');

  // 快速切换到任务 B 并再次请求同一文档标签
  selectedId = 't002';
  renderSheetDoc('task');
  assert.strictEqual(pending.length, 2, '任务 B 应发起一次文档请求');

  // 新请求先返回，旧请求晚返回
  settle(1, 'DOC-B');
  await new Promise(function (r) { setImmediate(r); });
  settle(0, 'DOC-A');
  await new Promise(function (r) { setImmediate(r); });

  const pre = body.children.filter(function (c) { return c.tag === 'pre'; });
  assert.strictEqual(pre.length, 1, '详情区应只有一个 pre');
  assert.strictEqual(pre[0].text, 'DOC-B', '旧请求不得覆盖当前任务 B 的正文');
  console.log('board sheet doc race: ok');
}

main().catch(function (e) { console.error(e); process.exit(1); });
