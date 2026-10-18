// POST /attack: find the nearest mob of a type, walk to it, fight it with the
// guard's fight(), then attribute what it dropped at the spot where it died and
// pick that up. See contracts/bot_api.md, "v3.3: /attack".

const { goals } = require('mineflayer-pathfinder')
const { fight } = require('./guard')
const { horizontalDistance } = require('./geometry')
const { insideAny, validateProtect } = require('./protect')
const { isAlive } = require('./targets')
const { emptyTravel, recordTravel } = require('./travel')
const {
  countGains, gotoGoalDetailed, inventoryCounts, pickUpEntities, readDroppedStack, round, settleInventory, stopMoving
} = require('./world')

const BUDGET_MS = 90000 // inside the action's 120 s timeout
const FIGHT_MS = 30000
const WALK_MS = 30000 // per candidate
const DEFAULT_MAX_DISTANCE = 32
const MAX_MAX_DISTANCE = 64
const MAX_CANDIDATES = 3
const NEAR_BLOCKS = 3 // the walk ends this close to the target
const ESCAPE_MARGIN_BLOCKS = 16 // a target beyond max_distance + this has got away
const WATCH_MS = 250
const DROP_RADIUS = 2.5 // horizontal, around the target's last position
const DROPS_BEFORE_DEATH_MS = 500
const DROPS_AFTER_DEATH_MS = 1500
const INTERFERENCE_RADIUS = 4

function validateAttack (body) {
  if (typeof body.mob !== 'string' || body.mob.length === 0) return '"mob" must be a non-empty string'
  if (body.mob === 'player') return '"mob" cannot be "player"'
  if (body.weapon != null && (typeof body.weapon !== 'string' || body.weapon.length === 0)) return '"weapon" must be a string or null'
  const distance = body.max_distance
  if (distance != null && !(typeof distance === 'number' && distance > 0 && distance <= MAX_MAX_DISTANCE)) {
    return `"max_distance" must be a positive number of at most ${MAX_MAX_DISTANCE}`
  }
  return validateProtect(body.protect)
}

// The baby flag of an AgeableMob is entity metadata index 16 in 1.21.1 (a boolean); it is looked
// up by its name in minecraft-data so a version with another layout keeps working.
function isBabyAnimal (bot, entity) {
  const info = bot.registry.entitiesByName[entity.name]
  if (info?.type !== 'animal') return false // a baby zombie drops loot, so it is kept
  const index = info.metadataKeys?.indexOf('baby') ?? -1
  return entity.metadata?.[index >= 0 ? index : 16] === true
}

// Living entities of the mob's name within maxDistance, nearest first; no baby animals, none inside a protected box.
function findCandidates (bot, mob, maxDistance, protect = []) {
  const origin = bot.entity.position
  return Object.values(bot.entities)
    .filter((entity) => entity.name === mob && entity.position && isAlive(entity) && !isBabyAnimal(bot, entity) && !insideAny(protect, entity.position))
    .map((entity) => ({ entity, distance: origin.distanceTo(entity.position) }))
    .filter(({ distance }) => distance <= maxDistance)
    .sort((a, b) => a.distance - b.distance)
}

// Walks until the bot is within NEAR_BLOCKS of `target`, re-aimed as it moves. Resolves
// 'reached', 'escaped' (it died, vanished or got too far), 'protected' (it entered a protected box),
// 'unreachable' or 'timeout'.
async function walkToTarget (bot, target, { movements, maxDistance, protect, timeoutMs, signal }) {
  const inProtectedBox = () => {
    const live = bot.entities[target.id]
    return Boolean(live) && insideAny(protect, live.position)
  }
  const gotAway = () => {
    const live = bot.entities[target.id]
    return !live || !isAlive(live) || inProtectedBox() || bot.entity.position.distanceTo(live.position) > maxDistance + ESCAPE_MARGIN_BLOCKS
  }
  const gotAwayHow = () => (inProtectedBox() ? 'protected' : 'escaped')
  let escaped = gotAway()
  if (escaped) return gotAwayHow()
  const watch = setInterval(() => {
    if (escaped || !gotAway()) return
    escaped = true
    stopMoving(bot)
  }, WATCH_MS)
  try {
    bot.pathfinder.setMovements(movements)
    const walk = await gotoGoalDetailed(bot, new goals.GoalFollow(target, NEAR_BLOCKS), timeoutMs, { signal })
    if (escaped || (!walk.reached && gotAway())) return gotAwayHow()
    if (walk.reached) return 'reached'
    return walk.end === 'timeout' ? 'timeout' : 'unreachable'
  } finally {
    clearInterval(watch)
  }
}

