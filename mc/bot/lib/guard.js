// The bot's reflex layer: decides whether a nearby mob is a threat, then fights
// it or runs from it. Pure policy (pickThreat, decide), the fight and the
// flight, and createGuard, the stateful object server.js ticks. The bot is
// injected so none of this needs a server. See contracts/bot_api.md, "The guard".

const { goals } = require('mineflayer-pathfinder')
const { round } = require('./world')
const { parseOwners } = require('./chat_commands')

const MODES = ['off', 'passive', 'defend']

const NEAR_BLOCKS = 5
const CLOSING_NEAR_BLOCKS = 8
const ATTACKER_WINDOW_S = 3
const CREEPER_BLOCKS = 5
const LOW_HEALTH = 6
const SCAN_BLOCKS = 16
const EYE_HEIGHT = 1.62
const CLOSING_DROP_BLOCKS = 1
const CLOSING_SAMPLE_AGE_S = 0.8
const OFF_HAND_SLOT = 45
const POLL_MS = 50
const WINDOW_WAIT_MS = 10000
const RESPOND_CAP_MS = 30000

const EXTRA_HOSTILE = new Set(['slime', 'magma_cube', 'phantom'])
const NEUTRAL = new Set(['enderman', 'zombified_piglin', 'piglin'])
const MATERIALS = ['netherite', 'diamond', 'iron', 'stone', 'golden', 'wooden']
const BANNED_FOOD = new Set(['pufferfish', 'spider_eye', 'poisonous_potato', 'rotten_flesh', 'chorus_fruit',
  'chicken', 'suspicious_stew', 'golden_apple'])

function isHostile (entity) {
  if (NEUTRAL.has(entity.name)) return false
  return entity.kind === 'hostile' || EXTRA_HOSTILE.has(entity.name)
}

// entities: [{id, name, kind, position, closing}]; recentAttackers: Map<id, lastHitSeconds>.
// Returns {entity, mob, id, distance, reason: 'near' | 'attacked'} or null.
function pickThreat (entities, botPos, recentAttackers, now, canSee = () => true) {
  const candidates = []
  for (const entity of entities) {
    const distance = botPos.distanceTo(entity.position)
    const hitAt = recentAttackers.get(entity.id)
    if (hitAt !== undefined && now - hitAt <= ATTACKER_WINDOW_S) {
      candidates.push({ entity, mob: entity.name, id: entity.id, distance, reason: 'attacked', hitAt })
    } else if (isHostile(entity) && (distance <= NEAR_BLOCKS || (distance <= CLOSING_NEAR_BLOCKS && entity.closing)) &&
        canSee(entity)) {
      candidates.push({ entity, mob: entity.name, id: entity.id, distance, reason: 'near' })
    }
  }
  const rank = (c) => (c.mob === 'creeper' && c.distance < CREEPER_BLOCKS ? 0 : c.reason === 'attacked' ? 1 : 2)
  candidates.sort((a, b) => {
    if (rank(a) !== rank(b)) return rank(a) - rank(b)
    return rank(a) === 1 ? b.hitAt - a.hitAt : a.distance - b.distance
  })
  if (candidates.length === 0) return null
  const { entity, mob, id, distance, reason } = candidates[0]
  return { entity, mob, id, distance, reason }
}

// -> 'fight' | 'flee' | 'back_off' | 'ignore'
function decide (mode, threat, health) {
  if (mode === 'off' || !threat) return 'ignore'
  if (threat.mob === 'creeper' && threat.distance < CREEPER_BLOCKS) return 'back_off'
  if (health < LOW_HEALTH) return 'flee'
  return mode === 'passive' ? 'flee' : 'fight'
}

