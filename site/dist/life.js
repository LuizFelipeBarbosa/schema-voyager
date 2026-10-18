// Life view of the trace panel (?run=<run>): the goal, the plan with the
// step running now, the hypothesis it tests, the revision moment, and the
// strip over the whole life. Pure functions of trace events and life.jsonl
// rows; trace.js loads the data and calls them. Shares trace.js's globals
// (say, esc, formatCounts, ...), so load this file first.

const VERDICT_TEXT = {
  match: '✓ MATCH',
  mismatch: '✕ MISMATCH',
  failed: 'FAILED',
  quarantined: '⚠ QUARANTINED',
  stochastic: '~ RANDOM DROP',
  done: 'done'
};
const CHANGE_NOTE = "the world's rules changed here (hidden from the agent)";
const RECENT_CERTIFIED = 5;
const RECENT_GOALS = 10; // the strip keeps this many goals (and the shown one) readable; older ones shrink to a dot

// --- State of one goal ---------------------------------------------------------

// What the goal's events say up to now. Traces from before v2.3 have no
// step_start and no plan fields: they get no running step, and the rule id
// stands in for the hypothesis.
function goalStory(events) {
  const story = {
    goal: null,
    end: null,
    lastT: events.length ? events[events.length - 1].t : 0,
    rows: [],
    running: null, // step_start of the step in flight, or null
    focus: null, // what the hypothesis card shows: { phase, hypothesis, step }
    mismatch: null,
    hypotheses: null,
    recovered: null, // the first matching step of the revised rule
    certified: null, // rules of the revised family certified after the recovery
    certifiedRules: [] // every rule this goal certified
  };
  const steps = [];
  const starts = new Map(); // step index -> its step_start
  let plan = null;
  let stepsSincePlan = [];
  for (const event of events) {
    switch (event.kind) {
      case 'goal': story.goal = event; break;
      case 'plan': plan = event; stepsSincePlan = []; break;
      case 'step_start': starts.set(event.index, event); break;
      case 'step':
        steps.push(event);
        stepsSincePlan.push(event);
        if (story.mismatch && !story.recovered && event.check === 'match' && isRevision(event.rule)) story.recovered = event;
        break;
      case 'contradiction':
        Object.assign(story, { mismatch: event, hypotheses: null, recovered: null, certified: null });
        break;
      case 'revision': story.hypotheses = event.hypotheses; break;
      case 'certified': {
        story.certifiedRules.push(...event.rules);
        const family = story.mismatch && ruleFamily(story.mismatch.rule);
        const revised = event.rules.filter((rule) => rule === family || ruleFamily(rule) === family);
        if (story.recovered && revised.length) story.certified = revised;
        break;
      }
      case 'episode_end': story.end = event; break;
    }
  }

  const lastStep = steps[steps.length - 1];
  const lastStart = [...starts.values()].pop();
  story.running = lastStart && !steps.some((step) => step.index === lastStart.index) ? lastStart : null;

  const remaining = remainingPlanSteps(plan, stepsSincePlan, story.running);
  story.rows = [
    ...steps.map((step) => ({ state: 'done', action: step.action, verdict: verdictOf(step), step, start: starts.get(step.index) })),
    ...(story.running ? [{ state: 'running', action: story.running.action, start: story.running }] : []),
    ...remaining.map((step) => ({ state: 'todo', action: step.action, start: step }))
  ];

  if (story.running) story.focus = { phase: 'testing', hypothesis: story.running, step: null };
  else if (lastStep) story.focus = { phase: 'result', hypothesis: { rule: lastStep.rule, action: lastStep.action, ...starts.get(lastStep.index) }, step: lastStep };
  else if (remaining.length) story.focus = { phase: 'next', hypothesis: remaining[0], step: null };
  return story;
}

// The latest plan lists what is left, but older traces keep the whole plan:
// drop the leading steps that ran since it was made, matching by action.
function remainingPlanSteps(plan, stepsSincePlan, running) {
  if (!plan) return [];
  const ran = [...stepsSincePlan.filter((step) => step.ok), ...(running ? [running] : [])];
  let cursor = 0;
  for (const step of ran) {
    if (plan.steps[cursor] && plan.steps[cursor].action === step.action) cursor += 1;
  }
  return plan.steps.slice(cursor);
}

