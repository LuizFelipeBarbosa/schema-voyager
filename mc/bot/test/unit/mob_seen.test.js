const test = require('node:test')
const assert = require('node:assert/strict')
const { SIGHTING_REPEAT_MS, createMobSightings } = require('../../lib/entities')

const cow = { cow: { count: 1, nearest: 4.2, kind: 'animal' } }

function sightingsAt () {
  const clock = { ms: 0 }
  return { clock, sightings: createMobSightings({ now: () => clock.ms }) }
}

test('a cow in view is logged once, with kind and nearest distance', () => {
  const { clock, sightings } = sightingsAt()
  assert.deepEqual(sightings.sample(cow), [{ mob: 'cow', kind: 'animal', distance: 4.2 }])
  clock.ms += 1000
  assert.deepEqual(sightings.sample(cow), [])
  clock.ms = SIGHTING_REPEAT_MS
  assert.deepEqual(sightings.sample(cow), [])
})

test('a cow still in view is logged again once its last sighting is over 300 s old', () => {
  const { clock, sightings } = sightingsAt()
  sightings.sample(cow)
  clock.ms = SIGHTING_REPEAT_MS + 1
  assert.equal(sightings.sample(cow).length, 1)
  clock.ms += 1000
  assert.deepEqual(sightings.sample(cow), [])
})

test('a mob that left and came back within 300 s is not logged again', () => {
  const { clock, sightings } = sightingsAt()
  sightings.sample(cow)
  clock.ms = 10_000
  assert.deepEqual(sightings.sample({}), [])
  clock.ms = 20_000
  assert.deepEqual(sightings.sample(cow), [])
})

test('hostiles and mobs are logged; players, items and other kinds never', () => {
  const { sightings } = sightingsAt()
  const events = sightings.sample({
    zombie: { count: 2, nearest: 9, kind: 'hostile' },
    phantom: { count: 1, nearest: 7, kind: 'mob' },
    player: { count: 1, nearest: 3, kind: 'player' },
    item: { count: 1, nearest: 1, kind: 'other' },
    villager: { count: 1, nearest: 3, kind: 'passive' }
  })
  assert.deepEqual(events.map((event) => event.mob), ['zombie', 'phantom'])
})
