// Trace panel: replays one episode trace (runs/<run>/episodes/*.jsonl) event
// by event, or follows it while the run is still writing it. Served by
// `schema-voyager panel`; a dropped .jsonl file also works without a server.

const SYSTEM_NAMES = { D: 'SchemaVoyager', B: 'Skill-only baseline', 'B+': 'Baseline with effect feedback' };
// What the holdout world changed, shown to the audience. The agent is never told.
const HIDDEN_RULES = { h3: 'coal ore drops coal only when mined with a stone-tier pickaxe.' };
const EFFECT_LABELS = { block_removed: 'block removed', drops: 'drops', inventory_delta: 'inventory' };
const FOLLOW_NEWEST = 'newest';
const DROPPED = 'dropped';
const FILE_POLL_MS = 700;
const INDEX_POLL_MS = 2000;
const MIN_GAP_S = 0.15;
const MAX_GAP_S = 6; // replay shortens long pauses, such as a Claude call
const LIVE_WINDOW_S = 60; // an unfinished trace written to this recently is live

const params = new URLSearchParams(location.search);
const byId = (id) => document.getElementById(id);

const player = {
  path: null, // the served trace on screen; null for a dropped file
  followNewest: false, // open each new trace as the run starts writing it
  events: [],
  index: -1, // last event shown
  playing: false,
  speed: 1,
  timer: null
};

// --- Loading and following traces --------------------------------------------

function parseTrace(text) {
  const lines = text.split('\n');
  lines.pop(); // empty, or a line the run is still writing
  return lines.filter((line) => line.trim()).map((line) => JSON.parse(line));
}

async function openPath(path, autoplay) {
  pause();
  player.path = path;
  player.events = [];
  player.index = -1;
  updateUrl();
  syncPicker();
  await pollFile();
  seek(0);
  if (autoplay) play();
}

function openDropped(name, text) {
  pause();
  player.path = null;
  player.followNewest = false;
  player.events = parseTrace(text.endsWith('\n') ? text : `${text}\n`);
  const picker = byId('picker');
  picker.querySelector(`option[value="${DROPPED}"]`)?.remove();
  picker.insertAdjacentHTML('afterbegin', `<option value="${DROPPED}">${esc(name)} (dropped)</option>`);
  picker.value = DROPPED;
  updateUrl();
  seek(0);
}

async function pollFile() {
  const path = player.path;
  if (!path) return;
  let text;
  try {
    const response = await fetch(`/${path}`, { cache: 'no-store' });
    if (!response.ok) return; // the run has not created it yet
    text = await response.text();
  } catch {
    return;
  }
  if (path !== player.path) return; // another trace was opened meanwhile
  const events = parseTrace(text);
  if (events.length === player.events.length) return;
  // D logs certification just after the episode ends; keep showing the newest event.
  const stayAtEnd = player.index === player.events.length - 1 && hasEnded(player.events);
  if (events.length < player.events.length) player.index = -1; // a rerun overwrote the file
  player.events = events;
  if (stayAtEnd) player.index = events.length - 1;
  render();
}

async function pollIndex() {
  let episodes;
  try {
    const response = await fetch('/episodes.json', { cache: 'no-store' });
    if (!response.ok) return null;
    episodes = await response.json();
  } catch {
    return null; // no panel server: only dropped files
  }
  renderPicker(episodes);
  const newest = episodes[0];
  if (player.followNewest && newest && newest.path !== player.path) openPath(newest.path, true);
  return episodes;
}

function updateUrl() {
  const next = new URLSearchParams(location.search);
  next.delete('file');
  next.delete('follow');
  if (player.followNewest) next.set('follow', FOLLOW_NEWEST);
  else if (player.path) next.set('file', player.path);
  history.replaceState(null, '', `?${next}`);
}

function hasEnded(events) {
  return events.some((event) => event.kind === 'episode_end');
}

function isLive() {
  const last = player.events[player.events.length - 1];
  return Boolean(player.path && last && !hasEnded(player.events) && Date.now() / 1000 - last.t < LIVE_WINDOW_S);
}

// --- Playback ----------------------------------------------------------------

function play() {
  if (player.index >= player.events.length - 1 && hasEnded(player.events)) seek(0); // replay from the top
  player.playing = true;
  scheduleNext();
  renderControls();
}

function pause() {
  player.playing = false;
  clearTimeout(player.timer);
  renderControls();
}

