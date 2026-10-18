const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { DiggingMovements } = require('../../lib/movements')
const { fakeWorldBot } = require('./world_fixture')

// Solid stone everywhere except the bot's own 1x2 space at y 10-11; the bot
// stands at 0,10,0 so the block it would dig is 0,9,0.
const node = { x: 0, y: 10, z: 0, remainingBlocks: 0 }
const dugBlock = new Vec3(0, 9, 0)

function movesDown (extra = {}, options) {
  const bot = fakeWorldBot({ '0,10,0': 'air', '0,11,0': 'air', ...extra }, { defaultBlock: 'stone' })
  const neighbors = []
  new DiggingMovements(bot, options).getMoveDown(node, neighbors)
  return neighbors
}

test('over solid stone the bot digs straight down, breaking the block under it', () => {
  const neighbors = movesDown()
  assert.equal(neighbors.length, 1)
  assert.deepEqual([neighbors[0].x, neighbors[0].y, neighbors[0].z], [0, 9, 0])
  assert.ok(neighbors[0].toBreak.some((pos) => pos.equals(dugBlock)))

  const all = new DiggingMovements(fakeWorldBot({ '0,10,0': 'air', '0,11,0': 'air' }, { defaultBlock: 'stone' })).getNeighbors(node)
  assert.ok(all.some((move) => move.y === 9 && move.x === 0 && move.z === 0 && move.toBreak.some((pos) => pos.equals(dugBlock))))
})

test('a block with water or lava on a side, or lava below it, is not dug', () => {
  assert.equal(movesDown({ '1,9,0': 'water' }).length, 0)
  assert.equal(movesDown({ '0,9,-1': 'lava' }).length, 0)
  assert.equal(movesDown({ '0,8,0': 'lava' }).length, 0)
})

test('digging down is refused when the fall would exceed MAX_DROP_DOWN', () => {
  // Air at y 8..6, stone at 5: the floor is 5 below the node.
  assert.equal(movesDown({ '0,8,0': 'air', '0,7,0': 'air', '0,6,0': 'air' }).length, 0)
  // Air at y 8, stone at 7: the floor is exactly MAX_DROP_DOWN below the node, so the bot lands at y 8.
  const neighbors = movesDown({ '0,8,0': 'air' })
  assert.equal(neighbors.length, 1)
  assert.equal(neighbors[0].y, 8)
})

test('the protected target and blocks inside a protect box are not dug', () => {
  assert.equal(movesDown({}, { protectedBlock: dugBlock }).length, 0)
  assert.equal(movesDown({}, { protectedBlock: new Vec3(5, 9, 5) }).length, 1)

  assert.equal(movesDown({}, { protect: [{ origin: [-1, 9, -1], size: [3, 1, 3] }] }).length, 0)
  assert.equal(movesDown({}, { protect: [{ origin: [5, 9, 5], size: [3, 1, 3] }] }).length, 1)
})

test('a plain drop with nothing to break is still offered', () => {
  const neighbors = movesDown({ '0,9,0': 'air', '0,8,0': 'air' })
  assert.equal(neighbors.length, 1)
  assert.equal(neighbors[0].toBreak.length, 0)
  assert.equal(neighbors[0].y, 8)
})
