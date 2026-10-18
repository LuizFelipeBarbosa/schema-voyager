const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { emptyTravel, recordTravel, travelSummary } = require('../../lib/travel')
const Vec3 = require('vec3').Vec3
const { TICK_MS, addCount, countDelta, countGains } = require('../../lib/world')

test('countDelta and countGains compare inventories', () => {
  const before = { oak_log: 2, dirt: 3, stick: 4 }
  const after = { oak_log: 1, dirt: 5, cobblestone: 1 }
  assert.deepEqual(countDelta(before, after), { oak_log: -1, dirt: 2, stick: -4, cobblestone: 1 })
  assert.deepEqual(countGains(before, after), { dirt: 2, cobblestone: 1 })
  assert.deepEqual(countDelta(before, before), {})
})

test('addCount accumulates', () => {
  const counts = {}
  addCount(counts, 'dirt')
  addCount(counts, 'dirt')
  addCount(counts, 'stone', 3)
  assert.deepEqual(counts, { dirt: 2, stone: 3 })
})

test('travelSummary: distance rounded, gains only, dug and placed passed through', () => {
  const travel = travelSummary({
    walkedBefore: 10,
    walkedAfter: 33.44,
    dug: { dirt: 3, stone: 1 },
    placed: { dirt: 1 },
    inventoryBefore: { dirt: 1, stick: 2 },
    inventoryAfter: { dirt: 3, stick: 2, cobblestone: 1 }
  })
  assert.deepEqual(travel, {
    distance: 23.4,
    dug: { dirt: 3, stone: 1 },
    placed: { dirt: 1 },
    picked_up: { dirt: 2, cobblestone: 1 },
    placed_cells: []
  })
})

test('emptyTravel is all zero and empty', () => {
  assert.deepEqual(emptyTravel(), { distance: 0, dug: {}, placed: {}, picked_up: {}, placed_cells: [] })
})

test('recordTravel counts completed digs, then unwraps bot.dig', async () => {
  const bot = new EventEmitter()
  bot.inventory = { slots: [] }
  let calls = 0
  const originalDig = async (block) => { calls++; if (block.name === 'bedrock') throw new Error('nope') }
  bot.dig = originalDig
  const episode = { walked: 5 }

  const recording = recordTravel(bot, episode)
  await bot.dig({ name: 'dirt' })
  await bot.dig({ name: 'dirt' })
  await bot.dig({ name: 'stone' })
  await assert.rejects(bot.dig({ name: 'bedrock' })) // aborted digs do not count
  episode.walked = 12.34
  await recording.settle()
  const travel = recording.finish()

  assert.equal(calls, 4)
  assert.equal(bot.dig, originalDig)
  assert.deepEqual(travel, {
    distance: 7.3,
    dug: { dirt: 2, stone: 1 },
    placed: {},
    picked_up: {},
    placed_cells: []
  })
})

// A fake bot whose world is a map of "x,y,z" -> block name (air when absent).
// placeBlock resolves at once; `landsAfter` ticks later (null: never) the dest becomes the held item.
function fakePlacingBot ({ landsAfter = 2, rejects = false, blocks = {}, heldItem = 'dirt' } = {}) {
  const bot = new EventEmitter()
  bot.inventory = { slots: [] }
  bot.heldItem = { name: heldItem }
  bot.entity = { onGround: true }
  bot.dig = async () => {}
  bot.blockAt = (pos) => ({ name: blocks[`${pos.x},${pos.y},${pos.z}`] ?? 'air' })
  bot.placeBlock = async (reference, face) => {
    const dest = reference.position.plus(face)
    if (landsAfter !== null) {
      setTimeout(() => { blocks[`${dest.x},${dest.y},${dest.z}`] = heldItem }, landsAfter * TICK_MS)
    }
    if (rejects) throw new Error('Event blockUpdate did not fire within timeout of 5000ms')
  }
  return bot
}

const below = (x, y, z) => [{ position: new Vec3(x, y - 1, z) }, new Vec3(0, 1, 0)]

test('recordTravel records a placement whose server update lands after the walk resolved', async () => {
  const bot = fakePlacingBot({ landsAfter: 2 })
  const recording = recordTravel(bot, { walked: 0 })
  await bot.placeBlock(...below(15, 66, 13))
  await recording.settle()
  const travel = recording.finish()
  assert.deepEqual(travel.placed_cells, [[15, 66, 13, 'dirt']])
  assert.deepEqual(travel.placed, { dirt: 1 })
})

test('recordTravel records three tower placements in order', async () => {
  const bot = fakePlacingBot({ landsAfter: 1 })
  const recording = recordTravel(bot, { walked: 0 })
  for (const y of [66, 67, 68]) await bot.placeBlock(...below(15, y, 13))
  await recording.settle()
  const travel = recording.finish()
  assert.deepEqual(travel.placed_cells, [[15, 66, 13, 'dirt'], [15, 67, 13, 'dirt'], [15, 68, 13, 'dirt']])
  assert.deepEqual(travel.placed, { dirt: 3 })
})

test('recordTravel: a rejected placement counts only if the block appeared anyway, and rethrows', async () => {
  const appeared = fakePlacingBot({ landsAfter: 1, rejects: true })
  const first = recordTravel(appeared, { walked: 0 })
  await assert.rejects(appeared.placeBlock(...below(1, 64, 1)), /did not fire/)
  await first.settle()
  assert.deepEqual(first.finish().placed_cells, [[1, 64, 1, 'dirt']])

  const missing = fakePlacingBot({ landsAfter: null, rejects: true })
  const second = recordTravel(missing, { walked: 0 })
  await assert.rejects(missing.placeBlock(...below(1, 64, 1)))
  await second.settle()
  assert.deepEqual(second.finish().placed_cells, [])
})

test('recordTravel ignores a cell that held a solid block before', async () => {
  const bot = fakePlacingBot({ landsAfter: 1, blocks: { '2,64,2': 'grass_block' } })
  const recording = recordTravel(bot, { walked: 0 })
  await bot.placeBlock(...below(2, 64, 2))
  await recording.settle()
  const travel = recording.finish()
  assert.deepEqual(travel.placed_cells, [])
  assert.deepEqual(travel.placed, {})
})

test('recordTravel records a cell attempted twice once', async () => {
  const bot = fakePlacingBot({ landsAfter: 1 })
  const recording = recordTravel(bot, { walked: 0 })
  await bot.placeBlock(...below(3, 64, 3))
  await bot.placeBlock(...below(3, 64, 3))
  await recording.settle()
  const travel = recording.finish()
  assert.deepEqual(travel.placed_cells, [[3, 64, 3, 'dirt']])
  assert.deepEqual(travel.placed, { dirt: 1 })
})

test('recordTravel restores placeBlock and dig; settle without attempts returns at once', async () => {
  const bot = fakePlacingBot()
  const { placeBlock, dig } = bot
  const recording = recordTravel(bot, { walked: 0 })
  assert.notEqual(bot.placeBlock, placeBlock)
  const startedAt = Date.now()
  await recording.settle()
  assert.ok(Date.now() - startedAt < TICK_MS)
  recording.finish()
  assert.equal(bot.placeBlock, placeBlock)
  assert.equal(bot.dig, dig)
})
