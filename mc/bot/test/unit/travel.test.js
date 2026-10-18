const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { emptyTravel, recordTravel, travelSummary } = require('../../lib/travel')
const { addCount, countDelta, countGains } = require('../../lib/world')

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

test('recordTravel counts completed digs and placements, then unwraps bot.dig', async () => {
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
  bot.emit('blockPlaced', { name: 'air' }, { name: 'dirt', position: { x: 3, y: 64, z: -2 } })
  episode.walked = 12.34
  await recording.settle()
  const travel = recording.finish()

  assert.equal(calls, 4)
  assert.equal(bot.dig, originalDig)
  assert.equal(bot.listenerCount('blockPlaced'), 0)
  assert.deepEqual(travel, {
    distance: 7.3,
    dug: { dirt: 2, stone: 1 },
    placed: { dirt: 1 },
    picked_up: {},
    placed_cells: [[3, 64, -2, 'dirt']]
  })
})

test('recordTravel lists each placement with its cell and name, in order', () => {
  const bot = new EventEmitter()
  bot.inventory = { slots: [] }
  const recording = recordTravel(bot, { walked: 0 })
  bot.emit('blockPlaced', { name: 'air' }, { name: 'dirt', position: { x: 1, y: 64, z: 2 } })
  bot.emit('blockPlaced', { name: 'air' }, { name: 'cobblestone', position: { x: 1, y: 65, z: 2 } })
  bot.emit('blockPlaced', { name: 'air' }, { name: 'dirt', position: { x: -4, y: 63, z: 0 } })
  const travel = recording.finish()
  assert.deepEqual(travel.placed_cells, [[1, 64, 2, 'dirt'], [1, 65, 2, 'cobblestone'], [-4, 63, 0, 'dirt']])
  assert.deepEqual(travel.placed, { dirt: 2, cobblestone: 1 })
})
