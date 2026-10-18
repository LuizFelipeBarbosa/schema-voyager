// Run: node site/dist/trace.test.mjs  (needs the sibling sv2 checkout's v2 traces)
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { changes, goalThreeEvents, lifeRows } from './life-fixture.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const V2_DIR = '/Users/luiz/Developer/crusoe-hackathon/sv2/runs/demo-v2-live/episodes';
const V1_TRACE = '/Users/luiz/Developer/crusoe-hackathon/sv2/results/episodes/B_h3_L6.jsonl';

function load(source) {
  const element = { addEventListener() {}, querySelector: () => null, insertAdjacentHTML() {}, classList: { add() {}, toggle() {} } };
  const context = {
    document: { getElementById: () => element, querySelector: (selector) => (selector === '.controls' ? element : null), addEventListener() {} },
    location: { search: '', hostname: 'localhost' },
    history: { replaceState() {} },
    fetch: async () => ({ ok: false }),
    setInterval() {},
    setTimeout() {},
    clearTimeout() {},
    URLSearchParams
  };
  vm.createContext(context);
  vm.runInContext(`${source}\nthis.api = { logLine, readable: typeof readable === "function" ? readable : null, stepLogLine, stateAt, episodeHtml, stepHtml, goalStory: typeof goalStory === "function" ? goalStory : null, lifeOverview: typeof lifeOverview === "function" ? lifeOverview : null, goalCardHtml: typeof goalCardHtml === "function" ? goalCardHtml : null, hypothesisHtml: typeof hypothesisHtml === "function" ? hypothesisHtml : null, revisionHtml: typeof revisionHtml === "function" ? revisionHtml : null, lifePlanHtml: typeof lifePlanHtml === "function" ? lifePlanHtml : null, overviewHtml: typeof overviewHtml === "function" ? overviewHtml : null };`, context);
  return context.api;
}

