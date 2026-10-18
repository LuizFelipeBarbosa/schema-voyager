const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const registry = require('prismarine-registry')('1.21.1')
const { createEventLog } = require('../../lib/events')
const { MODES, pickThreat, decide, fight, createGuard } = require('../../lib/guard')

const origin = new Vec3(0, 64, 0)

function mob (id, name, x, extra = {}) {
  return { id, name, kind: registry.entitiesByName[name]?.type, position: new Vec3(x, 64, 0), closing: false, ...extra }
}

// ---- pickThreat ----

test('pickThreat: a visible hostile within 5 blocks, or within 8 and closing', () => {
  const none = new Map()
  assert.equal(pickThreat([mob(1, 'zombie', 5)], origin, none, 100).reason, 'near')
  assert.equal(pickThreat([mob(1, 'zombie', 5.5)], origin, none, 100), null)
  assert.equal(pickThreat([mob(1, 'zombie', 8, { closing: true })], origin, none, 100).id, 1)
  assert.equal(pickThreat([mob(1, 'zombie', 8.5, { closing: true })], origin, none, 100), null)
  assert.equal(pickThreat([mob(1, 'zombie', 7)], origin, none, 100), null)
})

test('pickThreat: out of sight, passive animals and neutral mobs are not threats', () => {
  const none = new Map()
  assert.equal(pickThreat([mob(1, 'zombie', 3)], origin, none, 100, () => false), null)
  assert.equal(pickThreat([mob(1, 'cow', 2)], origin, none, 100), null)
  assert.equal(pickThreat([mob(1, 'enderman', 2)], origin, none, 100), null)
  assert.equal(pickThreat([mob(1, 'zombified_piglin', 2), mob(2, 'piglin', 2)], origin, none, 100), null)
  assert.equal(pickThreat([mob(1, 'slime', 2)], origin, none, 100).mob, 'slime')
  assert.equal(pickThreat([mob(1, 'phantom', 2)], origin, none, 100).mob, 'phantom')
})

test('pickThreat: an enderman is a threat once it attacks, within 3 s only', () => {
  const enderman = mob(1, 'enderman', 12)
  assert.deepEqual(pickThreat([enderman], origin, new Map([[1, 98]]), 100).reason, 'attacked')
  assert.equal(pickThreat([enderman], origin, new Map([[1, 96.5]]), 100), null)
  assert.equal(pickThreat([enderman], origin, new Map([[1, 97]]), 100).id, 1)
})

test('pickThreat: an attacker that left the list is not a threat', () => {
  assert.equal(pickThreat([], origin, new Map([[1, 99]]), 100), null)
})

test('pickThreat: a creeper first, then the most recent attacker, then the nearest', () => {
  const creeper = mob(1, 'creeper', 4)
  const skeleton = mob(2, 'skeleton', 12)
  const spider = mob(3, 'spider', 12)
  const zombie = mob(4, 'zombie', 2)
  const husk = mob(5, 'husk', 4)
  const attackers = new Map([[2, 98], [3, 99]])
  assert.equal(pickThreat([zombie, skeleton, spider, creeper], origin, attackers, 100).id, 1)
  assert.equal(pickThreat([zombie, skeleton, spider], origin, attackers, 100).id, 3)
  assert.equal(pickThreat([husk, zombie], origin, new Map(), 100).id, 4)
  const farCreeper = mob(6, 'creeper', 6, { closing: true })
  assert.equal(pickThreat([farCreeper, zombie], origin, new Map(), 100).id, 6)
  assert.equal(pickThreat([zombie, skeleton, farCreeper], origin, attackers, 100).id, 6)
})

// ---- decide ----

