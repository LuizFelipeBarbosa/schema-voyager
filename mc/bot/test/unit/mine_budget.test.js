const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { mine } = require('../../lib/mine')
const { createUnreachableMemory } = require('../../lib/targets')
const { fakeWorldBot } = require('./world_fixture')

// A bot over a flat floor with coal at the given y 10 positions, whose
// pathfinder never arrives. With `drifts` it keeps moving sideways, so the
// stall check cannot end an attempt, only the approach rule. With `noPath` the
// search reports noPath on an empty path and goto resolves at once.
function stuckBot (coal, { drifts = false, noPath = false } = {}) {
  const blocks = {}
  for (let x = -5; x <= 80; x++) for (let z = -30; z <= 30; z++) blocks[`${x},9,${z}`] = 'stone'
  for (const [x, z] of coal) blocks[`${x},10,${z}`] = 'coal_ore'
  const bot = fakeWorldBot(blocks)
  bot.listeners = new Set()
  bot.on = (event, fn) => bot.listeners.add({ event, fn })
  bot.removeListener = (event, fn) => {
    for (const entry of bot.listeners) if (entry.event === event && entry.fn === fn) bot.listeners.delete(entry)
  }
  bot.dig = async () => {}
  bot.inventory = { slots: [], items: () => [] }
  bot.heldItem = null
  bot.clearControlStates = () => {}
  bot.pathfinder = {
    goals: 0,
    setGoal () {},
    goto: () => {
      bot.pathfinder.goals++
      if (drifts) setInterval(() => { bot.entity.position = bot.entity.position.offset(0, 0, 2) }, 500)
      if (noPath) {
        for (const { event, fn } of bot.listeners) if (event === 'path_update') fn({ status: 'noPath', path: [], time: 5 })
        return Promise.resolve()
      }
      return new Promise(() => {})
    }
  }
  return bot
}

const session = () => ({
  episode: { skippedTargets: new Set(), walked: 0 },
  tracker: { log: { lastSeq: () => 0, since: () => [], setPhase () {}, record () {} } },
  unreachable: createUnreachableMemory()
})
const request = { block: 'coal_ore', blocks: ['coal_ore'], tool: null, dig_through: false, max_distance: 90 }

// Runs `mine` on mocked time, advancing the clock until it answers; returns
// the reply and the mocked milliseconds it took.
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

test('a stuck v2 mine tries four candidates, giving up on each after the stall time', async (t) => {
  const bot = stuckBot([[10, 0], [20, 0], [30, 0], [40, 0], [50, 0]])
  const sess = session()
  const { reply, elapsedMs } = await runMocked(t, bot, sess)
  assert.equal(reply.error, 'unreachable')
  assert.equal(bot.pathfinder.goals, 4)
  assert.deepEqual(reply.target, { x: 40, y: 10, z: 0 })
  assert.ok(elapsedMs >= 4 * 4000 && elapsedMs < 4 * 4000 + 1500, `took ${elapsedMs} ms`)
  assert.equal(sess.episode.skippedTargets.size, 4)
  assert.equal(sess.unreachable.has(bot, new Vec3(10, 10, 0)), false) // stalled: this episode only
  assert.deepEqual(reply.reach.attempts.map((a) => a.end), ['stalled', 'stalled', 'stalled', 'stalled'])
})

test('a v2 mine that keeps moving without getting closer ends each attempt after the approach window', async (t) => {
  const bot = stuckBot([[10, 0], [20, 0], [30, 0], [40, 0], [50, 0]], { drifts: true })
  const { reply, elapsedMs } = await runMocked(t, bot, session())
  assert.equal(reply.error, 'unreachable')
  assert.ok(bot.pathfinder.goals <= 4)
  assert.equal(reply.reach.reason, 'no_approach')
  assert.ok(elapsedMs >= 4 * 10000 && elapsedMs <= 4 * 10000 + 2000, `took ${elapsedMs} ms`)
})

test('candidates in the same pocket as a failed one are not tried in the same call', async (t) => {
  const bot = stuckBot([[10, 0], [10, 2], [11, 3], [30, 0]])
  const { reply } = await runMocked(t, bot, session())
  assert.equal(bot.pathfinder.goals, 2) // (10,0) and (30,0)
  assert.deepEqual(reply.target, { x: 30, y: 10, z: 0 })
})

test('a later call skips the targets an earlier call found unreachable', async (t) => {
  const bot = stuckBot([[10, 0], [20, 0]], { noPath: true })
  const sess = session()
  await runMocked(t, bot, sess)
  sess.episode.skippedTargets.clear() // a new episode
  t.mock.timers.reset()
  const second = await mine(bot, request, sess)
  assert.equal(second.error, 'not_found')
})
