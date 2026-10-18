// Build preview: draws the blueprint of the newest build goal that waits for approval (else the one being
// built) as one small grid per layer, with a legend, the materials and Approve / Discard buttons.
// Reads the `preview` field of the panel's GET /goals.json and posts to /goals/<id>/approve and /discard (v3.4b).

const SvBuildPreview = (() => {
  const POLL_MS = 2000;
  const FIXED_COLORS = { '#planks': '#c8a064', '#log': '#7a5530', glass: '#9fd8e8', torch: '#f2c94c', stone: '#8d908a', cobblestone: '#767973', dirt: '#8a6445' };
  const DOOR_COLOR = '#a9712f';

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  // Same value, same colour: a fixed colour for the usual materials, else a hue taken from the name.
  function colorOf(value) {
    if (FIXED_COLORS[value]) return FIXED_COLORS[value];
    if (value.endsWith('_door')) return DOOR_COLOR;
    let hash = 0;
    for (const char of value) hash = (hash * 31 + char.charCodeAt(0)) % 360;
    return `hsl(${hash}, 45%, 55%)`;
  }

  // "#oak_planks" -> "oak planks"
  function materialName(value) {
    return value.replace(/#/g, '').replace(/_/g, ' ');
  }

  function isPlaced(value) {
    return value !== 'air' && value !== 'any';
  }

  // One layer: `rows` are the strings of blueprint.render (rows north to south, "." for air, " " for any).
  function layerHtml(rows, palette) {
    const cells = rows.map((row) => [...row].map((char) => {
      if (char === ' ') return '<i class="bp-cell bp-any"></i>';
      if (char === '.') return '<i class="bp-cell bp-air"></i>';
      const value = palette[char] || char;
      return `<i class="bp-cell" style="background:${escapeHtml(colorOf(value))}" title="${escapeHtml(materialName(value))}"></i>`;
    }).join('')).join('');
    const columns = Math.max(0, ...rows.map((row) => row.length));
    return `<div class="bp-grid" style="grid-template-columns:repeat(${columns}, 1fr)">${cells}</div>`;
  }

  function legendHtml(palette) {
    const values = [...new Set(Object.values(palette).filter(isPlaced))];
    return values.map((value) => `<li><i class="bp-swatch" style="background:${escapeHtml(colorOf(value))}"></i>${escapeHtml(materialName(value))}</li>`).join('');
  }

  function materialsHtml(materials) {
    const entries = Object.entries(materials);
    if (!entries.length) return '';
    return entries.map(([value, count]) => `${count} ${escapeHtml(materialName(value))}`).join(', ');
  }

  // Layers bottom-up (render lists them that way), each labelled with its number.
  function previewHtml(goal) {
    const { name, size, layers, palette, materials } = goal.preview;
    const awaiting = goal.status === 'awaiting_approval';
    const buttons = awaiting
      ? `<div class="bp-actions"><button class="bp-approve" data-approve="${escapeHtml(goal.id)}">Approve</button><button class="bp-discard" data-discard="${escapeHtml(goal.id)}">Discard</button></div>`
      : '';
    const layerBlocks = layers.map((rows, y) => `<figure class="bp-layer"><figcaption>layer ${y}</figcaption>${layerHtml(rows, palette)}</figure>`).join('');
    return `<div class="card-head"><b>BUILD PREVIEW</b><span class="hint">${awaiting ? 'waiting for your approval' : escapeHtml(goal.status)}</span></div>
      <div class="bp-title"><span class="goal-id">${escapeHtml(goal.id)}</span> <strong>${escapeHtml(name)}</strong> <span class="bp-size">${size.map((n) => escapeHtml(n)).join(' x ')}</span></div>
      <div class="bp-layers">${layerBlocks}</div>
      <ul class="bp-legend">${legendHtml(palette)}</ul>
      <p class="bp-materials"><b>MATERIALS</b> ${materialsHtml(materials)}</p>
      ${buttons}`;
  }

  // The goal to show: the newest awaiting approval, else the newest running one.
  function chooseGoal(payload) {
    const withPreview = ((payload && payload.goals) || []).filter((goal) => goal.preview).reverse();
    return withPreview.find((goal) => goal.status === 'awaiting_approval') || withPreview.find((goal) => goal.status === 'running') || null;
  }

  // `root` is the mount point. Dependencies are injected so tests can fake them.
  function createController({ root, fetch, setInterval }) {
    let run = null; // the life of the last GET, sent back with each POST
    let errorText = '';
    let shown = null; // the goal drawn now

    function render() {
      const errorLine = errorText ? `<div class="bp-error">${escapeHtml(errorText)}</div>` : '';
      const html = shown ? previewHtml(shown) + errorLine : '';
      if (root.innerHTML !== html) root.innerHTML = html;
    }

    async function refresh() {
      try {
        const payload = await (await fetch('/goals.json')).json();
        run = payload.run;
        shown = chooseGoal(payload);
        render();
      } catch (error) {
        // The panel may be restarting; the next poll retries.
      }
    }

    async function answer(id, action) {
      try {
        const response = await fetch(`/goals/${encodeURIComponent(id)}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ run }) });
        const body = await response.json().catch(() => ({}));
        errorText = response.ok ? '' : body.error || `could not ${action}`;
      } catch (error) {
        errorText = 'could not reach the panel';
      }
      await refresh();
    }

    root.addEventListener('click', (event) => {
      const approve = event.target.closest('[data-approve]');
      const discard = event.target.closest('[data-discard]');
      if (approve) answer(approve.dataset.approve, 'approve');
      else if (discard) answer(discard.dataset.discard, 'discard');
    });

    refresh();
    setInterval(refresh, POLL_MS);
    return { refresh, answer };
  }

  return { colorOf, materialName, layerHtml, previewHtml, chooseGoal, createController };
})();

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('build-preview');
    if (root) SvBuildPreview.createController({ root, fetch: (...args) => fetch(...args), setInterval: (...args) => setInterval(...args) });
  });
}