function verdictOf(step) {
  if (!step.ok) return 'failed';
  return step.check || 'done';
}

// `mine_coal_ore.rev1@life-1001-20261017-1803` -> `mine_coal_ore`
function ruleFamily(ruleId) {
  return String(ruleId || '').replace(/@.*$/, '').replace(/\.(rev|fix)\d+$/, '');
}

// --- State of the whole life ---------------------------------------------------

// `lifeRows` and `changes` are the run's life.jsonl and changes.jsonl;
// `story` is the goal on screen. Totals count what had happened by `story.lastT`.
function lifeOverview(lifeRows, changes, story, episodes) {
  const shown = story.goal;
  const finished = lifeRows.filter((row) => row.ended <= story.lastT || (shown && row.index === shown.index));
  const chips = lifeRows.map((row) => ({
    index: row.index,
    label: `${row.count} ${row.goal}`,
    outcome: row.outcome,
    started: row.started,
    later: row.started > story.lastT,
    shown: Boolean(shown) && row.index === shown.index
  }));
  if (shown && !lifeRows.some((row) => row.index === shown.index)) {
    chips.push({
      index: shown.index,
      label: `${shown.count} ${shown.item}`,
      outcome: story.end ? (story.end.success ? 'success' : 'failed') : 'running',
      started: shown.t,
      later: false,
      shown: true
    });
  }
  for (const chip of chips) {
    const episode = episodes.find((candidate) => candidate.name.startsWith(`D_life_g${String(chip.index).padStart(3, '0')}_`));
    chip.path = episode ? episode.path : null;
  }

  const t0 = chips.length ? chips[0].started : 0;
  const timeline = [];
  let previousMinute = -1;
  const pending = [...changes].sort((a, b) => a.t - b.t);
  for (const chip of chips) {
    while (pending.length && pending[0].t < chip.started) timeline.push({ type: 'change', change: pending.shift() });
    const minute = Math.max(0, Math.floor((chip.started - t0) / 60));
    timeline.push({ type: 'chip', chip, minute: minute !== previousMinute ? minute : null });
    previousMinute = minute;
  }
  for (const change of pending) timeline.push({ type: 'change', change });

  // A running goal's certification is logged only once it has ended, so add it here.
  const certifiedNames = finished.flatMap((row) => row.certified_now);
  const runningGoal = shown && !lifeRows.some((row) => row.index === shown.index);
  const extra = (runningGoal ? story.certifiedRules : []).filter((rule) => !certifiedNames.includes(rule));
  const last = finished[finished.length - 1];
  return {
    timeline,
    certifiedCount: (last ? last.certified_total : 0) + extra.length,
    certifiedNames: [...certifiedNames, ...extra],
    calls: finished.reduce((sum, row) => sum + row.llm_calls, 0),
    tokens: finished.reduce((sum, row) => sum + row.tokens, 0),
    spend: finished.reduce((most, row) => Math.max(most, row.spend_usd), 0)
  };
}

// --- The latest world change -----------------------------------------------------

// How one entry of changes.jsonl played out, from the events of every goal since:
// where the agent caught it, what Claude proposed and how many calls that took, the
// first match of the revised rule and its certification (each possibly goals later).
// `goals` is [{ index, events }]; events after `now` have not happened yet.
function changeStory(change, goals, now) {
  const family = `${change.action}_${change.subject}`;
  const story = { change, caught: null, top: null, calls: 0, tokens: 0, recovered: null, certified: null };
  const ordered = [...goals].sort((a, b) => a.index - b.index);
  for (const goal of ordered) {
    for (const event of goal.events) {
      if (event.t < change.t || event.t > now) continue;
      const inFamily = ruleFamily(event.rule) === family;
      if (event.kind === 'contradiction' && !story.caught && inFamily) {
        story.caught = {
          goal: goal.index,
          step: Number(String(event.transition).split('/').pop()),
          expected: event.predicted ? effectsLine(decisiveEffect(event.predicted)) : event.detail,
          got: event.observed ? effectsLine(decisiveEffect(event.observed)) : 'nothing'
        };
      } else if (event.kind === 'revision' && story.caught && inFamily) {
        story.calls += 1;
        story.tokens += event.tokens || 0;
        story.top = story.top || event.hypotheses.find((hypothesis) => hypothesis.kept) || event.hypotheses[0] || null;
      } else if (event.kind === 'step' && story.caught && !story.recovered && inFamily && isRevision(event.rule) && event.check === 'match') {
        story.recovered = { goal: goal.index, action: event.action };
      } else if (event.kind === 'certified' && story.recovered && !story.certified) {
        const revised = event.rules.filter((rule) => isRevision(rule) && ruleFamily(rule) === family);
        if (revised.length) story.certified = { goal: goal.index, rules: revised };
      }
    }
  }
  return story;
}