test('decide: every mode against no threat, a creeper, low health and an ordinary threat', () => {
  const zombie = { mob: 'zombie', distance: 3 }
  const creeper = { mob: 'creeper', distance: 3 }
  const table = {
    off: ['ignore', 'ignore', 'ignore', 'ignore'],
    passive: ['ignore', 'back_off', 'flee', 'flee'],
    defend: ['ignore', 'back_off', 'flee', 'fight']
  }
  assert.deepEqual(Object.keys(table), MODES)
  for (const [mode, [noThreat, creeperNear, lowHealth, ordinary]] of Object.entries(table)) {
    assert.equal(decide(mode, null, 20), noThreat, `${mode} no threat`)
    assert.equal(decide(mode, creeper, 20), creeperNear, `${mode} creeper`)
    assert.equal(decide(mode, zombie, 5.5), lowHealth, `${mode} low health`)
    assert.equal(decide(mode, zombie, 20), ordinary, `${mode} ordinary`)
    assert.equal(decide(mode, zombie, 20, false), mode === 'defend' ? 'flee' : ordinary, `${mode} unarmed`)
    assert.equal(decide(mode, creeper, 20, false), creeperNear, `${mode} unarmed creeper`)
  }
  for (const mode of ['passive', 'defend']) {
    assert.equal(decide(mode, { mob: 'creeper', distance: 7 }, 20), 'back_off', `${mode} creeper at 7`)
    assert.equal(decide(mode, { mob: 'creeper', distance: 7, reason: 'attacked' }, 20), 'back_off', `${mode} creeper that attacked`)
  }
  assert.equal(decide('off', { mob: 'creeper', distance: 2 }, 20), 'ignore')
})

// ---- fight ----

function fakeFightBot ({ items = [], health = 20 } = {}) {
  const bot = new EventEmitter()
  const target = { id: 7, name: 'zombie', position: new Vec3(3, 64, 0) }
  bot.health = health
  bot.food = 20
  bot.entity = { id: 1, position: origin.clone() }
  bot.entities = { 7: target }
  bot.currentWindow = null
  bot.registry = registry
  bot.inventory = { items: () => items }
  bot.equipped = []
  bot.equip = async (item, slot) => { bot.equipped.push([item.name, slot]) }
  bot.unequip = async (slot) => { bot.equipped.push([null, slot]) }
  bot.setGoals = []
  bot.movementsSet = []
  bot.pathfinder = {
    setGoal: (goal) => bot.setGoals.push(goal),
    setMovements: (movements) => bot.movementsSet.push(movements)
  }
  const pvp = Object.assign(new EventEmitter(), {
    attacked: [],
    stops: 0,
    attack (entity) { this.attacked.push(entity) },
    stop () { this.stops++ }
  })
  bot.pvp = pvp
  return { bot, pvp, target }
}

const later = (ms, fn) => setTimeout(fn, ms)

test('fight: entityDead for the target is killed, and the end is cleaned up', async () => {
  const { bot, pvp, target } = fakeFightBot()
  const log = createEventLog()
  const movements = { name: 'safe' }
  later(30, () => bot.emit('entityDead', target))
  const result = await fight(bot, target, { timeoutMs: 1000, movements, eventLog: log })
  assert.equal(result.outcome, 'killed')
  assert.equal(pvp.attacked[0], target)
  assert.equal(pvp.movements, movements)
  assert.equal(pvp.stops, 1)
  assert.equal(bot.setGoals.at(-1), null)
  assert.deepEqual(bot.movementsSet, [movements])
  for (const event of ['health', 'entityDead', 'death']) assert.equal(bot.listenerCount(event), 0)
  assert.deepEqual(log.since(0).map((event) => event.type), ['fight_start', 'fight_end'])
  assert.equal(log.since(0)[0].weapon, null)
  assert.equal(log.since(0)[1].outcome, 'killed')
})

test('fight: an entityDead for another entity does not end it', async () => {
  const { bot, target } = fakeFightBot()
  later(10, () => bot.emit('entityDead', { id: 99 }))
  const result = await fight(bot, target, { timeoutMs: 120 })
  assert.equal(result.outcome, 'timeout')
})

test('fight: a target that left bot.entities is lost; so is one that is too far or when shouldStop turns true', async () => {
  const gone = fakeFightBot()
  later(30, () => { delete gone.bot.entities[7] })
  assert.equal((await fight(gone.bot, gone.target, { timeoutMs: 1000 })).outcome, 'lost')

  const far = fakeFightBot()
  later(30, () => { far.target.position = new Vec3(20, 64, 0) })
  assert.equal((await fight(far.bot, far.target, { timeoutMs: 1000 })).outcome, 'lost')

  const stopped = fakeFightBot()
  let stop = false
  later(30, () => { stop = true })
  assert.equal((await fight(stopped.bot, stopped.target, { timeoutMs: 1000, shouldStop: () => stop })).outcome, 'lost')
})

test('fight: health below 6 is fled, damage_taken sums the drops, and the bot flees', async () => {
  const { bot, target } = fakeFightBot()
  const log = createEventLog()
  later(20, () => { bot.health = 14; bot.emit('health') })
  later(40, () => { bot.health = 16; bot.emit('health') })
  later(60, () => { bot.health = 5; bot.emit('health') })
  // The flee ends once the target is 16 blocks away.
  later(90, () => { target.position = new Vec3(17, 64, 0) })
  const result = await fight(bot, target, { timeoutMs: 1000, eventLog: log })
  assert.equal(result.outcome, 'fled')
  assert.equal(log.since(0).at(-1).damage_taken, result.damage_taken)
  assert.equal(result.damage_taken, 17)
})