// Steps through a finished trace with its recorded spacing, shortened when
// long. A live trace shows each event as soon as the run has written it.
function scheduleNext() {
  clearTimeout(player.timer);
  if (!player.playing) return;
  const current = player.events[player.index];
  const next = player.events[player.index + 1];
  if (!next) {
    if (hasEnded(player.events)) pause();
    else player.timer = setTimeout(scheduleNext, FILE_POLL_MS);
    return;
  }
  const live = isLive();
  const gap = current && !live ? Math.min(Math.max(next.t - current.t, MIN_GAP_S), MAX_GAP_S) : MIN_GAP_S;
  player.timer = setTimeout(() => {
    seek(player.index + 1);
    scheduleNext();
  }, (gap * 1000) / (live ? 1 : player.speed));
}

function seek(index) {
  player.index = player.events.length ? Math.max(0, Math.min(index, player.events.length - 1)) : -1;
  render();
}

function control(action) {
  if (action === 'play') {
    if (player.playing) pause();
    else play();
    return;
  }
  if (action === 'end') {
    seek(player.events.length - 1); // keeps playing, so a live trace stays live
    return;
  }
  pause();
  if (action === 'start') seek(0);
  if (action === 'prev') seek(player.index - 1);
  if (action === 'next') seek(player.index + 1);
}

// --- Episode state at the current event --------------------------------------

function stateAt(events, index) {
  const state = {
    start: null,
    end: null,
    plan: null,
    plans: 0,
    replanReason: null,
    step: null,
    changes: [], // schema store changes, D only
    feedback: null, // what B was told after its last plan, B only
    tokens: 0,
    calls: 0,
    contradictions: 0
  };
  for (const event of events.slice(0, index + 1)) applyEvent(state, event);
  return state;
}

function applyEvent(state, event) {
  switch (event.kind) {
    case 'episode_start':
      state.start = event;
      break;
    case 'plan':
      state.plans += 1;
      state.plan = {
        title: `PLAN ${state.plans} · OVER RULES`,
        note: state.replanReason || (event.skills.length ? `reuses ${event.skills.join(', ')}` : ''),
        summary: null,
        steps: event.steps.map((step) => ({ ...step, status: 'todo' })),
        cursor: 0
      };
      state.replanReason = null;
      break;
    case 'llm_plan':
      state.plans += 1;
      state.tokens += event.tokens;
      state.calls += 1;
      state.plan = {
        title: `CLAUDE PLAN · ATTEMPT ${event.attempt}`,
        note: `${formatNumber(event.tokens)} tokens`,
        summary: event.summary,
        steps: event.actions.map((action) => ({ action, status: 'todo' })),
        cursor: 0
      };
      break;
    case 'step':
      state.step = event;
      advancePlan(state.plan, event);
      if (event.check === 'match' && isRevision(event.rule)) {
        state.changes.push({ tone: 'good', title: `${event.rule} confirmed at #${pad(event.index)}`, detail: 'The revised rule predicted what the server did.' });
      }
      break;
    case 'contradiction':
      state.contradictions += 1;
      state.replanReason = `replanned after the mismatch at #${pad(state.step ? state.step.index : 0)}`;
      state.changes.push({ tone: 'bad', title: `${event.rule} demoted in this world`, detail: event.detail });
      for (const skill of event.flagged) {
        state.changes.push({ tone: 'warn', title: `${skill} flagged`, detail: `It cites ${event.rule}.` });
      }
      break;
    case 'revision':
      state.tokens += event.tokens;
      state.calls += 1;
      state.changes.push({
        tone: 'llm',
        title: `Claude proposed ${event.hypotheses.length} ranked revisions`,
        detail: `${formatNumber(event.tokens)} tokens. Each must explain the counterexample.`,
        hypotheses: event.hypotheses
      });
      break;
    case 'feedback':
      state.feedback = event.summary;
      break;
    case 'certified':
      if (event.rules.length) state.changes.push({ tone: 'good', title: `Certified ${event.rules.length} rules`, detail: event.rules.join(', ') });
      break;
    case 'skill':
      state.changes.push({ tone: 'info', title: `Compiled ${event.skill}`, detail: `Cites ${event.rule_ids.join(', ')}.` });
      break;
    case 'plan_failed':
      state.changes.push({ tone: 'bad', title: 'No plan', detail: event.reason });
      break;
    case 'episode_end':
      state.end = event;
      state.tokens = event.llm_tokens;
      state.calls = event.llm_calls;
      break;
  }
}