// The newest change in full, the earlier ones as one line each. Empty before the first change.
function worldChangesHtml(stories) {
  if (!stories.length) return '';
  const latest = stories[stories.length - 1];
  const caught = latest.caught;
  const stages = [
    ['CAUGHT', caught && `goal ${caught.goal} · step ${caught.step} · expected <span>${say(caught.expected)}</span>, got <span class="differs">${say(caught.got)}</span>`, 'the agent has not run into it yet'],
    ['CLAUDE', latest.top && `${say(latest.top.summary || ruleLabel(latest.top.rule))} <small>${plural(latest.calls, 'call')} · ${formatNumber(latest.tokens)} tokens</small>`, 'no revision yet'],
    ['RECOVERED', latest.recovered && `goal ${latest.recovered.goal} · ${say(latest.recovered.action)} matched the revised rule`, 'the revised rule has not matched yet'],
    ['CERTIFIED', latest.certified && `goal ${latest.certified.goal} · ${say(latest.certified.rules.join(', '))}`, 'not certified yet']
  ];
  const rows = stages.map(([name, text, waiting]) => `
    <li class="${text ? 'done' : 'todo'}"><b>${name}</b><span>${text || waiting}</span></li>`);
  const earlier = stories.slice(0, -1).reverse().map((story) => `
    <li title="${esc(describeWorldChange(story.change))}">
      <span>⚡ ${esc(describeWorldChange(story.change))}</span>
      <span class="stages">${['caught', 'recovered', 'certified'].map((name) => `<i class="${story[name] ? 'done' : ''}">${story[name] ? `✓ ${name} g${story[name].goal}` : `… ${name}`}</i>`).join('')}</span>
    </li>`);
  return `
    <section class="change-card">
      <div class="change-head"><b>⚡ LATEST WORLD CHANGE</b><span>hidden from the agent</span></div>
      <div class="change-what">${esc(describeWorldChange(latest.change))}</div>
      <ol class="change-stages">${rows.join('')}</ol>
      ${earlier.length ? `<ul class="change-earlier">${earlier.join('')}</ul>` : ''}
    </section>`;
}

// --- Rendering -----------------------------------------------------------------

// `outcome` is the goal's row in life.jsonl once it has ended, else null.
function goalCardHtml(story, outcome) {
  const goal = story.goal;
  if (!goal) return '<p class="waiting">Waiting for the first goal…</p>';
  const success = outcome ? outcome.outcome === 'success' : story.end && story.end.success;
  const exploring = goal.item === 'explore';
  const status = !story.end && !outcome
    ? ['running', 'RUNNING']
    : (exploring && success ? ['neutral', 'EXPLORED'] : success ? ['success', 'SUCCESS'] : ['fail', outcome && outcome.outcome === 'interrupted' ? 'INTERRUPTED' : 'FAILED']);
  const endT = story.end ? story.end.t : null;
  const actions = story.end ? story.end.actions : (outcome ? outcome.actions : null);
  return `
    <div class="goal-top">
      <span class="goal-no">GOAL ${goal.index}</span>
      <span class="status ${status[0]}">${status[1]}${actions === null ? '' : ` · ${plural(actions, 'action')}`}</span>
      <span class="elapsed" id="life-elapsed" data-start="${goal.t}" data-end="${endT ?? ''}">${formatElapsed((endT ?? story.lastT) - goal.t)}</span>
    </div>
    <div class="goal-title">${exploring ? 'Explore' : `Get ${goal.count} ${say(goal.item)}`}</div>
    <div class="goal-why"><b>WHY</b>${say(goal.reason)}</div>`;
}

