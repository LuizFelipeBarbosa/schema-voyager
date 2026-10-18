const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const registry = require('prismarine-registry')('1.21.1')
const { attack, findCandidates, validateAttack } = require('../../lib/attack')
const { createEventLog } = require('../../lib/events')
const { fight, createGuard } = require('../../lib/guard')
const { look } = require('../../lib/targets')
const { fakeWorldBot } = require('./world_fixture')

const later = (ms, fn) => setTimeout(fn, ms)
const BABY_INDEX = registry.entitiesByName.cow.metadataKeys.indexOf('baby')

// ---- validateAttack ----

test('validateAttack: mob, weapon, max_distance and protect', () => {
  assert.equal(validateAttack({ mob: 'cow' }), null)
  assert.equal(validateAttack({ mob: 'cow', weapon: null, max_distance: 64, protect: [] }), null)
  assert.match(validateAttack({}), /mob/)
  assert.match(validateAttack({ mob: '' }), /mob/)
  assert.match(validateAttack({ mob: 'player' }), /player/)
  assert.match(validateAttack({ mob: 'cow', weapon: 3 }), /weapon/)
  assert.match(validateAttack({ mob: 'cow', max_distance: 65 }), /max_distance/)
  assert.match(validateAttack({ mob: 'cow', max_distance: 0 }), /max_distance/)
  assert.match(validateAttack({ mob: 'cow', max_distance: '9' }), /max_distance/)
  assert.match(validateAttack({ mob: 'cow', protect: [{ origin: [0, 0] }] }), /protect/)
})

test('baby metadata index is 16 in 1.21.1', () => {
  assert.equal(BABY_INDEX, 16)
})

// ---- a fake bot for attack() ----

function mobAt (id, name, x, metadata = {}) {
  return { id, name, position: new Vec3(x, 64, 0), metadata, isValid: true }
}

function fakeAttackBot (entities) {
  const bot = new EventEmitter()
  bot.health = 20
  bot.food = 20
  bot.entity = { id: 1, position: new Vec3(0, 64, 0) }
  bot.entities = Object.fromEntries(entities.map((entity) => [entity.id, entity]))
  bot.registry = registry
  bot.currentWindow = null
  bot.heldItem = null
  bot.inventory = Object.assign(new EventEmitter(), { slots: [], items: () => [] })
  bot._client = new EventEmitter()
  bot.equip = async () => {}
  bot.unequip = async () => {}
  bot.clearControlStates = () => {}
  bot.walkedTo = []
  bot.noPath = false
  bot.pathfinder = {
    movementsSet: [],
    setGoal () {},
    setMovements (movements) { this.movementsSet.push(movements) },
    async goto (goal) {
      if (bot.noPath) return bot.emit('path_update', { status: 'noPath' })
      bot.walkedTo.push(goal.entity.id)
      bot.entity.position = goal.entity.position.offset(-2, 0, 0)
    }
  }
  bot.pvp = Object.assign(new EventEmitter(), {
    onAttack: () => {},
    attack (entity) { this.onAttack(entity) },
    stop () {}
  })
  return bot
}

function fakeGuard () {
  const quarries = []
  return { quarries, setQuarry: (id) => quarries.push(id) }
}

function fixture (entities, body = {}, options = {}) {
  const bot = fakeAttackBot(entities)
  const guard = fakeGuard()
  const tracker = { log: createEventLog(), ownDrops: new Set() }
  const movements = { name: 'walking' }
  const run = () => attack(bot, { mob: 'cow', ...body }, { episode: { walked: 0 }, tracker, guard, movements, ...options })
  return { bot, guard, tracker, movements, run }
}

const eventTypes = (tracker) => tracker.log.since(0).map((event) => event.type)

// ---- candidates ----

