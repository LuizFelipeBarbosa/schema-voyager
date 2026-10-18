// The bot's reflex layer: decides whether a nearby mob is a threat, then fights
// it or runs from it. Pure policy (pickThreat, decide), the fight and the
// flight, and createGuard, the stateful object server.js ticks. The bot is
// injected so none of this needs a server. See contracts/bot_api.md, "The guard".

const { goals } = require('mineflayer-pathfinder')
const { round } = require('./world')
const { parseOwners } = require('./chat_commands')
const { createTactics, DEFAULT_TACTICS } = require('./tactics')

const MODES = ['off', 'passive', 'defend']

const NEAR_BLOCKS = 5
const CLOSING_NEAR_BLOCKS = 8
const ATTACKER_WINDOW_S = 3
const LOW_HEALTH = 6
const SCAN_BLOCKS = 16
const EYE_HEIGHT = 1.62
const CLOSING_DROP_BLOCKS = 1
const CLOSING_SAMPLE_AGE_S = 0.8
const OFF_HAND_SLOT = 45
const POLL_MS = 50
const WINDOW_WAIT_MS = 10000
const RESPOND_CAP_MS = 30000
const FIGHT_CLEANUP_MARGIN_MS = 5000 // the fight's own timeout ends this long before the cap
const RESPOND_BACKSTOP_MS = 10000 // past the cap: only if run() hangs
const REACH_BLOCKS = 4.5 // pvp's attackRange is 3.5
const OUT_OF_REACH_MS = 5000
const RANGED_OUT_OF_REACH_MS = 8000 // ranged mobs keep their range, so the bot gets longer to close in

const RANGED = new Set(['skeleton', 'stray', 'bogged', 'pillager', 'witch'])
const EXTRA_HOSTILE = new Set(['slime', 'magma_cube', 'phantom'])
const NEUTRAL = new Set(['enderman', 'zombified_piglin', 'piglin'])
const MATERIALS = ['netherite', 'diamond', 'iron', 'stone', 'golden', 'wooden']
const BANNED_FOOD = new Set(['pufferfish', 'spider_eye', 'poisonous_potato', 'rotten_flesh', 'chorus_fruit',
  'chicken', 'suspicious_stew', 'golden_apple'])

function isHostile (entity) {
  if (NEUTRAL.has(entity.name)) return false
  return entity.kind === 'hostile' || EXTRA_HOSTILE.has(entity.name)
}

// entities: [{id, name, kind, position, closing, playerDistance, playerClosing}]; recentAttackers and
// playerAttackers (who hit the followed player): Map<id, lastHitSeconds>. playerDistance is set only while a
// player is being protected. Returns {entity, mob, id, distance, reason: 'near' | 'attacked' | 'player_attacked'
// | 'player_near'} or null. Players are never a threat for the two player_ reasons.
function pickThreat (entities, botPos, recentAttackers, now, canSee = () => true, playerAttackers = new Map()) {
  const isRecent = (hitAt) => hitAt !== undefined && now - hitAt <= ATTACKER_WINDOW_S
  const candidates = []
  for (const entity of entities) {
    const distance = botPos.distanceTo(entity.position)
    const hitAt = recentAttackers.get(entity.id)
    const hitPlayerAt = playerAttackers.get(entity.id)
    const candidate = { entity, mob: entity.name, id: entity.id, distance }
    const hostile = isHostile(entity)
    if (isRecent(hitAt)) {
      candidates.push({ ...candidate, reason: 'attacked', hitAt })
    } else if (isRecent(hitPlayerAt) && entity.kind !== 'player') {
      candidates.push({ ...candidate, reason: 'player_attacked', hitAt: hitPlayerAt })
    } else if (hostile && (distance <= NEAR_BLOCKS || (distance <= CLOSING_NEAR_BLOCKS && entity.closing)) && canSee(entity)) {
      candidates.push({ ...candidate, reason: 'near' })
    } else if (hostile && entity.playerDistance !== undefined &&
        (entity.playerDistance <= NEAR_BLOCKS || (entity.playerDistance <= CLOSING_NEAR_BLOCKS && entity.playerClosing)) && canSee(entity)) {
      candidates.push({ ...candidate, reason: 'player_near' })
    }
  }
  const rank = (c) => (c.mob === 'creeper' ? 0 : c.reason === 'attacked' ? 1 : c.reason === 'player_attacked' ? 2 : 3)
  candidates.sort((a, b) => {
    if (rank(a) !== rank(b)) return rank(a) - rank(b)
    return rank(a) === 1 || rank(a) === 2 ? b.hitAt - a.hitAt : a.distance - b.distance
  })
  if (candidates.length === 0) return null
  const { entity, mob, id, distance, reason } = candidates[0]
  return { entity, mob, id, distance, reason }
}

