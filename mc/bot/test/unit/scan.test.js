const test = require('node:test')
const assert = require('node:assert/strict')
const { scan, validateScan } = require('../../lib/scan')
const { fakeWorldBot } = require('./world_fixture')

test('validateScan accepts an integer origin and a size of 1..32 with at most 8192 cells', () => {
  assert.equal(validateScan({ origin: [8, 64, -5], size: [3, 2, 2] }), null)
  assert.equal(validateScan({ origin: [0, 0, 0], size: [32, 32, 8] }), null)
  assert.equal(validateScan({ origin: [0, -60, 0], size: [1, 1, 1] }), null)
})

test('validateScan rejects a bad origin or size', () => {
  const bad = [
    {},
    { origin: [1, 2], size: [1, 1, 1] },
    { origin: [1.5, 2, 3], size: [1, 1, 1] },
    { origin: ['1', 2, 3], size: [1, 1, 1] },
    { origin: [1, 2, 3] },
    { origin: [1, 2, 3], size: [0, 1, 1] },
    { origin: [1, 2, 3], size: [33, 1, 1] },
    { origin: [1, 2, 3], size: [1, 1, 1.5] },
    { origin: [1, 2, 3], size: [32, 32, 9] }
  ]
  for (const body of bad) assert.equal(typeof validateScan(body), 'string', JSON.stringify(body))
})

test('scan lays out layers[y][z][x] with x west to east, z north to south, y bottom-up', () => {
  const bot = fakeWorldBot({ '8,64,-5': 'dirt', '9,64,-5': 'dirt', '9,64,-4': 'oak_planks', '9,65,-4': 'torch' })
  const result = scan(bot, { origin: [8, 64, -5], size: [3, 2, 2] })
  assert.deepEqual(result.layers, [
    [['dirt', 'dirt', 'air'], ['air', 'oak_planks', 'air']],
    [['air', 'air', 'air'], ['air', 'torch', 'air']]
  ])
  assert.deepEqual(result.origin, [8, 64, -5])
  assert.deepEqual(result.size, [3, 2, 2])
  assert.equal(result.ok, true)
})

test('scan answers null for a cell in a chunk that is not loaded', () => {
  const bot = fakeWorldBot({ '0,0,0': 'stone' })
  const blockAt = bot.blockAt
  bot.blockAt = (pos) => (pos.x >= 1 ? null : blockAt(pos))
  assert.deepEqual(scan(bot, { origin: [0, 0, 0], size: [2, 1, 1] }).layers, [[['stone', null]]])
})
