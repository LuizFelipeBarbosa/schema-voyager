const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const { goals } = require('mineflayer-pathfinder')
const { PVP } = require('mineflayer-pvp/lib/PVP')
const { TACTICS, DEFAULT_TACTICS, jumpTimeline, validateTactics, createTactics, isSafeStep } = require('../../lib/tactics')
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
  assert.deepEqual([...TACTICS], ['wtap', 'crit'])
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
  tactics.attach(target)
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
  tactics.attach(target)
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
  tactics.attach(target)
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
  tactics.attach(target)
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
  tactics.attach(target)
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
  const { bot, pvp, target } = fightFixture({ distance: 3.5, blocks: floorBlocks(['0,9,-1', '0,9,-2', '0,9,-3']) })
  const tactics = createTactics(bot, ['wtap'], { pvp })
  tactics.attach(target)
  await tickBot(t, bot, 5)
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