// `armed`: the bot holds a sword or an axe. -> 'fight' | 'flee' | 'back_off' | 'ignore'
function decide (mode, threat, health, armed = true) {
  if (mode === 'off' || !threat) return 'ignore'
  if (threat.mob === 'creeper') return 'back_off' // never fought: an explosion at point blank can take about 20 health
  if (health < LOW_HEALTH) return 'flee'
  return mode === 'passive' || !armed ? 'flee' : 'fight'
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
// `preferred`: an item name that wins over bestWeapon when the bot holds it.
async function equipForFight (bot, preferred = null) {
  const items = bot.inventory.items()
  const weapon = items.find((item) => item.name === preferred) ?? bestWeapon(items)
  try {
    if (weapon) await bot.equip(weapon, 'hand')
    else await bot.unequip('hand')
  } catch {}
  const shield = items.find((item) => item.name === 'shield' && item.slot !== OFF_HAND_SLOT)
  if (shield) await bot.equip(shield, 'off-hand').catch(() => {})
  return weapon?.name ?? null
}

// Listens for whatever ends a fight. `outcome` resolves once with the first one; `stop()` removes
// every listener and timer; `damageTaken()` sums the health drops so far. A target farther than
// REACH_BLOCKS for outOfReachMs in a row ends it `lost`, and `wasOutOfReach()` is then true.
function watchFight (bot, target, { timeoutMs, shouldStop, outOfReachMs, fleeBelow = LOW_HEALTH }) {
  let damage = 0
  let outOfReach = false
  let farSince = null
  let lastHealth = bot.health
  let finish
  const outcome = new Promise((resolve) => { finish = resolve })

  const onHealth = () => {
    if (bot.health < lastHealth) damage += lastHealth - bot.health
    lastHealth = bot.health
    if (bot.health < fleeBelow) finish('fled')
  }
  const onEntityDead = (entity) => { if (entity.id === target.id) finish('killed') }
  const onDeath = () => finish('died')
  const poll = setInterval(() => {
    const live = bot.entities[target.id]
    const distance = live ? bot.entity.position.distanceTo(live.position) : Infinity
    if (distance > SCAN_BLOCKS || shouldStop()) finish('lost')
    if (distance <= REACH_BLOCKS) {
      farSince = null
    } else {
      farSince ??= Date.now()
      if (Date.now() - farSince >= outOfReachMs) {
        outOfReach = true
        finish('lost')
      }
    }
  }, POLL_MS)
  const timer = setTimeout(() => finish('timeout'), timeoutMs)
  bot.on('health', onHealth)
  bot.on('entityDead', onEntityDead)
  bot.on('death', onDeath)

  return {
    outcome,
    damageTaken: () => damage,
    wasOutOfReach: () => outOfReach,
    stop () {
      clearInterval(poll)
      clearTimeout(timer)
      bot.removeListener('health', onHealth)
      bot.removeListener('entityDead', onEntityDead)
      bot.removeListener('death', onDeath)
    }
  }
}

// Fights `target` until an outcome. Resolves {outcome, damage_taken, duration_s, out_of_reach}; never rejects.
// After a `fled`, or a `lost` because the target stayed out of reach (unless `flee` is false), it flees from the target.
// `sprint`: the fight paths with a copy of `movements` that may sprint; the copy is never set back.
// `weapon`: the item name to prefer over the best sword or axe.
// `tactics`: a controller from lib/tactics.js, attached right after pvp.attack and detached first in the cleanup.
// `fleeBelow`: the health under which the fight ends `fled`.
// `fleeMs`, `fleeDistance`: the time limit and the distance of the flight after a `fled` or a `lost`.
async function fight (bot, target, { pvp = bot.pvp, timeoutMs = 30000, movements = null, eventLog = null, shouldStop = () => false, deadline = Infinity, outOfReachMs = OUT_OF_REACH_MS, flee: fleeAfterLost = true, sprint = false, weapon: preferredWeapon = null, tactics = null, fleeBelow = LOW_HEALTH, fleeMs = 10000, fleeDistance = SCAN_BLOCKS } = {}) {
  // `deadline` (ms since the epoch) bounds the whole call: the window wait, the fight and the flight after a `fled`.
  const startedAt = Date.now()
  const windowDeadline = Math.min(startedAt + WINDOW_WAIT_MS, deadline)
  while (bot.currentWindow && Date.now() < windowDeadline) await sleep(POLL_MS)

  const weapon = await equipForFight(bot, preferredWeapon)
  eventLog?.record('fight_start', {
    target: target.name, id: target.id, distance: round(bot.entity.position.distanceTo(target.position), 1), weapon
  })

  const watch = watchFight(bot, target, { timeoutMs: Math.max(0, Math.min(timeoutMs, deadline - Date.now())), shouldStop, outOfReachMs, fleeBelow })
  let outcome
  try {
    if (movements) pvp.movements = sprint ? sprintingCopy(movements) : movements
    pvp.attack(target)
    tactics?.attach(target)
    outcome = bot.health < fleeBelow ? 'fled' : await watch.outcome
  } catch {
    outcome = 'lost'
  } finally {
    tactics?.detach()
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
  const outOfReach = outcome === 'lost' && watch.wasOutOfReach()
  eventLog?.record('fight_end', { target: target.name, id: target.id, ...result })
  if (outcome === 'fled' || (outOfReach && fleeAfterLost)) await flee(bot, target, { minDistance: fleeDistance, timeoutMs: Math.max(0, Math.min(fleeMs, deadline - Date.now())), movements })
  return { ...result, out_of_reach: outOfReach }
}

function sprintingCopy (movements) {
  const copy = Object.assign(Object.create(Object.getPrototypeOf(movements)), movements)
  copy.allowSprinting = true
  return copy
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

function createGuard (bot, { eventLog, mode = process.env.BOT_GUARD || 'defend', movements = () => null, now = () => Date.now() / 1000, respondCapMs = RESPOND_CAP_MS, outOfReachMs = OUT_OF_REACH_MS, tactics = () => createTactics(bot, DEFAULT_TACTICS) } = {}) {
  assertMode(mode)
  const owners = parseOwners(process.env.BOT_OWNERS)
  const recentAttackers = new Map() // entity id -> seconds of its last hit on the bot
  const playerAttackers = new Map() // entity id -> seconds of its last hit on the protected player
  const distanceSamples = new Map() // entity id -> [{t, distance}] to the bot, oldest first
  const playerDistanceSamples = new Map() // the same, to the protected player
  const deadIds = new Set() // entities that died but are still in bot.entities until entityGone
  const beatenIds = new Set() // targets whose fight ended timeout, fled or out of reach, until entityGone
  let responding = false
  let fighting = null
  let quarryId = null // /attack's target: never a threat
  let protectedName = null // the followed player, whose attackers and nearby hostiles are threats

  const onEntityHurt = (entity, source) => {
    if (!source || !entity) return
    if (protectedName && entity.id === bot.players[protectedName]?.entity?.id) {
      if (source.type !== 'player') playerAttackers.set(source.id, now())
      return
    }
    if (entity !== bot.entity && entity.id !== bot.entity.id) return
    if (source.type === 'player' && owners.includes(String(source.username).toLowerCase())) return // never fight an owner
    recentAttackers.set(source.id, now())
  }
  const onEntityDead = (entity) => deadIds.add(entity.id)
  const onEntityGone = (entity) => {
    deadIds.delete(entity.id)
    beatenIds.delete(entity.id)
  }
  bot.on('entityHurt', onEntityHurt)
  bot.on('entityDead', onEntityDead)
  bot.on('entityGone', onEntityGone)

  // Distance to `entity` fell by CLOSING_DROP_BLOCKS or more since a sample at least CLOSING_SAMPLE_AGE_S old.
  function sampleClosing (distanceSamples, id, distance, t) {
    const samples = distanceSamples.get(id) ?? []
    const reference = samples.filter((sample) => t - sample.t >= CLOSING_SAMPLE_AGE_S).pop()
    samples.push({ t, distance })
    distanceSamples.set(id, samples.slice(Math.max(0, samples.indexOf(reference))))
    return reference !== undefined && reference.distance - distance >= CLOSING_DROP_BLOCKS
  }

  function nearbyEntities () {
    const t = now()
    const botPos = bot.entity.position
    const playerPos = protectedName ? bot.players[protectedName]?.entity?.position : undefined
    const list = []
    for (const ids of [deadIds, beatenIds]) for (const id of ids) if (!bot.entities[id]) ids.delete(id)
    for (const entity of Object.values(bot.entities)) {
      if (entity === bot.entity || !entity.position || deadIds.has(entity.id) || entity.id === quarryId) continue
      const distance = botPos.distanceTo(entity.position)
      if (distance > SCAN_BLOCKS) continue
      const kind = entity.type === 'player' ? 'player' : bot.registry.entitiesByName[entity.name]?.type
      const playerDistance = playerPos?.distanceTo(entity.position)
      list.push({
        id: entity.id, name: entity.name, kind, position: entity.position, closing: sampleClosing(distanceSamples, entity.id, distance, t),
        playerDistance, playerClosing: playerDistance !== undefined && sampleClosing(playerDistanceSamples, entity.id, playerDistance, t)
      })
    }
    const ids = new Set(list.map((entity) => entity.id))
    for (const samples of [distanceSamples, playerDistanceSamples]) for (const id of samples.keys()) if (!ids.has(id)) samples.delete(id)
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

  function isArmed () {
    return bestWeapon(bot.inventory.items()) !== null
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
        timeoutMs: remainingMs(respondCapMs - FIGHT_CLEANUP_MARGIN_MS), movements: movements(), eventLog,
        shouldStop: () => mode === 'off', deadline, sprint: true, tactics: tactics(),
        outOfReachMs: RANGED.has(threat.mob) ? RANGED_OUT_OF_REACH_MS : outOfReachMs
      })
      // fight() has already run the flight
      if (result.outcome === 'fled') recordFled(threat, 'low_health')
      if (result.out_of_reach) recordFled(threat, 'out_of_reach')
      if (['timeout', 'fled'].includes(result.outcome) || result.out_of_reach) beatenIds.add(threat.id)
      return { decision, outcome: result.outcome }
    }
    const backOff = decision === 'back_off'
    const reason = backOff ? 'creeper'
      : bot.health < LOW_HEALTH ? 'low_health'
        : mode === 'passive' ? 'passive'
          : beatenIds.has(threat.id) ? 'beaten' : 'unarmed'
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
    setQuarry (id) { quarryId = id },
    setProtected (username) {
      protectedName = username
      playerAttackers.clear()
      playerDistanceSamples.clear()
    },

    // One look at bot.entities; null when there is nothing to do or a respond() is running.
    check () {
      if (responding || !bot.entity) return null
      const t = now()
      for (const [id, hitAt] of recentAttackers) if (t - hitAt > ATTACKER_WINDOW_S) recentAttackers.delete(id)
      for (const [id, hitAt] of playerAttackers) if (t - hitAt > ATTACKER_WINDOW_S) playerAttackers.delete(id)
      const threat = pickThreat(nearbyEntities(), bot.entity.position, recentAttackers, t, (entity) => canSee(bot, bot.entities[entity.id]), playerAttackers)
      const decision = decide(mode, threat, bot.health, isArmed() && !beatenIds.has(threat?.id))
      return decision === 'ignore' ? null : { threat, decision }
    },

    // Never rejects; run() finishes its cleanup within respondCapMs, and a backstop past that only fires if it hangs.
    async respond ({ threat, decision }, { action = null } = {}) {
      responding = true
      const deadline = Date.now() + respondCapMs
      fighting = threat.mob
      eventLog?.record('threat', { mob: threat.mob, id: threat.id, distance: round(threat.distance, 1), reason: threat.reason, action })
      let cap
      try {
        const capped = new Promise((resolve) => {
          cap = setTimeout(() => {
            console.error(`guard: run() still going ${RESPOND_BACKSTOP_MS} ms past the deadline; answering timeout`)
            resolve({ decision, outcome: 'timeout' })
          }, respondCapMs + RESPOND_BACKSTOP_MS)
        })
        return await Promise.race([run({ threat, decision }, deadline), capped])
      } catch {
        return { decision, outcome: 'lost' }
      } finally {
        clearTimeout(cap)
        fighting = null
        responding = false
      }
    },

    dispose () {
      bot.removeListener('entityHurt', onEntityHurt)
      bot.removeListener('entityDead', onEntityDead)
      bot.removeListener('entityGone', onEntityGone)
    }
  }
}

function assertMode (mode) {
  if (!MODES.includes(mode)) throw new Error(`unknown guard mode: ${mode}`)
}

module.exports = { MODES, pickThreat, decide, fight, flee, createGuard }
