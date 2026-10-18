const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { chooseTargets, createUnreachableMemory, hazardNear, look } = require('../../lib/targets')
const { advanceLimit, insideBorder } = require('../../lib/explore')
const { fakeWorldBot } = require('./world_fixture')

const search = (bot, overrides = {}) => {
  const hazards = []
  const outcome = chooseTargets(bot, {
    blockNames: ['coal_ore', 'deepslate_coal_ore'],
    maxDistance: 32,
    buried: true,
    skipped: new Set(),
    unreachable: createUnreachableMemory(),
    onHazard: (event) => hazards.push(event),
    ...overrides
  })
  return { ...outcome, pos: outcome.positions[0] ?? null, hazards }
}

// Ores in solid stone: the ones listed in `open` get an air neighbour.
function oreWorld (ores, open = []) {
  const blocks = {}
  for (const [pos, name] of Object.entries(ores)) {
    blocks[pos] = name
    const [x, y, z] = pos.split(',').map(Number)
    for (const [dx, dy, dz] of [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]) {
      const key = `${x + dx},${y + dy},${z + dz}`
      if (!blocks[key]) blocks[key] = open.includes(pos) && dx === 1 ? 'air' : 'stone'
    }
  }
  return fakeWorldBot(blocks)
}

test('chooseTargets takes the nearest exposed block over a nearer buried one', () => {
  const bot = oreWorld({ '2,10,0': 'coal_ore', '9,10,0': 'deepslate_coal_ore' }, ['9,10,0'])
  assert.deepEqual(search(bot).pos, new Vec3(9, 10, 0))
})

test('chooseTargets falls back to the nearest buried block, but only with dig_through', () => {
  const bot = oreWorld({ '2,10,0': 'coal_ore', '9,10,3': 'coal_ore' })
  assert.deepEqual(search(bot).pos, new Vec3(2, 10, 0))
  assert.equal(search(bot, { buried: false }).pos, null)
})

test('chooseTargets skips failed targets', () => {
  const bot = oreWorld({ '2,10,0': 'coal_ore', '9,10,3': 'coal_ore' })
  assert.deepEqual(search(bot, { skipped: new Set([new Vec3(2, 10, 0).toString()]) }).pos, new Vec3(9, 10, 3))
})

test('chooseTargets refuses targets beside lava or flowing water, with a hazard event', () => {
  const bot = oreWorld({ '2,10,0': 'coal_ore', '9,10,3': 'coal_ore', '5,10,7': 'coal_ore' })
  const blocks = bot.blockAt
  bot.blockAt = (pos) => {
    if (pos.x === 2 && pos.y === 11 && pos.z === 0) return { name: 'lava' }
    if (pos.x === 9 && pos.y === 11 && pos.z === 3) return { name: 'water', getProperties: () => ({ level: '3' }) }
    if (pos.x === 5 && pos.y === 11 && pos.z === 7) return { name: 'water', getProperties: () => ({ level: '0' }) } // a source block
    return blocks(pos)
  }
  const outcome = search(bot)
  assert.deepEqual(outcome.pos, new Vec3(5, 10, 7))
  assert.equal(outcome.hazard, true)
  assert.deepEqual(outcome.hazards, [
    { kind: 'lava', position: { x: 2, y: 10, z: 0 } },
    { kind: 'water', position: { x: 9, y: 10, z: 3 } }
  ])
  assert.equal(hazardNear(bot, new Vec3(2, 10, 0)), 'lava')

  const onlyHazards = search(bot, { skipped: new Set([new Vec3(5, 10, 7).toString()]) })
  assert.deepEqual([onlyHazards.pos, onlyHazards.hazard], [null, true])
})

test('chooseTargets with nothing in range finds nothing, without a hazard', () => {
  const outcome = search(oreWorld({ '2,10,0': 'coal_ore' }), { maxDistance: 1 })
  assert.deepEqual([outcome.pos, outcome.hazard], [null, false])
})

test('chooseTargets with only unknown block names finds nothing', () => {
  const outcome = search(oreWorld({ '2,10,0': 'coal_ore' }), { blockNames: ['no_such_block'] })
  assert.deepEqual([outcome.pos, outcome.hazard], [null, false])
})