// Plan steps and step events are matched in order by action text. Explores
// that the episode inserts to find an out-of-view target match no plan step.
function advancePlan(plan, step) {
  const planned = plan && plan.steps[plan.cursor];
  if (!planned || planned.action !== step.action) return;
  planned.index = step.index;
  if (!step.ok) {
    planned.status = 'failed'; // may still be retried after an explore
    return;
  }
  if (step.check === 'mismatch') planned.status = 'mismatch';
  else if (!step.check && droppedNothing(observedEffects(step))) planned.status = 'empty'; // ran, but nobody checked
  else planned.status = 'done';
  plan.cursor += 1;
}

function droppedNothing(observed) {
  return Boolean(observed && observed.block_removed && !Object.keys(observed.drops).length);
}

function isRevision(ruleId) {
  return Boolean(ruleId && ruleId.includes('.rev'));
}

// --- Rendering ---------------------------------------------------------------

function render() {
  const state = stateAt(player.events, player.index);
  const system = state.start ? state.start.system : 'D';
  byId('episode').innerHTML = episodeHtml(state);
  byId('counters').innerHTML = countersHtml(state, system);
  byId('plan-card').innerHTML = planHtml(state);
  const stepCard = byId('step-card');
  stepCard.innerHTML = stepHtml(state.step, system);
  stepCard.classList.toggle('mismatch', Boolean(state.step && state.step.check === 'mismatch'));
  byId('store-card').innerHTML = system === 'D' ? storeHtml(state) : feedbackHtml(state, system);
  renderLog();
  renderControls();
  // Keep the current plan step centred and the newest store change readable from its top.
  const currentStep = document.querySelector('.plan-steps li.current');
  if (currentStep) currentStep.parentElement.scrollTop = currentStep.offsetTop - currentStep.parentElement.clientHeight / 2;
  const latestChange = document.querySelector('.change.latest');
  if (latestChange) latestChange.parentElement.scrollTop = latestChange.offsetTop;
}

function episodeHtml(state) {
  const start = state.start;
  if (!start) {
    const waiting = player.followNewest ? 'Waiting for an episode to start…' : 'No episode loaded. Pick a trace above, or drop a .jsonl trace here.';
    return `<p class="waiting">${waiting}</p>`;
  }
  const [statusClass, statusText] = state.end
    ? (state.end.success ? ['success', 'SUCCESS'] : ['fail', 'FAIL'])
    : ['running', isLive() ? '● LIVE' : 'REPLAY'];
  const hidden = HIDDEN_RULES[start.task];
  const end = state.end;
  return `
    <div class="episode-head">
      <span class="system ${start.system === 'D' ? 'system-D' : ''}">${esc(start.system)}</span>
      <div class="episode-title">
        <strong>${esc(SYSTEM_NAMES[start.system] || start.system)}</strong>
        <span>${esc(start.task.toUpperCase())} · layout ${esc(start.layout)} · world ${esc(start.world_id)}</span>
      </div>
      <span class="status ${statusClass}">${statusText}</span>
    </div>
    <p class="goal">${esc(start.description)}</p>
    ${hidden ? `<p class="hidden-rule"><b>HIDDEN RULE</b>In this world, ${esc(hidden)} The agent is not told.</p>` : ''}
    ${end ? `<p class="result">${end.actions} actions · ${plural(end.llm_calls, 'Claude call')} · ${formatNumber(end.llm_tokens)} tokens · ${plural(end.repeated_failures, 'repeated failure')}</p>` : ''}`;
}

function countersHtml(state, system) {
  const actions = state.end ? state.end.actions : (state.step ? state.step.index : 0);
  const budget = state.start ? state.start.budget : 0;
  const caught = system === 'D'
    ? `<strong>${state.contradictions}</strong>`
    : '<strong class="none">no step checks</strong>';
  return `
    <div class="counter"><span>ACTIONS</span><strong>${actions}<small> / ${budget}</small></strong></div>
    <div class="counter llm"><span>CLAUDE CALLS</span><strong>${state.calls}</strong></div>
    <div class="counter llm"><span>LLM TOKENS</span><strong>${formatNumber(state.tokens)}</strong></div>
    <div class="counter ${state.contradictions ? 'bad' : ''}"><span>MISMATCHES CAUGHT</span>${caught}</div>`;
}

