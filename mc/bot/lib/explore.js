// POST /explore: without a direction, walk the next 10-block step of an
// outward square spiral around the episode origin (E 1, S 1, W 2, N 2, E 3, ...
// steps), stopping before barrier blocks or after 30 s. With a direction (v2),
// walk up to `distance` blocks along it, in legs of 16 blocks, stopping at the
// world border, at barriers, before lava, or after 30 s; with dig_through the
// path may dig and pillar. Direction "up" (v2.1) climbs back to open sky.
// Every path attempt gives up after STALL_MS without moving or digging. A
// direction that does not move the bot is retried with a short hop and then the
// two neighbouring compass directions, within FALLBACK_BUDGET_MS in all.

const { Vec3 } = require('vec3')
const { goals } = require('mineflayer-pathfinder')
const { DIRECTIONS: COMPASS, directionVector, horizontalDistance } = require('./geometry')
const { recordTravel, withDiggingMovements } = require('./travel')
const { hasOpenSky, skyLevel } = require('./sky')
const { gotoGoal, inventoryCounts, positionOf } = require('./world')

const STEP_BLOCKS = 10
const EXPLORE_TIMEOUT_MS = 30000
const LEG_BLOCKS = 16
const DEFAULT_DISTANCE = 32
const CLIMB_TIMEOUT_MS = 100000 // under the 120 s action timeout; about 50 blocks of rock
const STALL_MS = 4000
const FAIL_FAST = { stallMs: STALL_MS }
const FALLBACK_BUDGET_MS = 20000
const SHORT_HOP_BLOCKS = 10
const DIRECTIONS = [{ x: 1, z: 0 }, { x: 0, z: 1 }, { x: -1, z: 0 }, { x: 0, z: -1 }] // E, S, W, N

function newSpiral () {
  return { x: 0, z: 0, direction: 0, legSteps: 1, stepsOnLeg: 0, turns: 0 }
}

// Moves the spiral cursor one step; returns the step's start offset and direction.
function advanceSpiral (spiral) {
  const from = { x: spiral.x, z: spiral.z }
  const direction = DIRECTIONS[spiral.direction]
  spiral.x += direction.x * STEP_BLOCKS
  spiral.z += direction.z * STEP_BLOCKS
  spiral.stepsOnLeg++
  if (spiral.stepsOnLeg === spiral.legSteps) {
    spiral.stepsOnLeg = 0
    spiral.direction = (spiral.direction + 1) % 4
    spiral.turns++
    if (spiral.turns % 2 === 0) spiral.legSteps++
  }
  return { from, direction }
}

function hasBarrierAt (bot, feet) {
  return [feet, feet.offset(0, 1, 0)].some((pos) => bot.blockAt(pos)?.name === 'barrier')
}

async function exploreSpiral (bot, episode) {
  const { from, direction } = advanceSpiral(episode.spiral)
  const origin = episode.origin.floored()

  // Last cell of the step before the first barrier along it.
  let target = origin.offset(from.x, 0, from.z)
  for (let i = 1; i <= STEP_BLOCKS; i++) {
    const cell = origin.offset(from.x + direction.x * i, 0, from.z + direction.z * i)
    if (hasBarrierAt(bot, cell)) break
    target = cell
  }

  // Both attempts share the 30 s budget.
  const start = bot.entity.position.clone()
  const reached = await walkTo(bot, target, Date.now() + EXPLORE_TIMEOUT_MS)
  const end = bot.entity.position
  const moved = new Vec3(end.x - start.x, 0, end.z - start.z).norm()
  const error = !reached && moved < 1 ? 'unreachable' : null
  return { ok: !error, error, position: positionOf(bot) }
}

// --- Directional walk (v2) ----------------------------------------------------

// Follows the world border packets. {centerX, centerZ, size}; vanilla starts
// with a border of 59999968 blocks.
function trackWorldBorder (client) {
  const border = { centerX: 0, centerZ: 0, size: 59999968 }
  client.on('initialize_world_border', (packet) => {
    Object.assign(border, { centerX: packet.x, centerZ: packet.z, size: packet.newDiameter })
  })
  client.on('world_border_center', (packet) => Object.assign(border, { centerX: packet.x, centerZ: packet.z }))
  client.on('world_border_size', (packet) => { border.size = packet.diameter })
  client.on('world_border_lerp_size', (packet) => { border.size = packet.newDiameter })
  return border
}

