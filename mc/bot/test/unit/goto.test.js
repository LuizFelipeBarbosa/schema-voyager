const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { goto, validateGoto } = require('../../lib/goto')
const { fakeWorldBot } = require('./world_fixture')

// A bot whose pathfinder.goto runs `onGoto(goal)` (and moves the bot to `endsAt` first).
function walkingBot (onGoto) {
  const bot = fakeWorldBot()
  bot.entity.onGround = true
  bot.on = () => {}
  bot.removeListener = () => {}
  bot.clearControlStates = () => {}
  bot.dig = async () => {}
  bot.inventory = { slots: [], items: () => [] }
  bot.pathfinder = {
    movements: 'previous',
    seen: [],
    setMovements (movements) { bot.pathfinder.movements = movements; bot.pathfinder.seen.push(movements) },
    setGoal () {},
    goto: async (goal) => onGoto(bot, goal)
  }
  return bot
}

const session = (extra = {}) => ({ episode: { walked: 0 }, ...extra })

test('validateGoto accepts integers, a range of 1..16 and optional protect', () => {
  assert.equal(validateGoto({ x: 10, y: 64, z: -3 }), null)
  assert.equal(validateGoto({ x: 10, y: 64, z: -3, range: 16, protect: [{ origin: [0, 0, 0], size: [1, 1, 1] }] }), null)
  const bad = [
    {},
    { x: 1, y: 2 },
    { x: 1.5, y: 2, z: 3 },
    { x: '1', y: 2, z: 3 },
    { x: 1, y: 2, z: 3, range: 0 },
    { x: 1, y: 2, z: 3, range: 17 },
    { x: 1, y: 2, z: 3, range: 2.5 },
    { x: 1, y: 2, z: 3, stall_ms: 999 },
    { x: 1, y: 2, z: 3, stall_ms: 55001 },
    { x: 1, y: 2, z: 3, stall_ms: 8000.5 },
    { x: 1, y: 2, z: 3, stall_ms: '8000' },
    { x: 1, y: 2, z: 3, protect: [{ origin: [0, 0], size: [1, 1, 1] }] }
  ]
  for (const stall_ms of [1000, 8000, 55000]) assert.equal(validateGoto({ x: 1, y: 2, z: 3, stall_ms }), null)
  for (const body of bad) assert.equal(typeof validateGoto(body), 'string', JSON.stringify(body))
})

test('goto arrives when the pathfinder ends within range, with world-preserving movements restored after', async () => {
  const bot = walkingBot(async (b, goal) => {
    assert.equal(goal.constructor.name, 'GoalNear')
    b.entity.position = new Vec3(10.5, 10, -2.5)
  })
  const result = await goto(bot, { x: 10, y: 10, z: -3, range: 2 }, session())
  assert.equal(result.ok, true)
  assert.equal(result.arrived, true)
  assert.deepEqual(Object.keys(result.travel).sort(), ['dug', 'distance', 'picked_up', 'placed', 'placed_cells'].sort())
  const [movements] = bot.pathfinder.seen
  assert.equal(movements.canDig, false)
  assert.equal(movements.canOpenDoors, false)
  assert.deepEqual(movements.scafoldingBlocks, [])
  assert.equal(bot.pathfinder.movements, 'previous')
})

test('goto answers unreachable when the pathfinder rejects or ends out of range', async () => {
  const rejecting = walkingBot(async () => { throw new Error('noPath') })
  const noPath = await goto(rejecting, { x: 10, y: 10, z: -3 }, session())
  assert.equal(noPath.ok, false)
  assert.equal(noPath.error, 'unreachable')
  assert.equal(noPath.arrived, false)
  assert.ok(noPath.travel)

  const short = walkingBot(async (b) => { b.entity.position = new Vec3(5.5, 10, 0.5) })
  const tooFar = await goto(short, { x: 10, y: 10, z: -3, range: 3 }, session())
  assert.equal(tooFar.error, 'unreachable')
})

test('goto with stall_ms ends a stalled walk unreachable, with where it ended', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] })
  const bot = walkingBot(() => new Promise(() => {})) // the bot stands still and the pathfinder never answers
  bot.entity.position = new Vec3(1.234, 10, -5.678)

  const pending = goto(bot, { x: 10, y: 10, z: -3, stall_ms: 2000 }, session())
  for (let elapsed = 0; elapsed < 3000; elapsed += 500) {
    t.mock.timers.tick(500)
    await new Promise((resolve) => setImmediate(resolve))
  }
  const result = await pending

  assert.deepEqual([result.ok, result.error, result.arrived], [false, 'unreachable', false])
  assert.ok(result.travel)
})

test('goto without stall_ms is not given up on for standing still, and an arrival has no position', async () => {
  const arrives = await goto(walkingBot(async (b) => { b.entity.position = new Vec3(10.5, 10, -2.5) }), { x: 10, y: 10, z: -3 }, session())
  assert.equal('position' in arrives, false)
})

test('goto aborted before and during the walk throws the signal reason, as /mine does', async () => {
  const abortWith = () => {
    const controller = new AbortController()
    return { signal: controller.signal, abort: () => controller.abort(Object.assign(new Error('aborted'), { code: 'aborted' })) }
  }

  const before = abortWith()
  before.abort()
  const idle = walkingBot(async () => assert.fail('walked'))
  await assert.rejects(goto(idle, { x: 1, y: 10, z: 1 }, session({ signal: before.signal })), { code: 'aborted' })

  const during = abortWith()
  const stuck = walkingBot(() => new Promise(() => {}))
  const walking = goto(stuck, { x: 1, y: 10, z: 1 }, session({ signal: during.signal }))
  await new Promise((resolve) => setImmediate(resolve))
  during.abort()
  await assert.rejects(walking, { code: 'aborted' })
  assert.equal(stuck.pathfinder.movements, 'previous')
})