test('attack: no candidate is not_found, with killed false and travel', async () => {
  const { run, guard } = fixture([mobAt(2, 'pig', 5), mobAt(3, 'cow', 40)])
  const result = await run()
  assert.equal(result.error, 'not_found')
  assert.equal(result.ok, false)
  assert.equal(result.killed, false)
  assert.ok(result.travel)
  assert.deepEqual(guard.quarries, [])
})

test('attack: the nearest cow first; a baby animal is skipped, a baby zombie is kept', async () => {
  const baby = mobAt(2, 'cow', 3, { [BABY_INDEX]: true })
  const near = mobAt(3, 'cow', 6)
  const far = mobAt(4, 'cow', 12)
  const { bot, run, guard } = fixture([far, baby, near])
  bot.pvp.onAttack = (entity) => later(10, () => bot.emit('entityDead', entity))
  const result = await run()
  assert.deepEqual(bot.walkedTo, [3])
  assert.equal(result.target.id, 3)
  assert.equal(result.target.distance, 6)
  assert.deepEqual(guard.quarries, [3, null])

  const zombie = fixture([mobAt(2, 'zombie', 3, { [BABY_INDEX]: true })], { mob: 'zombie' })
  zombie.bot.pvp.onAttack = (entity) => later(10, () => zombie.bot.emit('entityDead', entity))
  assert.equal((await zombie.run()).target.id, 2)
})

test('attack: a no_path tries the next candidate, three at most, then unreachable; the quarry is cleared', async () => {
  const { bot, run, guard, tracker } = fixture([2, 3, 4, 5].map((id) => mobAt(id, 'cow', id * 2)))
  bot.noPath = true
  const result = await run()
  assert.equal(result.error, 'unreachable')
  assert.equal(result.killed, false)
  assert.deepEqual(guard.quarries, [2, 3, 4, null])
  assert.equal(tracker.log.phase(), 'travel')
})

test('attack: a target that is gone moves on, and none left is escaped', async () => {
  const gone = mobAt(2, 'cow', 4)
  const { bot, run } = fixture([gone])
  bot.pathfinder.goto = async () => { delete bot.entities[2] }
  assert.equal((await run()).error, 'escaped')
})

test('attack: an abort during the walk throws the signal reason and clears the quarry', async () => {
  const controller = new AbortController()
  const { bot, run, guard } = fixture([mobAt(2, 'cow', 4)], {}, { signal: controller.signal })
  bot.pathfinder.goto = () => new Promise(() => {})
  later(30, () => controller.abort(new Error('aborted')))
  await assert.rejects(run(), /aborted/)
  assert.deepEqual(guard.quarries, [2, null])

  const before = fixture([mobAt(2, 'cow', 4)], {}, { signal: AbortSignal.abort(new Error('early')) })
  await assert.rejects(before.run(), /early/)
})

// ---- outcomes ----

test('attack: lost is escaped, timeout is timeout, fled is interrupted, died is quarantined', async () => {
  const lost = fixture([mobAt(2, 'cow', 4)])
  lost.bot.pvp.onAttack = () => later(20, () => { delete lost.bot.entities[2] })
  const lostResult = await lost.run()
  assert.equal(lostResult.error, 'escaped')
  assert.equal(lostResult.killed, false)
  assert.deepEqual(lost.guard.quarries, [2, null])
  assert.equal(lost.tracker.log.phase(), 'step')

  const slow = fixture([mobAt(2, 'cow', 4)], {}, { fightMs: 60 })
  assert.equal((await slow.run()).error, 'timeout')

  const fled = fixture([mobAt(2, 'cow', 4)])
  fled.bot.pvp.onAttack = (entity) => later(20, () => {
    fled.bot.health = 5
    fled.bot.emit('health')
    later(30, () => { entity.position = new Vec3(40, 64, 0) })
  })
  const fledResult = await fled.run()
  assert.equal(fledResult.error, 'interrupted')
  assert.deepEqual(fledResult.detail, { threat: 'cow', outcome: 'fled' })

  const died = fixture([mobAt(2, 'cow', 4)])
  died.bot.pvp.onAttack = () => later(20, () => died.bot.emit('death'))
  assert.equal((await died.run()).error, 'quarantined')
  assert.deepEqual(eventTypes(died.tracker), ['fight_start', 'fight_end'])
})

