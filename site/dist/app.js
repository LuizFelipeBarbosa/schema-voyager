// Field guide interactions. Every number, the example trace and the rule and
// skill cards come from results.js, which `schema-voyager export runs/<run>`
// writes from a finished run. Without it the page shows dashes.

const results = window.SCHEMA_VOYAGER_RESULTS || null;
const byId = (id) => document.getElementById(id);

const loopSteps = [
  {
    kicker: 'CURRENT STATE',
    title: 'See what the world gives you.',
    copy: 'The bot reports its position, its inventory and the blocks within 16 blocks. The agent starts from facts, not from a replay.',
    code: { observation: { goal: 'torch × 4', inventory: {}, nearby_blocks: { oak_log: 12, stone: 19, coal_ore: 5 } } }
  },
  {
    kicker: 'WORLD MODEL',
    title: 'Turn a mechanic into a promise.',
    copy: 'Claude writes one STRIPS-style rule per action from what it knows of vanilla Minecraft. Each starts as a hypothesis. Replaying the logged steps certifies it after two confirmations from two episodes with no contradiction.',
    code: { rule: { id: 'mine_coal_ore', preconditions: { tool_any: ['wooden_pickaxe', 'stone_pickaxe'] }, effects: { block_removed: true, drops: { coal: 1 } }, status: 'hypothesis' } }
  },
  {
    kicker: 'PLANNER',
    title: 'Chain the rules back from the goal.',
    copy: 'The planner sees only the rules in the store, never the game\'s recipe or loot tables. A wrong rule makes a wrong plan, and the step check catches it. Rules that a skill cites are tried first.',
    code: { plan: ['mine oak_log with hand', 'craft oak_planks', 'craft stick', '…', 'craft wooden_pickaxe', 'mine coal_ore with wooden_pickaxe', 'craft torch'] }
  },
  {
    kicker: 'VERIFIED EXECUTION',
    title: 'Check what the server decided.',
    copy: 'Each step is compared with its rule\'s exact prediction: whether the block went, which items dropped, what a craft changed. Pickup and pathing are tolerant. A mismatch demotes the rule in this world only, flags every skill that cites it, and asks Claude for ranked alternatives that must explain the counterexample.',
    code: { step: { action: 'mine coal_ore with wooden_pickaxe', predicted: { drops: { coal: 1 } }, observed: { drops: {} }, check: 'mismatch' } }
  },
  {
    kicker: 'SKILL COMPILER',
    title: 'Keep the procedure, cite its promises.',
    copy: 'A successful training episode becomes a skill tagged with the rule ids it used. When one of those rules is demoted, the skill is flagged instead of replayed blindly.',
    code: { skill: { id: 'skill_torch', rule_ids: ['mine_oak_log', 'craft_oak_planks', '…', 'mine_coal_ore', 'craft_torch'], status: 'ok' } }
  }
];

// --- The loop ----------------------------------------------------------------

function showLoopStep(index) {
  const step = loopSteps[index];
  document.querySelectorAll('.loop-step').forEach((item, i) => {
    item.classList.toggle('active', i === index);
    item.setAttribute('aria-selected', i === index ? 'true' : 'false');
  });
  byId('loop-kicker').textContent = step.kicker;
  byId('loop-title').textContent = step.title;
  byId('loop-copy').textContent = step.copy;
  byId('loop-code').innerHTML = highlight(step.code);
  byId('loop-progress').style.width = `${(index + 1) * 20}%`;
}

// --- The real episode: hero console and replay --------------------------------

// Collapses an episode trace into the moments worth narrating: runs of steps
// that went as predicted become one line; surprises, store changes, Claude's
// revisions and the result each get their own.
function keyMoments(events) {
  const moments = [];
  let asPredicted = [];
  const flush = () => {
    if (!asPredicted.length) return;
    const first = asPredicted[0];
    const last = asPredicted[asPredicted.length - 1];
    moments.push({
      n: pad(first.index),
      text: asPredicted.length === 1 ? last.action : `${asPredicted.length} steps, to ${last.action}`,
      note: 'as predicted',
      tone: ''
    });
    asPredicted = [];
  };
  for (const event of events) {
    if (event.kind === 'step' && event.ok && event.check !== 'mismatch' && !isRevision(event.rule)) {
      asPredicted.push(event);
      continue;
    }
    const moment = describeMoment(event);
    if (!moment) continue;
    flush();
    moments.push(moment);
  }
  flush();
  return moments;
}

