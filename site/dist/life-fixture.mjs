// A hand-made v2.3 life for the panel: goal 3 mines coal with a wooden pickaxe,
// is contradicted, revises the rule and recovers. Used by trace.test.mjs, and
// `node site/dist/life-fixture.mjs runs/fixture-v23` writes it as a run.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const T0 = 1_800_000_000;
const REVISED = 'mine_coal_ore.rev1@life-1-20261017-1800';
const wooden = { rule: 'mine_coal_ore', experimental: true, status: 'certified', confirmed: 3, needed: 2, goals: 2, goals_needed: 2, predicts: 'coal_ore with wooden_pickaxe -> 1 coal' };
const stone = { rule: REVISED, experimental: true, status: 'hypothesis', confirmed: 2, needed: 2, goals: 1, goals_needed: 2, predicts: 'coal_ore with stone_pickaxe -> 1 coal' };
const torch = { rule: 'craft_torch', experimental: false, status: 'prior', confirmed: 1, needed: 2, goals: 1, goals_needed: 2, predicts: '1 coal + 1 stick -> 4 torch' };
const stoneTool = { rule: 'craft_stone_pickaxe', experimental: true, status: 'prior', confirmed: 0, needed: 2, goals: 0, goals_needed: 2, predicts: '3 cobblestone + 2 stick -> 1 stone_pickaxe' };
const planStep = (action, hypothesis) => ({ action, ...hypothesis });

function step(t, index, action, rule, fields) {
  const kind = action.split(' ')[0];
  return { t, kind: 'step', index, action, rule, action_obj: { kind, target: action.split(' ')[1], tool: null, times: 1 }, ok: true, error: null, ...fields };
}
const mineAction = 'mine coal_ore with wooden_pickaxe';
const reviseAction = 'mine coal_ore with stone_pickaxe';

export const goalThreeEvents = [
  { t: T0 + 100, kind: 'goal', index: 3, item: 'torch', count: 4, reason: '2 steps, 2 rules still to test', budget: 30 },
  { t: T0 + 100, kind: 'episode_start', system: 'D', task: 'g003', description: 'Obtain 4 torch', world_id: 'life-1-20261017-1800', budget: 30, layout: 0 },
  { t: T0 + 101, kind: 'plan', steps: [planStep(mineAction, wooden), planStep('craft torch', torch)], skills: [] },
  { t: T0 + 102, kind: 'step_start', index: 1, action: mineAction, ...wooden },
  step(T0 + 112, 1, mineAction, 'mine_coal_ore', {
    block_removed: true, drops: {}, check: 'mismatch', check_detail: 'drops: expected {coal: 1}, saw nothing',
    predicted: { block_removed: true, drops: { coal: 1 } }, observed: { block_removed: true, drops: {} }
  }),
  { t: T0 + 112, kind: 'contradiction', rule: 'mine_coal_ore', transition: 'D/life/g003-torch/1', detail: 'drops: expected {coal: 1}, saw nothing', predicted: { block_removed: true, drops: { coal: 1 } }, observed: { block_removed: true, drops: {} }, flagged: ['skill_torch'] },
  { t: T0 + 115, kind: 'revision', rule: 'mine_coal_ore', tokens: 2800, hypotheses: [
    { rank: 1, id: REVISED, kept: true, summary: 'coal ore needs a stone pickaxe or better', rule: {} },
    { rank: 2, id: 'mine_coal_ore.rev2', kept: false, summary: 'coal ore drops coal only at night', rule: {} }
  ] },
  { t: T0 + 116, kind: 'plan', steps: [planStep('craft stone_pickaxe', stoneTool), planStep(reviseAction, stone), planStep('craft torch', torch)], skills: [] },
  { t: T0 + 117, kind: 'step_start', index: 2, action: 'craft stone_pickaxe', ...stoneTool },
  step(T0 + 120, 2, 'craft stone_pickaxe', 'craft_stone_pickaxe', { inventory_delta: { stone_pickaxe: 1 }, check: 'match', predicted: { inventory_delta: { stone_pickaxe: 1 } } }),
  { t: T0 + 121, kind: 'plan', steps: [planStep(reviseAction, stone), planStep('craft torch', torch)], skills: [] },
  { t: T0 + 122, kind: 'step_start', index: 3, action: reviseAction, ...stone },
  step(T0 + 130, 3, reviseAction, REVISED, {
    block_removed: true, drops: { coal: 1 }, check: 'match',
    predicted: { block_removed: true, drops: { coal: 1 } }, observed: { block_removed: true, drops: { coal: 1 } }
  }),
  { t: T0 + 131, kind: 'plan', steps: [planStep('craft torch', torch)], skills: [] },
  { t: T0 + 132, kind: 'step_start', index: 4, action: 'craft torch', ...torch },
  step(T0 + 133, 4, 'craft torch', 'craft_torch', { inventory_delta: { torch: 4 }, check: 'match', predicted: { inventory_delta: { torch: 4 } } }),
  { t: T0 + 134, kind: 'episode_end', success: true, actions: 4, llm_tokens: 2800, llm_calls: 1, repeated_failures: 0 },
  { t: T0 + 134, kind: 'certified', rules: [REVISED] }
];

// Goal 4 (a later goal) is where the revised rule's second goal certifies it.
export const laterGoalEvents = [
  { t: T0 + 140, kind: 'goal', index: 4, item: 'furnace', count: 1, reason: 'fixture', budget: 30 },
  { t: T0 + 160, kind: 'certified', rules: [REVISED] }
];

const lifeRow = (index, goal, count, started, ended, extra) => ({
  index, goal, count, reason: 'fixture', plan_length: 2, budget: 30, outcome: 'success', actions: 2, certified_now: [], certified_total: 0,
  llm_calls: 0, tokens: 0, spend_usd: 0.03, started: T0 + started, ended: T0 + ended, ...extra
});
export const lifeRows = [
  lifeRow(1, '#planks', 1, 0, 40, { llm_calls: 2, tokens: 6000, certified_now: ['craft_#planks'], certified_total: 1 }),
  lifeRow(2, 'stick', 4, 41, 99, { certified_now: ['craft_stick'], certified_total: 2, spend_usd: 0.04 }),
  lifeRow(3, 'torch', 4, 100, 134, { actions: 4, llm_calls: 1, tokens: 2800, certified_now: [REVISED], certified_total: 3, spend_usd: 0.05 }),
  lifeRow(4, 'furnace', 1, 135, 200, { outcome: 'failed', spend_usd: 0.05, certified_total: 3 })
];
export const changes = [
  { t: T0 + 60, change: 'stick2', action: 'craft', subject: 'stick', goal: 'g002' },
  { t: T0 + 101, change: 'r2', action: 'mine', subject: 'coal_ore', goal: 'g003' }
];
// What the demo's scramble does to the group rule `craft #planks`.
export const planksChange = { t: T0 + 170, change: 'planks2', action: 'craft', subject: '#planks', goal: 'g004' };

if (process.argv[2]) {
  const run = process.argv[2];
  mkdirSync(join(run, 'episodes'), { recursive: true });
  const lines = (rows) => rows.map((row) => JSON.stringify(row)).join('\n') + '\n';
  writeFileSync(join(run, 'episodes', 'D_life_g003_torch.jsonl'), lines(goalThreeEvents));
  writeFileSync(join(run, 'life.jsonl'), lines(lifeRows));
  writeFileSync(join(run, 'changes.jsonl'), lines(changes));
  writeFileSync(join(run, 'store.json'), '{}');
}
