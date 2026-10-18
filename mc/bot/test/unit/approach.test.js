const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { approachTarget } = require('../../lib/approach')
const { mine } = require('../../lib/mine')
const { createUnreachableMemory } = require('../../lib/targets')
const { gotoGoalDetailed } = require('../../lib/world')
const { fakeWorldBot } = require('./world_fixture')

const request = { block: 'coal_ore', blocks: ['coal_ore'], tool: null, dig_through: false, max_distance: 90 }

// A bot over a flat floor with coal at the given y 10 positions. `walk(bot,
// emit)` is its pathfinder's goto: it returns the promise goto would, and may
// emit pathfinder events or move the bot (setInterval runs on mocked time).
function walkerBot (coal, walk) {
  const blocks = {}
  for (let x = -5; x <= 80; x++) for (let z = -30; z <= 30; z++) blocks[`${x},9,${z}`] = 'stone'
  for (const [x, z] of coal) blocks[`${x},10,${z}`] = 'coal_ore'
  const bot = fakeWorldBot(blocks)
  const listeners = []
  const emit = (event, ...args) => listeners.filter((l) => l.event === event).forEach((l) => l.fn(...args))
  bot.listeners = listeners
  bot.on = (event, fn) => listeners.push({ event, fn })
  bot.removeListener = (event, fn) => {
    const index = listeners.findIndex((l) => l.event === event && l.fn === fn)
    if (index >= 0) listeners.splice(index, 1)
  }
  bot.dig = async () => {}
  bot.inventory = { slots: [], items: () => [] }
  bot.heldItem = null
  bot.clearControlStates = () => {}
  bot.pathfinder = {
    goals: 0,
    setGoal () {},
    goto: (goal) => {
      bot.pathfinder.goals++
      return walk(bot, emit, goal)
    }
  }
  return bot
}

const stuck = () => new Promise(() => {})
const stepEvery500ms = (dx, dz) => (bot) => {
  setInterval(() => { bot.entity.position = bot.entity.position.offset(dx, 0, dz) }, 500)
  return stuck()
}

const session = () => ({
  episode: { skippedTargets: new Set(), walked: 0 },
  tracker: { log: { lastSeq: () => 0, since: () => [], setPhase () {}, record () {} } },
  unreachable: createUnreachableMemory()
})

// Runs `mine` on mocked time until it answers; returns the reply and the
// mocked milliseconds it took.
async function runMocked (t, bot, sess) {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] })
  const startedAt = Date.now()
  let reply = null
  const running = mine(bot, request, sess).then((r) => { reply = r })
  while (!reply) {
    t.mock.timers.tick(250)
    await new Promise((resolve) => setImmediate(resolve))
  }
  await running
  return { reply, elapsedMs: Date.now() - startedAt }
}

const remembered = (sess, bot, x, z) => sess.unreachable.has(bot, new Vec3(x, 10, z))
const skipped = (sess, x, z) => sess.episode.skippedTargets.has(new Vec3(x, 10, z).toString())

test('a walk that keeps getting closer runs to the call budget and marks nothing', async (t) => {
  const bot = walkerBot([[60, 0]], stepEvery500ms(0.3, 0))
  const sess = session()
  const { reply, elapsedMs } = await runMocked(t, bot, sess)
  assert.equal(reply.error, 'unreachable')
  assert.equal(reply.reach.reason, 'approaching')
  assert.equal(reply.reach.attempts.length, 1)
  assert.equal(bot.pathfinder.goals, 1)
  assert.ok(elapsedMs >= 60000 && elapsedMs < 61000, `took ${elapsedMs} ms`)
  assert.equal(skipped(sess, 60, 0), false)
  assert.equal(remembered(sess, bot, 60, 0), false)
})

test('a walk that drifts sideways ends no_approach after 10 s, then the next candidate is tried', async (t) => {
  const bot = walkerBot([[30, 0], [50, 0]], stepEvery500ms(0, 1))
  const sess = session()
  const { reply, elapsedMs } = await runMocked(t, bot, sess)
  assert.deepEqual(reply.reach.attempts.map((a) => a.end), ['no_approach', 'no_approach'])
  assert.ok(reply.reach.attempts.every((a) => a.ms >= 10000 && a.ms < 11000), JSON.stringify(reply.reach.attempts))
  assert.ok(elapsedMs < 22000, `took ${elapsedMs} ms`)
  for (const x of [30, 50]) {
    assert.equal(skipped(sess, x, 0), true)
    assert.equal(remembered(sess, bot, x, 0), false)
  }
})