test('fight: the bot death is died; a small timeoutMs is timeout', async () => {
  const dying = fakeFightBot()
  later(20, () => dying.bot.emit('death'))
  assert.equal((await fight(dying.bot, dying.target, { timeoutMs: 1000 })).outcome, 'died')

  const slow = fakeFightBot()
  const result = await fight(slow.bot, slow.target, { timeoutMs: 60 })
  assert.equal(result.outcome, 'timeout')
  assert.equal(slow.pvp.stops, 1)
})

test('fight: the sword wins over the axe, and the shield goes to the off-hand', async () => {
  const items = [
    { name: 'diamond_axe', slot: 36 }, { name: 'stone_sword', slot: 37 },
    { name: 'iron_sword', slot: 38 }, { name: 'shield', slot: 39 }
  ]
  const { bot, target } = fakeFightBot({ items })
  const log = createEventLog()
  await fight(bot, target, { timeoutMs: 30, eventLog: log })
  assert.deepEqual(bot.equipped, [['iron_sword', 'hand'], ['shield', 'off-hand']])
  assert.equal(log.since(0)[0].weapon, 'iron_sword')
})

test('fight: an axe is used without a sword, a shield already in the off-hand stays, a failed equip does not end it', async () => {
  const { bot, target } = fakeFightBot({ items: [{ name: 'wooden_axe', slot: 36 }, { name: 'shield', slot: 45 }] })
  bot.equip = async () => { throw new Error('no') }
  const result = await fight(bot, target, { timeoutMs: 30 })
  assert.equal(result.outcome, 'timeout')

  const second = fakeFightBot({ items: [{ name: 'golden_axe', slot: 36 }, { name: 'netherite_axe', slot: 37 }, { name: 'shield', slot: 45 }] })
  await fight(second.bot, second.target, { timeoutMs: 30 })
  assert.deepEqual(second.bot.equipped, [['netherite_axe', 'hand']])
})

test('fight: waits for an open window to close first', async () => {
  const { bot, pvp, target } = fakeFightBot()
  bot.currentWindow = {}
  let attackedWhileOpen = false
  pvp.attack = function (entity) { attackedWhileOpen = bot.currentWindow !== null; this.attacked.push(entity) }
  later(120, () => { bot.currentWindow = null })
  await fight(bot, target, { timeoutMs: 30 })
  assert.equal(attackedWhileOpen, false)
})

// ---- createGuard ----

const sword = { name: 'iron_sword', slot: 36 }

function guardFixture ({ items = [sword], ...options } = {}) {
  const fixture = fakeFightBot({ items })
  fixture.bot.entity = { id: 1, position: origin.clone() }
  let clock = 1000
  const log = createEventLog()
  const guard = createGuard(fixture.bot, { eventLog: log, now: () => clock, ...options })
  return { ...fixture, guard, log, advance: (seconds) => { clock += seconds } }
}

test('guard: an entityHurt with a source makes that entity an attacked threat', () => {
  const { bot, guard } = guardFixture({ mode: 'defend' })
  delete bot.entities[7]
  bot.entities[8] = { id: 8, name: 'skeleton', position: new Vec3(12, 64, 0) }
  assert.equal(guard.check(), null)
  bot.emit('entityHurt', { id: 99 }, bot.entities[8]) // another entity's hurt
  assert.equal(guard.check(), null)
  bot.emit('entityHurt', bot.entity, bot.entities[8])
  const { threat, decision } = guard.check()
  assert.equal(threat.mob, 'skeleton')
  assert.equal(threat.reason, 'attacked')
  assert.equal(decision, 'fight')
  guard.dispose()
  assert.equal(bot.listenerCount('entityHurt'), 0)
})

