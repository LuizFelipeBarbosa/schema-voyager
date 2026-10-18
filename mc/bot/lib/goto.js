// POST /goto: walk to within `range` of a point, never digging or placing. See
// contracts/bot_api.md (v3.4b).

const { Movements, goals } = require('mineflayer-pathfinder')
const { validateProtect } = require('./protect')
const { sprintAllowed } = require('./movements')
const { recordTravel } = require('./travel')
const { gotoGoal } = require('./world')

const DEFAULT_RANGE = 3
const MAX_RANGE = 16
const WALK_MS = 55000 // under the 60 s action timeout

function validateGoto (body) {
  if (![body.x, body.y, body.z].every(Number.isInteger)) return '"x", "y" and "z" must be integers'
  if (body.range != null && !(Number.isInteger(body.range) && body.range >= 1 && body.range <= MAX_RANGE)) {
    return `"range" must be an integer from 1 to ${MAX_RANGE}`
  }
  return validateProtect(body.protect)
}

// World-preserving movements, as /place's walk without scaffold: never digs, places or opens doors.
async function withWalkingMovements (bot, fn) {
  const previous = bot.pathfinder.movements
  const movements = new Movements(bot)
  movements.canDig = false
  movements.canOpenDoors = false
  movements.allow1by1towers = false
  movements.scafoldingBlocks = []
  movements.allowSprinting = sprintAllowed(bot)
  bot.pathfinder.setMovements(movements)
  try {
    return await fn()
  } finally {
    bot.pathfinder.setMovements(previous)
  }
}

// `session`: {episode, signal}. An abort throws `signal.reason`.
async function goto (bot, { x, y, z, range = DEFAULT_RANGE }, { episode, signal = null }) {
  signal?.throwIfAborted()
  const recording = recordTravel(bot, episode)
  let arrived
  try {
    arrived = await withWalkingMovements(bot, () => gotoGoal(bot, new goals.GoalNear(x, y, z, range), WALK_MS, { signal }))
  } catch (err) {
    recording.finish()
    throw err
  }
  return { ok: arrived, error: arrived ? null : 'unreachable', arrived, travel: recording.finish() }
}

module.exports = { goto, validateGoto }