test('look reports the nearest block per sector and id, with distance and exposure', () => {
  const bot = oreWorld({
    '0,10,-20': 'coal_ore', // N
    '0,10,-30': 'coal_ore', // N, farther: not reported
    '40,10,0': 'deepslate_coal_ore', // E
    '-10,10,10': 'coal_ore' // SW
  }, ['40,10,0'])
  const reply = look(bot, { blocks: ['coal_ore', 'deepslate_coal_ore', 'no_such_block'], radius: 64 })
  assert.equal(reply.ok, true)
  assert.equal(reply.radius, 64)
  assert.deepEqual(Object.keys(reply.directions), ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'])
  assert.deepEqual(reply.directions.N, { coal_ore: { x: 0, y: 10, z: -20, distance: 20, exposed: false } })
  assert.equal(reply.directions.E.deepslate_coal_ore.exposed, true)
  assert.deepEqual(Object.keys(reply.directions.SW), ['coal_ore'])
  assert.deepEqual(reply.directions.S, {})
})

test('look caps the radius at 96', () => {
  const reply = look(oreWorld({ '0,10,-20': 'coal_ore' }), { blocks: ['coal_ore'], radius: 500 })
  assert.equal(reply.radius, 96)
})

test('insideBorder and advanceLimit stop at the border, lava and barriers', () => {
  const border = { centerX: 0, centerZ: 0, size: 20 }
  assert.equal(insideBorder(border, 10, 0), true)
  assert.equal(insideBorder(border, 10.5, 0), false)

  const open = {}
  for (let x = -5; x <= 60; x++) { open[`${x},9,0`] = 'stone'; open[`${x},10,0`] = 'air' }
  const bot = fakeWorldBot(open)
  const wide = { centerX: 0, centerZ: 0, size: 1000 }
  const from = new Vec3(0.5, 10, 0.5)
  const east = { x: 1, z: 0 }
  assert.deepEqual(advanceLimit(bot, wide, from, east, 16), { blocks: 16, stoppedBy: null, position: null })
  assert.equal(advanceLimit(bot, border, from, east, 16).stoppedBy, 'border')
  assert.equal(advanceLimit(bot, border, from, east, 16).blocks, 9)

  open['6,10,0'] = 'lava'
  const lava = advanceLimit(fakeWorldBot(open), wide, from, east, 16)
  assert.deepEqual([lava.blocks, lava.stoppedBy, lava.position], [5, 'lava', { x: 6, y: 10, z: 0 }])
  open['6,10,0'] = 'barrier'
  assert.equal(advanceLimit(fakeWorldBot(open), wide, from, east, 16).stoppedBy, 'barrier')
})

// A flat stone floor at y 9 and open air above it, so every block listed in
// `ores` sits at y 10 with room to stand beside it; `walls` fills cells with stone.
function floorWorld (ores, { walls = [], from = 0, to = 40 } = {}) {
  const blocks = {}
  for (let x = from; x <= to; x++) {
    for (let z = -20; z <= 20; z++) blocks[`${x},9,${z}`] = 'stone'
  }
  for (const pos of walls) blocks[pos] = 'stone'
  return fakeWorldBot({ ...blocks, ...ores })
}

test('chooseTargets orders exposed blocks by height difference, then distance', () => {
  const bot = floorWorld({ '12,10,0': 'coal_ore', '3,10,5': 'coal_ore', '6,10,0': 'coal_ore' })
  bot.entity.position = new Vec3(0.5, 10, 0.5)
  const near = (x, z) => new Vec3(x, 10, z)
  assert.deepEqual(search(bot).positions, [near(3, 5), near(6, 0), near(12, 0)])

  // The same distance, but one is 8 blocks below: it goes last.
  const lowBlocks = { '6,2,0': 'coal_ore', '0,10,6': 'coal_ore' }
  assert.deepEqual(search(floorWorld(lowBlocks)).positions, [new Vec3(0, 10, 6), new Vec3(6, 2, 0)])
})

test('chooseTargets prefers a block with a place to stand over a nearer one without', () => {
  // Coal at (3,10,0) is open to the air above it but boxed in at the sides.
  const walls = ['2,10,0', '4,10,0', '3,10,1', '3,10,-1']
  const bot = floorWorld({ '3,10,0': 'coal_ore', '11,10,0': 'coal_ore' }, { walls })
  assert.deepEqual(search(bot).positions, [new Vec3(11, 10, 0), new Vec3(3, 10, 0)])
})

test('chooseTargets keeps buried blocks after every exposed one', () => {
  const bot = oreWorld({ '2,10,0': 'coal_ore', '9,10,0': 'coal_ore', '14,10,4': 'coal_ore' }, ['14,10,4'])
  assert.deepEqual(search(bot).positions, [new Vec3(14, 10, 4), new Vec3(2, 10, 0), new Vec3(9, 10, 0)])
})

test('chooseTargets leaves out remembered unreachable targets', () => {
  const bot = oreWorld({ '2,10,0': 'coal_ore', '9,10,3': 'coal_ore' })
  const unreachable = createUnreachableMemory()
  unreachable.remember(bot, new Vec3(2, 10, 0))
  assert.deepEqual(search(bot, { unreachable }).positions, [new Vec3(9, 10, 3)])
})

test('the unreachable memory outlives the episode but expires after its time to live', () => {
  const bot = oreWorld({ '2,10,0': 'coal_ore' })
  const pos = new Vec3(2, 10, 0)
  let clock = 1000
  const memory = createUnreachableMemory({ ttlMs: 600000, now: () => clock })
  assert.equal(memory.has(bot, pos), false)
  memory.remember(bot, pos)
  clock += 599999
  assert.equal(memory.has(bot, pos), true)
  clock += 1
  assert.equal(memory.has(bot, pos), false)
})

test('the unreachable memory forgets a target whose block changed', () => {
  const blocks = { '2,10,0': 'coal_ore' }
  const bot = fakeWorldBot(blocks)
  const pos = new Vec3(2, 10, 0)
  const memory = createUnreachableMemory()
  memory.remember(bot, pos)
  assert.equal(memory.has(bot, pos), true)
  blocks['2,10,0'] = 'air' // mined by someone
  assert.equal(memory.has(bot, pos), false)
  blocks['2,10,0'] = 'coal_ore' // a different block put back is a new target
  assert.equal(memory.has(bot, pos), false)
})
