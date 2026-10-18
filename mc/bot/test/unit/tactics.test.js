const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const { goals } = require('mineflayer-pathfinder')
const { PVP } = require('mineflayer-pvp/lib/PVP')
const { TACTICS, DEFAULT_TACTICS, jumpTimeline, validateTactics, createTactics, isSafeStep, clearLine } = require('../../lib/tactics')
const { fakeWorldBot } = require('./world_fixture')

// A flat stone floor at y 9 under a bot at y 10, facing -z (yaw 0); `without` lists floor cells to remove.
function floorBlocks (without = [], extra = {}) {
  const blocks = {}
  for (let x = -5; x <= 5; x++) for (let z = -8; z <= 5; z++) blocks[`${x},9,${z}`] = 'stone'
  for (const key of without) delete blocks[key]
  return { ...blocks, ...extra }
}

// A bot, the real pvp logic (update, checkRange) on top of it, and a target `distance` blocks ahead.
function fightFixture ({ distance = 3, blocks = floorBlocks(), shield = false } = {}) {
  const bot = Object.assign(new EventEmitter(), fakeWorldBot(blocks))
  const calls = []
  const record = (name) => (...args) => { calls.push(name); return args }
  bot.entity = { position: new Vec3(0.5, 10, 0.5), velocity: new Vec3(0, 0, 0), yaw: 0, onGround: true }
  bot.heldItem = { name: 'iron_sword' }
  bot.supportFeature = () => false
  bot.getEquipmentDestSlot = () => 45
  bot.inventory = { slots: { 45: shield ? { name: 'shield' } : null }, items: () => [] }
  bot.controls = {}
  bot.setControlState = (control, value) => { bot.controls[control] = value; calls.push(`control:${control}:${value}`) }
  bot.lookAt = async () => { calls.push('look') }
  bot.attack = record('attack')
  bot.activateItem = record('activate')
  bot.deactivateItem = record('deactivate')
  bot.goals = []
  bot.pathfinder = { setGoal: (goal) => bot.goals.push(goal) }
  const target = { id: 7, name: 'player', type: 'player', height: 1.8, heldItem: { name: 'iron_sword' }, position: new Vec3(0.5, 10, 0.5 - distance) }
  const pvp = Object.assign(Object.create(PVP.prototype), {
    bot, target, timeToNextAttack: 0, wasInRange: false, attackRange: 3.5, followRange: 2, viewDistance: 128,
    blockingExplosion: false, meleeAttackRate: { getTicks: () => 5 }
  })
  bot.on('physicTick', () => pvp.update())
  return { bot, pvp, target, calls, attacks: () => calls.filter((call) => call === 'attack').length }
}

const flush = () => new Promise((resolve) => setImmediate(resolve))

// Runs `count` physics ticks; with fake timers also lets the shield's 100 and 150 ms steps pass.
async function tickBot (t, bot, count = 1) {
  for (let i = 0; i < count; i++) {
    bot.emit('physicsTick')
    bot.emit('physicTick')
    await flush()
    t.mock.timers.tick(100)
    await flush()
    t.mock.timers.tick(150)
    await flush()
  }
}

const fakeTimers = (t) => t.mock.timers.enable({ apis: ['setTimeout'] })

// Attaches, and runs the 2 ticks the mover's line gate needs with the target 3.8 blocks away (in range of
// the mover, out of reach), then puts the target back where it was: the next tick takes over and may strike.
async function attachWithMover (t, tactics, bot, target) {
  const place = target.position
  target.position = new Vec3(place.x, place.y, bot.entity.position.z - 3.8)
  tactics.attach(target)
  await tickBot(t, bot, 2)
  target.position = place
}

test('validateTactics: [] is fine; a non-array, non-string, unknown or repeated name is a problem', () => {
  assert.equal(validateTactics([]), null)
  assert.match(validateTactics('crit'), /array/)
  assert.match(validateTactics([3]), /strings/)
  assert.match(validateTactics(['nope']), /unknown/)
  const registry = { a: () => ({}), b: () => ({}) }
  assert.equal(validateTactics(['b', 'a'], registry), null)
  assert.match(validateTactics(['a', 'b', 'a'], registry), /repeated/)
  assert.deepEqual([...TACTICS], ['wtap', 'strafe', 'shield', 'crit', 'disengage'])
  assert.deepEqual([...DEFAULT_TACTICS], ['crit', 'shield'])
})

test('createTactics: an invalid list throws', () => {
  const { bot, pvp } = fightFixture()
  assert.throws(() => createTactics(bot, ['nope'], { pvp }), /unknown tactic/)
})