test('a bot that never moves stalls at 4 s on each attempt and marks the episode only', async (t) => {
  const bot = walkerBot([[10, 0], [20, 0], [30, 0]], stuck)
  const sess = session()
  const { reply } = await runMocked(t, bot, sess)
  assert.deepEqual(reply.reach.attempts.map((a) => a.end), ['stalled', 'stalled', 'stalled'])
  assert.ok(reply.reach.attempts.every((a) => a.ms >= 4000 && a.ms < 5000), JSON.stringify(reply.reach.attempts))
  for (const x of [10, 20, 30]) {
    assert.equal(skipped(sess, x, 0), true)
    assert.equal(remembered(sess, bot, x, 0), false)
  }
})

test('no_path is remembered across episodes, and a later call skips the block', async (t) => {
  const bot = walkerBot([[10, 0]], (b, emit) => {
    emit('path_update', { status: 'noPath', path: [], time: 3 })
    return Promise.reject(new Error('No path'))
  })
  const sess = session()
  const { reply } = await runMocked(t, bot, sess)
  assert.equal(reply.reach.reason, 'no_path')
  assert.equal(remembered(sess, bot, 10, 0), true)
  sess.episode.skippedTargets.clear()
  t.mock.timers.reset()
  assert.equal((await mine(bot, request, sess)).error, 'not_found')
})

test('the end follows the last path_update status, whether goto rejects or resolves', async (t) => {
  const cases = [
    { name: 'timeout status then a Timeout rejection', end: 'path_timeout', memory: false,
      walk: (b, emit) => { emit('path_update', { status: 'timeout', path: [{}], time: 40 }); return Promise.reject(new Error('Timeout')) } },
    { name: 'noPath status on an empty path, goto resolves', end: 'no_path', memory: true,
      walk: (b, emit) => { emit('path_update', { status: 'noPath', path: [], time: 2 }); return Promise.resolve() } },
    { name: 'success status, goto resolves away from the goal', end: 'ended', memory: false,
      walk: (b, emit) => { emit('path_update', { status: 'success', path: [{}], time: 2 }); return Promise.resolve() } },
    { name: 'partial status, goto rejects (goal changed)', end: 'ended', memory: false,
      walk: (b, emit) => { emit('path_update', { status: 'partial', path: [{}], time: 2 }); return Promise.reject(new Error('GoalChanged')) } }
  ]
  for (const { name, end, memory, walk } of cases) {
    const bot = walkerBot([[10, 0]], walk)
    const sess = session()
    const { reply } = await runMocked(t, bot, sess)
    t.mock.timers.reset()
    assert.equal(reply.reach.reason, end, name)
    assert.equal(skipped(sess, 10, 0), true, name)
    assert.equal(remembered(sess, bot, 10, 0), memory, name)
  }
})

test('reach.attempts carry the documented keys with rounded numbers, and count resets and think time', async (t) => {
  const bot = walkerBot([[10, 0]], (b, emit) => {
    emit('path_reset', 'goal_updated')
    emit('path_reset', 'block_updated')
    emit('path_update', { status: 'partial', path: [{}], time: 10.4 })
    emit('path_update', { status: 'noPath', path: [], time: 20.4 })
    return Promise.resolve()
  })
  const { reply } = await runMocked(t, bot, session())
  const [attempt] = reply.reach.attempts
  assert.deepEqual(Object.keys(attempt), ['target', 'end', 'ms', 'start', 'closest', 'resets', 'think_ms'])
  assert.deepEqual(attempt.target, [10, 10, 0])
  assert.equal(attempt.resets, 2)
  assert.equal(attempt.think_ms, 20) // the partial update is superseded by the noPath one of the same search
  // the bot stands at (0.5, 10, 0.5); the target's centre is (10.5, 10.5, 0.5)
  assert.equal(attempt.start, 10)
  assert.equal(attempt.closest, 10)
  assert.equal(typeof attempt.ms, 'number')
  assert.equal(bot.listeners.length, 0) // the listeners are gone
})

