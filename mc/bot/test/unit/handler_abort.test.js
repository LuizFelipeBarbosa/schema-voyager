const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { craft } = require('../../lib/craft')
const { explore } = require('../../lib/explore')
const { mine } = require('../../lib/mine')
const { findPlacedStation } = require('../../lib/station')
const { createUnreachableMemory } = require('../../lib/targets')
const { gotoGoal, pickUpEntities } = require('../../lib/world')
const { fakeWorldBot } = require('./world_fixture')

function newAbort () {
  const controller = new AbortController()
  const abort = () => controller.abort(Object.assign(new Error('aborted'), { code: 'aborted' }))
  return { signal: controller.signal, abort }
}

const nextTick = () => new Promise((resolve) => setImmediate(resolve))

// Resolves to the rejection of `promise`, which must happen within `limitMs`.
async function rejectionWithin (promise, limitMs = 100) {
  const startedAt = Date.now()
  const err = await promise.then(() => assert.fail('resolved'), (e) => e)
  assert.ok(Date.now() - startedAt < limitMs, `took ${Date.now() - startedAt} ms`)
  return err
}

// A bot whose pathfinder never arrives; counts its goals and setGoal(null) calls.
function stuckBot (blocks = {}) {
  const bot = fakeWorldBot(blocks)
  bot.entity.onGround = true
  bot.on = () => {}
  bot.removeListener = () => {}
  bot.dig = async () => {}
  bot.inventory = { slots: [], items: () => [] }
  bot.heldItem = null
  bot.clearControlStates = () => {}
  bot.pathfinder = {
    goals: 0,
    stops: 0,
    setMovements () {},
    setGoal (goal) { if (goal === null) bot.pathfinder.stops++ },
    goto: () => { bot.pathfinder.goals++; return new Promise(() => {}) }
  }
  return bot
}

function floorBlocks (extra = {}) {
  const blocks = {}
  for (let x = -40; x <= 40; x++) for (let z = -40; z <= 40; z++) blocks[`${x},9,${z}`] = 'stone'
  return { ...blocks, ...extra }
}

const neverEnds = { isEnd: () => false }

test('gotoGoal throws at entry when already aborted, without walking', async () => {
  const bot = stuckBot()
  const { signal, abort } = newAbort()
  abort()
  await assert.rejects(gotoGoal(bot, neverEnds, 1000, { signal }), { code: 'aborted' })
  assert.equal(bot.pathfinder.goals, 0)
})

test('gotoGoal stops the walk and throws promptly when aborted mid-walk', async () => {
  const bot = stuckBot()
  const { signal, abort } = newAbort()
  const walking = gotoGoal(bot, neverEnds, 5000, { signal })
  await nextTick()
  abort()
  const err = await rejectionWithin(walking)
  assert.equal(err.code, 'aborted')
  assert.equal(bot.pathfinder.stops, 1)
})

test('gotoGoal without a signal is unchanged: a failed walk is false', async () => {
  const bot = stuckBot()
  assert.equal(await gotoGoal(bot, neverEnds, 20), false)
  assert.equal(bot.pathfinder.stops, 1)
})

test('pickUpEntities stops walking once aborted', async () => {
  const bot = stuckBot()
  bot.entities = { 1: { id: 1, position: new Vec3(5, 10, 5) }, 2: { id: 2, position: new Vec3(6, 10, 5) } }
  const { signal, abort } = newAbort()
  const picking = pickUpEntities(bot, Object.values(bot.entities), { signal })
  await nextTick()
  abort()
  assert.equal((await rejectionWithin(picking)).code, 'aborted')
  assert.equal(bot.pathfinder.goals, 1)
})