test('guard: a hit from an owner is no threat, from another player or a zombie it is', () => {
  const previous = process.env.BOT_OWNERS
  process.env.BOT_OWNERS = 'Otopo,Tester'
  try {
    const { bot, guard } = guardFixture({ mode: 'defend' })
    delete bot.entities[7]
    bot.entities[8] = { id: 8, type: 'player', username: 'otopo', name: 'player', position: new Vec3(3, 64, 0) }
    bot.entities[9] = { id: 9, type: 'player', username: 'Stranger', name: 'player', position: new Vec3(3, 64, 0) }
    bot.entities[10] = { id: 10, type: 'hostile', name: 'zombie', position: new Vec3(12, 64, 0) }

    bot.emit('entityHurt', bot.entity, bot.entities[8])
    assert.equal(guard.check(), null)
    bot.emit('entityHurt', bot.entity, bot.entities[9])
    assert.equal(guard.check().threat.reason, 'attacked')
    delete bot.entities[9]
    bot.emit('entityHurt', bot.entity, bot.entities[10])
    assert.equal(guard.check().threat.mob, 'zombie')
  } finally {
    if (previous === undefined) delete process.env.BOT_OWNERS
    else process.env.BOT_OWNERS = previous
  }
})

test('guard: a near zombie is a threat in defend, ignored in off, and a closing one at 8 blocks counts', () => {
  const { bot, target, guard, advance } = guardFixture({ mode: 'defend' })
  assert.equal(guard.check().threat.reason, 'near')
  guard.setMode('off')
  assert.equal(guard.check(), null)
  guard.setMode('passive')
  assert.equal(guard.check().decision, 'flee')

  target.position = new Vec3(8, 64, 0)
  assert.equal(guard.check(), null)
  advance(1)
  target.position = new Vec3(7, 64, 0)
  assert.equal(guard.check().threat.id, 7)
  assert.equal(bot.entities[7], target)
})

test('guard: a mob that died is no threat while its body is in bot.entities, and its id is forgotten after entityGone', () => {
  const { bot, target, guard } = guardFixture({ mode: 'defend' })
  assert.equal(guard.check().threat.id, 7)
  bot.emit('entityDead', target)
  assert.equal(guard.check(), null)
  assert.equal(guard.check(), null)
  bot.emit('entityGone', target)
  assert.equal(guard.check().threat.id, 7) // the id is back in use
  guard.dispose()
  assert.equal(bot.listenerCount('entityDead'), 0)
  assert.equal(bot.listenerCount('entityGone'), 0)
})

test('guard: the id of a dead mob that left bot.entities without entityGone is pruned', () => {
  const { bot, target, guard } = guardFixture({ mode: 'defend' })
  bot.emit('entityDead', target)
  assert.equal(guard.check(), null)
  delete bot.entities[7]
  assert.equal(guard.check(), null)
  bot.entities[7] = target
  assert.equal(guard.check().threat.id, 7)
})

test('guard: a creeper 7 blocks away and closing is a back_off in defend, and one that hit the bot too', () => {
  const { bot, guard, advance } = guardFixture({ mode: 'defend' })
  delete bot.entities[7]
  bot.entities[8] = { id: 8, name: 'creeper', position: new Vec3(8, 64, 0) }
  assert.equal(guard.check(), null)
  advance(1)
  bot.entities[8].position = new Vec3(7, 64, 0)
  assert.equal(guard.check().decision, 'back_off')
  bot.entities[8].position = new Vec3(12, 64, 0)
  assert.equal(guard.check(), null)
  bot.emit('entityHurt', bot.entity, bot.entities[8])
  assert.equal(guard.check().decision, 'back_off')
})

test('guard: setMode rejects an unknown mode, and the default comes from BOT_GUARD', () => {
  const { guard } = guardFixture({ mode: 'passive' })
  assert.throws(() => guard.setMode('bogus'), /mode/)
  assert.equal(guard.mode, 'passive')
  const previous = process.env.BOT_GUARD
  process.env.BOT_GUARD = 'off'
  try {
    assert.equal(guardFixture().guard.mode, 'off')
    delete process.env.BOT_GUARD
    assert.equal(guardFixture().guard.mode, 'defend')
  } finally {
    if (previous === undefined) delete process.env.BOT_GUARD
    else process.env.BOT_GUARD = previous
  }
})

test('guard: status shows fighting during respond, check is null meanwhile, and respond records threat', async () => {
  const { bot, target, guard, log } = guardFixture({ mode: 'defend' })
  const picked = guard.check()
  assert.deepEqual(guard.status(), { mode: 'defend', fighting: null })
  const responding = guard.respond(picked, { action: '/mine' })
  assert.deepEqual(guard.status(), { mode: 'defend', fighting: 'zombie' })
  assert.equal(guard.check(), null)
  await new Promise((resolve) => setTimeout(resolve, 30))
  bot.emit('entityDead', target)
  assert.deepEqual(await responding, { decision: 'fight', outcome: 'killed' })
  assert.deepEqual(guard.status(), { mode: 'defend', fighting: null })
  const threat = log.since(0).find((event) => event.type === 'threat')
  assert.deepEqual([threat.mob, threat.id, threat.reason, threat.action], ['zombie', 7, 'near', '/mine'])
})

