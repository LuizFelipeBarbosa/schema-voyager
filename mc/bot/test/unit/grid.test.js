const test = require('node:test')
const assert = require('node:assert/strict')
const registry = require('prismarine-registry')('1.21.1')
const { Recipe } = require('prismarine-recipe')(registry)
const {
  assignGrid, gridOf, gridSlotIndex, planMixedGrid, recipeSlots, slotAlternatives
} = require('../../lib/grid')

const id = (name) => registry.itemsByName[name].id

// A bot with just the inventory and recipe lookups planMixedGrid uses.
function botHolding (held) {
  const items = Object.entries(held).map(([name, count]) => ({ type: id(name), name, count }))
  return {
    registry,
    inventory: { items: () => items },
    recipesAll: (itemId) => Recipe.find(itemId, null)
  }
}

test('gridSlotIndex numbers cells row by row after the output slot', () => {
  assert.equal(gridSlotIndex(0, 0, 2), 1)
  assert.equal(gridSlotIndex(1, 1, 2), 4)
  assert.equal(gridSlotIndex(2, 1, 3), 6)
  assert.equal(gridSlotIndex(0, 2, 3), 7)
})

test('recipeSlots lists shaped cells and lays shapeless ingredients in reading order', () => {
  const pickaxe = { inShape: [[{ id: 5 }, { id: 5 }, { id: 5 }], [{ id: -1 }, { id: 9 }, { id: -1 }]] }
  assert.deepEqual(recipeSlots(pickaxe).map((slot) => [slot.x, slot.y, slot.id]),
    [[0, 0, 5], [1, 0, 5], [2, 0, 5], [1, 1, 9]])
  const shapeless = { requiresTable: false, ingredients: [{ id: 1 }, { id: 2 }, { id: 3 }] }
  assert.deepEqual(recipeSlots(shapeless).map((slot) => [slot.x, slot.y]), [[0, 0], [1, 0], [0, 1]])
})

test('slotAlternatives reads the accepted variants off the sibling recipes', () => {
  const shaped = (ids, count = 1) => ({ inShape: [ids.map((cell) => ({ id: cell }))], result: { count }, requiresTable: false })
  const recipe = shaped([1, 9])
  const siblings = [recipe, shaped([2, 9]), shaped([3, 9]), shaped([4, 9], 4), shaped([5, 9, 9])]
  // the count-4 sibling and the longer shape are different recipes
  assert.deepEqual(slotAlternatives(recipe, siblings).map((slot) => slot.ids), [[1, 2, 3], [9]])
})

test('assignGrid mixes variants when no single one is enough', () => {
  const slots = [{ ids: [1, 2] }, { ids: [1, 2] }, { ids: [1, 2] }, { ids: [9] }]
  const chosen = assignGrid(slots, { 1: 2, 2: 1, 9: 1 })
  assert.equal(chosen[3], 9)
  assert.deepEqual(chosen.slice(0, 3).sort(), [1, 1, 2])
})

test('assignGrid gives the scarce id to the cell that needs it, and fails when short', () => {
  assert.deepEqual(assignGrid([{ ids: [1, 2] }, { ids: [2] }], { 1: 1, 2: 1 }), [1, 2])
  assert.equal(assignGrid([{ ids: [1, 2] }, { ids: [1, 2] }, { ids: [1, 2] }], { 1: 1, 2: 1 }), null)
  assert.equal(assignGrid([{ ids: [7] }], {}), null)
})

test('gridOf puts chosen ids at their cells', () => {
  const slots = [{ x: 0, y: 0 }, { x: 1, y: 1 }]
  assert.deepEqual(gridOf(slots, [4, 5], 2), [[4, null], [null, 5]])
})

test('planMixedGrid: a pickaxe from 2 oak and 1 birch planks', () => {
  const plan = planMixedGrid(botHolding({ oak_planks: 2, birch_planks: 1, stick: 2 }), id('wooden_pickaxe'))
  assert.ok(plan)
  assert.equal(plan.recipe.requiresTable, true)
  assert.deepEqual(plan.grid[1], [null, 'stick', null])
  assert.deepEqual(plan.grid[2], [null, 'stick', null])
  assert.deepEqual([...plan.grid[0]].sort(), ['birch_planks', 'oak_planks', 'oak_planks'])
})

test('planMixedGrid: a stone pickaxe from 2 cobblestone and 1 cobbled_deepslate', () => {
  const plan = planMixedGrid(botHolding({ cobblestone: 2, cobbled_deepslate: 1, stick: 2 }), id('stone_pickaxe'))
  assert.deepEqual([...plan.grid[0]].sort(), ['cobbled_deepslate', 'cobblestone', 'cobblestone'])
})

test('planMixedGrid: no plan when an ingredient is missing', () => {
  assert.equal(planMixedGrid(botHolding({ oak_planks: 2, birch_planks: 1 }), id('wooden_pickaxe')), null) // no sticks
  assert.equal(planMixedGrid(botHolding({ oak_planks: 1, stick: 2 }), id('wooden_pickaxe')), null)
})

test('planMixedGrid checks the whole request: two pickaxes need six planks', () => {
  const bot = botHolding({ oak_planks: 3, birch_planks: 2, stick: 4 })
  assert.ok(planMixedGrid(bot, id('wooden_pickaxe'), 1))
  assert.equal(planMixedGrid(bot, id('wooden_pickaxe'), 2), null)
})

test('planMixedGrid fills a 2x2 grid for a table-less recipe', () => {
  const plan = planMixedGrid(botHolding({ oak_planks: 3, birch_planks: 1 }), id('crafting_table'))
  assert.equal(plan.recipe.requiresTable, false)
  assert.equal(plan.grid.length, 2)
  assert.deepEqual(plan.grid.flat().sort(), ['birch_planks', 'oak_planks', 'oak_planks', 'oak_planks'])
})