test('gate: the first strike is at once, then one per full cooldown (13 ticks for an iron sword)', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, attacks } = fightFixture({ shield: true })
  const tactics = createTactics(bot, [], { pvp })
  tactics.attach(target)
  await tickBot(t, bot, 1)
  assert.equal(attacks(), 1)
  await tickBot(t, bot, 12)
  assert.equal(attacks(), 1)
  await tickBot(t, bot, 1)
  assert.equal(attacks(), 2)
  await tickBot(t, bot, 60 - 14)
  assert.equal(attacks(), 5) // ticks 1, 14, 27, 40, 53
  tactics.detach()
})

test('gate: leaving and re-entering range does not reset the cooldown', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, attacks } = fightFixture()
  const tactics = createTactics(bot, [], { pvp })
  tactics.attach(target)
  await tickBot(t, bot, 1)
  assert.equal(attacks(), 1)
  for (let i = 0; i < 4; i++) { // knockback: out of range and back in, again and again
    target.position = new Vec3(0.5, 10, -4.5)
    await tickBot(t, bot, 1)
    target.position = new Vec3(0.5, 10, -2.5)
    await tickBot(t, bot, 1)
  }
  assert.equal(attacks(), 1)
  await tickBot(t, bot, 5)
  assert.equal(attacks(), 2)
  tactics.detach()
})

test('gate: a tactic can delay the strike, and sees the cooldown in its context', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, attacks } = fightFixture()
  let open = false
  const seen = []
  const registry = { late: () => ({ canStrike (ctx) { seen.push(ctx); return open } }) }
  const tactics = createTactics(bot, ['late'], { pvp, registry })
  tactics.attach(target)
  await tickBot(t, bot, 3)
  assert.equal(attacks(), 0)
  assert.equal(seen[0].cooldownTicks, 13)
  assert.equal(seen[0].ticksSinceStrike, Infinity)
  open = true
  await tickBot(t, bot, 1)
  assert.equal(attacks(), 1)
  tactics.detach()
})

test('strike: with an off-hand shield it is deactivate, attack, activate; hooks run around the attack', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls } = fightFixture({ shield: true })
  const registry = { hooked: () => ({ beforeStrike: () => calls.push('before'), afterStrike: () => calls.push('after') }) }
  const tactics = createTactics(bot, ['hooked'], { pvp, registry })
  tactics.attach(target)
  await tickBot(t, bot, 1)
  const strike = calls.filter((call) => !call.startsWith('control:') && call !== 'look')
  assert.deepEqual(strike, ['before', 'deactivate', 'attack', 'after', 'activate'])
  tactics.detach()
})

test('strike: a tactic that owns the shield skips pvp\'s routine', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls } = fightFixture({ shield: true })
  const tactics = createTactics(bot, ['mine'], { pvp, registry: { mine: () => ({ ownsShield: true }) } })
  tactics.attach(target)
  await tickBot(t, bot, 1)
  assert.deepEqual(calls.filter((call) => /^(de)?activate$/.test(call)), [])
  assert.ok(calls.includes('attack'))
  tactics.detach()
})

test('strike: without a shield there is no activate or deactivate', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls } = fightFixture()
  const tactics = createTactics(bot, [], { pvp })
  tactics.attach(target)
  await tickBot(t, bot, 1)
  assert.ok(calls.includes('attack'))
  assert.ok(!calls.includes('activate') && !calls.includes('deactivate'))
  tactics.detach()
})

test('mover: within 4 blocks the goal is cleared; forward and sprint from 3.5, released at 2', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target } = fightFixture({ distance: 3.5 })
  const tactics = createTactics(bot, [], { pvp })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1)
  assert.deepEqual(bot.goals, [null])
  assert.equal(bot.controls.forward, true)
  assert.equal(bot.controls.sprint, true)
  target.position = new Vec3(0.5, 10, -1.5)
  await tickBot(t, bot, 1)
  assert.equal(bot.controls.forward, false)
  assert.equal(bot.controls.sprint, false)
  assert.equal(bot.goals.length, 1)
  tactics.detach()
})

test('mover: only changed controls are sent', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls } = fightFixture({ distance: 3.5 })
  const tactics = createTactics(bot, [], { pvp })
  tactics.attach(target)
  await tickBot(t, bot, 5)
  assert.equal(calls.filter((call) => call.startsWith('control:')).length, 2) // forward, sprint
  tactics.detach()
})

test('mover: past 5 blocks the follow goal returns and its controls are released; so does a height gap', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target } = fightFixture({ distance: 3.5 })
  const tactics = createTactics(bot, [], { pvp })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1)
  target.position = new Vec3(0.5, 10, -4.5) // between 4 and 5: still active
  await tickBot(t, bot, 1)
  assert.equal(bot.goals.length, 1)
  target.position = new Vec3(0.5, 10, -5.5)
  await tickBot(t, bot, 1)
  assert.equal(bot.controls.forward, false)
  assert.equal(bot.controls.sprint, false)
  assert.ok(bot.goals[1] instanceof goals.GoalFollow)
  assert.equal(bot.goals[1].entity, target)
  assert.equal(bot.goals[1].rangeSq, 4)
  target.position = new Vec3(0.5, 12, -3) // 2 blocks higher: not the mover's
  await tickBot(t, bot, 1)
  assert.equal(bot.goals.length, 2)
  tactics.detach()
})