// ---- protect ----

const PEN = [{ origin: [10, 60, -2], size: [4, 10, 4] }] // x 10..13 (half-open)

test('findCandidates: a mob inside a protect box is skipped, one outside (or on the far edge) is kept', () => {
  const bot = fakeAttackBot([mobAt(2, 'cow', 10.5), mobAt(3, 'cow', 14), mobAt(4, 'cow', 9.9), mobAt(5, 'cow', 13.9)])
  assert.deepEqual(findCandidates(bot, 'cow', 32, PEN).map(({ entity }) => entity.id), [4, 3])
  assert.deepEqual(findCandidates(bot, 'cow', 32).length, 4)
})

test('attack: the only mob in a protect box is not_found', async () => {
  const { run, guard } = fixture([mobAt(2, 'cow', 11)], { protect: PEN })
  assert.equal((await run()).error, 'not_found')
  assert.deepEqual(guard.quarries, [])
})

test('attack: a target that walks into a protect box during the walk is escaped, protected', async () => {
  const { bot, run } = fixture([mobAt(2, 'cow', 8)], { protect: PEN })
  bot.pathfinder.goto = async () => { bot.entities[2].position = new Vec3(11, 64, 0) }
  const result = await run()
  assert.equal(result.error, 'escaped')
  assert.equal(result.protected, true)
  assert.equal(result.killed, false)
})

test('attack: a target that walks into a protect box during the fight is let go as escaped, protected', async () => {
  const { bot, run, guard } = fixture([mobAt(2, 'cow', 8)], { protect: PEN })
  bot.pvp.onAttack = (entity) => later(20, () => { entity.position = new Vec3(11, 64, 0) })
  const result = await run()
  assert.equal(result.error, 'escaped')
  assert.equal(result.protected, true)
  assert.deepEqual(guard.quarries, [2, null])
})

test('attack: an escape outside any protect box has no protected flag', async () => {
  const { bot, run } = fixture([mobAt(2, 'cow', 4)], { protect: PEN })
  bot.pvp.onAttack = () => later(20, () => { delete bot.entities[2] })
  const result = await run()
  assert.equal(result.error, 'escaped')
  assert.equal('protected' in result, false)
})

// ---- the kill and its drops ----

function itemEntity (id, name, count, position) {
  return { id, name: 'item', position, getDroppedItem: () => ({ name, count }) }
}

