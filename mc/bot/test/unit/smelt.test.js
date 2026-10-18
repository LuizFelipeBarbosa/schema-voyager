const test = require('node:test')
const assert = require('node:assert/strict')
const { fuelNeeded, itemsPerFuel, smeltShortfall, smeltVerdict } = require('../../lib/smelt')

test('fuel values: coal and charcoal 8, wooden fuel 1.5, stick 0.5, anything else none', () => {
  assert.equal(itemsPerFuel('coal'), 8)
  assert.equal(itemsPerFuel('charcoal'), 8)
  for (const name of ['oak_planks', 'birch_log', 'oak_wood', 'crimson_stem']) assert.equal(itemsPerFuel(name), 1.5)
  assert.equal(itemsPerFuel('stick'), 0.5)
  assert.equal(itemsPerFuel('dirt'), null)
})

test('fuelNeeded rounds up to whole fuel items', () => {
  assert.equal(fuelNeeded('coal', 3), 1)
  assert.equal(fuelNeeded('coal', 8), 1)
  assert.equal(fuelNeeded('coal', 9), 2)
  assert.equal(fuelNeeded('birch_planks', 1), 1)
  assert.equal(fuelNeeded('birch_planks', 2), 2)
  assert.equal(fuelNeeded('birch_planks', 3), 2)
  assert.equal(fuelNeeded('stick', 1), 2)
  assert.equal(fuelNeeded('stick', 3), 6)
})

test('smeltVerdict is ok only when every input was smelted and an output taken', () => {
  assert.deepEqual(smeltVerdict({ smelted: 3, outputTaken: 3, times: 3 }), { ok: true, detail: null })
  assert.deepEqual(smeltVerdict({ smelted: 1, outputTaken: 2, times: 1 }), { ok: true, detail: null }) // yield 2 per input
  assert.deepEqual(smeltVerdict({ smelted: 0, outputTaken: 0, times: 1 }), { ok: false, detail: 'smelted 0 of 1' })
  assert.deepEqual(smeltVerdict({ smelted: 1, outputTaken: 1, times: 2 }), { ok: false, detail: 'smelted 1 of 2' })
})

test('smeltShortfall lists what the inventory lacks', () => {
  const request = { ingredient: 'raw_iron', fuel: 'coal', times: 3 }
  assert.deepEqual(smeltShortfall({ raw_iron: 3, coal: 1 }, request), {})
  assert.deepEqual(smeltShortfall({ raw_iron: 1 }, request), { raw_iron: 2, coal: 1 })
  assert.deepEqual(smeltShortfall({ raw_iron: 3, coal: 0 }, request), { coal: 1 })
})

test('smeltShortfall adds up an ingredient that is also the fuel', () => {
  assert.deepEqual(smeltShortfall({ oak_planks: 4 }, { ingredient: 'oak_planks', fuel: 'oak_planks', times: 3 }), { oak_planks: 1 })
})