function lifePlanHtml(story) {
  if (!story.rows.length) return `${cardHead('PLAN')}<p class="empty">No plan yet.</p>`;
  const items = story.rows.map((row, i) => {
    const tag = row.state === 'running' ? '▶ RUNNING' : row.state === 'done' ? VERDICT_TEXT[row.verdict] : '';
    const predicts = row.state !== 'done' && row.start && row.start.predicts ? `<small>${say(row.start.predicts)}</small>` : '';
    return `
      <li class="row ${row.state} ${row.verdict || ''}">
        <span class="n">${i + 1}</span>
        <span class="act">${say(row.action)}${predicts}</span>
        <span class="tag">${tag}</span>
      </li>`;
  });
  return `${cardHead('PLAN', plural(story.rows.length, 'step'))}<ol class="plan-rows">${items.join('')}</ol>`;
}

function hypothesisHtml(story) {
  const focus = story.focus;
  if (!focus) return `${cardHead('HYPOTHESIS')}<p class="empty">Nothing under test yet.</p>`;
  const hypothesis = focus.hypothesis;
  const step = focus.step;
  const label = { testing: 'TESTING NOW', result: 'LAST TEST', next: 'NEXT TO TEST' }[focus.phase];
  const prediction = hypothesis.predicts
    ? hypothesis.predicts.split('->').pop().trim()
    : (step && step.predicted ? effectsLine(decisiveEffect(step.predicted)) : '');
  const confirmations = confirmationText(hypothesis);
  const meta = [
    hypothesis.status ? `<span class="pill ${esc(hypothesis.status)}">${esc(hypothesis.status)}</span>` : '',
    confirmations ? `<span class="count">${esc(confirmations)}</span>` : '',
    hypothesis.experimental ? '<span class="pill experiment">experiment</span>' : '',
    hypothesis.rule ? `<code>${say(hypothesis.rule)}</code>` : ''
  ].join('');
  const observed = step && step.ok ? (step.observed || (step.action_obj && observedEffects(step))) : null;
  const predicted = step && step.predicted && step.check ? step.predicted : null;
  const outcome = step ? `
    <div class="hyp-result ${verdictOf(step)}">
      <b>${VERDICT_TEXT[verdictOf(step)]}</b>
      ${predicted ? `<div>predicted <span>${say(effectsLine(decisiveEffect(predicted)))}</span></div>` : ''}
      ${observed ? `<div>observed <span class="${predicted && JSON.stringify(predicted) !== JSON.stringify(observed) ? 'differs' : ''}">${say(effectsLine(decisiveEffect(observed)))}</span></div>` : ''}
      ${!step.ok ? `<div>${esc(step.error)}</div>` : ''}
    </div>` : '';
  return `
    <div class="hyp ${focus.phase}">
      <div class="hyp-label">${label}</div>
      <div class="hyp-claim">${say(hypothesis.action || hypothesis.rule || 'no rule recorded')}</div>
      ${prediction ? `<div class="hyp-prediction">→ ${say(prediction)}</div>` : ''}
      <div class="hyp-meta">${meta}</div>
      ${outcome}
    </div>`;
}

// Certification needs `needed` confirmations in `goals_needed` distinct goals. A rule that
// is certified says so (its pill); otherwise "1 of 2 confirmations · in 1 of 2 goals".
// Counts past the need are capped, so a hypothesis never reads "6/2 confirmed".
function confirmationText(hypothesis) {
  if (hypothesis.status === 'certified' || !Number.isInteger(hypothesis.confirmed) || !Number.isInteger(hypothesis.needed)) return '';
  const confirmations = `${Math.min(hypothesis.confirmed, hypothesis.needed)} of ${hypothesis.needed} confirmations`;
  if (!Number.isInteger(hypothesis.goals) || !Number.isInteger(hypothesis.goals_needed)) return confirmations;
  return `${confirmations} · in ${Math.min(hypothesis.goals, hypothesis.goals_needed)} of ${hypothesis.goals_needed} goals`;
}

// One line for an effects object: "drops coal ×1", "inventory +4 stick".
function effectsLine(effects) {
  return Object.entries(effects).map(([key, value]) => `${EFFECT_LABELS[key] || key} ${formatEffect(key, value)}`).join(', ');
}