function sleep (ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// Best sword, else best axe, else null.
function bestWeapon (items) {
  const rankOf = (item) => MATERIALS.indexOf(item.name.split('_')[0])
  for (const suffix of ['_sword', '_axe']) {
    const found = items.filter((item) => item.name.endsWith(suffix) && rankOf(item) >= 0)
    if (found.length > 0) return found.sort((a, b) => rankOf(a) - rankOf(b))[0]
  }
  return null
}

// Equips the weapon (or the bare hand) and a shield; equip errors never end the fight.
async function equipForFight (bot) {
  const items = bot.inventory.items()
  const weapon = bestWeapon(items)
  try {
    if (weapon) await bot.equip(weapon, 'hand')
    else await bot.unequip('hand')
  } catch {}
  const shield = items.find((item) => item.name === 'shield' && item.slot !== OFF_HAND_SLOT)
  if (shield) await bot.equip(shield, 'off-hand').catch(() => {})
  return weapon?.name ?? null
}

// Listens for whatever ends a fight. `outcome` resolves once with the first one; `stop()` removes
// every listener and timer; `damageTaken()` sums the health drops so far.
function watchFight (bot, target, { timeoutMs, shouldStop }) {
  let damage = 0
  let lastHealth = bot.health
  let finish
  const outcome = new Promise((resolve) => { finish = resolve })

  const onHealth = () => {
    if (bot.health < lastHealth) damage += lastHealth - bot.health
    lastHealth = bot.health
    if (bot.health < LOW_HEALTH) finish('fled')
  }
  const onEntityDead = (entity) => { if (entity.id === target.id) finish('killed') }
  const onDeath = () => finish('died')
  const poll = setInterval(() => {
    const live = bot.entities[target.id]
    if (!live || bot.entity.position.distanceTo(live.position) > SCAN_BLOCKS || shouldStop()) finish('lost')
  }, POLL_MS)
  const timer = setTimeout(() => finish('timeout'), timeoutMs)
  bot.on('health', onHealth)
  bot.on('entityDead', onEntityDead)
  bot.on('death', onDeath)

  return {
    outcome,
    damageTaken: () => damage,
    stop () {
      clearInterval(poll)
      clearTimeout(timer)
      bot.removeListener('health', onHealth)
      bot.removeListener('entityDead', onEntityDead)
      bot.removeListener('death', onDeath)
    }
  }
}

// Fights `target` until an outcome. Resolves {outcome, damage_taken, duration_s}; never rejects.
async function fight (bot, target, { pvp = bot.pvp, timeoutMs = 30000, movements = null, eventLog = null, shouldStop = () => false, deadline = Infinity } = {}) {
  // `deadline` (ms since the epoch) bounds the whole call: the window wait, the fight and the flight after a `fled`.
  const startedAt = Date.now()
  const windowDeadline = Math.min(startedAt + WINDOW_WAIT_MS, deadline)
  while (bot.currentWindow && Date.now() < windowDeadline) await sleep(POLL_MS)

  const weapon = await equipForFight(bot)
  eventLog?.record('fight_start', {
    target: target.name, id: target.id, distance: round(bot.entity.position.distanceTo(target.position), 1), weapon
  })

  const watch = watchFight(bot, target, { timeoutMs: Math.max(0, Math.min(timeoutMs, deadline - Date.now())), shouldStop })
  let outcome
  try {
    if (movements) pvp.movements = movements
    pvp.attack(target)
    outcome = bot.health < LOW_HEALTH ? 'fled' : await watch.outcome
  } catch {
    outcome = 'lost'
  } finally {
    watch.stop()
    pvp.stop()
    bot.pathfinder.setGoal(null)
    if (movements) bot.pathfinder.setMovements(movements)
  }

  const result = {
    outcome,
    damage_taken: round(watch.damageTaken(), 1),
    duration_s: round((Date.now() - startedAt) / 1000, 1)
  }
  eventLog?.record('fight_end', { target: target.name, ...result })
  if (outcome === 'fled') await flee(bot, target, { minDistance: SCAN_BLOCKS, timeoutMs: Math.max(0, Math.min(10000, deadline - Date.now())), movements })
  return result
}

// Moves away from `from` until it is minDistance away, the time is up or the bot dies.
// Resolves {distance, died}; never rejects.
async function flee (bot, from, { minDistance, timeoutMs, movements = null } = {}) {
  let died = false
  const onDeath = () => { died = true }
  bot.on('death', onDeath)
  try {
    if (movements) bot.pathfinder.setMovements(movements)
    bot.pathfinder.setGoal(new goals.GoalInvert(new goals.GoalFollow(from, minDistance)), true)
    const deadline = Date.now() + timeoutMs
    while (!died && Date.now() < deadline && bot.entity.position.distanceTo(from.position) < minDistance) {
      await sleep(POLL_MS)
    }
  } catch {} finally {
    bot.removeListener('death', onDeath)
    bot.pathfinder.setGoal(null)
  }
  return { distance: round(bot.entity.position.distanceTo(from.position), 1), died }
}

// True when no block stands between the bot's eyes and the entity's middle.
function canSee (bot, entity) {
  if (!bot.world?.raycast) return true
  const eyes = bot.entity.position.offset(0, EYE_HEIGHT, 0)
  const middle = entity.position.offset(0, (entity.height ?? 0) / 2, 0)
  const toEntity = middle.minus(eyes)
  const distance = toEntity.norm()
  const block = bot.world.raycast(eyes, toEntity.normalize(), distance)
  return !block || eyes.distanceTo(block.intersect ?? block.position) >= distance
}

function createGuard (bot, { eventLog, mode = process.env.BOT_GUARD || 'defend', movements = () => null, now = () => Date.now() / 1000 } = {}) {
  assertMode(mode)
  const owners = parseOwners(process.env.BOT_OWNERS)
  const recentAttackers = new Map() // entity id -> seconds of its last hit on the bot
  const distanceSamples = new Map() // entity id -> [{t, distance}], oldest first
  let responding = false
  let fighting = null

  const onEntityHurt = (entity, source) => {
    if (!source || !entity || (entity !== bot.entity && entity.id !== bot.entity.id)) return
    if (source.type === 'player' && owners.includes(String(source.username).toLowerCase())) return // never fight an owner
    recentAttackers.set(source.id, now())
  }
  bot.on('entityHurt', onEntityHurt)

  // Distance to `entity` fell by CLOSING_DROP_BLOCKS or more since a sample at least CLOSING_SAMPLE_AGE_S old.
  function sampleClosing (id, distance, t) {
    const samples = distanceSamples.get(id) ?? []
    const reference = samples.filter((sample) => t - sample.t >= CLOSING_SAMPLE_AGE_S).pop()
    samples.push({ t, distance })
    distanceSamples.set(id, samples.slice(Math.max(0, samples.indexOf(reference))))
    return reference !== undefined && reference.distance - distance >= CLOSING_DROP_BLOCKS
  }

  function nearbyEntities () {
    const t = now()
    const botPos = bot.entity.position
    const list = []
    for (const entity of Object.values(bot.entities)) {
      if (entity === bot.entity || !entity.position) continue
      const distance = botPos.distanceTo(entity.position)
      if (distance > SCAN_BLOCKS) continue
      const kind = entity.type === 'player' ? 'player' : bot.registry.entitiesByName[entity.name]?.type
      list.push({ id: entity.id, name: entity.name, kind, position: entity.position, closing: sampleClosing(entity.id, distance, t) })
    }
    const ids = new Set(list.map((entity) => entity.id))
    for (const id of distanceSamples.keys()) if (!ids.has(id)) distanceSamples.delete(id)
    return list
  }

  async function eatIfHungry () {
    if (bot.health >= LOW_HEALTH || bot.food >= 20) return
    const food = bot.inventory.items().find((item) => bot.registry.foodsByName[item.name] && !BANNED_FOOD.has(item.name))
    if (!food) return
    try {
      await bot.equip(food, 'hand')
      await bot.consume()
    } catch {}
  }

  function recordFled (threat, reason) {
    eventLog?.record('fled', { from: threat.mob, reason, distance: round(threat.distance, 1), health: round(bot.health, 1) })
  }

  async function run ({ threat, decision }, deadline) {
    const remainingMs = (limitMs) => Math.max(0, Math.min(limitMs, deadline - Date.now()))
    const target = bot.entities[threat.id]
    if (!target) return { decision, outcome: 'lost' }
    if (decision === 'fight') {
      const result = await fight(bot, target, {
        timeoutMs: remainingMs(30000), movements: movements(), eventLog, shouldStop: () => mode === 'off', deadline
      })
      if (result.outcome === 'fled') recordFled(threat, 'low_health') // fight() has already run
      return { decision, outcome: result.outcome }
    }
    const backOff = decision === 'back_off'
    const reason = backOff ? 'creeper' : bot.health < LOW_HEALTH ? 'low_health' : 'passive'
    recordFled(threat, reason)
    const { died } = await flee(bot, target, backOff
      ? { minDistance: 8, timeoutMs: remainingMs(8000), movements: movements() }
      : { minDistance: SCAN_BLOCKS, timeoutMs: remainingMs(10000), movements: movements() })
    if (!died && !backOff && deadline - Date.now() > 0) await eatIfHungry()
    return { decision, outcome: died ? 'died' : 'fled' }
  }

  return {
    get mode () { return mode },
    setMode (next) {
      assertMode(next)
      mode = next
    },
    status: () => ({ mode, fighting }),

    // One look at bot.entities; null when there is nothing to do or a respond() is running.
    check () {
      if (responding || !bot.entity) return null
      const t = now()
      for (const [id, hitAt] of recentAttackers) if (t - hitAt > ATTACKER_WINDOW_S) recentAttackers.delete(id)
      const threat = pickThreat(nearbyEntities(), bot.entity.position, recentAttackers, t, (entity) => canSee(bot, bot.entities[entity.id]))
      const decision = decide(mode, threat, bot.health)
      return decision === 'ignore' ? null : { threat, decision }
    },

    // Never rejects; takes at most RESPOND_CAP_MS.
    async respond ({ threat, decision }, { action = null } = {}) {
      responding = true
      const deadline = Date.now() + RESPOND_CAP_MS
      fighting = threat.mob
      eventLog?.record('threat', { mob: threat.mob, id: threat.id, distance: round(threat.distance, 1), reason: threat.reason, action })
      let cap
      try {
        const capped = new Promise((resolve) => { cap = setTimeout(() => resolve({ decision, outcome: 'timeout' }), RESPOND_CAP_MS) })
        return await Promise.race([run({ threat, decision }, deadline), capped])
      } catch {
        return { decision, outcome: 'lost' }
      } finally {
        clearTimeout(cap)
        fighting = null
        responding = false
      }
    },

    dispose () { bot.removeListener('entityHurt', onEntityHurt) }
  }
}

function assertMode (mode) {
  if (!MODES.includes(mode)) throw new Error(`unknown guard mode: ${mode}`)
}

module.exports = { MODES, pickThreat, decide, fight, flee, createGuard }