test('mover: the line gate takes over after 3 clear ticks; a wall on the line keeps GoalFollow', async (t) => {
  fakeTimers(t)
  const clear = fightFixture({ distance: 3 })
  const clearTactics = createTactics(clear.bot, [], { pvp: clear.pvp })
  clearTactics.attach(clear.target)
  await tickBot(t, clear.bot, 2)
  assert.deepEqual(clear.bot.goals, [])
  await tickBot(t, clear.bot, 1)
  assert.deepEqual(clear.bot.goals, [null])
  assert.equal(clear.bot.controls.forward, true)
  clearTactics.detach()

  const walled = fightFixture({ distance: 3, blocks: floorBlocks([], { '0,10,-2': 'stone', '0,11,-2': 'stone' }) })
  const walledTactics = createTactics(walled.bot, [], { pvp: walled.pvp })
  walledTactics.attach(walled.target)
  await tickBot(t, walled.bot, 10)
  assert.deepEqual(walled.bot.goals, [])
  assert.ok(!walled.bot.controls.forward)
  walledTactics.detach()
})

test('mover: 3 failing gate ticks in a row hand the chase back; a single failing tick does not', async (t) => {
  fakeTimers(t)
  const blocks = floorBlocks()
  const { bot, pvp, target } = fightFixture({ distance: 3, blocks })
  const tactics = createTactics(bot, [], { pvp })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1)
  assert.equal(bot.goals.length, 1)
  blocks['0,10,-2'] = 'stone'
  await tickBot(t, bot, 2)
  delete blocks['0,10,-2'] // clear again before the third
  await tickBot(t, bot, 1)
  assert.equal(bot.goals.length, 1)
  blocks['0,10,-2'] = 'stone'
  await tickBot(t, bot, 2)
  assert.equal(bot.goals.length, 1)
  await tickBot(t, bot, 1)
  assert.equal(bot.goals.length, 2)
  assert.ok(bot.goals[1] instanceof goals.GoalFollow)
  assert.equal(bot.controls.forward, false)
  assert.equal(bot.controls.sprint, false)
  tactics.detach()
})

test('mover: forward pressed 10 ticks without moving 0.2 blocks hands back, and the mover stays out for 40 ticks', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target } = fightFixture({ distance: 3 })
  const tactics = createTactics(bot, [], { pvp })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1)
  assert.equal(bot.controls.forward, true)
  await tickBot(t, bot, 9) // the fake bot never moves
  assert.equal(bot.goals.length, 1)
  await tickBot(t, bot, 1)
  assert.equal(bot.goals.length, 2)
  assert.ok(bot.goals[1] instanceof goals.GoalFollow)
  assert.equal(bot.controls.forward, false)
  await tickBot(t, bot, 40) // the line is clear all along
  assert.equal(bot.goals.length, 2)
  await tickBot(t, bot, 2)
  assert.equal(bot.goals.length, 3)
  assert.equal(bot.goals[2], null)
  tactics.detach()
})

test('mover: a bot that is moving is not stuck', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target } = fightFixture({ distance: 4 })
  const tactics = createTactics(bot, [], { pvp })
  await attachWithMover(t, tactics, bot, target)
  for (let i = 0; i < 30; i++) {
    bot.entity.position.z -= 0.03
    await tickBot(t, bot, 1)
  }
  assert.equal(bot.goals.length, 1)
  tactics.detach()
})

test('mover: a steer hook changes the plan', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target } = fightFixture({ distance: 3.5 })
  const registry = { sidestep: () => ({ steer: (ctx, controls) => { controls.left = true; controls.sprint = false } }) }
  const tactics = createTactics(bot, ['sidestep'], { pvp, registry })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1)
  assert.equal(bot.controls.left, true)
  assert.equal(bot.controls.forward, true)
  assert.ok(!bot.controls.sprint)
  tactics.detach()
})

test('safety: no floor 2 blocks down or lava ahead releases forward; a 1-block drop is fine', async (t) => {
  fakeTimers(t)
  const ahead = ['0,9,-1', '0,9,-2', '0,9,-3'] // the bot stands in x 0, z 0; forward is -z, 0.8 block ahead is z -1
  const cases = [
    ['a 2-block drop', (blocks) => ahead.forEach((key) => delete blocks[key]), false],
    ['lava on the floor', (blocks) => { blocks['0,9,-1'] = 'lava' }, false],
    ['lava in the feet cell', (blocks) => { blocks['0,10,-1'] = 'lava' }, false],
    ['a 1-block drop', (blocks) => { ahead.forEach((key) => delete blocks[key]); blocks['0,8,-1'] = 'stone' }, true],
    ['solid ground', () => {}, true]
  ]
  for (const [label, change, walks] of cases) {
    const blocks = floorBlocks()
    const { bot, pvp, target } = fightFixture({ distance: 3.5, blocks })
    const tactics = createTactics(bot, [], { pvp })
    await attachWithMover(t, tactics, bot, target)
    await tickBot(t, bot, 1) // the mover takes over and walks in
    assert.equal(bot.controls.forward, true, label)
    change(blocks) // the line gate needs 3 ticks to notice; the step check is at once
    await tickBot(t, bot, 1)
    assert.equal(Boolean(bot.controls.forward), walks, label)
    tactics.detach()
  }
})