function planHtml(state) {
  const plan = state.plan;
  if (!plan) return `${cardHead('PLAN')}<p class="empty">No plan yet.</p>`;
  const currentIndex = state.step ? state.step.index : null;
  const steps = plan.steps.map((step, i) => {
    const classes = [step.status, step.experimental ? 'experimental' : '', step.index === currentIndex ? 'current' : ''];
    const tag = step.experimental ? 'HYPOTHESIS' : (step.status === 'failed' ? 'RETRYING' : '');
    return `<li class="${classes.join(' ')}"><span class="n">${pad(i + 1)}</span><span>${esc(step.action)}</span><span class="tag">${tag}</span></li>`;
  });
  return `
    ${cardHead(plan.title, plan.note)}
    ${plan.summary ? `<p class="plan-summary">${esc(plan.summary)}</p>` : ''}
    <ol class="plan-steps">${steps.join('')}</ol>`;
}

function stepHtml(step, system) {
  if (!step) return `${cardHead('CURRENT STEP')}<p class="empty">No action yet.</p>`;
  const action = `
    <div class="step-action">
      <span class="n">#${pad(step.index)}</span>
      <strong>${esc(step.action)}</strong>
      ${step.rule ? `<span class="rule">rule ${esc(step.rule)}</span>` : ''}
    </div>`;
  if (!step.ok) {
    const missing = step.detail && step.detail.missing ? `<small>missing ${esc(formatCounts(step.detail.missing))}</small>` : '';
    return `${cardHead('CURRENT STEP')}${action}<div class="verdict failed">failed: ${esc(step.error)}${missing}</div>`;
  }
  const observed = step.observed || observedEffects(step);
  if (!observed) return `${cardHead('CURRENT STEP')}${action}<p class="empty">Walked one leg of the search spiral.</p>`;

  const predicted = step.predicted && step.check ? step.predicted : null;
  const predictedColumn = predicted
    ? `<dl>${effectRows(predicted, null)}</dl>`
    : `<p class="no-prediction">None. ${esc(SYSTEM_NAMES[system] || system)} does not predict step effects.</p>`;
  return `
    ${cardHead('CURRENT STEP', step.experimental ? 'testing a hypothesis' : '')}
    ${action}
    <div class="effects">
      <div><span class="label">${predicted ? 'PREDICTED BY THE RULE' : 'PREDICTED'}</span>${predictedColumn}</div>
      <div><span class="label">OBSERVED ON THE SERVER</span><dl>${effectRows(observed, predicted)}</dl></div>
    </div>
    ${verdictHtml(step, observed)}`;
}

function verdictHtml(step, observed) {
  const note = step.check_detail ? `<small>${esc(step.check_detail)}</small>` : '';
  if (step.check === 'mismatch') return `<div class="verdict mismatch">✕ MISMATCH${note}</div>`;
  if (step.check === 'match') return `<div class="verdict match">✓ matches the prediction${note}</div>`;
  if (droppedNothing(observed)) {
    return '<div class="verdict unchecked">dropped nothing<small>Not checked: there was no prediction to compare with.</small></div>';
  }
  return '';
}

function observedEffects(step) {
  const kind = step.action_obj.kind;
  if (kind === 'mine') return { block_removed: Boolean(step.block_removed), drops: step.drops || {} };
  if (kind === 'craft') return { inventory_delta: step.inventory_delta || {} };
  return null;
}

function effectRows(effects, compareTo) {
  return Object.entries(effects).map(([key, value]) => {
    const differs = compareTo && JSON.stringify(compareTo[key]) !== JSON.stringify(value);
    return `<dt>${EFFECT_LABELS[key] || esc(key)}</dt><dd class="${differs ? 'differs' : ''}">${esc(formatEffect(key, value))}</dd>`;
  }).join('');
}

function storeHtml(state) {
  const head = cardHead('SCHEMA STORE', 'changes this episode');
  if (!state.changes.length) return `${head}<p class="empty">No changes yet. Rules that predict correctly stay as they are.</p>`;
  const changes = state.changes.map((change, i) => `
    <li class="change ${change.tone}${i === state.changes.length - 1 ? ' latest' : ''}">
      <strong>${esc(change.title)}</strong>
      ${change.detail ? `<p>${esc(change.detail)}</p>` : ''}
      ${change.hypotheses ? hypothesesHtml(change.hypotheses) : ''}
    </li>`);
  return `${head}<ol class="changes">${changes.join('')}</ol>`;
}

function hypothesesHtml(hypotheses) {
  const items = hypotheses.map((hypothesis) => {
    const why = hypothesis.kept ? hypothesis.rule.rationale : 'Rejected: as written, it predicts the drop that just failed to happen.';
    return `
      <li class="${hypothesis.kept ? '' : 'rejected'}">
        <code>${esc(ruleLabel(hypothesis.rule))}</code><span class="badge">${hypothesis.kept ? 'KEPT' : 'REJECTED'}</span>
        <span class="why">${esc(why)}</span>
      </li>`;
  });
  return `<ol class="hypotheses">${items.join('')}</ol>`;
}

