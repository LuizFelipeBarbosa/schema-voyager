const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const {
  directionAttempts, explore, hasTimeForAttempt, neighbourDirections, standableNeighbours
} = require('../../lib/explore')
const { fakeWorldBot } = require('./world_fixture')

function floorBlocks (extra = {}) {
  const blocks = {}
  for (let x = -40; x <= 40; x++) for (let z = -40; z <= 40; z++) blocks[`${x},9,${z}`] = 'stone'
  return { ...blocks, ...extra }
}

// A bot on a flat floor whose pathfinder never arrives and never moves it.
function stuckBot (blocks = floorBlocks()) {
  const bot = fakeWorldBot(blocks)
  bot.entity.onGround = true
  bot.listeners = new Set()
  bot.on = (event, fn) => bot.listeners.add(fn)
  bot.removeListener = (event, fn) => bot.listeners.delete(fn)
  bot.dig = async () => {}
  bot.inventory = { slots: [], items: () => [] }
  bot.clearControlStates = () => {}
  bot.pathfinder = { goals: 0, setGoal () {}, goto: () => { bot.pathfinder.goals++; return new Promise(() => {}) } }
  return bot
}

const session = () => ({
  episode: { walked: 0 },
  tracker: { log: { record () {} } },
  border: { centerX: 0, centerZ: 0, size: 1e6 }
})

test('neighbourDirections wraps around the compass', () => {
  assert.deepEqual(neighbourDirections('NW'), ['W', 'N'])
  assert.deepEqual(neighbourDirections('N'), ['NW', 'NE'])
})

test('directionAttempts: request, short hop, then the neighbours', () => {
  assert.deepEqual(directionAttempts('NW', 32), [
    { direction: 'NW', distance: 32 },
    { direction: 'NW', distance: 10 },
    { direction: 'W', distance: 10 },
    { direction: 'N', distance: 10 }
  ])
  assert.deepEqual(directionAttempts('E', 6).map((a) => a.direction), ['E', 'NE', 'SE'])
})

test('hasTimeForAttempt needs a whole stall time left in the budget', () => {
  assert.equal(hasTimeForAttempt(15000), true)
  assert.equal(hasTimeForAttempt(16500), false)
})

test('standableNeighbours finds floored cells with room, and none inside a hole', () => {
  const open = fakeWorldBot(floorBlocks())
  assert.equal(standableNeighbours(open, new Vec3(0, 10, 0), -1, 1).length, 4)

  const walls = {}
  for (const [x, z] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) for (const y of [10, 11]) walls[`${x},${y},${z}`] = 'stone'
  const hole = fakeWorldBot(floorBlocks(walls))
  assert.equal(standableNeighbours(hole, new Vec3(0, 10, 0), -1, 1).length, 0)
})

test('a stalled explore gives up in about the stall time per attempt, not the full timeout', async (t) => {
  const bot = stuckBot()
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] })
  const startedAt = Date.now()
  let reply = null
  const running = explore(bot, session(), { direction: 'NW', distance: 32 }).then((r) => { reply = r })
  while (!reply) {
    t.mock.timers.tick(250)
    await new Promise((resolve) => setImmediate(resolve))
  }
  await running
  const elapsedMs = Date.now() - startedAt

  assert.equal(reply.error, 'unreachable')
  assert.equal(reply.ok, false)
  assert.equal(reply.travel.distance, 0)
  assert.equal(bot.pathfinder.goals, 4) // request, short hop, W, N: one stalled goal each
  assert.ok(elapsedMs >= 4 * 4000 && elapsedMs <= 20000, `took ${elapsedMs} ms`)
})