test('isSafeStep: hazards and unloaded blocks', () => {
  const bot = fakeWorldBot(floorBlocks([], { '1,9,0': 'water', '3,9,0': 'cactus' }))
  const at = (x) => new Vec3(x + 0.5, 10, 0.5)
  assert.equal(isSafeStep(bot, at(0)), true)
  assert.equal(isSafeStep(bot, at(1)), false)
  assert.equal(isSafeStep(bot, at(3)), false)
  assert.equal(isSafeStep({ blockAt: () => null }, at(0)), false)
})

test('isSafeStep: a wall or step at the feet, or a block at the head, is unsafe', () => {
  const bot = fakeWorldBot(floorBlocks([], { '1,10,0': 'stone', '2,11,0': 'stone', '3,10,0': 'oak_leaves' }))
  const at = (x) => new Vec3(x + 0.5, 10, 0.5)
  assert.equal(isSafeStep(bot, at(0)), true)
  assert.equal(isSafeStep(bot, at(1)), false) // feet and head blocked by a wall, or just the feet by a step
  assert.equal(isSafeStep(bot, at(2)), false) // head only
  assert.equal(isSafeStep(bot, at(3)), false)
  assert.equal(isSafeStep({ blockAt: (pos) => pos.y === 11 ? null : fakeWorldBot(floorBlocks()).blockAt(pos) }, at(0)), false) // head unloaded
})

test('clearLine: open floor and a 1-block drop are clear; a trunk, a step, a 2-block hole, lava and water are not', () => {
  const from = new Vec3(0.5, 10, 0.5)
  const to = new Vec3(0.5, 10, -3.5)
  const line = (blocks, position = {}) => {
    const bot = fakeWorldBot(blocks)
    Object.assign(bot.entity, position)
    return clearLine(bot, from, to)
  }
  const hole = ['0,9,-2']
  assert.equal(line(floorBlocks()), true)
  assert.equal(line(floorBlocks([], { '0,10,-2': 'oak_log', '0,11,-2': 'oak_log' })), false, 'a trunk')
  assert.equal(line(floorBlocks([], { '0,10,-2': 'stone' })), false, 'a step up')
  assert.equal(line(floorBlocks(hole, { '0,8,-2': 'stone' })), true, 'a 1-block drop')
  assert.equal(line(floorBlocks(hole)), false, 'a 2-block hole')
  assert.equal(line(floorBlocks([], { '0,9,-2': 'lava' })), false, 'lava')
  assert.equal(line(floorBlocks([], { '0,10,0': 'water', '0,9,0': 'stone' })), false, 'standing in water')
  assert.equal(line(floorBlocks([], { '0,11,0': 'water' })), false, 'water at the head')
  assert.equal(line(floorBlocks(), { isInWater: true }), false, 'swimming')
})

test('detach: restores pvp, the listeners and the controls, and ends an in-flight strike harmlessly', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls, attacks } = fightFixture({ distance: 3.5, shield: true })
  const before = { physicsTick: bot.listenerCount('physicsTick'), physicTick: bot.listenerCount('physicTick') }
  const tactics = createTactics(bot, [], { pvp })
  await attachWithMover(t, tactics, bot, target)
  assert.equal(Object.prototype.hasOwnProperty.call(pvp, 'attemptAttack'), true)
  bot.emit('physicsTick')
  bot.emit('physicTick') // the strike starts: the shield goes down and waits 100 ms
  await flush()
  assert.deepEqual(calls.filter((call) => call === 'deactivate'), ['deactivate'])
  tactics.detach()
  tactics.detach() // idempotent
  assert.equal(Object.prototype.hasOwnProperty.call(pvp, 'attemptAttack'), false)
  assert.equal(bot.listenerCount('physicsTick'), before.physicsTick)
  assert.equal(bot.listenerCount('physicTick'), before.physicTick)
  assert.equal(bot.controls.forward, false)
  assert.equal(bot.controls.sprint, false)
  t.mock.timers.tick(100)
  await flush()
  t.mock.timers.tick(150)
  await flush()
  assert.equal(attacks(), 0)
  assert.ok(!calls.includes('activate'))
})

// crit: the bot walks in from 3 blocks; with the iron sword's 13-tick cooldown and no shield the strike
// lead is 1, so the jump is due once 13 - (7 + 1 - 1) = 6 ticks have passed since the last strike.
const CRIT_JUMP_AT = 6

