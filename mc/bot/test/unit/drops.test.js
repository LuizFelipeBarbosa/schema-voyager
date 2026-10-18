const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const { watchDrops } = require('../../lib/world')

function itemEntity (id, position, name, count) {
  return { id, name: 'item', position, getDroppedItem: () => ({ name, count }) }
}

function dropScene (options) {
  const bot = new EventEmitter()
  const target = new Vec3(10, 64, 10)
  const watcher = watchDrops(bot, target, options)
  bot.emit('entitySpawn', itemEntity(1, new Vec3(10.5, 64.3, 10.5), 'oak_log', 1)) // in the cell
  bot.emit('entitySpawn', itemEntity(2, new Vec3(11.4, 64.5, 10.5), 'oak_log', 1)) // neighbour's cell, 0.9 from the centre
  bot.emit('entitySpawn', itemEntity(3, new Vec3(10.2, 65.2, 10.2), 'stick', 2)) // in the grown cell
  bot.emit('entitySpawn', { id: 4, name: 'experience_orb', position: new Vec3(10.5, 64.5, 10.5) })
  return watcher.stop()
}

test('v1 drop window counts everything within 1.5 blocks of the block centre', () => {
  const { counts, entities } = dropScene({})
  assert.deepEqual(counts, { oak_log: 2, stick: 2 })
  assert.equal(entities.length, 3)
})

test('the in-cell filter counts only drops spawned in the target cell', () => {
  const { counts, entities } = dropScene({ inCell: true })
  assert.deepEqual(counts, { oak_log: 1, stick: 2 })
  assert.deepEqual(entities.map((entity) => entity.id), [1, 3])
})

test('onDrop sees each counted entity; onLost sees one whose stack cannot be read', () => {
  const bot = new EventEmitter()
  const seen = []
  const lost = []
  const watcher = watchDrops(bot, new Vec3(0, 0, 0), { inCell: true, onDrop: (e) => seen.push(e.id), onLost: (e) => lost.push(e.id) })
  bot.emit('entitySpawn', itemEntity(1, new Vec3(0.5, 0.5, 0.5), 'dirt', 1))
  bot.emit('entitySpawn', { id: 2, name: 'item', position: new Vec3(0.5, 0.5, 0.5), getDroppedItem: () => { throw new Error('no metadata') } })
  const { counts } = watcher.stop()
  assert.deepEqual(seen, [1, 2])
  assert.deepEqual(lost, [2])
  assert.deepEqual(counts, { dirt: 1 })
})
