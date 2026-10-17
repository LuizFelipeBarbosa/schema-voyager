const loopSteps = [
  {
    kicker: 'CURRENT STATE',
    title: 'See what the world gives you.',
    copy: 'The environment emits a compact observation: position, nearby blocks, inventory, action budget, and the goal. The agent starts with facts, not a long replay.',
    code: `<span class="code-key">observation</span> = {\n  <span class="code-key">goal</span>: <span class="code-string">"wooden_pickaxe"</span>,\n  <span class="code-key">inventory</span>: {},\n  <span class="code-key">visible_blocks</span>: {<span class="code-string">"log"</span>: 2}\n}`
  },
  {
    kicker: 'WORLD MODEL',
    title: 'Turn a mechanic into a promise.',
    copy: 'The schema store holds rules with preconditions, predicted effects, and evidence. A rule can guide planning only after an episode has verified it.',
    code: `<span class="code-key">rule</span> = {\n  <span class="code-key">preconditions</span>: [<span class="code-string">"logs &gt;= 1"</span>],\n  <span class="code-key">effect</span>: <span class="code-string">"planks += 4"</span>,\n  <span class="code-key">confidence</span>: 2\n}`
  },
  {
    kicker: 'PLANNER',
    title: 'Compose the shortest safe route.',
    copy: 'The loop recursively satisfies a goal: raw resources first, nested recipes next, final craft last. The planner uses the schema and the current inventory together.',
    code: `<span class="code-key">plan</span> = [\n  <span class="code-string">"move → log"</span>,\n  <span class="code-string">"mine × 2"</span>,\n  <span class="code-string">"craft planks"</span>,\n  <span class="code-string">"craft sticks"</span>,\n  <span class="code-string">"craft pickaxe"</span>\n]`
  },
  {
    kicker: 'VERIFIED EXECUTION',
    title: 'Act, then check the next observation.',
    copy: 'Every action carries a predicted effect. The verifier compares it to the observation that comes back. A failed transition is evidence against the current model.',
    code: `<span class="code-key">step</span> = {\n  <span class="code-key">action</span>: <span class="code-string">"craft wooden_pickaxe"</span>,\n  <span class="code-key">predicted</span>: <span class="code-string">"inventory += 1"</span>,\n  <span class="code-key">verified</span>: <span class="code-string">"crafted:wooden_pickaxe"</span>\n}`
  },
  {
    kicker: 'SKILL COMPILER',
    title: 'Keep the procedure that survived.',
    copy: 'A successful episode becomes a versioned skill with a postcondition and replay validator. The next holdout can call it before exploring from scratch.',
    code: `<span class="code-key">skill</span> = {\n  <span class="code-key">id</span>: <span class="code-string">"skill_wooden_pickaxe"</span>,\n  <span class="code-key">steps</span>: [<span class="code-string">"move"</span>, <span class="code-string">"mine"</span>, <span class="code-string">"craft"</span>],\n  <span class="code-key">postcondition</span>: <span class="code-string">"pickaxe &gt;= 1"</span>\n}`
  }
];

const byId = (id) => document.getElementById(id);

document.querySelectorAll('.loop-step').forEach((button) => {
  button.addEventListener('click', () => {
    const index = Number(button.dataset.step);
    const step = loopSteps[index];
    document.querySelectorAll('.loop-step').forEach((item) => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    byId('loop-kicker').textContent = step.kicker;
    byId('loop-title').textContent = step.title;
    byId('loop-copy').textContent = step.copy;
    byId('loop-code').innerHTML = step.code;
    byId('loop-progress').style.width = `${(index + 1) * 20}%`;
  });
});

const scoreData = {
  hybrid: { actions: '13.00', tokens: '39', reuse: '100%', failures: '0', actionNote: '−40% vs baseline', tokenNote: '−64% vs baseline' },
  baseline: { actions: '21.67', tokens: '108', reuse: '0%', failures: '0', actionNote: 'probe-heavy control', tokenNote: 'exploration proxy' }
};

document.querySelectorAll('.score-tab').forEach((button) => {
  button.addEventListener('click', () => {
    const mode = button.dataset.mode;
    const data = scoreData[mode];
    document.querySelectorAll('.score-tab').forEach((item) => item.classList.toggle('active', item === button));
    byId('score-actions').textContent = data.actions;
    byId('score-tokens').textContent = data.tokens;
    byId('score-reuse').textContent = data.reuse;
    byId('score-failures').textContent = data.failures;
    byId('score-actions-note').textContent = data.actionNote;
    byId('score-tokens-note').textContent = data.tokenNote;
    document.querySelector('.hybrid-bar').style.opacity = mode === 'hybrid' ? '1' : '.45';
    document.querySelector('.baseline-bar').style.opacity = mode === 'baseline' ? '1' : '.65';
  });
});

let replayTimer;
byId('replay-button').addEventListener('click', () => {
  clearInterval(replayTimer);
  const items = [...document.querySelectorAll('.replay-item')];
  items.forEach((item) => { item.classList.remove('active'); item.querySelector('i').textContent = 'waiting'; });
  byId('replay-state').textContent = 'RUNNING';
  byId('replay-message').textContent = 'Replaying verified control path…';
  byId('replay-cost').textContent = '0 / 54 tok';
  let index = 0;
  replayTimer = setInterval(() => {
    if (index >= items.length) {
      clearInterval(replayTimer);
      byId('replay-state').textContent = 'SUCCESS';
      byId('replay-message').textContent = 'Postcondition confirmed. Skill retained.';
      byId('replay-cost').textContent = '54 / 54 tok';
      return;
    }
    const item = items[index];
    item.classList.add('active');
    item.querySelector('i').textContent = index === items.length - 1 ? 'verified' : 'done';
    byId('replay-cost').textContent = `${(index + 1) * 11} / 54 tok`;
    index += 1;
  }, 540);
});

const observer = new IntersectionObserver((entries) => {
  entries.forEach((entry) => { if (entry.isIntersecting) entry.target.classList.add('is-visible'); });
}, { threshold: .1 });
document.querySelectorAll('.reveal').forEach((element) => observer.observe(element));