test('crit: it is registered and valid', () => {
  assert.ok(TACTICS.includes('crit'))
  assert.equal(validateTactics(['crit']), null)
  const { bot, pvp } = fightFixture()
  assert.deepEqual(createTactics(bot, ['crit'], { pvp }).names, ['crit'])
})

test('jumpTimeline: rises for 6 ticks, descends from the 7th and lands on the 12th', () => {
  assert.deepEqual(jumpTimeline(), { descendsAt: 7, landsAt: 12 })
})

test('crit: with the cooldown nearly full the mover jumps with sprint released; far from full it does not', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target } = fightFixture()
  const tactics = createTactics(bot, ['crit'], { pvp })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1) // no strike yet: the cooldown counts as full
  assert.equal(bot.controls.jump, true)
  assert.ok(!bot.controls.sprint)
  assert.equal(bot.controls.forward, true)
  tactics.detach()
})

test('crit: the jump is due when the cooldown has CRIT_JUMP_AT ticks to go, and sprint goes first', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, attacks } = fightFixture()
  bot.controlState = bot.controls // the real bot's name for the pressed controls
  const tactics = createTactics(bot, ['crit'], { pvp })
  tactics.attach(target)
  bot.entity.onGround = false // arrive airborne: the first strike is a falling one
  bot.entity.velocity.y = -0.1
  await tickBot(t, bot, 1)
  assert.equal(attacks(), 1)
  bot.entity.onGround = true
  bot.entity.velocity.y = 0
  let jumpedAt = null
  for (let sinceStrike = 1; sinceStrike < 13 && jumpedAt === null; sinceStrike++) {
    await tickBot(t, bot, 1)
    if (bot.controls.jump) jumpedAt = sinceStrike
  }
  // sprint was pressed by the walk-in, so the first due tick only releases it; the jump follows a tick later
  assert.equal(jumpedAt, CRIT_JUMP_AT + 1)
  assert.equal(bot.controls.sprint, false)
  assert.equal(bot.controls.forward, true)
  tactics.detach()
})

test('crit: on the ground with a jump planned there is no strike; descending, there is, with sprint released', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, attacks } = fightFixture()
  const tactics = createTactics(bot, ['crit'], { pvp })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1) // jump requested, the fake bot stays put
  assert.equal(bot.controls.jump, true)
  assert.equal(attacks(), 0)
  bot.entity.onGround = false // rising: still no strike (2 ticks in, the lead is 1)
  bot.entity.velocity.y = 0.3
  await tickBot(t, bot, 1)
  assert.equal(attacks(), 0)
  bot.entity.velocity.y = -0.1
  await tickBot(t, bot, 1)
  assert.equal(attacks(), 1)
  assert.ok(!bot.controls.sprint)
  tactics.detach()
})

test('crit: a jump that is rising into the fall opens the gate early; with a shield 3 ticks, not 1', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, attacks } = fightFixture({ shield: true })
  const tactics = createTactics(bot, ['crit'], { pvp })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1) // jump requested at tick 1
  bot.entity.onGround = false
  bot.entity.velocity.y = 0.3
  await tickBot(t, bot, 3) // 3 sims in: 3 + 3 < 8
  assert.equal(attacks(), 0)
  await tickBot(t, bot, 1) // 4 sims in: 4 + 3 < 8 still; one more
  assert.equal(attacks(), 0)
  await tickBot(t, bot, 1) // 5 + 3 >= 8
  assert.equal(attacks(), 1)
  tactics.detach()
})

test('crit: a ceiling, or a target out of jumping range, means no jump and a ground strike after the fallback', async (t) => {
  fakeTimers(t)
  const cases = [
    ['a ceiling', { distance: 3, blocks: floorBlocks([], { '0,12,0': 'stone' }) }],
    ['a target 6 blocks away', { distance: 6 }]
  ]
  for (const [label, options] of cases) {
    const { bot, pvp, target, attacks } = fightFixture(options)
    pvp.attackRange = 7
    const tactics = createTactics(bot, ['crit'], { pvp })
    tactics.attach(target)
    await tickBot(t, bot, 10)
    assert.equal(attacks(), 0, `${label}: still waiting`)
    await tickBot(t, bot, 2)
    assert.equal(attacks(), 1, `${label}: struck on the ground`)
    assert.ok(!bot.controls.jump, label)
    tactics.detach()
  }
})

test('crit: a jump that lands without a strike allows the ground strike, and no new jump', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, attacks } = fightFixture()
  const tactics = createTactics(bot, ['crit'], { pvp })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1) // jump requested
  assert.equal(attacks(), 0)
  await tickBot(t, bot, 1) // still on the ground after the jump tick: it never left
  assert.equal(attacks(), 1)
  assert.ok(!bot.controls.jump)
  tactics.detach()
})

