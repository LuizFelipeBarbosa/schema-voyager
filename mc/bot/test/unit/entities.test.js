const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const registry = require('prismarine-registry')('1.21.1')
const { entityKind, nearbyEntities } = require('../../lib/entities')

const self = { id: 0, name: 'player', type: 'player', position: new Vec3(0, 64, 0) }

function botWith (...others) {
  const entities = Object.fromEntries([self, ...others].map((entity, index) => [index, entity]))
  return { registry, entity: self, entities }
}

function at (name, x, extra = {}) {
  return { id: 1, name, type: registry.entitiesByName[name]?.type ?? 'other', position: new Vec3(x, 64, 0), ...extra }
}

test('counts per type, nearest rounded to 0.1, kind from minecraft-data', () => {
  const bot = botWith(at('zombie', 6.44), at('zombie', 9), at('cow', 11), at('villager', 3), at('squid', 4), at('bat', 5), at('phantom', 7))
  assert.deepEqual(nearbyEntities(bot), {
    zombie: { count: 2, nearest: 6.4, kind: 'hostile' },
    cow: { count: 1, nearest: 11, kind: 'animal' },
    villager: { count: 1, nearest: 3, kind: 'passive' },
    squid: { count: 1, nearest: 4, kind: 'water_creature' },
    bat: { count: 1, nearest: 5, kind: 'ambient' },
    phantom: { count: 1, nearest: 7, kind: 'mob' }
  })
})

test('only entities within 16 blocks count', () => {
  const bot = botWith(at('zombie', 16), at('zombie', 16.5), at('cow', 40))
  assert.deepEqual(nearbyEntities(bot), { zombie: { count: 1, nearest: 16, kind: 'hostile' } })
  assert.deepEqual(nearbyEntities(bot, 50).cow, { count: 1, nearest: 40, kind: 'animal' })
})

test('the bot itself is not counted', () => {
  assert.deepEqual(nearbyEntities(botWith()), {})
})

test('items, experience orbs, projectiles and armor stands are left out', () => {
  const bot = botWith(
    at('item', 2), at('experience_orb', 2, { type: 'orb' }), at('arrow', 2), at('armor_stand', 2),
    at('oak_boat', 2), at('mystery', 2, { type: 'other' }), { id: 9, name: 'zombie', type: 'hostile' }
  )
  assert.deepEqual(nearbyEntities(bot), {})
})

test('every player is counted under player', () => {
  const alice = { id: 1, name: 'player', type: 'player', username: 'Alice', position: new Vec3(3, 64, 0) }
  const bob = { id: 2, name: 'player', type: 'player', username: 'Bob', position: new Vec3(0, 64, 4.25) }
  assert.deepEqual(nearbyEntities(botWith(alice, bob)), { player: { count: 2, nearest: 3, kind: 'player' } })
})

test('{} before the bot has spawned, and never throws', () => {
  assert.deepEqual(nearbyEntities({ registry, entities: {} }), {})
  assert.deepEqual(nearbyEntities({}), {})
})

test('entityKind', () => {
  assert.equal(entityKind(registry, { name: 'creeper', type: 'hostile' }), 'hostile')
  assert.equal(entityKind(registry, { name: 'item', type: 'other' }), null)
  assert.equal(entityKind(registry, { name: 'nope', type: 'other' }), null)
})