// The part of an effects object a mismatch is about: what dropped or what the inventory gained.
function decisiveEffect(effects) {
  if ('drops' in effects) return { drops: effects.drops };
  if ('inventory_delta' in effects) return { inventory_delta: effects.inventory_delta };
  return effects;
}

// The mismatch, the ranked revisions, the recovery and the certification, as they arrive.
function revisionHtml(story) {
  const mismatch = story.mismatch;
  if (!mismatch) return '';
  const expected = mismatch.predicted ? effectsLine(decisiveEffect(mismatch.predicted)) : mismatch.detail;
  const got = mismatch.observed ? effectsLine(decisiveEffect(mismatch.observed)) : '';
  const hypotheses = story.hypotheses ? `
    <div class="rev-stage ranked">
      <b>CLAUDE'S REVISIONS, RANKED</b>
      <ol>${story.hypotheses.map((hypothesis) => `
        <li class="${hypothesis.kept ? 'kept' : 'rejected'}">
          <span class="badge">${hypothesis.kept ? 'KEPT' : 'REJECTED'}</span>
          <span>${say(hypothesis.summary || ruleLabel(hypothesis.rule))}</span>
        </li>`).join('')}</ol>
    </div>` : '';
  const recovered = story.recovered ? `
    <div class="rev-stage recovered">
      <b>RECOVERED</b>
      <span>${say(story.recovered.action)} → ${say(effectsLine(decisiveEffect(story.recovered.observed || observedEffects(story.recovered))))} · the revised rule predicted it</span>
    </div>` : '';
  const certified = story.certified ? `
    <div class="rev-stage certified"><b>CERTIFIED</b><span>${say(story.certified.join(', '))}</span></div>` : '';
  return `
    <div class="mismatch-box">
      <div class="mismatch-head">MISMATCH</div>
      <div class="mismatch-body">
        <div>expected <span>${say(expected)}</span></div>
        <div>got <span>${say(got || 'nothing')}</span></div>
        <div class="demoted">${say(mismatch.rule)} demoted in this world</div>
      </div>
    </div>${hypotheses}${recovered}${certified}`;
}

function overviewHtml(overview) {
  const chips = overview.timeline.filter((item) => item.type === 'chip');
  const readableFrom = chips.length - RECENT_GOALS;
  const items = overview.timeline.map((item) => {
    if (item.type === 'change') {
      return `<li class="world-change" title="${esc(describeWorldChange(item.change))}"><b>⚡</b><span>${say(item.change.subject)}</span></li>`;
    }
    const chip = item.chip;
    const compact = chips.indexOf(item) < readableFrom && !chip.shown;
    const classes = ['chip', chip.outcome, compact ? 'compact' : '', chip.shown ? 'shown' : '', chip.later ? 'later' : '', chip.path ? 'link' : ''];
    const attributes = `class="${classes.join(' ')}" ${chip.path ? `data-path="${esc(chip.path)}"` : ''} title="goal ${chip.index}: ${esc(readable(chip.label))} (${esc(chip.outcome)})"`;
    if (compact) return `<li ${attributes}></li>`;
    const minute = item.minute === null ? '' : `<i>${item.minute}m</i>`;
    return `<li ${attributes}>${minute}<b>${chip.index}</b><span>${say(chip.label)}</span></li>`;
  });
  const recent = overview.certifiedNames.slice(-RECENT_CERTIFIED).reverse();
  return `
    <div class="strip-head"><b>THIS LIFE</b><span>${esc(CHANGE_NOTE)} · click a goal to pin it</span></div>
    <ol class="timeline">${items.join('')}</ol>
    <div class="totals">
      <div class="total certified"><span>RULES CERTIFIED</span><strong>${overview.certifiedCount}</strong><small>${recent.length ? recent.map((name) => `+ ${say(name)}`).join('<br>') : 'none yet'}</small></div>
      <div class="total llm"><span>CLAUDE CALLS</span><strong>${overview.calls}</strong><small>${formatNumber(overview.tokens)} tokens</small></div>
      <div class="total llm"><span>SPEND SO FAR</span><strong>$${overview.spend.toFixed(2)}</strong></div>
    </div>`;
}

function formatElapsed(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}
