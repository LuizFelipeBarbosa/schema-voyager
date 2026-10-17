// POST /explore: walk the next 10-block step of an outward square spiral
// around the episode origin (E 1, S 1, W 2, N 2, E 3, ... steps), stopping
// before barrier blocks or after 30 s.

const { Vec3 } = require('vec3')
const { goals } = require('mineflayer-pathfinder')
const { gotoGoal, positionOf } = require('./world')

const STEP_BLOCKS = 10
const EXPLORE_TIMEOUT_MS = 30000
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

async function explore (bot, episode) {
  const { from, direction } = advanceSpiral(episode.spiral)
  const origin = episode.origin.floored()

  // Last cell of the step before the first barrier along it.
  let target = origin.offset(from.x, 0, from.z)
  for (let i = 1; i <= STEP_BLOCKS; i++) {
    const cell = origin.offset(from.x + direction.x * i, 0, from.z + direction.z * i)
    if (hasBarrierAt(bot, cell)) break
    target = cell
  }

  // Exact waypoint first; if it is blocked (say, a tree trunk), anywhere
  // within 2 blocks of it. Both attempts share the 30 s budget.
  const start = bot.entity.position.clone()
  const deadline = Date.now() + EXPLORE_TIMEOUT_MS
  const reached = await gotoGoal(bot, new goals.GoalXZ(target.x, target.z), EXPLORE_TIMEOUT_MS) ||
    (Date.now() < deadline && await gotoGoal(bot, new goals.GoalNearXZ(target.x, target.z, 2), deadline - Date.now()))
  const end = bot.entity.position
  const moved = new Vec3(end.x - start.x, 0, end.z - start.z).norm()
  const error = !reached && moved < 1 ? 'unreachable' : null
  return { ok: !error, error, position: positionOf(bot) }
}

module.exports = { explore, newSpiral }
