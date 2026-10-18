const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const registry = require('prismarine-registry')('1.21.1')
const { decodeMultiBlockChange, watchServerBlockChange } = require('../../lib/blockchange')

// A 1.21 record: state id above 12 bits of (x << 8 | z << 4 | y) offsets.
const record = (stateId, x, y, z) => (stateId << 12) | (x << 8) | (z << 4) | y

test('decodeMultiBlockChange turns section offsets into world positions', () => {
  const changes = decodeMultiBlockChange({
    chunkCoordinates: { x: 1, y: 4, z: -2 },
    records: [record(5, 3, 7, 15), record(0, 0, 0, 0)]
  })
  assert.deepEqual(changes.map((change) => [change.pos.x, change.pos.y, change.pos.z, change.stateId]),
    [[19, 71, -17, 5], [16, 64, -32, 0]])
})

function fakeBot () {
  const bot = new EventEmitter()
  bot.registry = registry
  bot._client = new EventEmitter()
  return bot
}

test('the server confirms a break through block_change or multi_block_change', async () => {
  const pos = new Vec3(18, 66, -30)
  const stone = registry.blocksByName.stone.defaultState
  const air = registry.blocksByName.air.defaultState

  const single = fakeBot()
  const singleWatch = watchServerBlockChange(single, pos, 'stone')
  single._client.emit('block_change', { location: { x: 18, y: 66, z: -30 }, type: stone }) // rejected: still stone
  single._client.emit('block_change', { location: { x: 19, y: 66, z: -30 }, type: air }) // another cell
  assert.equal(singleWatch.confirmed(), false)
  single._client.emit('block_change', { location: { x: 18, y: 66, z: -30 }, type: air })
  assert.equal(singleWatch.confirmed(), true)
  singleWatch.stop()
  assert.equal(single._client.listenerCount('block_change'), 0)

  const multi = fakeBot()
  const multiWatch = watchServerBlockChange(multi, pos, 'stone')
  multi._client.emit('multi_block_change', { chunkCoordinates: { x: 1, y: 4, z: -2 }, records: [record(air, 2, 2, 2)] })
  assert.equal(await multiWatch.waitForConfirmation(100), true)
  multiWatch.stop()
})

test('waitForConfirmation gives up after the timeout', async () => {
  const bot = fakeBot()
  const watch = watchServerBlockChange(bot, new Vec3(0, 0, 0), 'stone')
  assert.equal(await watch.waitForConfirmation(120), false)
  watch.stop()
})