test('guard: setMode off ends a running fight', async () => {
  const { guard } = guardFixture({ mode: 'defend' })
  const responding = guard.respond(guard.check())
  setTimeout(() => guard.setMode('off'), 30)
  assert.equal((await responding).outcome, 'lost')
})

test('guard: a passive threat is fled from and recorded', async () => {
  const { target, guard, log } = guardFixture({ mode: 'passive' })
  target.position = new Vec3(3, 64, 0)
  const picked = guard.check()
  setTimeout(() => { target.position = new Vec3(17, 64, 0) }, 30)
  assert.deepEqual(await guard.respond(picked), { decision: 'flee', outcome: 'fled' })
  const fled = log.since(0).find((event) => event.type === 'fled')
  assert.deepEqual([fled.from, fled.reason, fled.health], ['zombie', 'passive', 20])
})

test('guard: a fight that ends fled records fled and leaves no path goal set', async () => {
  const { bot, target, guard, log } = guardFixture({ mode: 'defend' })
  const picked = guard.check()
  setTimeout(() => { bot.health = 5; bot.emit('health') }, 20)
  setTimeout(() => { target.position = new Vec3(17, 64, 0) }, 60)
  assert.deepEqual(await guard.respond(picked), { decision: 'fight', outcome: 'fled' })
  assert.equal(bot.setGoals.at(-1), null)
  const fled = log.since(0).find((event) => event.type === 'fled')
  assert.deepEqual([fled.from, fled.reason, fled.health], ['zombie', 'low_health', 5])
})

test('fight: the deadline bounds the flight after a fled', async () => {
  const { bot, target } = fakeFightBot()
  later(20, () => { bot.health = 5; bot.emit('health') })
  const startedAt = Date.now()
  const result = await fight(bot, target, { timeoutMs: 1000, deadline: Date.now() + 150 })
  assert.equal(result.outcome, 'fled')
  assert.ok(Date.now() - startedAt < 400)
  assert.equal(bot.setGoals.at(-1), null)
})

test('guard: check is null before the bot has spawned', () => {
  const { bot, guard } = guardFixture({ mode: 'defend' })
  bot.entity = undefined
  assert.equal(guard.check(), null)
})

test('guard: an unarmed bot flees in defend and records fled unarmed; an armed one fights', async () => {
  const unarmed = guardFixture({ mode: 'defend', items: [{ name: 'iron_pickaxe', slot: 36 }] })
  const picked = unarmed.guard.check()
  assert.equal(picked.decision, 'flee')
  setTimeout(() => { unarmed.target.position = new Vec3(17, 64, 0) }, 30)
  assert.deepEqual(await unarmed.guard.respond(picked), { decision: 'flee', outcome: 'fled' })
  const fled = unarmed.log.since(0).find((event) => event.type === 'fled')
  assert.deepEqual([fled.from, fled.reason], ['zombie', 'unarmed'])
  assert.equal(unarmed.pvp.attacked.length, 0)

  assert.equal(guardFixture({ mode: 'defend', items: [{ name: 'stone_axe', slot: 36 }] }).guard.check().decision, 'fight')
  assert.equal(guardFixture({ mode: 'defend' }).guard.check().decision, 'fight')
})

test('guard: a target that stays out of reach ends the fight lost and the guard flees from it', async () => {
  const { target, guard, log, bot } = guardFixture({ mode: 'defend', outOfReachMs: 100 })
  const picked = guard.check()
  target.position = new Vec3(6, 64, 0)
  setTimeout(() => { target.position = new Vec3(17, 64, 0) }, 400)
  assert.deepEqual(await guard.respond(picked), { decision: 'fight', outcome: 'lost' })
  const events = log.since(0)
  assert.equal(events.find((event) => event.type === 'fight_end').outcome, 'lost')
  assert.equal(events.find((event) => event.type === 'fled').reason, 'out_of_reach')
  assert.equal(bot.setGoals.at(-1), null)
})

