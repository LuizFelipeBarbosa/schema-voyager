const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const registry = require('prismarine-registry')('1.21.1')
const { Recipe } = require('prismarine-recipe')(registry)
const { isWholeRuns } = require('../../lib/craft')
const { findSpots } = require('../../lib/station')

const recipesFor = (name) => Recipe.find(registry.itemsByName[name].id, null)
const wholeRuns = (delta, item, runs) => isWholeRuns(delta, item, runs, recipesFor(item), registry)

test('isWholeRuns accepts n runs of a recipe', () => {
  assert.ok(wholeRuns({ birch_planks: -2, stick: 4 }, 'stick', 1))
  assert.ok(wholeRuns({ birch_planks: -4, stick: 8 }, 'stick', 2))
  assert.ok(wholeRuns({ stick: -7, ladder: 3 }, 'ladder', 1))
})

test('isWholeRuns takes any tag variant, mixed', () => {
  assert.ok(wholeRuns({ birch_planks: -1, spruce_planks: -1, stick: 4 }, 'stick', 1))
  assert.ok(wholeRuns({ birch_planks: -3, spruce_planks: -1, stick: 8 }, 'stick', 2))
})

test('isWholeRuns does not judge the gain by the recipe result count', () => {
  // a datapack made sticks come 2 per run
  assert.ok(wholeRuns({ birch_planks: -4, stick: 4 }, 'stick', 2))
})

test('isWholeRuns rejects a craft that lost items in the grid', () => {
  assert.equal(wholeRuns({ birch_planks: -6, stick: 4 }, 'stick', 2), false)
  assert.equal(wholeRuns({ birch_planks: -6, stick: 4 }, 'stick', 1), false)
  assert.equal(wholeRuns({ birch_planks: -2 }, 'stick', 1), false)
  assert.equal(wholeRuns({}, 'stick', 1), false)
})

test('isWholeRuns rejects a gain that is not a multiple of the runs, and other changes', () => {
  assert.equal(wholeRuns({ birch_planks: -4, stick: 5 }, 'stick', 2), false)
  assert.equal(wholeRuns({ birch_planks: -2, stick: 4, dirt: 1 }, 'stick', 1), false)
  assert.equal(wholeRuns({ birch_planks: -2, cobblestone: -1, stick: 4 }, 'stick', 1), false)
  assert.equal(wholeRuns({ birch_planks: -1, stick: 4 }, 'stick', 1), false)
})

test('isWholeRuns rejects ingredients that no variant of the recipe accepts', () => {
  assert.equal(wholeRuns({ cobblestone: -2, stick: 4 }, 'stick', 1), false)
})

// A bot standing at (0.5, 70, 0.5); `solid` lists the full blocks, all else is air.
function botWith (solid) {
  const blockAt = (pos) => (solid.has(`${pos.x},${pos.y},${pos.z}`)
    ? { name: 'stone', boundingBox: 'block' }
    : { name: 'air', boundingBox: 'empty' })
  return { entity: { position: new Vec3(0.5, 70, 0.5) }, blockAt }
}

test('findSpots prefers a cell on the ground', () => {
  const solid = new Set()
  for (let x = -2; x <= 2; x++) for (let z = -2; z <= 2; z++) solid.add(`${x},69,${z}`)
  const [first] = findSpots(botWith(solid))
  assert.deepEqual(first.faceToCell, new Vec3(0, 1, 0))
  assert.equal(first.pos.y, 70)
})

test('findSpots finds a cell beside the pillar a bot stands on', () => {
  const spots = findSpots(botWith(new Set(['0,69,0'])))
  assert.ok(spots.length > 0)
  assert.ok(spots.every((spot) => spot.pos.x !== 0 || spot.pos.z !== 0 || spot.pos.y < 70))
})