test('attack: drops within 0.5 s before to 1.5 s after the death and 2.5 blocks count; others do not', async () => {
  const cow = mobAt(2, 'cow', 4)
  const { bot, run, tracker, guard } = fixture([cow], { weapon: 'stone_sword' })
  bot.heldItem = { name: 'stone_sword' }
  const deathSpot = cow.position
  bot.pvp.onAttack = () => {
    later(20, () => bot.emit('entitySpawn', itemEntity(10, 'cooked_beef', 1, deathSpot.offset(0, 0, 0.5)))) // 0.42 s before the death: inside the 0.5 s window
    later(260, () => bot.emit('entitySpawn', itemEntity(11, 'beef', 2, deathSpot.offset(1, 0, 1)))) // 0.2 s before death
    later(300, () => bot.emit('entitySpawn', itemEntity(12, 'leather', 1, deathSpot.offset(3, 0, 0)))) // 3 blocks away
    later(300, () => bot.emit('entityDead', { id: 9, name: 'zombie', position: deathSpot.offset(0, 0, 3) })) // interference
    later(310, () => bot.emit('entityDead', { id: 8, name: 'zombie', position: deathSpot.offset(0, 0, 6) })) // too far
    later(440, () => bot.emit('entityDead', cow))
    later(500, () => bot.emit('entitySpawn', itemEntity(13, 'bone', 1, deathSpot.offset(0, 0, 1)))) // after death
    later(700, () => { bot.inventory.slots[36] = { name: 'beef', count: 2 } })
  }
  const result = await run()
  assert.equal(result.ok, true)
  assert.equal(result.killed, true)
  assert.equal(result.weapon_used, 'stone_sword')
  assert.deepEqual(result.death_position, { x: 4, y: 64, z: 0 })
  assert.deepEqual(result.drops, { cooked_beef: 1, beef: 2, bone: 1 })
  assert.deepEqual([...tracker.ownDrops].sort(), [10, 11, 13])
  assert.deepEqual(result.picked_up, { beef: 2 })
  assert.deepEqual(result.inventory, { beef: 2 })
  assert.deepEqual(result.inventory_before_step, {})
  const interference = tracker.log.since(0).filter((event) => event.type === 'interference')
  assert.equal(interference.length, 1)
  assert.equal(interference[0].id, 9)
  assert.equal(interference[0].mob, 'zombie')
  assert.equal(interference[0].phase, 'step')
  assert.deepEqual(eventTypes(tracker).filter((type) => type === 'threat'), [])
  assert.equal(tracker.log.since(0).find((event) => event.type === 'fight_end').id, 2)
  assert.deepEqual(guard.quarries, [2, null])
  for (const event of ['entitySpawn', 'entityUpdate', 'entityDead', 'health', 'death']) assert.equal(bot.listenerCount(event), 0)
})

test('attack: an item spawned 0.8 s before the death is not a drop', async () => {
  const cow = mobAt(2, 'cow', 4)
  const { bot, run, tracker } = fixture([cow])
  bot.pvp.onAttack = () => {
    later(10, () => bot.emit('entitySpawn', itemEntity(10, 'beef', 1, cow.position)))
    later(850, () => bot.emit('entityDead', cow))
  }
  const result = await run()
  assert.deepEqual(result.drops, {})
  assert.equal(tracker.ownDrops.size, 0)
})

test('attack: a stack whose metadata arrives after the spawn is read on the update', async () => {
  const cow = mobAt(2, 'cow', 4)
  const { bot, run } = fixture([cow])
  let stack = null
  const item = { id: 10, name: 'item', position: cow.position.clone(), getDroppedItem: () => stack }
  bot.pvp.onAttack = () => {
    later(20, () => bot.emit('entityDead', cow))
    later(30, () => bot.emit('entitySpawn', item))
    later(60, () => { stack = { name: 'beef', count: 3 }; bot.emit('entityUpdate', item) })
  }
  assert.deepEqual((await run()).drops, { beef: 3 })
})

// ---- guard changes ----

test('guard: setQuarry makes check() skip that entity', () => {
  const bot = new EventEmitter()
  bot.health = 20
  bot.food = 20
  bot.entity = { id: 1, position: new Vec3(0, 64, 0) }
  bot.entities = { 7: { id: 7, name: 'zombie', position: new Vec3(3, 64, 0) } }
  bot.registry = registry
  bot.inventory = { items: () => [{ name: 'iron_sword', slot: 36 }] }
  const guard = createGuard(bot, { eventLog: createEventLog(), mode: 'defend', now: () => 1000 })
  assert.equal(guard.check().threat.id, 7)
  guard.setQuarry(7)
  assert.equal(guard.check(), null)
  guard.setQuarry(null)
  assert.equal(guard.check().threat.id, 7)
})

function fightBot () {
  const bot = fakeAttackBot([])
  const target = { id: 7, name: 'zombie', position: new Vec3(3, 64, 0) }
  bot.entities[7] = target
  bot.inventory = { items: () => [{ name: 'iron_sword', slot: 36 }, { name: 'diamond_axe', slot: 37 }] }
  bot.equipped = []
  bot.equip = async (item) => { bot.equipped.push(item.name) }
  bot.setGoals = []
  bot.pathfinder = { movementsSet: [], setGoal: (goal) => bot.setGoals.push(goal), setMovements (m) { this.movementsSet.push(m) } }
  return { bot, target }
}