function describeMoment(event) {
  switch (event.kind) {
    case 'plan':
      return { n: '··', text: `plan: ${event.steps.length} steps over rules`, note: 'planned', tone: '', plan: true };
    case 'step':
      if (!event.ok) return { n: pad(event.index), text: event.action, note: event.error, tone: 'bad' };
      if (event.check === 'mismatch') return { n: pad(event.index), text: event.action, note: 'mismatch', tone: 'bad' };
      return { n: pad(event.index), text: `${event.action}, ${formatCounts(event.observed.drops || {})}`, note: 'hypothesis confirmed', tone: '' };
    case 'contradiction':
      return { n: '··', text: `demote ${event.rule}${event.flagged.length ? ` · flag ${event.flagged.join(', ')}` : ''}`, note: 'store', tone: 'bad' };
    case 'revision': {
      const best = event.hypotheses.find((h) => h.kept);
      return { n: '··', text: `Claude: ${best ? ruleLabel(best.rule) : 'no usable revision'}`, note: `${formatNumber(event.tokens)} tok`, tone: 'llm', tokens: event.tokens };
    }
    case 'episode_end':
      return { n: '··', text: `${event.actions} actions, ${event.llm_calls} Claude call${event.llm_calls === 1 ? '' : 's'}`, note: event.success ? 'goal met' : 'failed', tone: event.success ? '' : 'bad' };
    default:
      return null;
  }
}

function renderConsole(trace) {
  const moments = keyMoments(trace.events).filter((moment) => !moment.plan);
  byId('console-trace').innerHTML = moments.map((m) => `<div class="trace-line"><span>${esc(m.n)}</span><b>${esc(m.text)}</b><i class="${m.tone}">${esc(m.note)}</i></div>`).join('');
  const end = trace.events.find((event) => event.kind === 'episode_end');
  if (end) byId('console-cost').innerHTML = `${end.llm_calls} · ${formatNumber(end.llm_tokens)} <small>tokens</small>`;
  byId('console-title').textContent = `RUN / H3 · LAYOUT ${trace.layout}`;
  byId('console-source').textContent = `results / episodes / D_h3_L${trace.layout}`;
}

let replayTimer;

function renderReplay(moments, shown) {
  byId('replay-track').innerHTML = moments.map((m, i) => `
    <div class="replay-item${i < shown ? ' active' : ''}"><span>${esc(m.n)}</span><b>${esc(m.text)}</b><i class="${i < shown ? m.tone : ''}">${i < shown ? esc(m.note) : 'waiting'}</i></div>`).join('');
  const tokens = moments.slice(0, shown).reduce((sum, m) => sum + (m.tokens || 0), 0);
  byId('replay-cost').textContent = `${formatNumber(tokens)} tokens`;
}

function startReplay(moments, end) {
  clearInterval(replayTimer);
  let shown = 0;
  byId('replay-state').textContent = 'RUNNING';
  byId('replay-message').textContent = 'Replaying the logged episode…';
  renderReplay(moments, shown);
  replayTimer = setInterval(() => {
    shown += 1;
    renderReplay(moments, shown);
    if (shown < moments.length) return;
    clearInterval(replayTimer);
    byId('replay-state').textContent = end && end.success ? 'SUCCESS' : 'FAIL';
    byId('replay-message').textContent = 'One revision call, then the hypothesis held.';
  }, 700);
}

// --- Rule and skill cards ----------------------------------------------------

function renderCards(rule, skill) {
  if (rule) {
    byId('rule-id').textContent = rule.id;
    byId('rule-status').textContent = rule.status.toUpperCase();
    byId('rule-body').innerHTML = highlight({ preconditions: rule.preconditions, effects: rule.effects, source: rule.source, scope: rule.scope });
    byId('rule-evidence').textContent = `${rule.confirmations} confirmations / ${rule.episodes} episodes`;
  }
  if (skill) {
    byId('skill-id').textContent = skill.id;
    byId('skill-status').textContent = skill.status.toUpperCase();
    byId('skill-status').classList.toggle('flagged', skill.status === 'flagged');
    byId('skill-body').innerHTML = highlight({ rule_ids: skill.rule_ids, status: skill.status, flagged_by: skill.flagged_by });
    byId('skill-evidence').textContent = skill.evidence.join(', ');
  }
}

// --- Scoreboard --------------------------------------------------------------

function holdoutRows() {
  const rows = results ? results.rows.filter((row) => row.task === 'h3') : [];
  return Object.fromEntries(rows.map((row) => [row.system, row]));
}

