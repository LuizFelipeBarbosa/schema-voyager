const test = require('node:test')
const assert = require('node:assert/strict')
const { fuelNeeded, itemsPerFuel, smeltShortfall } = require('../../lib/smelt')

test('coal and charcoal smelt 8 items each; any other fuel counts 1 per item', () => {
  assert.equal(itemsPerFuel('coal'), 8)
  assert.equal(itemsPerFuel('charcoal'), 8)
  assert.equal(itemsPerFuel('oak_planks'), 1)
})

test('fuelNeeded rounds up to whole fuel items', () => {
  assert.equal(fuelNeeded('coal', 3), 1)
  assert.equal(fuelNeeded('coal', 8), 1)
  assert.equal(fuelNeeded('coal', 9), 2)
  assert.equal(fuelNeeded('birch_planks', 3), 3)
})

test('smeltShortfall lists what the inventory lacks', () => {
  const request = { ingredient: 'raw_iron', fuel: 'coal', times: 3 }
  assert.deepEqual(smeltShortfall({ raw_iron: 3, coal: 1 }, request), {})
  assert.deepEqual(smeltShortfall({ raw_iron: 1 }, request), { raw_iron: 2, coal: 1 })
  assert.deepEqual(smeltShortfall({ raw_iron: 3, coal: 0 }, request), { coal: 1 })
})

test('smeltShortfall adds up an ingredient that is also the fuel', () => {
  assert.deepEqual(smeltShortfall({ oak_planks: 4 }, { ingredient: 'oak_planks', fuel: 'oak_planks', times: 3 }), { oak_planks: 2 })
})
