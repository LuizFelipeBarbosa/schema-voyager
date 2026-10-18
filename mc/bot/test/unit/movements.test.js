const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { DiggingMovements, GoalBesideBlock } = require('../../lib/movements')
const { fakeWorldBot, registry } = require('./world_fixture')

// Solid stone everywhere except the bot's own 1x2 space at y 10-11.
function stoneWorld (extra = {}) {
  const blocks = { '0,10,0': 'air', '0,11,0': 'air', ...extra }
  return fakeWorldBot(blocks, { defaultBlock: 'stone' })
}

test('DiggingMovements: drop limit, scaffold from the request only, towers only with scaffold', () => {
  const none = new DiggingMovements(stoneWorld())
  assert.equal(none.maxDropDown, 3)
  assert.deepEqual(none.scafoldingBlocks, [])
  assert.equal(none.allow1by1towers, false)

  const withDirt = new DiggingMovements(stoneWorld(), { scaffoldNames: ['dirt', 'not_an_item'] })
  assert.deepEqual(withDirt.scafoldingBlocks, [registry.itemsByName.dirt.id])
  assert.equal(withDirt.allow1by1towers, true)
})

test('DiggingMovements never sprints (sprinting costs hunger)', () => {
  assert.equal(new DiggingMovements(stoneWorld()).allowSprinting, false)
})

test('DiggingMovements treats gravel, sand and powder as gravity blocks', () => {
  const movements = new DiggingMovements(stoneWorld())
  for (const name of ['sand', 'red_sand', 'gravel', 'white_concrete_powder']) {
    assert.ok(movements.gravityBlocks.has(registry.blocksByName[name].id), name)
  }
  assert.ok(!movements.gravityBlocks.has(registry.blocksByName.stone.id))
})

test('no neighbour of a cell breaks the block straight below it', () => {
  const movements = new DiggingMovements(stoneWorld(), { scaffoldNames: ['dirt'] })
  const node = { x: 0, y: 10, z: 0, remainingBlocks: 5 }
  const neighbors = movements.getNeighbors(node)
  assert.ok(neighbors.length > 0, 'digging sideways and up is possible')
  const below = new Vec3(0, 9, 0)
  for (const move of neighbors) {
    assert.ok(!move.toBreak.some((pos) => pos.equals(below)), `move to ${move.x},${move.y},${move.z} would break the floor`)
  }
  assert.ok(!neighbors.some((move) => move.x === 0 && move.y === 9 && move.z === 0), 'no move down through the floor')
})

test('a plain drop is still allowed', () => {
  const movements = new DiggingMovements(stoneWorld({ '0,9,0': 'air', '0,8,0': 'air' }))
  const neighbors = []
  movements.getMoveDown({ x: 0, y: 10, z: 0, remainingBlocks: 0 }, neighbors)
  assert.equal(neighbors.length, 1)
  assert.equal(neighbors[0].y, 8) // lands on the stone at y 7
})

test('a block with water or lava on any side is never broken', () => {
  const movements = new DiggingMovements(stoneWorld({ '3,10,0': 'water', '6,10,0': 'lava', '9,11,0': 'water' }))
  const block = (x, y, z) => movements.bot.blockAt(new Vec3(x, y, z))
  assert.equal(movements.safeToBreak(block(2, 10, 0)), false) // water beside
  assert.equal(movements.safeToBreak(block(7, 10, 0)), false) // lava beside
  assert.equal(movements.safeToBreak(block(9, 10, 0)), false) // water above
  assert.equal(movements.safeToBreak(block(3, 9, 0)), false) // water above
  assert.equal(movements.safeToBreak(block(-5, 10, 0)), true)
})

test('the protected block is never broken, the others are', () => {
  const movements = new DiggingMovements(stoneWorld(), { protectedBlock: new Vec3(1, 10, 0) })
  assert.equal(movements.safeToBreak(movements.bot.blockAt(new Vec3(1, 10, 0))), false)
  assert.equal(movements.safeToBreak(movements.bot.blockAt(new Vec3(-1, 10, 0))), true)
})

test('no digging under a gravity block', () => {
  const movements = new DiggingMovements(stoneWorld({ '-1,11,0': 'gravel' }))
  assert.equal(movements.safeToBreak(movements.bot.blockAt(new Vec3(-1, 10, 0))), false)
})

test('lava is never walked into', () => {
  const movements = new DiggingMovements(stoneWorld({ '1,10,0': 'lava', '1,11,0': 'air', '1,9,0': 'stone' }))
  const neighbors = movements.getNeighbors({ x: 0, y: 10, z: 0, remainingBlocks: 0 })
  assert.ok(!neighbors.some((move) => move.x === 1 && move.z === 0 && move.y === 10))
})

test('GoalBesideBlock ends beside or under the target, never on top of it', () => {
  const goal = new GoalBesideBlock(new Vec3(5, 20, 5))
  assert.equal(goal.isEnd(new Vec3(5, 21, 5)), false) // standing on the target
  assert.equal(goal.isEnd(new Vec3(6, 20, 5)), true) // feet beside it
  assert.equal(goal.isEnd(new Vec3(5, 19, 4)), true) // head beside it
  assert.equal(goal.isEnd(new Vec3(5, 18, 5)), true) // below it, head cell touching
  assert.equal(goal.isEnd(new Vec3(5, 22, 5)), false) // two above: the target is the floor's floor
  assert.equal(goal.isEnd(new Vec3(8, 20, 5)), false)
  assert.ok(goal.heuristic(new Vec3(15, 20, 5)) > goal.heuristic(new Vec3(7, 20, 5)))
})

test('a drop of more than 3 blocks is refused', () => {
  const movements = new DiggingMovements(stoneWorld({ '0,9,0': 'air', '0,8,0': 'air', '0,7,0': 'air' }))
  const neighbors = []
  movements.getMoveDown({ x: 0, y: 10, z: 0, remainingBlocks: 0 }, neighbors)
  assert.equal(neighbors.length, 0) // the landing stone is at y 6: a 4-block drop
})