test('fight: a target that comes back within reach resets the out-of-reach time', async () => {
  const { bot, target } = fakeFightBot()
  target.position = new Vec3(6, 64, 0)
  later(70, () => { target.position = new Vec3(3, 64, 0) })
  later(120, () => { target.position = new Vec3(6, 64, 0) })
  const result = await fight(bot, target, { timeoutMs: 260, outOfReachMs: 150 })
  assert.equal(result.outcome, 'timeout')
})

test('guard: a target whose fight ended timeout or fled is only fled from until entityGone', async () => {
  const timedOut = guardFixture({ mode: 'defend', respondCapMs: 5100 }) // the fight times out after 100 ms
  assert.equal((await timedOut.guard.respond(timedOut.guard.check())).outcome, 'timeout')
  assert.equal(timedOut.guard.check().decision, 'flee')
  timedOut.bot.emit('entityGone', timedOut.target)
  assert.equal(timedOut.guard.check().decision, 'fight')

  const fled = guardFixture({ mode: 'defend' })
  const picked = fled.guard.check()
  setTimeout(() => { fled.bot.health = 5; fled.bot.emit('health') }, 20)
  setTimeout(() => { fled.target.position = new Vec3(17, 64, 0) }, 60)
  await fled.guard.respond(picked)
  fled.bot.health = 20
  fled.target.position = new Vec3(3, 64, 0)
  const again = fled.guard.check()
  assert.equal(again.decision, 'flee')
  setTimeout(() => { fled.target.position = new Vec3(17, 64, 0) }, 30)
  await fled.guard.respond(again)
  assert.equal(fled.log.since(0).filter((event) => event.type === 'fled').at(-1).reason, 'beaten')
  assert.equal(fled.pvp.attacked.length, 1)

  fled.target.position = new Vec3(3, 64, 0)
  delete fled.bot.entities[7]
  fled.guard.check() // prunes the id without entityGone
  fled.bot.entities[7] = fled.target
  assert.equal(fled.guard.check().decision, 'fight')
})

test('guard: a killed target is not remembered as beaten', async () => {
  const { bot, target, guard } = guardFixture({ mode: 'defend' })
  const responding = guard.respond(guard.check())
  setTimeout(() => bot.emit('entityDead', target), 30)
  assert.equal((await responding).outcome, 'killed')
  bot.emit('entityGone', target)
  assert.equal(guard.check().decision, 'fight')
})

test('guard: respond resolves only after the fight cleanup, with fight_end already recorded', async () => {
  const { bot, pvp, guard, log } = guardFixture({ mode: 'defend', respondCapMs: 5100 })
  const result = await guard.respond(guard.check())
  assert.equal(result.outcome, 'timeout')
  assert.equal(pvp.stops, 1)
  assert.equal(bot.setGoals.at(-1), null)
  assert.equal(log.since(0).at(-1).type, 'fight_end')
})

// ---- fight: tactics and fleeBelow ----

test('fight: tactics attach right after pvp.attack and detach before pvp.stop', async () => {
  const { bot, pvp, target } = fakeFightBot()
  const order = []
  pvp.attack = function (entity) { order.push('pvp.attack') }
  pvp.stop = function () { order.push('pvp.stop') }
  const tactics = { attach: (entity) => order.push(`attach:${entity.id}`), detach: () => order.push('detach') }
  later(20, () => bot.emit('entityDead', target))
  const result = await fight(bot, target, { timeoutMs: 1000, tactics })
  assert.equal(result.outcome, 'killed')
  assert.deepEqual(order, ['pvp.attack', 'attach:7', 'detach', 'pvp.stop'])
})

test('fight: fleeBelow 0 keeps fighting at 3 health; the default ends fled below 6', async () => {
  const bold = fakeFightBot()
  later(20, () => { bold.bot.health = 3; bold.bot.emit('health') })
  later(60, () => bold.bot.emit('entityDead', bold.target))
  assert.equal((await fight(bold.bot, bold.target, { timeoutMs: 1000, fleeBelow: 0 })).outcome, 'killed')

  const startsLow = fakeFightBot({ health: 3 })
  later(10, () => startsLow.bot.emit('entityDead', startsLow.target))
  assert.equal((await fight(startsLow.bot, startsLow.target, { timeoutMs: 1000, fleeBelow: 0 })).outcome, 'killed')

  const careful = fakeFightBot()
  later(20, () => { careful.bot.health = 3; careful.bot.emit('health') })
  later(50, () => { careful.target.position = new Vec3(17, 64, 0) })
  assert.equal((await fight(careful.bot, careful.target, { timeoutMs: 1000 })).outcome, 'fled')
})
