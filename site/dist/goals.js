// Goals box: sends a goal to the bot and lists the goals with a status chip
// and a Cancel button. Talks to the panel server's /goals routes (v3.1f).

const SvGoals = (() => {
  const POLL_MS = 2000;
  const MAX_SHOWN = 20;
  const CANCELLABLE = new Set(['submitted', 'queued', 'running', 'awaiting_approval']);
  const SHOWS_REASON = new Set(['failed', 'cancelled', 'unsupported']);

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  // `submitted` and `queued` read the same to the audience.
  function goalChip(status) {
    const shown = status === 'submitted' ? 'queued' : status;
    return `<span class="goal-chip goal-chip-${escapeHtml(shown.replace(/_/g, '-'))}">${escapeHtml(shown.replace(/_/g, ' '))}</span>`;
  }

  function goalRowHtml(goal) {
    const reason = SHOWS_REASON.has(goal.status) && goal.reason ? `<div class="goal-reason">${escapeHtml(goal.reason)}</div>` : '';
    const cancel = CANCELLABLE.has(goal.status) ? `<button class="goal-cancel" data-cancel="${escapeHtml(goal.id)}">Cancel</button>` : '';
    return `<li class="goal-row">
      <span class="goal-id">${escapeHtml(goal.id)}</span>
      <span class="goal-text">${escapeHtml(goal.text)}</span>
      ${goalChip(goal.status)}${cancel}${reason}
    </li>`;
  }

  // Newest first, at most MAX_SHOWN.
  function goalsListHtml(payload) {
    const goals = ((payload && payload.goals) || []).slice(-MAX_SHOWN).reverse();
    if (!goals.length) return '<li class="goal-empty">no goals yet</li>';
    return goals.map(goalRowHtml).join('');
  }

  // `root` is the mount point. Dependencies are injected so tests can fake them.
  function createController({ root, fetch, setInterval }) {
    root.innerHTML = `<div class="goals-form">
        <input class="goals-input" type="text" maxlength="200" placeholder="Give the bot a goal, like: get me 10 torches" aria-label="Goal for the bot" />
        <button class="goals-send">Send</button>
      </div>
      <div class="goals-error" hidden></div>
      <ul class="goals-list"></ul>`;
    const input = root.querySelector('.goals-input');
    const sendButton = root.querySelector('.goals-send');
    const errorLine = root.querySelector('.goals-error');
    const list = root.querySelector('.goals-list');
    let run = null; // the life of the last GET, sent back with each POST

    function showError(message) {
      errorLine.textContent = message || '';
      errorLine.hidden = !message;
    }

    async function refresh() {
      try {
        const payload = await (await fetch('/goals.json')).json();
        run = payload.run;
        list.innerHTML = goalsListHtml(payload);
      } catch (error) {
        // The panel may be restarting; the next poll retries.
      }
    }

    async function post(path, body) {
      const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      return { ok: response.ok, body: await response.json().catch(() => ({})) };
    }

    async function send() {
      const text = input.value.trim();
      if (!text) return;
      try {
        const result = await post('/goals', { text, run });
        if (result.ok) {
          input.value = '';
          showError('');
        } else {
          showError(result.body.detail || result.body.error || 'the panel refused the goal');
        }
      } catch (error) {
        showError('could not reach the panel');
      }
      await refresh();
    }

    async function cancel(id) {
      try {
        const result = await post(`/goals/${encodeURIComponent(id)}/cancel`, { run });
        showError(result.ok ? '' : result.body.detail || result.body.error || 'could not cancel');
      } catch (error) {
        showError('could not reach the panel');
      }
      await refresh();
    }

    sendButton.addEventListener('click', send);
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') send(); });
    list.addEventListener('click', (event) => {
      const button = event.target.closest('[data-cancel]');
      if (button) cancel(button.dataset.cancel);
    });

    refresh();
    setInterval(refresh, POLL_MS);
    return { refresh, send, cancel };
  }

  return { goalChip, goalsListHtml, createController };
})();

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('goals');
    if (root) SvGoals.createController({ root, fetch: (...args) => fetch(...args), setInterval: (...args) => setInterval(...args) });
  });
}