test('baseline: without crit nothing jumps', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target } = fightFixture()
  const tactics = createTactics(bot, [], { pvp })
  tactics.attach(target)
  await tickBot(t, bot, 20)
  assert.ok(!bot.controls.jump)
  tactics.detach()
})

test('wtap: it is registered, valid with crit in either order, and names stay as given', () => {
  assert.ok(TACTICS.includes('wtap') && TACTICS.includes('crit'))
  assert.equal(validateTactics(['crit', 'wtap']), null)
  assert.equal(validateTactics(['wtap', 'crit']), null)
  const { bot, pvp } = fightFixture()
  assert.deepEqual(createTactics(bot, ['crit', 'wtap'], { pvp }).names, ['crit', 'wtap'])
})

test('wtap: forward and sprint are released for 3 ticks after a strike, then pressed again (a fresh sprint)', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls, attacks } = fightFixture({ distance: 2 })
  const tactics = createTactics(bot, ['wtap'], { pvp })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1) // the first strike: nothing struck before, so the mover has pressed in
  assert.equal(attacks(), 1)
  assert.equal(bot.controls.forward, true)
  assert.equal(bot.controls.sprint, true)
  calls.length = 0
  for (let i = 0; i < 3; i++) {
    await tickBot(t, bot, 1)
    assert.equal(bot.controls.forward, false, `released on tick ${i + 1}`)
    assert.equal(bot.controls.sprint, false, `released on tick ${i + 1}`)
  }
  await tickBot(t, bot, 1) // outside the window, 2 blocks from the target: still walking in
  assert.equal(bot.controls.forward, true)
  assert.equal(bot.controls.sprint, true)
  assert.deepEqual(calls.filter((call) => call.startsWith('control:sprint')), ['control:sprint:false', 'control:sprint:true'])
  tactics.detach()
})

test('wtap: the safety check still releases an unsafe step', async (t) => {
  fakeTimers(t)
  const blocks = floorBlocks()
  const { bot, pvp, target } = fightFixture({ distance: 3.5, blocks })
  const tactics = createTactics(bot, ['wtap'], { pvp })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1)
  assert.equal(bot.controls.forward, true)
  for (const key of ['0,9,-1', '0,9,-2', '0,9,-3']) delete blocks[key]
  await tickBot(t, bot, 1)
  assert.ok(!bot.controls.forward)
  assert.ok(!bot.controls.sprint)
  tactics.detach()
})

test('wtap + crit: the hooks run in REGISTRY order, so both name orders apply the same controls', async (t) => {
  fakeTimers(t)
  const run = async (names) => {
    const { bot, pvp, target, calls } = fightFixture()
    bot.controlState = bot.controls
    const tactics = createTactics(bot, names, { pvp })
    tactics.attach(target)
    await tickBot(t, bot, 30)
    tactics.detach()
    return calls
  }
  const crit = await run(['crit', 'wtap'])
  assert.deepEqual(await run(['wtap', 'crit']), crit)
  assert.ok(crit.includes('control:jump:true'), 'crit still jumps')
  assert.ok(!crit.slice(0, crit.indexOf('control:jump:true')).includes('control:sprint:true'), 'crit keeps sprint off up to its jump')
})

// A random() that returns the given values in turn.
const scripted = (...values) => () => values.shift()

test('strafe: it is registered between wtap and crit, and valid with crit', () => {
  assert.deepEqual([...TACTICS], ['wtap', 'strafe', 'shield', 'crit', 'disengage'])
  assert.equal(validateTactics(['crit', 'strafe']), null)
})

test('strafe: within 4 blocks exactly one side is held, switched after the drawn 15..35 ticks, then a new draw', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target } = fightFixture({ distance: 3 })
  // side: 0.1 -> left; first duration 0 -> 15 ticks; second 0.999 -> 35 ticks; third 0 -> 15
  const tactics = createTactics(bot, ['strafe'], { pvp, random: scripted(0.1, 0, 0.999, 0) })
  await attachWithMover(t, tactics, bot, target)
  const sides = []
  for (let i = 0; i < 15 + 35 + 1; i++) {
    bot.entity.position.z -= 0.03 // walking in, so the stuck backstop stays out of it
    await tickBot(t, bot, 1)
    assert.equal(Boolean(bot.controls.left) !== Boolean(bot.controls.right), true, `tick ${i + 1}: exactly one side`)
    sides.push(bot.controls.left ? 'left' : 'right')
  }
  assert.deepEqual(sides.slice(0, 15), Array(15).fill('left'))
  assert.deepEqual(sides.slice(15, 50), Array(35).fill('right'))
  assert.equal(sides[50], 'left')
  tactics.detach()
})