// Records, from the moment it is called, every item entity that spawns and every entity that
// dies (spawn packets can arrive before or after entityDead, so the window is applied later).
// `stop()` returns {death, drops, others}: the target's death {at, position} or null, the
// item entities with their spawn time, position and stack, and the other deaths.
function watchKill (bot, target, { onNearbyItem }) {
  const items = []
  const deaths = []
  const onSpawn = (entity) => {
    if (entity.name !== 'item') return
    items.push({ entity, at: Date.now(), position: entity.position.clone(), stack: readDroppedStack(entity) })
    if (entity.position.distanceTo(target.position) <= DROP_RADIUS) onNearbyItem(entity)
  }
  // The item's metadata can arrive after its spawn.
  const onUpdate = (entity) => {
    const item = items.find((candidate) => candidate.entity.id === entity.id)
    if (item && !item.stack) item.stack = readDroppedStack(entity)
  }
  const onDead = (entity) => {
    if (entity.id === bot.entity.id) return
    deaths.push({ entity, at: Date.now(), position: entity.position.clone() })
  }
  bot.on('entitySpawn', onSpawn)
  bot.on('entityUpdate', onUpdate)
  bot.on('entityDead', onDead)
  return {
    death: () => deaths.find((death) => death.entity.id === target.id) ?? null,
    stop () {
      bot.removeListener('entitySpawn', onSpawn)
      bot.removeListener('entityUpdate', onUpdate)
      bot.removeListener('entityDead', onDead)
      const death = deaths.find((candidate) => candidate.entity.id === target.id) ?? null
      return { death, items, others: deaths.filter((candidate) => candidate !== death) }
    }
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const cellOf = (pos) => ({ x: round(pos.x, 1), y: round(pos.y, 1), z: round(pos.z, 1) })

// `session`: {episode, tracker, signal, guard, movements}; an abort throws `signal.reason`.
// `movements`: the walking movements (the world-preserving ones server.js builds).
async function attack (bot, body, { episode, tracker, guard, movements, signal = null, fightMs = FIGHT_MS }) {
  signal?.throwIfAborted()
  const { mob, weapon = null, max_distance: maxDistance = DEFAULT_MAX_DISTANCE, protect = [] } = body
  const deadline = Date.now() + BUDGET_MS
  const result = {
    mob,
    target: null,
    weapon,
    weapon_used: null,
    killed: false,
    death_position: null,
    drops: {},
    picked_up: {},
    inventory_before_step: inventoryCounts(bot),
    travel: emptyTravel()
  }
  let letGoProtected = false // the target (the last candidate tried, or the one fought) entered a protected box
  const finish = (error, detail) => ({
    ok: !error,
    error: error || null,
    ...result,
    ...(error === 'escaped' && letGoProtected && { protected: true }),
    ...(detail && { detail }),
    inventory: inventoryCounts(bot)
  })

  const candidates = findCandidates(bot, mob, maxDistance, protect).slice(0, MAX_CANDIDATES)
  if (candidates.length === 0) return finish('not_found')

  // Travel: the first candidate that can be reached.
  const recording = recordTravel(bot, episode)
  let target = null
  let failure = 'escaped'
  try {
    for (const candidate of candidates) {
      signal?.throwIfAborted()
      if (Date.now() >= deadline) {
        failure = 'timeout'
        break
      }
      guard.setQuarry(candidate.entity.id)
      const end = await walkToTarget(bot, candidate.entity, {
        movements, maxDistance, protect, timeoutMs: Math.min(WALK_MS, deadline - Date.now()), signal
      })
      if (end === 'reached') {
        target = candidate.entity
        result.target = { id: target.id, ...cellOf(target.position), distance: round(candidate.distance, 1) }
        break
      }
      letGoProtected = end === 'protected'
      failure = end === 'protected' ? 'escaped' : end
    }
  } finally {
    result.travel = recording.finish()
    if (!target) guard.setQuarry(null)
  }
  if (!target) return finish(failure)

  // The step starts here.
  tracker.log.setPhase('step')
  result.inventory_before_step = inventoryCounts(bot)
  const kill = watchKill(bot, target, { onNearbyItem: (entity) => tracker.ownDrops.add(entity.id) })
  const targetInProtectedBox = () => {
    const live = bot.entities[target.id]
    if (!live || !insideAny(protect, live.position)) return false
    letGoProtected = true
    return true
  }
  let fought
  let seen
  try {
    fought = await fight(bot, target, {
      timeoutMs: Math.max(0, Math.min(fightMs, deadline - Date.now())), flee: false, sprint: true, weapon,
      shouldStop: () => signal?.aborted === true || targetInProtectedBox(), movements, eventLog: tracker.log, deadline
    })
    result.weapon_used = bot.heldItem?.name ?? null
    signal?.throwIfAborted()
    const diedAt = kill.death()?.at
    if (diedAt) await sleep(Math.max(0, diedAt + DROPS_AFTER_DEATH_MS - Date.now()))
    signal?.throwIfAborted()
  } finally {
    seen = kill.stop()
    guard.setQuarry(null)
  }
  const { death, items, others } = seen

  switch (fought.outcome) {
    case 'killed': break
    case 'lost': return finish('escaped')
    case 'timeout': return finish('timeout')
    case 'fled': return finish('interrupted', { threat: mob, outcome: 'fled' })
    default: return finish('quarantined') // died: the wrapper answers it first
  }
  result.killed = true
  result.death_position = cellOf(death.position)

  // The drops: items that spawned at the death spot, shortly before to shortly after it.
  const drops = {}
  const dropped = []
  for (const item of items) {
    const near = horizontalDistance(item.position, death.position) <= DROP_RADIUS
    const inWindow = item.at >= death.at - DROPS_BEFORE_DEATH_MS && item.at <= death.at + DROPS_AFTER_DEATH_MS
    if (!near || !inWindow) {
      // onNearbyItem marked it while the target was still alive or just before the window opened
      tracker.ownDrops.delete(item.entity.id)
      continue
    }
    const stack = item.stack ?? readDroppedStack(item.entity)
    if (!stack) {
      tracker.log.record('target_despawned', { detail: `drop entity ${item.entity.id} vanished before its stack was read` })
      continue
    }
    tracker.ownDrops.add(item.entity.id)
    drops[stack.name] = (drops[stack.name] ?? 0) + stack.count
    dropped.push(item.entity)
  }
  result.drops = drops
  for (const other of others) {
    const inWindow = other.at >= death.at - DROPS_BEFORE_DEATH_MS && other.at <= death.at + DROPS_AFTER_DEATH_MS
    if (inWindow && other.position.distanceTo(death.position) <= INTERFERENCE_RADIUS) {
      tracker.log.record('interference', { mob: other.entity.name, id: other.entity.id, position: cellOf(other.position) })
    }
  }

  await pickUpEntities(bot, dropped, { signal })
  await settleInventory(bot)
  const inventory = inventoryCounts(bot)
  result.picked_up = countGains(result.inventory_before_step, inventory)
  return finish(null)
}

module.exports = { attack, findCandidates, validateAttack }