function insideBorder (border, x, z) {
  const half = border.size / 2
  return Math.abs(x - border.centerX) <= half && Math.abs(z - border.centerZ) <= half
}

// How many blocks (at most `maxBlocks`) the bot can advance from `from` along
// the unit vector before the world border, an unloaded chunk, a barrier or
// lava: {blocks, stoppedBy, position}.
function advanceLimit (bot, border, from, unit, maxBlocks) {
  const baseY = Math.floor(from.y)
  for (let i = 1; i <= maxBlocks; i++) {
    const x = Math.floor(from.x + unit.x * i)
    const z = Math.floor(from.z + unit.z * i)
    const stopAt = (stoppedBy) => ({ blocks: i - 1, stoppedBy, position: { x, y: baseY, z } })
    if (!insideBorder(border, x + 0.5, z + 0.5)) return stopAt('border')
    if (!bot.blockAt(new Vec3(x, baseY, z), false)) return stopAt('unloaded')
    for (let dy = -2; dy <= 2; dy++) {
      const name = bot.blockAt(new Vec3(x, baseY + dy, z), false)?.name
      if (name === 'lava') return stopAt('lava')
      if (name === 'barrier' && dy >= 0 && dy <= 1) return stopAt('barrier')
    }
  }
  return { blocks: maxBlocks, stoppedBy: null, position: null }
}

// Exact waypoint first; if it is blocked (say, a tree trunk), anywhere within
// 2 blocks of it. Both fail fast, and a stalled first attempt is not followed by
// a second one (the bot is stuck, not blocked by one cell).
async function walkTo (bot, target, deadline) {
  const startedAt = Date.now()
  if (await gotoGoal(bot, new goals.GoalXZ(target.x, target.z), deadline - startedAt, { failFast: FAIL_FAST })) return true
  const stalled = Date.now() - startedAt >= STALL_MS
  return !stalled && Date.now() < deadline &&
    await gotoGoal(bot, new goals.GoalNearXZ(target.x, target.z, 2), deadline - Date.now(), { failFast: FAIL_FAST })
}

// Pure: the two compass directions next to `direction`, counter-clockwise first.
function neighbourDirections (direction) {
  const index = COMPASS.indexOf(direction)
  return [COMPASS[(index + COMPASS.length - 1) % COMPASS.length], COMPASS[(index + 1) % COMPASS.length]]
}

// Pure: what to try, in order, for a direction that does not move the bot: the
// request itself, a short hop the same way (when the request was longer), then
// the neighbouring directions.
function directionAttempts (direction, distance) {
  const hop = distance > SHORT_HOP_BLOCKS ? [{ direction, distance: SHORT_HOP_BLOCKS }] : []
  return [
    { direction, distance },
    ...hop,
    ...neighbourDirections(direction).map((neighbour) => ({ direction: neighbour, distance: Math.min(distance, SHORT_HOP_BLOCKS) }))
  ]
}

// Pure: whether another fallback attempt can still stall out inside the budget.
function hasTimeForAttempt (elapsedMs) {
  return elapsedMs + STALL_MS <= FALLBACK_BUDGET_MS
}

// Cells next to `feet` the bot could stand in: a solid floor, and free feet and
// head cells, `dyMin` to `dyMax` blocks from its own level.
function standableNeighbours (bot, feet, dyMin, dyMax) {
  const solid = (pos) => bot.blockAt(pos)?.boundingBox === 'block'
  const free = (pos) => { const block = bot.blockAt(pos); return block?.boundingBox === 'empty' && block.name !== 'lava' }
  const cells = []
  for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    for (let dy = dyMax; dy >= dyMin; dy--) {
      const cell = feet.offset(dx, dy, dz)
      if (solid(cell.offset(0, -1, 0)) && free(cell) && free(cell.offset(0, 1, 0))) {
        cells.push(cell)
        break
      }
    }
  }
  return cells
}

// A bot that cannot walk off: no standable cell next to it within a block of its
// own level (a 1x1 hole it dug, a pillar it placed). First steps to a standable
// cell (any drop of up to 4 blocks), else, when it may dig, climbs towards open sky.
async function getOutOfEnclosure (bot, { digThrough, scaffold }) {
  const feet = bot.entity.position.floored()
  if (standableNeighbours(bot, feet, -1, 1).length > 0) return
  const [exit] = standableNeighbours(bot, feet, -4, 1)
  if (exit) {
    await gotoGoal(bot, new goals.GoalNear(exit.x, exit.y, exit.z, 1), STALL_MS * 2, { failFast: FAIL_FAST })
  } else if (digThrough && !hasOpenSky(bot)) {
    await exploreUp(bot, { scaffold })
  }
}