test('strafe: beyond 4 blocks (mover still active) neither side is pressed', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target } = fightFixture({ distance: 3 })
  const tactics = createTactics(bot, ['strafe'], { pvp, random: scripted(0.1, 0.5) })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1)
  target.position = new Vec3(0.5, 10, -4.5) // 4..5 blocks: strafe's range is over, the mover's is not
  await tickBot(t, bot, 1)
  assert.equal(bot.goals.length, 1)
  assert.ok(!bot.controls.left && !bot.controls.right)
  assert.equal(bot.controls.forward, true)
  target.position = new Vec3(0.5, 10, -3.5)
  await tickBot(t, bot, 1)
  assert.equal(bot.controls.left, true)
  tactics.detach()
})

test('strafe: a side whose step is unsafe is released, and the next tick strafes the other way', async (t) => {
  fakeTimers(t)
  // facing -z (yaw 0), left is -x: 0.8 block to the left is x -1, lava on the floor there
  const { bot, pvp, target } = fightFixture({ distance: 3, blocks: floorBlocks([], { '-1,9,0': 'lava' }) })
  const tactics = createTactics(bot, ['strafe'], { pvp, random: scripted(0.1, 0.5, 0.5) })
  await attachWithMover(t, tactics, bot, target)
  await tickBot(t, bot, 1)
  assert.ok(!bot.controls.left && !bot.controls.right)
  await tickBot(t, bot, 1)
  assert.equal(bot.controls.right, true)
  assert.ok(!bot.controls.left)
  tactics.detach()
})

test('strafe + crit: crit still jumps with sprint released, and the side is held through the jump', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target } = fightFixture()
  bot.controlState = bot.controls
  const tactics = createTactics(bot, ['crit', 'strafe'], { pvp, random: scripted(0.9, 0.99) })
  tactics.attach(target)
  for (let i = 0; i < 4; i++) {
    await tickBot(t, bot, 1)
    if (bot.controls.jump) break
  }
  assert.equal(bot.controls.jump, true)
  assert.equal(bot.controls.right, true)
  assert.ok(!bot.controls.sprint)
  bot.entity.onGround = false
  bot.entity.velocity = new Vec3(0, 0.2, 0)
  await tickBot(t, bot, 3)
  assert.equal(bot.controls.right, true)
  assert.ok(!bot.controls.sprint)
  tactics.detach()
})

// shield: the opponent (iron sword, 13-tick cooldown) swings; the shield is wanted from 13 - 7 = 6 ticks later.
const swing = (bot, target) => bot.emit('entitySwingArm', target)
const shieldCalls = (calls) => calls.filter((call) => /^(de)?activate$|^attack$/.test(call))

test('shield: it is registered between strafe and crit, and valid with crit', () => {
  assert.deepEqual([...TACTICS], ['wtap', 'strafe', 'shield', 'crit', 'disengage'])
  assert.equal(validateTactics(['crit', 'shield']), null)
})

test('shield: pvp\'s routine is skipped, so the strike has no 100 ms wait', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls } = fightFixture({ shield: true })
  const tactics = createTactics(bot, ['shield'], { pvp })
  tactics.attach(target)
  bot.emit('physicsTick')
  bot.emit('physicTick')
  await flush() // no timers ticked
  assert.ok(calls.includes('attack'))
  tactics.detach()
})

test('shield: raised 7 ticks before their strike, up until they swing, lowered for ours', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls, attacks } = fightFixture({ shield: true, distance: 4.4 }) // in their range, out of ours
  const tactics = createTactics(bot, ['shield'], { pvp })
  tactics.attach(target)
  swing(bot, target) // at tick 0: their next strike is due at tick 13
  await tickBot(t, bot, 5)
  assert.deepEqual(shieldCalls(calls), [])
  await tickBot(t, bot, 1) // tick 6 = 13 - 7
  assert.deepEqual(shieldCalls(calls), ['activate'])
  await tickBot(t, bot, 4) // ticks 7..10
  target.position = new Vec3(0.5, 10, -3) // in reach from tick 11, when the shield is warm
  await tickBot(t, bot, 3) // ticks 11..13: our first strike is held, their strike is near
  assert.equal(attacks(), 0)
  swing(bot, target) // they strike at tick 13: the next is due at 26
  await tickBot(t, bot, 1)
  assert.deepEqual(shieldCalls(calls), ['activate', 'deactivate', 'attack'])
  await tickBot(t, bot, 1) // 26 - 15 = 11 ticks away: the shield stays down
  assert.deepEqual(shieldCalls(calls), ['activate', 'deactivate', 'attack'])
  await tickBot(t, bot, 4) // tick 19 = 26 - 7
  assert.deepEqual(shieldCalls(calls).slice(3), ['activate'])
  tactics.detach()
})

