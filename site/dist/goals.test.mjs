// Run: node site/dist/goals.test.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const context = { setInterval() {} }; // no document: goals.js must not attach itself
vm.createContext(context);
vm.runInContext(`${readFileSync(join(here, 'goals.js'), 'utf8')}\nthis.api = SvGoals;`, context);
const { goalChip, goalsListHtml, createController } = context.api;

const goal = (id, status, extra = {}) => ({ id, t: 0, status, source: 'user', text: `goal ${id}`, reason: null, ...extra });
const STATUSES = ['submitted', 'queued', 'running', 'done', 'failed', 'cancelled', 'unsupported', 'awaiting_approval'];
const cancelCount = (html) => (html.match(/data-cancel=/g) || []).length;

// Fakes for the few DOM bits and the network the controller touches
function fakeElement() {
  const listeners = {};
  return { value: '', textContent: '', innerHTML: '', hidden: true, addEventListener: (type, fn) => { listeners[type] = fn; }, fire: (type, event) => listeners[type](event) };
}

function setup(payload, respond = () => ({ ok: true, status: 201, body: { ok: true } }), options = {}) {
  const calls = [];
  const els = { '.goals-input': fakeElement(), '.goals-send': fakeElement(), '.goals-error': fakeElement(), '.goals-run': fakeElement(), '.goals-list': fakeElement() };
  const timers = [];
  const state = { payload };
  const fetch = async (url, options) => {
    calls.push({ url, options });
    if (!options) return { ok: state.getStatus === undefined, status: state.getStatus || 200, json: async () => state.payload };
    const reply = respond(url, options);
    return { ok: reply.ok, json: async () => reply.body };
  };
  const root = { innerHTML: '', querySelector: (selector) => els[selector] };
  const controller = createController({ root, fetch, setInterval: (fn, ms) => timers.push({ fn, ms }), ...options });
  return { calls, els, timers, state, controller, posts: () => calls.filter((c) => c.options) };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));