test('/mine aborted while travelling rejects, marks no target and walks to no second candidate', async () => {
  const blocks = floorBlocks({ '10,10,0': 'coal_ore', '20,10,0': 'coal_ore' })
  const bot = stuckBot(blocks)
  const { signal, abort } = newAbort()
  const episode = { skippedTargets: new Set(), walked: 0 }
  const unreachable = createUnreachableMemory()
  const tracker = { log: { lastSeq: () => 0, since: () => [], setPhase () {}, record () {} } }
  const body = { block: 'coal_ore', blocks: ['coal_ore'], tool: null, dig_through: false, max_distance: 90 }
  const mining = mine(bot, body, { episode, tracker, unreachable, signal })
  await nextTick()
  abort()
  assert.equal((await rejectionWithin(mining)).code, 'aborted')
  assert.equal(bot.pathfinder.goals, 1)
  assert.equal(episode.skippedTargets.size, 0)
  assert.equal(unreachable.has(bot, new Vec3(10, 10, 0)), false)
})

test('/explore aborted mid-leg rejects and starts no new leg', async () => {
  const bot = stuckBot(floorBlocks())
  const { signal, abort } = newAbort()
  const session = { episode: { walked: 0 }, tracker: { log: { record () {} } }, border: { centerX: 0, centerZ: 0, size: 1e6 }, signal }
  const exploring = explore(bot, session, { direction: 'E', distance: 32 })
  await nextTick()
  abort()
  assert.equal((await rejectionWithin(exploring)).code, 'aborted')
  assert.equal(bot.pathfinder.goals, 1)
})

test('/explore up aborted mid-climb rejects and starts no new climb', async () => {
  const bot = stuckBot(floorBlocks({ '0,12,0': 'stone' })) // a roof over the bot
  const { signal, abort } = newAbort()
  const session = { episode: { walked: 0 }, tracker: { log: { record () {} } }, border: {}, signal }
  const climbing = explore(bot, session, { direction: 'up', dig_through: true })
  await nextTick()
  abort()
  assert.equal((await rejectionWithin(climbing)).code, 'aborted')
  assert.equal(bot.pathfinder.goals, 1)
})

test('/craft aborted between runs throws and remembers the table it placed', async () => {
  const blocks = floorBlocks()
  const bot = stuckBot(blocks)
  const { signal, abort } = newAbort()
  const slots = new Array(46).fill(null)
  slots[36] = { name: 'crafting_table', count: 1 }
  bot.inventory = { slots, items: () => [slots[36]], count: () => 99, on () {}, removeListener () {} }
  bot._client = { on () {}, removeListener () {} }
  bot.findBlock = () => null
  bot.recipesFor = (id) => require('prismarine-recipe')(bot.registry).Recipe.find(id, null)
  bot.equip = async () => {}
  bot.placeBlock = async (against, face) => { blocks[`${against.position.plus(face)}`.replace(/[() ]/g, '')] = 'crafting_table' }
  let runs = 0
  bot.craft = async () => { if (++runs === 1) abort() }
  const episode = { walked: 0 }
  const session = { tracker: { log: { setPhase () {} }, ownDrops: new Set() }, episode, signal }

  const err = await rejectionWithin(craft(bot, { item: 'wooden_pickaxe', times: 2 }, session), 1000)
  assert.equal(err.code, 'aborted')
  assert.equal(runs, 1)
  assert.equal(episode.standingStations.length, 1)
  assert.equal(episode.standingStations[0].name, 'crafting_table')
  assert.equal(bot.blockAt(episode.standingStations[0].position).name, 'crafting_table')
})

test('findPlacedStation finds a remembered table 10 blocks away, forgets one that is gone, ignores a far one', () => {
  const bot = fakeWorldBot({ '10,10,0': 'crafting_table', '30,10,0': 'crafting_table' })
  bot.findBlock = () => null
  const episode = {
    standingStations: [
      { name: 'crafting_table', position: new Vec3(10, 10, 0) },
      { name: 'crafting_table', position: new Vec3(30, 10, 0) },
      { name: 'crafting_table', position: new Vec3(-5, 10, 0) } // now air
    ]
  }
  const near = findPlacedStation(bot, 'crafting_table', { episode })
  assert.deepEqual(near.position, new Vec3(10, 10, 0))
  assert.deepEqual(episode.standingStations.map((s) => s.position.x), [10, 30]) // the gone one is forgotten

  episode.standingStations.shift()
  assert.equal(findPlacedStation(bot, 'crafting_table', { episode }), null) // 30 blocks away
  assert.equal(findPlacedStation(bot, 'crafting_table'), null) // no episode: only the 4-block search
})
