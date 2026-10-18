// Run: node site/dist/trace.test.mjs  (needs the sibling sv2 checkout's v2 traces)
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const V2_DIR = '/Users/luiz/Developer/crusoe-hackathon/sv2/runs/demo-v2-live/episodes';
const V1_TRACE = '/Users/luiz/Developer/crusoe-hackathon/sv2/results/episodes/B_h3_L6.jsonl';

function load(source) {
  const element = { addEventListener() {}, querySelector: () => null, insertAdjacentHTML() {}, classList: { add() {}, toggle() {} } };
  const context = {
    document: { getElementById: () => element, querySelector: () => null, addEventListener() {} },
    location: { search: '', hostname: 'localhost' },
    history: { replaceState() {} },
    fetch: async () => ({ ok: false }),
    setInterval() {},
    setTimeout() {},
    clearTimeout() {},
    URLSearchParams
  };
  vm.createContext(context);
  vm.runInContext(`${source}\nthis.api = { logLine, readable: typeof readable === "function" ? readable : null, stepLogLine, stateAt, episodeHtml, stepHtml };`, context);
  return context.api;
}

const plain = (value) => JSON.parse(JSON.stringify(value));
const readTrace = (path) => readFileSync(path, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
const now = load(readFileSync(join(here, 'trace.js'), 'utf8'));
const before = load(execFileSync('git', ['show', 'v2/open-world:site/dist/trace.js'], { cwd: here, encoding: 'utf8' }));

// v2: every logged line is readable text
const g009 = readTrace(join(V2_DIR, 'D_life_g009_torch.jsonl'));
const explore = readTrace(join(V2_DIR, 'D_life_g033_explore.jsonl'));
const others = ['D_life_g015_furnace.jsonl', 'D_life_g042_iron_shovel.jsonl', 'D_life_g034_chest.jsonl'].flatMap((name) => readTrace(join(V2_DIR, name)));
const bare = new Set(['goal', 'travel', 'world_change', 'look', 'quarantined', 'repair']);
for (const event of [...g009, ...explore, ...others]) {
  const [, text] = now.logLine(event);
  if (text === null) continue;
  assert.ok(!bare.has(text), `bare kind: ${text}`);
  assert.ok(!/#(log|planks|leaves|sapling|cobblestone)\b|@life-/.test(text), `unreadable: ${text}`);
}
const kinds = new Set(others.concat(g009, explore).map((event) => event.kind));
for (const kind of ['goal', 'travel', 'world_change', 'look', 'quarantined', 'repair']) assert.ok(kinds.has(kind), `no ${kind} in sample traces`);

console.log('--- g009 as the log renders it');
for (const event of g009) {
  const [, text] = now.logLine(event);
  if (text !== null && event.kind !== 'plan') console.log(' ', text.slice(0, 140));
}

// specific shapes
assert.equal(now.readable('craft #planks'), 'craft planks (any wood)');
assert.equal(now.readable('mine_coal_ore.rev1@life-1001-20261017-1803'), 'mine_coal_ore rev1');
assert.equal(now.readable('#01 mine #log with hand'), '#01 mine log (any wood) with hand');
assert.deepEqual(plain(now.logLine({ kind: 'world_change', change: 'r2', action: 'mine', subject: 'coal_ore' })),
  ['change', 'WORLD CHANGED (the agent is not told): coal ore now drops coal only for a stone-tier pickaxe']);
assert.deepEqual(plain(now.logLine({ kind: 'travel', index: 1, action: 'x', distance: 0.4, dug: {}, placed: {}, picked_up: {} })), ['', null]);
assert.equal(now.logLine({ kind: 'travel', distance: 45.2, dug: { stone: 3 }, placed: {}, picked_up: { cobblestone: 2 } })[1], 'travel 45 blocks · dug stone 3 · picked up cobblestone 2');
assert.match(now.logLine({ kind: 'mystery', a: 1 })[1], /^mystery \{"a":1\}$/);

const quarantinedStep = others.find((event) => event.kind === 'step' && event.check === 'quarantined');
console.log('--- quarantined step:', now.stepLogLine(quarantinedStep)[1]);
assert.match(now.stepLogLine(quarantinedStep)[1], /quarantined, not evidence/);
const quarantinedCard = now.stepHtml(quarantinedStep, 'D');
assert.match(quarantinedCard, /QUARANTINED — not evidence/);
assert.match(quarantinedCard, /stray_pickup, interference/);
assert.doesNotMatch(quarantinedCard, /OBSERVED ON THE SERVER[\s\S]*error/);

// header: banner appears once the world change has happened, explore is neutral
const changeAt = g009.findIndex((event) => event.kind === 'world_change');
const headerAt = (events, index) => now.episodeHtml(now.stateAt(events, index));
assert.doesNotMatch(headerAt(g009, changeAt - 1), /HIDDEN CHANGE/);
assert.match(headerAt(g009, changeAt), /HIDDEN CHANGE<\/b>coal ore now drops coal only for a stone-tier pickaxe\. The agent is not told\./);
assert.match(headerAt(g009, 5), /GOAL 9 · life-/);
assert.doesNotMatch(headerAt(g009, 5), /layout/);
assert.match(headerAt(explore, explore.length - 1), /EXPLORED/);

// v1 regression: logLine output unchanged for every event
const v1 = readTrace(V1_TRACE);
for (const event of v1) assert.deepEqual(plain(now.logLine(event)), plain(before.logLine(event)));
const endIndex = v1.length - 1;
const noBlankLines = (html) => html.replace(/\n\s*(?=\n)/g, '');
assert.equal(noBlankLines(headerAt(v1, endIndex)), noBlankLines(before.episodeHtml(before.stateAt(v1, endIndex))));
const v1Step = v1.find((event) => event.kind === 'step');
assert.equal(now.stepHtml(v1Step, 'B'), before.stepHtml(v1Step, 'B'));
console.log(`v1 ok: ${v1.length} events identical`);
console.log('all panel tests passed');
