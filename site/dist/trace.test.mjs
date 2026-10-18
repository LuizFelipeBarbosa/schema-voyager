// Run: node site/dist/trace.test.mjs  (needs the sibling sv2 checkout's v2 traces)
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { changes, goalThreeEvents, laterGoalEvents, lifeRows, planksChange } from './life-fixture.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const V2_DIR = '/Users/luiz/Developer/crusoe-hackathon/sv2/runs/demo-v2-live/episodes';
const V1_TRACE = '/Users/luiz/Developer/crusoe-hackathon/sv2/results/episodes/B_h3_L6.jsonl';

function load(source) {
  const element = { addEventListener() {}, querySelector: () => null, insertAdjacentHTML() {}, classList: { add() {}, toggle() {} } };
  const context = {
    document: { getElementById: () => element, querySelector: (selector) => (['.controls', '.layout'].includes(selector) ? element : null), querySelectorAll: () => [], addEventListener() {} },
    location: { search: '', hostname: 'localhost' },
    window: {},
    history: { replaceState() {} },
    fetch: async () => ({ ok: false }),
    setInterval() {},
    setTimeout() {},
    clearTimeout() {},
    URLSearchParams
  };
  vm.createContext(context);
  vm.runInContext(`${source}\nthis.api = { logLine, readable: typeof readable === "function" ? readable : null, stepLogLine, stateAt, episodeHtml, stepHtml, goalStory: typeof goalStory === "function" ? goalStory : null, lifeOverview: typeof lifeOverview === "function" ? lifeOverview : null, goalCardHtml: typeof goalCardHtml === "function" ? goalCardHtml : null, changeStory: typeof changeStory === "function" ? changeStory : null, worldChangesHtml: typeof worldChangesHtml === "function" ? worldChangesHtml : null, hypothesisHtml: typeof hypothesisHtml === "function" ? hypothesisHtml : null, revisionHtml: typeof revisionHtml === "function" ? revisionHtml : null, lifePlanHtml: typeof lifePlanHtml === "function" ? lifePlanHtml : null, overviewHtml: typeof overviewHtml === "function" ? overviewHtml : null, compactMode: typeof compactMode === "function" ? compactMode : null, routeOf: typeof routeOf === "function" ? routeOf : null, scaleLength: typeof scaleLength === "function" ? scaleLength : null, ruleLabel };`, context);
  return context.api;
}