function feedbackHtml(state, system) {
  const head = cardHead('FEEDBACK TO CLAUDE', system === 'B+' ? 'includes each action\'s inventory change' : 'errors and inventory');
  if (!state.feedback) return `${head}<p class="empty">None yet. The baseline hears back only when a plan ends short of the goal.</p>`;
  return `${head}<p class="feedback">${esc(state.feedback)}</p>`;
}

function renderLog() {
  const log = byId('log');
  log.innerHTML = player.events.slice(0, player.index + 1).map((event, i) => {
    const [tone, text] = logLine(event);
    if (text === null) return '';
    return `<li data-index="${i}" class="${tone}${i === player.index ? ' current' : ''}">${esc(text)}</li>`;
  }).join('');
  log.scrollTop = log.scrollHeight;
}

function logLine(event) {
  switch (event.kind) {
    case 'episode_start':
      return ['', `start · ${event.description} · budget ${event.budget}`];
    case 'plan':
      return ['', `plan · ${event.steps.length} steps${event.skills.length ? ` · reuses ${event.skills.join(', ')}` : ''}`];
    case 'llm_plan':
      return ['llm', `Claude plan ${event.attempt} · ${event.actions.length} actions · ${formatNumber(event.tokens)} tokens`];
    case 'step':
      return stepLogLine(event);
    case 'contradiction':
      return ['bad', `contradiction · ${event.rule} demoted here${event.flagged.length ? ` · flagged ${event.flagged.join(', ')}` : ''}`];
    case 'revision':
      return ['llm', `Claude revision · ${event.hypotheses.length} hypotheses · ${formatNumber(event.tokens)} tokens`];
    case 'feedback':
      return ['', 'feedback sent to Claude'];
    case 'certified':
      return event.rules.length ? ['good', `certified · ${event.rules.join(', ')}`] : ['', null];
    case 'skill':
      return ['', `skill · ${event.skill}`];
    case 'plan_failed':
      return ['bad', `no plan · ${event.reason}`];
    case 'episode_end':
      return [event.success ? 'good' : 'bad', `${event.success ? 'SUCCESS' : 'FAIL'} · ${event.actions} actions · ${formatNumber(event.llm_tokens)} tokens`];
    default:
      return ['', event.kind];
  }
}

function stepLogLine(step) {
  const prefix = `#${pad(step.index)} ${step.action} →`;
  if (!step.ok) return ['warn', `${prefix} failed: ${step.error}`];
  if (step.check === 'mismatch') return ['bad', `${prefix} MISMATCH, ${step.check_detail}`];
  const observed = step.observed || observedEffects(step);
  if (!observed) return ['', `${prefix} done`];
  const effect = 'drops' in observed ? `drops ${formatCounts(observed.drops)}` : formatDelta(observed.inventory_delta);
  if (step.check === 'match') return [isRevision(step.rule) ? 'good' : '', `${prefix} ok, ${effect}`];
  return [droppedNothing(observed) ? 'warn' : '', `${prefix} ${effect}`];
}

function renderPicker(episodes) {
  const picker = byId('picker');
  const key = episodes.map((episode) => episode.path).join('|');
  if (key !== picker.dataset.key && document.activeElement !== picker) {
    picker.dataset.key = key;
    const runs = new Map(); // newest run first, since episodes come newest first
    for (const episode of episodes) {
      if (!runs.has(episode.run)) runs.set(episode.run, []);
      runs.get(episode.run).push(episode);
    }
    const groups = [...runs].map(([run, list]) => {
      const options = list
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((episode) => `<option value="${esc(episode.path)}">${esc(episode.name)}</option>`);
      return `<optgroup label="${esc(run)}">${options.join('')}</optgroup>`;
    });
    picker.innerHTML = `<option value="${FOLLOW_NEWEST}">● Follow the newest episode</option>${groups.join('')}`;
  }
  syncPicker();
}

function syncPicker() {
  byId('picker').value = player.followNewest ? FOLLOW_NEWEST : (player.path || DROPPED);
}

function renderControls() {
  const scrub = byId('scrub');
  scrub.max = Math.max(player.events.length - 1, 0);
  scrub.value = Math.max(player.index, 0);
  byId('position').textContent = `${player.index + 1} / ${player.events.length}`;
  byId('play').textContent = player.playing ? '❚❚' : '▶';
}