async function exploreDirection (bot, { border, tracker }, { direction, distance, digThrough, scaffold }) {
  const startedAt = Date.now()
  const start = bot.entity.position.clone()
  const deadline = startedAt + EXPLORE_TIMEOUT_MS

  const walkLegs = async (unit, legsDistance) => {
    const legsStart = bot.entity.position.clone()
    let progress = 0
    while (progress < legsDistance && Date.now() < deadline) {
      const from = bot.entity.position.clone()
      const leg = advanceLimit(bot, border, from, unit, Math.min(LEG_BLOCKS, Math.ceil(legsDistance - progress)))
      if (leg.stoppedBy === 'lava') tracker.log.record('hazard', { kind: 'lava', position: leg.position })
      if (leg.blocks < 1) return
      const target = from.floored().offset(Math.round(unit.x * leg.blocks), 0, Math.round(unit.z * leg.blocks))
      await walkTo(bot, target, deadline)
      const now = bot.entity.position
      progress = (now.x - legsStart.x) * unit.x + (now.z - legsStart.z) * unit.z
      if (horizontalDistance(from, now) < 1) return
      if (leg.stoppedBy) return // reached the last safe cell before the obstacle
    }
  }
  const tryAttempts = async () => {
    const attempts = directionAttempts(direction, distance)
    for (const [i, attempt] of attempts.entries()) {
      if (i > 0 && !hasTimeForAttempt(Date.now() - startedAt)) return
      const attemptStart = bot.entity.position.clone()
      await walkLegs(directionVector(attempt.direction), attempt.distance)
      if (horizontalDistance(attemptStart, bot.entity.position) >= 1) return
      if (i === 0) await getOutOfEnclosure(bot, { digThrough, scaffold })
    }
  }
  if (digThrough) await withDiggingMovements(bot, { scaffoldNames: scaffold }, tryAttempts)
  else await tryAttempts()
  const moved = horizontalDistance(start, bot.entity.position)
  const error = moved < 1 ? 'unreachable' : null
  return { ok: !error, error, position: positionOf(bot) }
}

// Back to open sky: path straight up to the level above the highest overhead
// block of the column, digging up and pillaring with `scaffold` blocks only (never down:
// DiggingMovements). A leg that ends without gaining height ends the climb,
// since the pathfinder found no way (or would have to break next to a liquid).
async function exploreUp (bot, { scaffold }) {
  const startY = bot.entity.position.y
  const deadline = Date.now() + CLIMB_TIMEOUT_MS
  const climb = async () => {
    while (!hasOpenSky(bot) && Date.now() < deadline) {
      const legStartY = bot.entity.position.y
      const column = bot.entity.position.floored()
      await gotoGoal(bot, new goals.GoalBlock(column.x, skyLevel(bot), column.z), deadline - Date.now(), { failFast: FAIL_FAST })
      if (bot.entity.position.y - legStartY < 1) return
    }
  }
  await withDiggingMovements(bot, { scaffoldNames: scaffold }, climb)
  const sky = hasOpenSky(bot)
  const error = !sky && bot.entity.position.y - startY < 1 ? 'unreachable' : null
  return { ok: !error, error, position: positionOf(bot), sky }
}

// `session`: {episode, tracker, border}. The v1 fields come first; `travel`
// covers the whole walk, which is all travel (nothing follows it).
async function explore (bot, session, body = {}) {
  const { episode } = session
  const recording = recordTravel(bot, episode)
  let outcome = null
  let travel = null
  try {
    const scaffold = Array.isArray(body.scaffold) ? body.scaffold : []
    if (body.direction === 'up') {
      outcome = await exploreUp(bot, { scaffold })
    } else if (body.direction) {
      outcome = await exploreDirection(bot, session, {
        direction: body.direction,
        distance: body.distance ?? DEFAULT_DISTANCE,
        digThrough: body.dig_through === true,
        scaffold
      })
    } else {
      outcome = await exploreSpiral(bot, episode)
    }
  } finally {
    travel = recording.finish()
  }
  return { ...outcome, travel, inventory_before_step: inventoryCounts(bot) }
}

module.exports = {
  advanceLimit, directionAttempts, explore, hasTimeForAttempt, insideBorder, neighbourDirections,
  newSpiral, standableNeighbours, trackWorldBorder
}