test('shield: with their strike overdue it goes up again right after our strike, and leaving their range lowers it', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls } = fightFixture({ shield: true })
  const tactics = createTactics(bot, ['shield'], { pvp })
  tactics.attach(target)
  await tickBot(t, bot, 1) // up, then down for the first strike
  assert.deepEqual(shieldCalls(calls), ['activate', 'deactivate', 'attack'])
  await tickBot(t, bot, 1)
  assert.deepEqual(shieldCalls(calls).slice(3), ['activate'])
  target.position = new Vec3(0.5, 10, -5)
  await tickBot(t, bot, 1)
  assert.deepEqual(shieldCalls(calls).slice(4), ['deactivate'])
  tactics.detach()
})

test('shield: canStrike holds while warm and their strike is due within 3 ticks, releases after their swing', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, attacks } = fightFixture({ shield: true })
  const tactics = createTactics(bot, ['shield'], { pvp })
  tactics.attach(target)
  await tickBot(t, bot, 1) // not warm: the first strike goes
  swing(bot, target) // at tick 1: due at 14, the shield is up since tick 2 (their strike was overdue)
  await tickBot(t, bot, 11) // ticks 2..12: warm, due in more than 3 ticks until tick 11; our cooldown is full at 14
  assert.equal(attacks(), 1)
  await tickBot(t, bot, 2) // ticks 13, 14: gate open, their strike due in 1 and 0 ticks
  assert.equal(attacks(), 1)
  swing(bot, target)
  await tickBot(t, bot, 1)
  assert.equal(attacks(), 2)
  tactics.detach()
})

test('shield: canStrike never holds more than 8 ticks in a row', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, attacks } = fightFixture({ shield: true })
  const tactics = createTactics(bot, ['shield'], { pvp })
  tactics.attach(target)
  await tickBot(t, bot, 1) // the first strike; they never swing, so their strike stays overdue
  await tickBot(t, bot, 20) // ticks 2..21: the gate opens at 14, held ticks 14..21
  assert.equal(attacks(), 1)
  await tickBot(t, bot, 1)
  assert.equal(attacks(), 2)
  tactics.detach()
})

test('shield: a skeleton farther than 3 blocks keeps the shield up; closer, the normal rule applies', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls } = fightFixture({ shield: true, distance: 8 })
  target.name = 'skeleton'
  const tactics = createTactics(bot, ['shield'], { pvp })
  tactics.attach(target)
  swing(bot, target)
  await tickBot(t, bot, 1)
  assert.deepEqual(calls.filter((call) => /^(de)?activate$/.test(call)), ['activate'])
  target.position = new Vec3(0.5, 10, -2) // 2 blocks, its strike not due for 12 ticks
  await tickBot(t, bot, 1)
  assert.deepEqual(calls.filter((call) => /^(de)?activate$/.test(call)), ['activate', 'deactivate'])
  tactics.detach()
})

test('shield + crit: crit\'s strike lead is 1 (no pvp routine), crit alone with a shield still 3', async (t) => {
  fakeTimers(t)
  const rising = async (tactics) => {
    const { bot, pvp, target, attacks } = fightFixture({ shield: true })
    const controller = createTactics(bot, tactics, { pvp })
    controller.attach(target)
    await tickBot(t, bot, 1) // jump requested at tick 1
    bot.entity.onGround = false
    bot.entity.velocity.y = 0.3
    let ticks = 0
    while (attacks() === 0 && ticks < 30) { await tickBot(t, bot, 1); ticks++ }
    controller.detach()
    return ticks
  }
  const crit = await rising(['crit'])
  const withShield = await rising(['shield', 'crit'])
  assert.ok(withShield >= crit + 2, `${withShield} vs ${crit}`)
})

test('shield: detach lowers a raised shield and removes the entitySwingArm listener', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls } = fightFixture({ shield: true, distance: 8 })
  target.name = 'skeleton'
  const listeners = bot.listenerCount('entitySwingArm')
  const tactics = createTactics(bot, ['shield'], { pvp })
  tactics.attach(target)
  assert.equal(bot.listenerCount('entitySwingArm'), listeners + 1)
  await tickBot(t, bot, 1)
  assert.deepEqual(calls.filter((call) => call === 'activate').length, 1)
  tactics.detach()
  assert.ok(calls.includes('deactivate'))
  assert.equal(bot.listenerCount('entitySwingArm'), listeners)
})

test('shield: a mob\'s next strike is due 20 ticks after its swing, a player\'s with an iron sword 13', async (t) => {
  fakeTimers(t)
  const raisedAfter = async (mob) => {
    const { bot, pvp, target, calls } = fightFixture({ shield: true, distance: 4.4 })
    if (mob) Object.assign(target, { name: 'zombie', type: 'mob', heldItem: undefined })
    const tactics = createTactics(bot, ['shield'], { pvp })
    tactics.attach(target)
    swing(bot, target)
    let ticks = 0
    while (!calls.includes('activate') && ticks < 30) { await tickBot(t, bot, 1); ticks++ }
    tactics.detach()
    return ticks
  }
  assert.equal(await raisedAfter(false), 13 - 7) // raised at tick 6
  assert.equal(await raisedAfter(true), 20 - 7) // raised at tick 13
})