test('fight: fight_end carries the target id', async () => {
  const { bot, target } = fightBot()
  const log = createEventLog()
  later(20, () => bot.emit('entityDead', target))
  await fight(bot, target, { timeoutMs: 1000, eventLog: log })
  assert.equal(log.since(0).find((event) => event.type === 'fight_end').id, 7)
})

test('fight: flee false skips the flight after an out-of-reach lost', async () => {
  const { bot, target } = fightBot()
  later(20, () => { target.position = new Vec3(8, 64, 0) })
  const result = await fight(bot, target, { timeoutMs: 2000, outOfReachMs: 60, flee: false })
  assert.equal(result.outcome, 'lost')
  assert.equal(result.out_of_reach, true)
  assert.ok(bot.setGoals.every((goal) => goal === null))
})

test('fight: sprint paths with a copy that sprints, and the walking movements are set back', async () => {
  const { bot, target } = fightBot()
  const movements = { name: 'walking', allowSprinting: false }
  let usedByPvp = null
  bot.pvp.onAttack = () => { usedByPvp = bot.pvp.movements }
  await fight(bot, target, { timeoutMs: 30, movements, sprint: true })
  assert.notEqual(usedByPvp, movements)
  assert.equal(usedByPvp.allowSprinting, true)
  assert.equal(usedByPvp.name, 'walking')
  assert.equal(movements.allowSprinting, false)
  assert.deepEqual(bot.pathfinder.movementsSet, [movements])

  const plain = fightBot()
  await fight(plain.bot, plain.target, { timeoutMs: 30, movements })
  assert.equal(plain.bot.pvp.movements, movements)
})

test('fight: weapon prefers the named item when held, else the best weapon', async () => {
  const axe = fightBot()
  await fight(axe.bot, axe.target, { timeoutMs: 30, weapon: 'diamond_axe' })
  assert.deepEqual(axe.bot.equipped, ['diamond_axe'])
  const missing = fightBot()
  await fight(missing.bot, missing.target, { timeoutMs: 30, weapon: 'netherite_sword' })
  assert.deepEqual(missing.bot.equipped, ['iron_sword'])
})

// ---- look with mobs ----

test('look: the nearest living mob of each name per sector, floored, next to the blocks', () => {
  const bot = fakeWorldBot({ '0,5,-3': 'stone' })
  const cow = (id, x, z, extra = {}) => ({ id, name: 'cow', position: new Vec3(x, 10, z), metadata: {}, isValid: true, ...extra })
  bot.entities = {
    1: cow(1, 0.5, -8.5), // N, 8.9... farther
    2: cow(2, 1.2, -4.4), // N, nearer
    3: cow(3, 9.7, 0.5), // E
    4: cow(4, 0.5, 30.5), // S, beyond the radius
    5: cow(5, -6.5, 0.5, { metadata: { 9: 0 } }), // W, dying
    6: { id: 6, name: 'pig', position: new Vec3(2.5, 10, 0.5), metadata: {} }
  }
  const result = look(bot, { blocks: ['stone'], mobs: ['cow'], radius: 20 })
  assert.deepEqual(result.directions.N.cow, { x: 1, y: 10, z: -5, distance: 4.9 })
  assert.equal(result.directions.N.stone.z, -3)
  assert.deepEqual(result.directions.E.cow, { x: 9, y: 10, z: 0, distance: 9.2 })
  assert.equal(result.directions.S.cow, undefined)
  assert.equal(result.directions.W.cow, undefined)
  assert.equal(result.directions.E.pig, undefined)

  const mobsOnly = look(bot, { mobs: ['pig'], radius: 20 })
  assert.deepEqual(Object.keys(mobsOnly.directions.E), ['pig'])
})