const plain = (value) => JSON.parse(JSON.stringify(value));
const readTrace = (path) => readFileSync(path, 'utf8').trim().split('\n').map((line) => JSON.parse(line));
const now = load(['life.js', 'minimap.js', 'trace.js'].map((name) => readFileSync(join(here, name), 'utf8')).join('\n'));
const before = load(execFileSync('git', ['show', 'main:site/dist/trace.js'], { cwd: here, encoding: 'utf8' }));

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
assert.match(now.hypothesisHtml(story), /hyp-claim">mine coal_ore with wooden_pickaxe<[\s\S]*hyp-prediction">→ 1 coal</);
assert.match(now.hypothesisHtml(story), /pill certified">certified<\/span><span class="pill experiment">experiment/);
assert.doesNotMatch(now.hypothesisHtml(story), /confirmations|confirmed/);
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

// confirmation wording: certified rules say so, hypotheses count toward both needs
const hyp = (fields) => now.hypothesisHtml({ focus: { phase: 'testing', hypothesis: { action: 'mine log with hand', rule: 'r', status: 'hypothesis', ...fields }, step: null } });
assert.match(hyp({ confirmed: 1, needed: 2, goals: 1, goals_needed: 2 }), /1 of 2 confirmations · in 1 of 2 goals/);
assert.match(hyp({ confirmed: 6, needed: 2, goals: 1, goals_needed: 2 }), /2 of 2 confirmations · in 1 of 2 goals/);
assert.match(hyp({ confirmed: 1, needed: 2 }), /1 of 2 confirmations</);
assert.doesNotMatch(hyp({ confirmed: 6, needed: 2, goals: 1, goals_needed: 2 }), /6\/2|confirmed/);
assert.doesNotMatch(hyp({ status: 'certified', confirmed: 6, needed: 2, goals: 2, goals_needed: 2 }), /confirmations/);
assert.doesNotMatch(hyp({}), /confirmations/);

// the world-change card outlives the goal that hit the change
const T_END = 1_800_000_000 + 1000;
const goalThreeToRecovery = goalThreeEvents.slice(0, goalThreeEvents.findIndex((event) => event.kind === 'certified'));
const r2 = changes[1];
let card = now.changeStory(r2, [{ index: 3, events: goalThreeToRecovery }], T_END);
assert.deepEqual(plain([card.caught.goal, card.caught.step, card.caught.expected, card.caught.got, card.calls, card.tokens]), [3, 1, 'drops coal ×1', 'drops nothing', 1, 2800]);
assert.equal(card.top.summary, 'coal ore needs a stone pickaxe or better');
assert.equal(card.recovered.goal, 3);
assert.equal(card.certified, null);
card = now.changeStory(r2, [{ index: 3, events: goalThreeToRecovery }, { index: 4, events: laterGoalEvents }], T_END);
assert.deepEqual(plain(card.certified), { goal: 4, rules: ['mine_coal_ore.rev1@life-1-20261017-1800'] });
assert.equal(now.changeStory(r2, [{ index: 3, events: goalThreeToRecovery }], r2.t + 5).caught, null, 'nothing is known before it happens');
const cardHtml = now.worldChangesHtml([now.changeStory(r2, [{ index: 3, events: goalThreeToRecovery }, { index: 4, events: laterGoalEvents }], T_END)]);
assert.match(cardHtml, /LATEST WORLD CHANGE[\s\S]*coal ore now drops coal only for a stone-tier pickaxe[\s\S]*CAUGHT[\s\S]*goal 3 · step 1[\s\S]*expected <span>drops coal ×1<\/span>, got <span class="differs">drops nothing[\s\S]*1 call · 2,800 tokens[\s\S]*RECOVERED[\s\S]*CERTIFIED[\s\S]*goal 4/);
assert.doesNotMatch(cardHtml, /change-earlier/);
// the next change fires: it takes the card, the finished one becomes a line
const pending = now.worldChangesHtml([card, now.changeStory(planksChange, [], T_END)]);
assert.equal((pending.match(/class="change-card"/g) || []).length, 1);
assert.match(pending, /change-what">planks \(any wood\): a log now makes 2 planks instead of 4[\s\S]*the agent has not run into it yet/);
assert.match(pending, /change-earlier[\s\S]*coal ore now drops coal only for a stone-tier pickaxe[\s\S]*✓ caught g3[\s\S]*✓ recovered g3[\s\S]*✓ certified g4/);

// a 30-minute life: 60 goals in one row, the last ten and the running goal readable
const longRows = Array.from({ length: 59 }, (_, i) => ({ ...lifeRows[0], index: i + 1, started: lifeRows[0].started + i * 30, ended: lifeRows[0].started + i * 30 + 29, outcome: i % 7 === 0 ? 'failed' : 'success', certified_now: [] }));
const longStory = { ...story, goal: { ...story.goal, index: 60, t: longRows[58].ended + 1 }, end: null, lastT: longRows[58].ended + 30, certifiedRules: [] };
const longStrip = now.overviewHtml(now.lifeOverview(longRows, changes, longStory, []));
assert.equal((longStrip.match(/class="chip /g) || []).length, 60);
assert.equal((longStrip.match(/class="chip [^"]*compact/g) || []).length, 50);
assert.match(longStrip, /class="chip running[^"]*shown/);
assert.equal((longStrip.match(/class="world-change"/g) || []).length, 2);
assert.match(longStrip, /RULES CERTIFIED/);

// no # on screen: every piece of rendered text, with the fixture's group names in it
const everything = [
  now.goalCardHtml(story, lifeRows[0]), now.hypothesisHtml(story), now.revisionHtml(story), now.lifePlanHtml(story),
  now.overviewHtml(now.lifeOverview(lifeRows, [...changes, planksChange], story, episodes)), pending, longStrip,
  now.hypothesisHtml(now.goalStory([{ t: 1, kind: 'goal', index: 1, item: '#planks', count: 1, reason: 'craft #planks' }, { t: 2, kind: 'step_start', index: 1, action: 'craft #planks', rule: 'craft_#planks', predicts: '1 #log -> 4 #planks' }])),
  now.revisionHtml({ mismatch: { rule: 'craft_#planks', predicted: { inventory_delta: { '#planks': 4 } }, observed: { inventory_delta: { '#planks': 2 } } }, hypotheses: [{ kept: true, summary: 'craft #planks -> 2 #planks' }], recovered: null, certified: ['craft_#planks.rev1@life-1-x'] })
];
for (const html of everything) assert.doesNotMatch(html.replace(/&#39;/g, "'").replace(/<[^>]*>/g, ' '), /#/);
assert.equal(now.readable('craft #planks changed (planks2)'), 'craft planks (any wood) changed (planks2)');
assert.equal(now.readable('craft #stone_bricks'), 'craft stone_bricks');
// --- Compact stage view
const flags = (query) => now.compactMode(new URLSearchParams(query));
assert.equal(flags('run=r&viewer=http://localhost:3207'), true, 'on by default with a viewer');
assert.equal(flags('run=r'), false, 'off without a viewer');
assert.equal(flags('run=r&viewer=1&compact=0'), false);
assert.equal(flags('run=r&compact=1'), true);
assert.equal(flags('viewer=1&compact=false'), false);

// the plan: the running step plus the next 3, done steps folded into verdicts
const planRow = (state, verdict, i) => ({ state, verdict, action: `mine log${i}`, start: { predicts: 'log -> 1 log' } });
const longPlan = { rows: [planRow('done', 'match', 1), planRow('done', 'mismatch', 2), planRow('done', 'failed', 3), planRow('running', undefined, 4), ...[5, 6, 7, 8, 9].map((i) => planRow('todo', undefined, i))] };
const compactPlan = now.lifePlanHtml(longPlan, true);
assert.equal((compactPlan.match(/class="row /g) || []).length, 4, 'running + 3');
assert.match(compactPlan, /3 done[\s\S]*class="match"[\s\S]*class="mismatch"[\s\S]*class="failed"/);
assert.match(compactPlan, /<span class="n">4<\/span>[\s\S]*<span class="n">7<\/span>/);
assert.doesNotMatch(compactPlan, /<span class="n">8<\/span>/);
assert.match(compactPlan, /2 more not shown/);
assert.equal((now.lifePlanHtml(longPlan).match(/class="row /g) || []).length, 9, 'non-compact lists every step');
assert.equal((now.lifePlanHtml({ rows: longPlan.rows.slice(0, 3) }, true).match(/class="row /g) || []).length, 0, 'a finished goal is just the verdicts');
// the world-change card: one row of stages instead of four
const compactChange = now.worldChangesHtml([now.changeStory(r2, [{ index: 3, events: goalThreeToRecovery }], T_END)], true);
assert.match(compactChange, /change-card compact[\s\S]*✓ caught[\s\S]*✓ recovered[\s\S]*… certified[\s\S]*coal ore now drops coal only for a stone-tier pickaxe/);
assert.doesNotMatch(compactChange, /<ol class="change-stages"/);

// --- Route minimap: positions of the steps, starts of the goals, the last target
const at = (x, z, y = 64) => ({ x, y, z });
const stepAt = (index, position, target) => ({ kind: 'step', index, detail: { position, ...(target ? { target } : {}) } });
const goalsWithRoute = [
  { index: 2, events: [stepAt(1, at(0, 0)), stepAt(2, at(10, -4))] },
  { index: 3, events: [stepAt(1, at(14, -4), at(20, -8)), stepAt(2, at(18, -9))] },
  { index: 4, events: [] }
];
let route = now.routeOf(goalsWithRoute, 3, null);
assert.deepEqual(plain(route.starts), [{ goal: 2, x: 0, z: 0 }, { goal: 3, x: 10, z: -4 }, { goal: 4, x: 18, z: -9 }], 'a goal starts where the last one stopped');
assert.deepEqual(plain(route.trail.map((point) => [point.x, point.z])), [[10, -4], [14, -4], [18, -9]]);
assert.deepEqual(plain([route.current, route.target]), [at(18, -9), { x: 20, y: 64, z: -8 }]);
assert.equal(route.hasData, true);
route = now.routeOf(goalsWithRoute, 3, { goal: 3, points: [at(19, -9)] });
assert.deepEqual(plain(route.current), at(19, -9), 'the live position is the current one');
route = now.routeOf(goalsWithRoute, 3, { goal: 4, points: [at(50, 50)] });
assert.deepEqual(plain(route.current), at(18, -9), 'a live position of another goal is ignored');
// old runs: steps without positions, and explore steps with them
const withoutPositions = g009.map((event) => (event.detail ? { ...event, detail: { ...event.detail, position: undefined } } : event));
const oldRoute = now.routeOf([{ index: 1, events: withoutPositions }], 1, null);
assert.deepEqual([oldRoute.hasData, oldRoute.trail.length, oldRoute.current], [false, 0, null], 'no route data');
assert.equal(now.routeOf([], null, null).hasData, false);
const exploreRoute = now.routeOf([{ index: 33, events: explore }], 33, null);
assert.ok(exploreRoute.hasData === (explore.some((event) => event.kind === 'step' && event.detail && event.detail.position)));
assert.equal(now.scaleLength(37), 20);
assert.equal(now.scaleLength(0.4), 1);

// Goals from the queue (v3.1h): the user's words on the card, the interrupt next to the outcome
const userGoal = { t: 1, kind: 'goal', index: 7, item: 'torch', count: 10, reason: 'asked', budget: 30, goal_id: 'u001', source: 'user', text: 'get me 10 torches' };
assert.match(now.goalCardHtml(now.goalStory([userGoal]), null), /Your goal: get me 10 torches/);
assert.doesNotMatch(now.goalCardHtml(now.goalStory([userGoal]), null), /Get 10 torch/);
const scriptGoal = now.goalCardHtml(now.goalStory([{ ...userGoal, text: '<script>alert(1)</script>' }]), null);
assert.ok(scriptGoal.includes('&lt;script&gt;') && !scriptGoal.includes('<script>'), 'the goal text is escaped');
assert.match(now.goalCardHtml(oldStory, null), /Get 5 torch/, 'an old goal card is unchanged');
assert.match(now.goalCardHtml(now.goalStory([userGoal]), { ...lifeRows[2], outcome: 'interrupted', interrupt: 'cancelled' }), /INTERRUPTED · cancelled/);

const killRule = { action: { type: 'kill', mob: 'cow' }, effects: { killed: true, drops: { beef: 1 }, allowed: { beef: [1, 3], leather: [0, 2] } } };
assert.equal(now.ruleLabel(killRule), 'kill cow → beef 1-3, leather 0-2');

// Home, chest and composite-goal events (v3.5); a withdraw step is a plain step
const logText = (event) => now.logLine({ t: 1, ...event })[1];
assert.equal(logText({ kind: 'stored', items: { coal: 64, dirt: 120 }, free_slots_before: 3, free_slots_after: 9 }), 'Stored coal x64, dirt x120 in the chest');
assert.equal(logText({ kind: 'home_set', home: { name: 'small house', origin: [4, 76, 31] } }), 'home set · small house at 4 76 31');
assert.equal(logText({ kind: 'home_placed', station: 'crafting_table', pos: [5, 76, 36] }), 'home crafting table placed at 5 76 36');
assert.equal(logText({ kind: 'home_placed', pos: [5, 76, 36] }), 'home station placed at 5 76 36');
assert.match(logText({ kind: 'home_place_failed', station: 'chest', pos: [5, 76, 36], error: 'no_support' }), /home chest not placed at 5 76 36 · no_support/);
assert.match(logText({ kind: 'home_lost', station: 'furnace', pos: [5, 76, 36] }), /home furnace lost at 5 76 36/);
assert.equal(logText({ kind: 'goal_children', goal_id: 'u004', children: [{ id: 'u005', label: '8 torches' }, { id: 'u006', label: '4 cooked beef' }] }), 'u004 split into u005 8 torches, u006 4 cooked beef');
assert.match(logText({ kind: 'goal_replanned', goal_id: 'u004', failed: 'u005', reason: 'no coal', children: [{ id: 'u007', label: 'x' }] }), /u004 replanned after u005 failed \(no coal\) · 1 new children/);
assert.equal(logText({ kind: 'goal_parent_end', goal_id: 'u004', status: 'done' }), 'u004 done');
assert.equal(now.logLine({ t: 1, kind: 'goal_parent_end', goal_id: 'u004', status: 'failed' })[0], 'bad');
const withdrawStep = { kind: 'step', t: 1, index: 3, action: 'withdraw coal x2 from 5 76 36', ok: true, action_obj: { kind: 'withdraw' } };
assert.match(now.stepLogLine(withdrawStep)[1], /withdraw coal x2 from 5 76 36 → done/);
assert.match(now.stepHtml(withdrawStep, 'D'), /Done/);
assert.deepEqual(plain(now.logLine({ t: 1, kind: 'mystery', a: 1 })), ['', 'mystery {"a":1}'], 'unknown events render as before');

console.log('life view ok');
console.log(`v1 ok: ${v1.length} events identical`);
console.log('all panel tests passed');