const plain = (value) => JSON.parse(JSON.stringify(value));
const readTrace = (path) => readFileSync(path, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
const now = load(['life.js', 'trace.js'].map((name) => readFileSync(join(here, name), 'utf8')).join('\n'));
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
assert.equal(noBlankLines(now.stepHtml(v1Step, 'B')), noBlankLines(before.stepHtml(v1Step, 'B')));

// --- Life view (life.js) on the v2.3 fixture
const upTo = (kind, nth = 1) => {
  let seen = 0;
  return goalThreeEvents.findIndex((event) => event.kind === kind && ++seen === nth) + 1;
};
const storyAt = (count) => now.goalStory(goalThreeEvents.slice(0, count));
const rowStates = (story) => plain(story.rows.map((row) => `${row.state}:${row.verdict || ''}`));

// the first step is running: it is highlighted and nothing is done yet
let story = storyAt(upTo('step_start', 1));
assert.deepEqual(rowStates(story), ['running:', 'todo:']);
assert.equal(story.focus.phase, 'testing');
assert.match(now.hypothesisHtml(story), /coal_ore with wooden_pickaxe -&gt; 1 coal/);
assert.match(now.hypothesisHtml(story), /certified<\/span><span class="count">3\/2 confirmed.*experiment/);
assert.equal(now.revisionHtml(story), '');
assert.match(now.goalCardHtml(story, null), /GOAL 3[\s\S]*Get 4 torch[\s\S]*2 steps, 2 rules still to test/);

// the step ends in a mismatch: MISMATCH box, predicted vs observed
story = storyAt(upTo('contradiction'));
assert.deepEqual(rowStates(story), ['done:mismatch', 'todo:']);
assert.match(now.revisionHtml(story), /MISMATCH[\s\S]*expected <span>drops coal ×1<\/span>[\s\S]*got <span>drops nothing<\/span>[\s\S]*mine_coal_ore demoted/);
assert.match(now.hypothesisHtml(story), /predicted <span>drops coal ×1[\s\S]*observed <span class="differs">drops nothing/);
assert.doesNotMatch(now.revisionHtml(story), /RANKED|RECOVERED/);

// the revision's ranked hypotheses, kept and rejected
story = storyAt(upTo('revision'));
assert.match(now.revisionHtml(story), /RANKED[\s\S]*KEPT[\s\S]*stone pickaxe or better[\s\S]*REJECTED[\s\S]*only at night/);
assert.doesNotMatch(now.revisionHtml(story), /RECOVERED/);

// the revised plan: the old step is done, the revised ones are left
story = storyAt(upTo('plan', 2));
assert.deepEqual(rowStates(story), ['done:mismatch', 'todo:', 'todo:', 'todo:']);

// recovered on the first match of the revised rule, then certified
const recoveredAt = goalThreeEvents.findIndex((event) => event.kind === 'step' && event.index === 3) + 1;
story = storyAt(recoveredAt);
assert.deepEqual(rowStates(story), ['done:mismatch', 'done:match', 'done:match', 'todo:']);
assert.match(now.revisionHtml(story), /RECOVERED[\s\S]*mine coal_ore with stone_pickaxe → drops coal ×1/);
assert.doesNotMatch(now.revisionHtml(story), /CERTIFIED/);
story = storyAt(goalThreeEvents.length);
assert.match(now.revisionHtml(story), /CERTIFIED[\s\S]*mine_coal_ore rev1/);
assert.deepEqual(rowStates(story), ['done:mismatch', 'done:match', 'done:match', 'done:match']);
assert.equal(story.rows.length, 4);
assert.match(now.goalCardHtml(story, lifeRows[2]), /SUCCESS · 4 actions[\s\S]*0:34/);

// the strip: chips by outcome, both world changes in place, running totals
const episodes = [{ name: 'D_life_g003_torch', path: 'runs/f/episodes/D_life_g003_torch.jsonl' }];
const overview = now.lifeOverview(lifeRows, changes, story, episodes);
assert.deepEqual(plain(overview.timeline.map((item) => item.type)), ['chip', 'chip', 'change', 'chip', 'change', 'chip']);
assert.equal(overview.timeline.filter((item) => item.type === 'chip').find((item) => item.chip.index === 4).chip.outcome, 'failed');
assert.deepEqual([overview.calls, overview.tokens, overview.spend, overview.certifiedCount], [3, 8800, 0.05, 3]);
const strip = now.overviewHtml(overview);
assert.equal((strip.match(/class="world-change"/g) || []).length, 2);
assert.match(strip, /the world&#39;s rules changed here \(hidden from the agent\)/);
assert.match(strip, /data-path="runs\/f\/episodes\/D_life_g003_torch.jsonl"/);
assert.match(strip, /\+ mine_coal_ore rev1/);
// before goal 3 ends, its rule is counted from its certified event only once logged
const early = now.lifeOverview(lifeRows.slice(0, 2), changes, storyAt(upTo('plan')), episodes);
assert.equal(early.timeline.filter((item) => item.type === 'chip').length, 3);
assert.equal(early.timeline.find((item) => item.chip && item.chip.index === 3).chip.outcome, 'running');
assert.deepEqual([early.calls, early.certifiedCount], [2, 2]);

// old traces without step_start or plan fields: no running step, rule id and predicted at the end
const oldStory = now.goalStory(g009.slice(0, g009.findIndex((event) => event.kind === 'contradiction') + 1));
assert.equal(oldStory.running, null);
assert.equal(oldStory.rows.filter((row) => row.state === 'running').length, 0);
assert.equal(oldStory.rows[0].state, 'done');
assert.match(now.hypothesisHtml(oldStory), /LAST TEST[\s\S]*mine_coal_ore[\s\S]*MISMATCH[\s\S]*predicted <span>drops coal ×1/);
assert.match(now.goalCardHtml(oldStory, null), /Get 5 torch/);
assert.match(now.lifePlanHtml(now.goalStory(g009.slice(0, 3))), /craft torch/);
const oldFull = now.goalStory(g009);
assert.equal(oldFull.rows.filter((row) => row.state === 'todo').length, 0);
console.log('life view ok');
console.log(`v1 ok: ${v1.length} events identical`);
console.log('all panel tests passed');