function showSystem(system, rows) {
  document.querySelectorAll('.score-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.system === system));
  document.querySelectorAll('.token-bar').forEach((bar) => bar.classList.toggle('dim', bar.dataset.system !== system));
  const row = rows[system];
  if (!row) return;
  byId('score-rate').textContent = Math.round((100 * row.successes) / row.episodes);
  byId('score-rate-note').textContent = `% holdout success · ${row.successes} of ${row.episodes} layouts`;
  byId('score-tokens').textContent = formatStat(row.llm_tokens);
  byId('score-calls').textContent = formatStat(row.llm_calls);
  byId('score-repeats').textContent = formatStat(row.repeated_failures);
  byId('score-after').textContent = formatStat(row.actions_after_contradiction);
  const d = rows.D && rows.D.llm_tokens.mean;
  byId('score-tokens-note').textContent = system === 'D' || !d ? 'input + output, from API usage' : `${(row.llm_tokens.mean / d).toFixed(1)}× D's tokens`;
  byId('score-calls-note').textContent = system === 'D' ? 'only to revise a broken rule' : 'every plan and replan';
}

function renderTokenBars(rows) {
  const systems = ['D', 'B', 'B+'].filter((system) => rows[system]);
  const max = Math.max(...systems.map((system) => rows[system].llm_tokens.mean));
  byId('token-bars').innerHTML = systems.map((system) => {
    const mean = rows[system].llm_tokens.mean;
    return `
      <div class="bar-label"><span>${esc(system)} · LLM tokens per holdout episode</span><b>${formatNumber(Math.round(mean))}</b></div>
      <div class="bar-track"><span class="bar token-bar ${system === 'D' ? 'hybrid-bar' : 'baseline-bar'}" data-system="${esc(system)}" style="width: ${(100 * mean) / max}%"></span></div>`;
  }).join('');
}

function renderSource(rows) {
  const n = rows.D ? rows.D.episodes : 0;
  byId('score-source').textContent = `Real server · H3 on ${n} layouts per system · run ${results.run} · commit ${results.commit}. ` +
    'After a failed plan, B and B+ get the same "rules may differ from vanilla" sentence as D\'s revision prompt. ' +
    'D\'s one-time prior-rule call before training is not charged to any episode.' +
    (results.note ? ` ${results.note}` : '');
  byId('closing-run').textContent = `BUILD 02 · RUN ${results.run.toUpperCase()} · ${results.exported}`;
}

// --- Formatting --------------------------------------------------------------

// JSON-like markup for the code panels; short flat objects and lists stay on one line.
function highlight(value, indent = '') {
  if (typeof value === 'string') return `<span class="code-string">"${esc(value)}"</span>`;
  if (value === null || typeof value !== 'object') return esc(String(value));
  const inner = `${indent}  `;
  const isList = Array.isArray(value);
  const entries = isList ? value.map((item) => [null, item]) : Object.entries(value);
  if (!entries.length) return isList ? '[]' : '{}';
  const flat = entries.length <= 3 && entries.every(([, item]) => item === null || typeof item !== 'object');
  const parts = entries.map(([key, item]) => (key === null ? '' : `<span class="code-key">${esc(key)}</span>: `) + highlight(item, inner));
  const [open, close] = isList ? ['[', ']'] : ['{', '}'];
  if (flat) return `${open}${parts.join(', ')}${close}`;
  return `${open}\n${parts.map((part) => inner + part).join(',\n')}\n${indent}${close}`;
}

function ruleLabel(rule) {
  if (rule.action.type === 'mine') {
    const tools = rule.preconditions.tool_any || [];
    return `${tools.length ? tools.join(' / ') : 'any tool or bare hand'} → ${formatCounts(rule.effects.drops)}`;
  }
  return `craft ${rule.action.item} → ${formatCounts(rule.effects.gains)}`;
}

function isRevision(ruleId) {
  return Boolean(ruleId && ruleId.includes('.rev'));
}

function formatStat(stat) {
  if (!stat) return '–';
  const big = stat.mean >= 100;
  const mean = big ? formatNumber(Math.round(stat.mean)) : stat.mean.toFixed(1);
  if (stat.sd === null) return mean;
  return `${mean} ± ${big ? Math.round(stat.sd) : stat.sd.toFixed(1)}`;
}

function formatCounts(counts) {
  const entries = Object.entries(counts || {});
  return entries.length ? entries.map(([item, count]) => `${item} ×${count}`).join(', ') : 'nothing';
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString('en-US');
}

function pad(value) {
  return String(value).padStart(2, '0');
}

function esc(value) {
  const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(value ?? '').replace(/[&<>"']/g, (char) => entities[char]);
}

// --- Wiring ------------------------------------------------------------------

document.querySelectorAll('.loop-step').forEach((button, index) => button.addEventListener('click', () => showLoopStep(index)));
showLoopStep(0);

if (results) {
  const rows = holdoutRows();
  renderTokenBars(rows);
  renderSource(rows);
  document.querySelectorAll('.score-tab').forEach((tab) => tab.addEventListener('click', () => showSystem(tab.dataset.system, rows)));
  showSystem('D', rows);
  renderCards(results.rule, results.skill);
  if (results.trace) {
    const moments = keyMoments(results.trace.events);
    const end = results.trace.events.find((event) => event.kind === 'episode_end');
    renderConsole(results.trace);
    renderReplay(moments, 0);
    byId('replay-title').textContent = `H3 / LAYOUT ${results.trace.layout} / D`;
    byId('trace-link').href = `trace.html?file=results/episodes/D_h3_L${results.trace.layout}.jsonl`;
    byId('replay-button').addEventListener('click', () => startReplay(moments, end));
  }
}

const observer = new IntersectionObserver((entries) => {
  entries.forEach((entry) => { if (entry.isIntersecting) entry.target.classList.add('is-visible'); });
}, { threshold: .1 });
document.querySelectorAll('.reveal').forEach((element) => observer.observe(element));