test('an abort mid-walk throws the signal reason and marks nothing', async (t) => {
  const bot = walkerBot([[10, 0], [20, 0]], stuck)
  const sess = session()
  const controller = new AbortController()
  const reason = Object.assign(new Error('aborted'), { code: 'aborted' })
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] })
  const running = mine(bot, request, { ...sess, signal: controller.signal })
  const outcome = running.then(() => null, (err) => err)
  for (let i = 0; i < 4; i++) {
    t.mock.timers.tick(250)
    await new Promise((resolve) => setImmediate(resolve))
  }
  controller.abort(reason)
  assert.equal(await outcome, reason)
  assert.equal(sess.episode.skippedTargets.size, 0)
  assert.equal(remembered(sess, bot, 10, 0), false)
})

test('approachTarget ends on the reached attempt, reporting it last', async () => {
  const bot = walkerBot([], (b) => { b.entity.position = new Vec3(10.5, 10, 0.5); return Promise.resolve() })
  const target = new Vec3(10, 10, 1)
  const goal = { isEnd: (node) => node.x === 10 && node.z === 0 }
  const marked = []
  const outcome = await approachTarget(bot, [target], {
    goalFor: () => goal,
    withMovements: (pos, walk) => walk(),
    mark: (pos, end) => marked.push(end)
  })
  assert.equal(outcome.reached, true)
  assert.equal(outcome.targetPos, target)
  assert.equal(outcome.attempts.at(-1).end, 'reached')
  assert.deepEqual(marked, [])
})

test('gotoGoalDetailed without approach reports null distances and delegates timeouts', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] })
  const bot = walkerBot([], stuck)
  const running = gotoGoalDetailed(bot, { isEnd: () => false }, 5000)
  await new Promise((resolve) => setImmediate(resolve))
  t.mock.timers.tick(5000)
  const outcome = await running
  assert.equal(outcome.end, 'timeout')
  assert.equal(outcome.reached, false)
  assert.equal(outcome.start, null)
  assert.equal(outcome.closest, null)
})

test('a successful mine carries reach whose last attempt is reached', async (t) => {
  const bot = walkerBot([[10, 0]], (b) => { b.entity.position = new Vec3(9.5, 10, 0.5); return Promise.resolve() })
  const clientListeners = []
  bot._client = {
    on: (event, fn) => clientListeners.push({ event, fn }),
    removeListener () {}
  }
  bot.inventory.on = () => {}
  bot.inventory.removeListener = () => {}
  bot.inventory.slots = []
  bot.pathfinder.setMovements = () => {}
  bot.entities = {}
  const airState = bot.registry.blocksByName.air.defaultState
  bot.dig = async (block) => {
    const { x, y, z } = block.position
    for (const { event, fn } of clientListeners) if (event === 'block_change') fn({ location: { x, y, z }, type: airState })
    bot.world.getBlockStateId = (pos) => pos.equals(block.position) ? airState : bot.registry.blocksByName.stone.defaultState
    bot.blockAt = (pos) => pos.equals(block.position) ? { name: 'air', position: pos } : block
  }
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] })
  let reply = null
  const sess = session()
  sess.tracker.ownDrops = new Set()
  sess.tracker.watchInterference = () => () => {}
  const running = mine(bot, { ...request, dig_through: true }, sess).then((r) => { reply = r })
  while (!reply) {
    t.mock.timers.tick(250)
    await new Promise((resolve) => setImmediate(resolve))
  }
  await running
  assert.equal(reply.ok, true, JSON.stringify(reply))
  assert.equal(reply.reach.reason, 'reached')
  assert.equal(reply.reach.attempts.at(-1).end, 'reached')
})

test('think time counts each A* search once: partial times are cumulative within a search', async () => {
  const thinkMsOf = async (updates) => {
    const bot = walkerBot([], (b, emit) => {
      for (const update of updates) emit('path_update', { path: [{}], ...update })
      return Promise.resolve()
    })
    return (await gotoGoalDetailed(bot, { isEnd: () => false }, 5000)).thinkMs
  }
  assert.equal(await thinkMsOf([
    { status: 'partial', time: 40 },
    { status: 'partial', time: 80 },
    { status: 'success', time: 120 },
    { status: 'partial', time: 30 } // a search still running when the attempt ends
  ]), 150)
  assert.equal(await thinkMsOf([{ status: 'noPath', time: 12 }]), 12)
})
