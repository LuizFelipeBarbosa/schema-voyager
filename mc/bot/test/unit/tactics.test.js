const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const { goals } = require('mineflayer-pathfinder')
const { PVP } = require('mineflayer-pvp/lib/PVP')
const { TACTICS, DEFAULT_TACTICS, validateTactics, createTactics, isSafeStep } = require('../../lib/tactics')
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
  const target = { id: 7, name: 'player', height: 1.8, position: new Vec3(0.5, 10, 0.5 - distance) }
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

test('validateTactics: [] is fine; a non-array, non-string, unknown or repeated name is a problem', () => {
  assert.equal(validateTactics([]), null)
  assert.match(validateTactics('crit'), /array/)
  assert.match(validateTactics([3]), /strings/)
  assert.match(validateTactics(['nope']), /unknown/)
  const registry = { a: () => ({}), b: () => ({}) }
  assert.equal(validateTactics(['b', 'a'], registry), null)
  assert.match(validateTactics(['a', 'b', 'a'], registry), /repeated/)
  assert.deepEqual([...TACTICS], Object.keys({}))
  assert.deepEqual([...DEFAULT_TACTICS], [])
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
  tactics.attach(target)
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
  tactics.attach(target)
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

test('mover: a steer hook changes the plan', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target } = fightFixture({ distance: 3.5 })
  const registry = { sidestep: () => ({ steer: (ctx, controls) => { controls.left = true; controls.sprint = false } }) }
  const tactics = createTactics(bot, ['sidestep'], { pvp, registry })
  tactics.attach(target)
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
    ['a 2-block drop', floorBlocks(ahead), false],
    ['lava on the floor', floorBlocks([], { '0,9,-1': 'lava' }), false],
    ['lava in the feet cell', floorBlocks([], { '0,10,-1': 'lava' }), false],
    ['a 1-block drop', floorBlocks(ahead, { '0,8,-1': 'stone' }), true],
    ['solid ground', floorBlocks(), true]
  ]
  for (const [label, blocks, walks] of cases) {
    const { bot, pvp, target } = fightFixture({ distance: 3.5, blocks })
    const tactics = createTactics(bot, [], { pvp })
    tactics.attach(target)
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

test('detach: restores pvp, the listeners and the controls, and ends an in-flight strike harmlessly', async (t) => {
  fakeTimers(t)
  const { bot, pvp, target, calls, attacks } = fightFixture({ distance: 3.5, shield: true })
  const before = { physicsTick: bot.listenerCount('physicsTick'), physicTick: bot.listenerCount('physicTick') }
  const tactics = createTactics(bot, [], { pvp })
  tactics.attach(target)
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