// --- Formatting --------------------------------------------------------------

function cardHead(title, note) {
  return `<div class="card-head"><b>${esc(title)}</b><span>${esc(note || '')}</span></div>`;
}

function ruleLabel(rule) {
  if (rule.action.type === 'mine') {
    const tools = rule.preconditions.tool_any || [];
    const how = tools.length ? `with ${tools.join(' / ')}` : 'with any tool or bare hand';
    return `mine ${rule.action.block} ${how} → ${formatCounts(rule.effects.drops)}`;
  }
  return `craft ${rule.action.item} → ${formatCounts(rule.effects.gains)}`;
}

function formatEffect(key, value) {
  if (key === 'block_removed') return value ? 'yes' : 'no';
  if (key === 'inventory_delta') return formatDelta(value);
  return formatCounts(value);
}

function formatCounts(counts) {
  const entries = Object.entries(counts || {});
  return entries.length ? entries.map(([item, count]) => `${item} ×${count}`).join(', ') : 'nothing';
}

function formatDelta(delta) {
  const entries = Object.entries(delta || {}).filter(([, count]) => count);
  if (!entries.length) return 'no change';
  return entries.map(([item, count]) => `${count > 0 ? '+' : '−'}${Math.abs(count)} ${item}`).join(', ');
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString('en-US');
}

function plural(count, noun) {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

function pad(value) {
  return String(value).padStart(2, '0');
}

function esc(value) {
  const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(value ?? '').replace(/[&<>"']/g, (char) => entities[char]);
}

// --- Wiring ------------------------------------------------------------------

function wireControls() {
  document.querySelector('.controls').addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    button.blur(); // so the space bar is not also a click on it
    control(button.dataset.action);
  });
  byId('scrub').addEventListener('input', (event) => {
    pause();
    seek(Number(event.target.value));
  });
  byId('speed').addEventListener('change', (event) => {
    player.speed = Number(event.target.value);
    scheduleNext();
  });
  byId('log').addEventListener('click', (event) => {
    const item = event.target.closest('li');
    if (!item) return;
    pause();
    seek(Number(item.dataset.index));
  });
  byId('picker').addEventListener('change', (event) => {
    const value = event.target.value;
    if (value === DROPPED) return;
    player.followNewest = value === FOLLOW_NEWEST;
    if (player.followNewest) {
      updateUrl();
      pollIndex();
    } else {
      openPath(value, false);
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.target.matches('select, input')) return;
    const action = { ' ': 'play', ArrowLeft: 'prev', ArrowRight: 'next', Home: 'start', End: 'end' }[event.key];
    if (!action) return;
    event.preventDefault();
    control(action);
  });
}

function wireDrop() {
  const hint = byId('drop-hint');
  let depth = 0;
  document.addEventListener('dragenter', (event) => {
    event.preventDefault();
    depth += 1;
    hint.hidden = false;
  });
  document.addEventListener('dragleave', () => {
    depth = Math.max(depth - 1, 0);
    if (!depth) hint.hidden = true;
  });
  document.addEventListener('dragover', (event) => event.preventDefault());
  document.addEventListener('drop', async (event) => {
    event.preventDefault();
    depth = 0;
    hint.hidden = true;
    const file = event.dataTransfer.files[0];
    if (file) openDropped(file.name, await file.text());
  });
}

// ?viewer=1 embeds the bot's prismarine viewer (port 3007) beside the panel,
// for recording both in one window; ?viewer=<url> points it elsewhere.
function embedViewer() {
  const setting = params.get('viewer');
  if (!setting) return;
  const viewer = byId('viewer');
  viewer.src = setting === '1' ? `http://${location.hostname}:3007` : setting;
  viewer.hidden = false;
  document.querySelector('.layout').classList.add('with-viewer');
}

async function main() {
  embedViewer();
  wireControls();
  wireDrop();
  player.speed = Number(params.get('speed')) || 1;
  byId('speed').value = String(player.speed);

  const episodes = await pollIndex(); // null without the panel server
  if (!episodes) byId('picker').innerHTML = '<option value="">drop a .jsonl trace here</option>';
  if (params.get('file')) {
    await openPath(params.get('file'), params.has('autoplay'));
  } else if (episodes) {
    player.followNewest = true;
    updateUrl();
    await pollIndex();
  }
  render();
  setInterval(pollFile, FILE_POLL_MS);
  if (episodes) setInterval(pollIndex, INDEX_POLL_MS);
}

main();