const tests = {
  'chip and label per status'() {
    const expected = { submitted: 'queued', queued: 'queued', running: 'running', done: 'done', failed: 'failed', cancelled: 'cancelled', unsupported: 'unsupported', awaiting_approval: 'awaiting approval' };
    for (const status of STATUSES) {
      const html = goalChip(status);
      const cssClass = expected[status].replace(' ', '-');
      assert.ok(html.includes(`goal-chip-${cssClass}`), html);
      assert.ok(html.includes(`>${expected[status]}</span>`), html);
    }
  },
  'cancel only on the four non-final statuses'() {
    for (const status of STATUSES) {
      const html = goalsListHtml({ run: 'r', goals: [goal('u001', status)] });
      const cancellable = ['submitted', 'queued', 'running', 'awaiting_approval'].includes(status);
      assert.equal(cancelCount(html), cancellable ? 1 : 0, status);
    }
  },
  'reason shown for failed, cancelled and unsupported only'() {
    for (const status of STATUSES) {
      const html = goalsListHtml({ run: 'r', goals: [goal('u001', status, { reason: 'no recipe' })] });
      assert.equal(html.includes('no recipe'), ['failed', 'cancelled', 'unsupported'].includes(status), status);
    }
  },
  'newest first, at most 20'() {
    const goals = Array.from({ length: 25 }, (_, i) => goal(`g${String(i).padStart(2, '0')}`, 'done'));
    const html = goalsListHtml({ run: 'r', goals });
    assert.equal((html.match(/class="goal-row"/g) || []).length, 20);
    assert.ok(html.indexOf('g24') < html.indexOf('g23'));
    assert.ok(html.includes('g05') && !html.includes('g04'));
  },
  'escapes text and reason'() {
    const html = goalsListHtml({ run: 'r', goals: [goal('u001', 'failed', { text: '<script>alert(1)</script>', reason: '<script>bad()</script>' })] });
    assert.ok(!html.includes('<script>'), html);
    assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;') && html.includes('&lt;script&gt;bad()'));
  },
  'run null renders an empty list'() {
    assert.doesNotThrow(() => goalsListHtml({ run: null, goals: [] }));
    assert.ok(goalsListHtml({ run: null, goals: [] }).includes('no goals yet'));
  },
  async 'send posts json with text and run'() {
    const t = setup({ run: 'life-1', goals: [] });
    await settle();
    t.els['.goals-input'].value = '  get me 10 torches ';
    await t.controller.send();
    const [post] = t.posts();
    assert.equal(post.url, '/goals');
    assert.equal(post.options.method, 'POST');
    assert.equal(post.options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(post.options.body), { text: 'get me 10 torches', run: 'life-1' });
    assert.equal(t.els['.goals-input'].value, '', 'cleared on 201');
    assert.equal(t.calls.at(-1).url, '/goals.json', 'refreshes after sending');
  },
  async 'empty text sends nothing'() {
    const t = setup({ run: null, goals: [] });
    await settle();
    t.els['.goals-input'].value = '   ';
    await t.controller.send();
    assert.equal(t.posts().length, 0);
  },
  async 'error response shows its detail and keeps the text'() {
    const t = setup({ run: 'r', goals: [] }, () => ({ ok: false, body: { ok: false, error: 'bad_request', detail: 'text too long' } }));
    await settle();
    t.els['.goals-input'].value = 'x';
    await t.controller.send();
    assert.equal(t.els['.goals-error'].textContent, 'text too long');
    assert.equal(t.els['.goals-error'].hidden, false);
    assert.equal(t.els['.goals-input'].value, 'x');
  },
  async 'cancel posts the cancel route, via a click on the button'() {
    const t = setup({ run: 'life-1', goals: [goal('u003', 'running')] });
    await settle();
    t.els['.goals-list'].fire('click', { target: { closest: () => ({ dataset: { cancel: 'u003' } }) } });
    await settle();
    const [post] = t.posts();
    assert.equal(post.url, '/goals/u003/cancel');
    assert.equal(post.options.method, 'POST');
    assert.deepEqual(JSON.parse(post.options.body), { run: 'life-1' });
    assert.equal(t.calls.at(-1).url, '/goals.json');
  },
  async 'poll calls GET /goals.json and re-renders'() {
    const t = setup({ run: 'r', goals: [goal('u001', 'queued')] });
    await settle();
    assert.equal(t.timers[0].ms, 2000);
    assert.ok(t.els['.goals-list'].innerHTML.includes('u001'));
    t.state.payload = { run: 'r', goals: [goal('u001', 'done'), goal('u002', 'running')] };
    await t.timers[0].fn();
    assert.equal(t.calls.at(-1).url, '/goals.json');
    assert.ok(t.els['.goals-list'].innerHTML.includes('u002'));
  },
  async 'run null payload renders without errors'() {
    const t = setup({ run: null, goals: [] });
    await settle();
    assert.ok(t.els['.goals-list'].innerHTML.includes('no goals yet'));
  },
  async 'the run line names the run, or says there is none'() {
    const t = setup({ run: 'life-1', goals: [] });
    await settle();
    assert.equal(t.els['.goals-run'].textContent, 'Run: life-1');
    t.state.payload = { run: null, goals: [] };
    await t.controller.refresh();
    assert.equal(t.els['.goals-run'].textContent, 'No live run');
  },
  async 'pageRun goes in the GET url and the POST body'() {
    const t = setup({ run: 'other', goals: [] }, undefined, { pageRun: 'my run' });
    await settle();
    assert.equal(t.calls[0].url, '/goals.json?run=my%20run');
    t.els['.goals-input'].value = 'x';
    await t.controller.send();
    assert.deepEqual(JSON.parse(t.posts()[0].options.body), { text: 'x', run: 'my run' });
    await t.controller.cancel('u001');
    assert.deepEqual(JSON.parse(t.posts()[1].options.body), { run: 'my run' });
  },
  async 'ambiguous_run on GET shows the message and keeps the list'() {
    const t = setup({ run: 'r', goals: [goal('u001', 'queued')] });
    await settle();
    t.state.payload = { ok: false, error: 'ambiguous_run', runs: ['a', 'b'] };
    t.state.getStatus = 409;
    await t.controller.refresh();
    assert.equal(t.els['.goals-error'].textContent, 'Several lives are running: a, b. Open the panel with ?run=<name>.');
    assert.equal(t.els['.goals-error'].hidden, false);
    assert.ok(t.els['.goals-list'].innerHTML.includes('u001'));
    assert.equal(t.els['.goals-run'].textContent, 'Run: r');
    assert.ok(t.els['.goals-input']);
  },
  async 'a good GET clears the ambiguity message'() {
    const t = setup({ run: 'r', goals: [] });
    await settle();
    t.state.payload = { ok: false, error: 'ambiguous_run', runs: ['a', 'b'] };
    t.state.getStatus = 409;
    await t.controller.refresh();
    t.state.payload = { run: 'a', goals: [] };
    delete t.state.getStatus;
    await t.controller.refresh();
    assert.equal(t.els['.goals-error'].hidden, true);
  },
  async 'ambiguous_run on POST shows the message'() {
    const ambiguous = { ok: false, error: 'ambiguous_run', runs: ['a', 'b'] };
    const t = setup({ run: null, goals: [] }, () => ({ ok: false, body: ambiguous }));
    await settle();
    t.state.payload = ambiguous; // the follow-up GET is ambiguous too
    t.state.getStatus = 409;
    t.els['.goals-input'].value = 'x';
    await t.controller.send();
    assert.equal(t.els['.goals-error'].textContent, 'Several lives are running: a, b. Open the panel with ?run=<name>.');
    assert.equal(t.els['.goals-input'].value, 'x');
  }
};

for (const [name, run] of Object.entries(tests)) {
  await run();
  console.log('ok', name);
}
console.log('all goals tests passed');
