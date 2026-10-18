const test = require('node:test')
const assert = require('node:assert/strict')
const { ringBoxes, outsideRect, edibleHeld, pickHungerGoal, killBeforeSmelt, findFoodKillRule } = require('../v3_polish2_live')

// The x/z cells a list of boxes covers, as "x,z" keys.
function cellsOf (boxes) {
  const cells = []
  for (const { min, max } of boxes) for (let x = min[0]; x <= max[0]; x++) for (let z = min[2]; z <= max[2]; z++) cells.push(`${x},${z}`)
  return cells
}

const FAR_HOUSE = { origin: [100, 60, 100], size: [5, 4, 5] }

test('ringBoxes: with no house in the way, a ring of 32 cells at Chebyshev distance 4, 4 high', () => {
  const boxes = ringBoxes([10, 64, 20], FAR_HOUSE)
  const cells = cellsOf(boxes)
  assert.equal(cells.length, 32)
  assert.equal(new Set(cells).size, 32, 'no cell twice')
  for (const key of cells) {
    const [x, z] = key.split(',').map(Number)
    assert.equal(Math.max(Math.abs(x - 10), Math.abs(z - 20)), 4)
  }
  for (const { min, max } of boxes) {
    assert.equal(min[1], 63)
    assert.equal(max[1], 66)
  }
})

test('ringBoxes: nothing goes inside the footprint, everything else of the ring stays', () => {
  const house = { origin: [8, 60, 10], size: [4, 4, 5] } // x 8..11, z 10..14
  const full = new Set(cellsOf(ringBoxes([10, 64, 14], FAR_HOUSE)))
  const clipped = cellsOf(ringBoxes([10, 64, 14], house))
  const inside = (key) => {
    const [x, z] = key.split(',').map(Number)
    return x >= 8 && x <= 11 && z >= 10 && z <= 14
  }
  assert.ok(clipped.every((key) => !inside(key)))
  assert.equal(new Set(clipped).size, clipped.length, 'no cell twice')
  assert.deepEqual([...full].filter((key) => !inside(key)).sort(), [...clipped].sort())
  assert.ok(clipped.length < full.size)
})

test('ringBoxes: a footprint covering a whole side leaves the other sides', () => {
  const boxes = ringBoxes([0, 64, 0], { origin: [-10, 60, -10], size: [21, 4, 5] }) // z -10..-6 swallows the north side
  assert.ok(cellsOf(boxes).every((key) => Number(key.split(',')[1]) > -6))
  assert.ok(cellsOf(boxes).length > 0)
})

test('outsideRect: a cut across the middle of a segment leaves its two ends', () => {
  const parts = outsideRect({ x1: 0, x2: 8, z1: 0, z2: 0 }, { x1: 3, x2: 5, z1: -2, z2: 2 })
  assert.deepEqual(parts.map((p) => [p.x1, p.x2]).sort((a, b) => a[0] - b[0]), [[0, 2], [6, 8]])
})

test('edibleHeld: lists foods held, ignoring other items and zero counts', () => {
  const held = edibleHeld({ bread: 3, cobblestone: 64, rotten_flesh: 5, apple: 0, cooked_beef: 1, poppy: 2 })
  assert.deepEqual(held.sort(), ['bread', 'cooked_beef', 'rotten_flesh'])
  assert.deepEqual(edibleHeld({}), [])
  assert.deepEqual(edibleHeld(undefined), [])
})

test('pickHungerGoal: a new system goal for a cooked meat, by item or by text', () => {
  const goals = [
    { id: 'g1', source: 'system', item: 'cooked_beef', text: 'hunger: 4 cooked_beef' },
    { id: 'g2', source: 'user', item: 'cooked_beef', text: 'get me cooked beef' },
    { id: 'g3', source: 'system', item: 'torch', text: 'arm: 1 stone_sword' },
    { id: 'g4', source: 'system', text: 'hunger: 4 cooked_porkchop' }
  ]
  assert.equal(pickHungerGoal(goals).id, 'g1')
  assert.equal(pickHungerGoal(goals, new Set(['g1'])).id, 'g4')
  assert.equal(pickHungerGoal(goals, new Set(['g1', 'g4'])), undefined)
})

test('killBeforeSmelt: needs successful steps, in that order, with the meat as the smelt output', () => {
  const step = (kind, target, ok = true) => ({ kind: 'step', ok, action_obj: { kind, target } })
  assert.equal(killBeforeSmelt([step('kill', 'cow'), step('smelt', 'cooked_beef')], 'cooked_beef').ok, true)
  assert.equal(killBeforeSmelt([step('smelt', 'cooked_beef'), step('kill', 'cow')], 'cooked_beef').ok, false)
  assert.equal(killBeforeSmelt([step('kill', 'cow', false), step('smelt', 'cooked_beef')], 'cooked_beef').ok, false)
  assert.equal(killBeforeSmelt([step('kill', 'cow'), step('smelt', 'iron_ingot')], 'cooked_beef').ok, false)
  assert.equal(killBeforeSmelt([{ kind: 'plan' }, step('mine', 'oak_log'), step('kill', 'pig'), step('smelt', 'cooked_porkchop')], 'cooked_porkchop').ok, true)
})

test('findFoodKillRule: a kill rule for a food animal only', () => {
  const rule = (type, mob) => ({ id: `${type}-${mob}`, action: { type, mob } })
  assert.equal(findFoodKillRule({ rules: [rule('kill', 'zombie'), rule('mine', 'cow'), rule('kill', 'pig')] }).id, 'kill-pig')
  assert.equal(findFoodKillRule({ rules: [rule('kill', 'zombie')] }), undefined)
  assert.equal(findFoodKillRule(null), undefined)
})
