const test = require('node:test')
const assert = require('node:assert/strict')
const { enclosureBoxes, outsideRect, edibleHeld, pickHungerGoal, killBeforeSmelt, findFoodKillRule } = require('../v3_polish2_live')

// Every cell a list of boxes covers, as "x,y,z" keys (duplicates kept).
function cellsOf (boxes) {
  const cells = []
  for (const { min, max } of boxes) {
    for (let x = min[0]; x <= max[0]; x++) for (let y = min[1]; y <= max[1]; y++) for (let z = min[2]; z <= max[2]; z++) cells.push(`${x},${y},${z}`)
  }
  return cells
}

const CHEST = [10, 64, 14]
const FAR_HOUSE = { origin: [100, 60, 100], size: [5, 4, 5] }
const NEAR_HOUSE = { origin: [8, 60, 10], size: [4, 4, 4] } // x 8..11, z 10..13: overlaps the enclosure beside the chest

// The cells the enclosure must cover: the box chest +-4 from y-1 to y+3, without the chest and the footprint.
function expectedCells (chest, { origin, size }) {
  const expected = []
  for (let x = chest[0] - 4; x <= chest[0] + 4; x++) {
    for (let z = chest[2] - 4; z <= chest[2] + 4; z++) {
      for (let y = chest[1] - 1; y <= chest[1] + 3; y++) {
        const inHouse = x >= origin[0] && x < origin[0] + size[0] && z >= origin[2] && z < origin[2] + size[2]
        if (!inHouse && !(x === chest[0] && y === chest[1] && z === chest[2])) expected.push(`${x},${y},${z}`)
      }
    }
  }
  return expected
}

for (const [name, house] of [['no house near', FAR_HOUSE], ['a house beside the chest', NEAR_HOUSE]]) {
  test(`enclosureBoxes (${name}): disjoint, without the chest or footprint, covering every other cell`, () => {
    const cells = cellsOf(enclosureBoxes(CHEST, house))
    assert.equal(new Set(cells).size, cells.length, 'no cell twice')
    assert.ok(!cells.includes(CHEST.join(',')), 'the chest cell is not filled')
    assert.deepEqual([...cells].sort(), expectedCells(CHEST, house).sort())
  })
}

test('enclosureBoxes: a footprint swallowing the chest column leaves the rest', () => {
  const house = { origin: [5, 60, 9], size: [10, 4, 10] } // covers x 5..14, z 9..18, chest column included
  const cells = cellsOf(enclosureBoxes(CHEST, house))
  assert.deepEqual([...cells].sort(), expectedCells(CHEST, house).sort())
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
