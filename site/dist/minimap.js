// Route minimap of the life view: a top-down map (north up) of where the bot
// went in the goal on screen. routeOf() is pure; drawRoute() paints a canvas.
// Positions come from step events' detail.position (recorded runs; traces from
// before contract v2.5 mostly have none) and, when the panel follows a live
// bot, from polling its /status.

const ROUTE_COLORS = { trail: '#e9b949', current: '#9bd9b6', target: '#f0604d', start: '#94958f', grid: '#2a302b', text: '#e8e7df' };
const ROUTE_SCALE_STEPS = [1, 2, 5, 10, 20, 50, 100, 200, 500];
const ROUTE_MIN_MOVE = 0.5; // blocks the live bot must move to add a trail point
const ROUTE_MIN_SPAN = 16; // blocks; a bot that has barely moved still gets a readable map

const isPoint = (value) => Boolean(value) && Number.isFinite(value.x) && Number.isFinite(value.z);

// `goals` is [{ index, events }], the shown goal's events cut at what is on screen;
// `live` is { goal, points } from the bot's status, or null.
// Returns { shownIndex, starts: [{ goal, x, z }], trail, current, target, hasData }: a goal starts where
// the previous one left the bot, else at its first recorded position.
function routeOf(goals, shownIndex, live) {
  const starts = [];
  let previousEnd = null;
  let trail = [];
  let target = null;
  for (const goal of [...goals].sort((a, b) => a.index - b.index)) {
    const steps = goal.events.filter((event) => event.kind === 'step' && event.detail);
    const recorded = steps.map((step) => step.detail.position).filter(isPoint);
    const start = previousEnd || recorded[0] || null;
    if (start) starts.push({ goal: goal.index, x: start.x, z: start.z });
    if (goal.index === shownIndex) {
      trail = start ? [start, ...recorded] : recorded;
      target = steps.map((step) => step.detail.target).filter(isPoint).pop() || null;
    }
    previousEnd = recorded[recorded.length - 1] || previousEnd;
  }
  if (live && live.goal === shownIndex && live.points.length) trail = [...trail, ...live.points];
  return { shownIndex, starts, trail, current: trail[trail.length - 1] || null, target, hasData: starts.length > 0 || trail.length > 0 };
}

// The largest round length (in blocks) that fits `maxBlocks`.
function scaleLength(maxBlocks) {
  return ROUTE_SCALE_STEPS.filter((length) => length <= maxBlocks).pop() || 1;
}

function drawRoute(canvas, route) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, width, height);
  if (!route.hasData) return;

  const points = [...route.starts, ...route.trail, ...(route.target ? [route.target] : [])];
  const xs = points.map((point) => point.x);
  const zs = points.map((point) => point.z);
  const margin = 16;
  const spanX = Math.max(Math.max(...xs) - Math.min(...xs), ROUTE_MIN_SPAN);
  const spanZ = Math.max(Math.max(...zs) - Math.min(...zs), ROUTE_MIN_SPAN);
  const scale = Math.min((width - 2 * margin) / spanX, (height - 2 * margin) / spanZ);
  const centreX = (Math.max(...xs) + Math.min(...xs)) / 2;
  const centreZ = (Math.max(...zs) + Math.min(...zs)) / 2;
  const at = (point) => [width / 2 + (point.x - centreX) * scale, height / 2 + (point.z - centreZ) * scale]; // -z is north, up

  if (route.trail.length > 1) {
    ctx.strokeStyle = ROUTE_COLORS.trail;
    ctx.lineWidth = 2;
    ctx.beginPath();
    route.trail.forEach((point, i) => (i ? ctx.lineTo(...at(point)) : ctx.moveTo(...at(point))));
    ctx.stroke();
  }

  ctx.font = '11px "DM Mono", monospace';
  ctx.textBaseline = 'middle';
  for (const start of route.starts) {
    const [x, y] = at(start);
    ctx.fillStyle = ROUTE_COLORS.start;
    ctx.fillRect(x - 3, y - 3, 6, 6);
    if (start.goal === route.shownIndex) ctx.fillText(`g${start.goal}`, x + 6, y - 7);
  }
  if (route.target) {
    const [x, y] = at(route.target);
    ctx.strokeStyle = ROUTE_COLORS.target;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x - 5, y - 5); ctx.lineTo(x + 5, y + 5);
    ctx.moveTo(x + 5, y - 5); ctx.lineTo(x - 5, y + 5);
    ctx.stroke();
  }
  if (route.current) {
    const [x, y] = at(route.current);
    ctx.fillStyle = ROUTE_COLORS.current;
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, 2 * Math.PI);
    ctx.fill();
    ctx.strokeStyle = ROUTE_COLORS.current;
    ctx.beginPath();
    ctx.arc(x, y, 9, 0, 2 * Math.PI);
    ctx.stroke();
  }

  // Scale bar bottom left, north mark top right.
  const length = scaleLength(60 / scale);
  ctx.strokeStyle = ctx.fillStyle = ROUTE_COLORS.text;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(8, height - 12); ctx.lineTo(8 + length * scale, height - 12);
  ctx.stroke();
  ctx.fillText(`${length} m`, 8, height - 22);
  ctx.textAlign = 'right';
  ctx.fillText('N ↑', width - 8, 12);
  ctx.textAlign = 'left';
}
