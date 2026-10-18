// Run: node site/dist/build_preview.test.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const context = { setInterval() {} }; // no document: build_preview.js must not attach itself
vm.createContext(context);
vm.runInContext(`${readFileSync(join(here, 'build_preview.js'), 'utf8')}\nthis.api = SvBuildPreview;`, context);
const { colorOf, materialName, layerHtml, previewHtml, chooseGoal, createController } = context.api;

const palette = { L: '#log', P: '#planks', D: 'oak_door', T: 'torch' };
const preview = {
  name: 'small hut',
  size: [5, 2, 5],
  layers: [['LPPPL', 'P...P', 'P...P', 'P...P', 'LPDPL'], ['LPPPL', 'PT.  ', 'P...P', 'P...P', 'LPDPL']],
  palette,
  materials: { '#log': 8, '#planks': 40, oak_door: 1, torch: 1 }
};
const goal = (id, status, extra = {}) => ({ id, status, text: `goal ${id}`, blueprint: {}, preview, ...extra });
const count = (html, pattern) => (html.match(pattern) || []).length;

function fakeRoot() {
  const listeners = {};
  return { innerHTML: '', addEventListener: (type, fn) => { listeners[type] = fn; }, fire: (type, event) => listeners[type](event) };
}

function setup(payload) {
  const calls = [];
  const timers = [];
  const state = { payload, reply: { ok: true, body: { ok: true } } };
  const fetch = async (url, options) => {
    calls.push({ url, options });
    if (!options) return { ok: true, json: async () => state.payload };
    return { ok: state.reply.ok, json: async () => state.reply.body };
  };
  const root = fakeRoot();
  const controller = createController({ root, fetch, setInterval: (fn, ms) => timers.push({ fn, ms }) });
  const click = (attribute, id) => root.fire('click', { target: { closest: (selector) => (selector === `[data-${attribute}]` ? { dataset: { [attribute]: id } } : null) } });
  return { calls, timers, state, root, controller, click, posts: () => calls.filter((c) => c.options) };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));

const tests = {
  'one grid per layer with a cell per character, labelled bottom-up'() {
    const html = previewHtml(goal('u001', 'awaiting_approval'));
    assert.equal(count(html, /class="bp-grid"/g), 2);
    assert.equal(count(html, /class="bp-cell/g), 50);
    assert.ok(html.indexOf('layer 0') < html.indexOf('layer 1'));
    assert.ok(!html.includes('layer 2'));
  },
  'air is faint, any is blank, palette cells are coloured by value'() {
    const html = layerHtml(['L. T'], palette);
    assert.equal(count(html, /bp-air/g), 1);
    assert.equal(count(html, /bp-any/g), 1);
    assert.ok(html.includes(`background:${colorOf('#log')}`));
    assert.ok(html.includes(`background:${colorOf('torch')}`));
    assert.ok(html.includes('grid-template-columns:repeat(4, 1fr)'));
  },
  'colours are stable and differ by value'() {
    assert.equal(colorOf('obsidian'), colorOf('obsidian'));
    assert.notEqual(colorOf('obsidian'), colorOf('bricks'));
    assert.notEqual(colorOf('#planks'), colorOf('#log'));
    assert.equal(colorOf('oak_door'), colorOf('birch_door'));
  },
  'legend and materials list use names without # and _'() {
    const html = previewHtml(goal('u001', 'awaiting_approval'));
    assert.equal(count(html, /class="bp-swatch"/g), 4);
    assert.ok(html.includes('</i>planks</li>') && html.includes('</i>oak door</li>'));
    assert.ok(html.includes('40 planks, 8 log') || html.includes('8 log, 40 planks'));
    assert.ok(html.includes('1 oak door'));
    assert.equal(materialName('#oak_planks'), 'oak planks');
    assert.ok(!/MATERIALS<\/b>[^<]*[#_]/.test(html));
  },
  'buttons only while awaiting approval'() {
    for (const status of ['awaiting_approval', 'running', 'queued', 'done']) {
      const html = previewHtml(goal('u001', status));
      const shown = status === 'awaiting_approval' ? 1 : 0;
      assert.equal(count(html, /data-approve=/g), shown, status);
      assert.equal(count(html, /data-discard=/g), shown, status);
    }
  },
  'escapes the name, id and palette values'() {
    const evil = { ...preview, name: '<script>alert(1)</script>', palette: { X: '<b onload="x">' }, layers: [['X']], materials: { '<b onload="x">': 1 } };
    const html = previewHtml(goal('<i>', 'awaiting_approval', { preview: evil }));
    assert.ok(!html.includes('<script>') && !html.includes('<b onload') && !html.includes('<i>'), html);
    assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
  },
  'chooses the newest awaiting goal, else the newest running one, else none'() {
    const payload = (...goals) => ({ run: 'r', goals });
    const chosen = chooseGoal(payload(goal('a', 'awaiting_approval'), goal('b', 'awaiting_approval'), goal('c', 'running')));
    assert.equal(chosen.id, 'b');
    assert.equal(chooseGoal(payload(goal('a', 'running'), goal('b', 'running'), goal('c', 'awaiting_approval', { preview: null }))).id, 'b');
    assert.equal(chooseGoal(payload(goal('a', 'done'), goal('b', 'queued'), goal('c', 'running', { preview: undefined }))), null);
    assert.equal(chooseGoal({ run: null, goals: [] }), null);
  },
  async 'polls every 2 s and draws the chosen goal, or nothing'() {
    const t = setup({ run: 'r', goals: [goal('u001', 'awaiting_approval')] });
    await settle();
    assert.equal(t.timers[0].ms, 2000);
    assert.equal(t.calls[0].url, '/goals.json');
    assert.ok(t.root.innerHTML.includes('data-approve="u001"'));
    t.state.payload = { run: 'r', goals: [goal('u001', 'done')] };
    await t.timers[0].fn();
    assert.equal(t.root.innerHTML, '');
  },
  async 'approve and discard post to the goal with the run, then refresh'() {
    for (const [attribute, action] of [['approve', 'approve'], ['discard', 'discard']]) {
      const t = setup({ run: 'life-1', goals: [goal('u003', 'awaiting_approval')] });
      await settle();
      t.click(attribute, 'u003');
      await settle();
      const [post] = t.posts();
      assert.equal(post.url, `/goals/u003/${action}`);
      assert.equal(post.options.method, 'POST');
      assert.equal(post.options.headers['Content-Type'], 'application/json');
      assert.deepEqual(JSON.parse(post.options.body), { run: 'life-1' });
      assert.equal(t.calls.at(-1).url, '/goals.json');
    }
  },
  async 'a refused answer shows the error'() {
    const t = setup({ run: 'r', goals: [goal('u003', 'awaiting_approval')] });
    t.state.reply = { ok: false, body: { ok: false, error: 'not_awaiting_approval', status: 'queued' } };
    await settle();
    await t.controller.answer('u003', 'approve');
    assert.ok(t.root.innerHTML.includes('not_awaiting_approval'));
  }
};

for (const [name, run] of Object.entries(tests)) {
  await run();
  console.log('ok', name);
}
console.log('all build preview tests passed');
