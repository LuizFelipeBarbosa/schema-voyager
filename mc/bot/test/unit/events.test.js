const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const { attachEventTracker, createEventLog } = require('../../lib/events')

test('seq increases and events carry t, type and the current phase', () => {
  const log = createEventLog(200, () => 1760731234.5124)
  const first = log.record('damage', { amount: 2, health: 18 })
  log.setPhase('step')
  const second = log.record('interference', { block: 'gravel' })
  assert.deepEqual(first, { seq: 1, t: 1760731234.512, type: 'damage', phase: 'travel', amount: 2, health: 18 })
  assert.equal(second.seq, 2)
  assert.equal(second.phase, 'step')
  assert.equal(log.lastSeq(), 2)
})

test('since returns the events after a seq, oldest first', () => {
  const log = createEventLog()
  for (let i = 0; i < 5; i++) log.record('damage')
  assert.deepEqual(log.since(3).map((event) => event.seq), [4, 5])
  assert.deepEqual(log.since(5), [])
  assert.equal(log.since(0).length, 5)
})

test('the ring buffer keeps the last 200 events and seq keeps counting', () => {
  const log = createEventLog()
  for (let i = 0; i < 250; i++) log.record('damage')
  const kept = log.since(0)
  assert.equal(kept.length, 200)
  assert.equal(kept[0].seq, 51)
  assert.equal(kept[199].seq, 250)
  assert.equal(log.lastSeq(), 250)
})

function fakeBot () {
  const bot = new EventEmitter()
  bot.health = 20
  bot.entity = { position: new Vec3(1.5, 64, 2.5) }
  return bot
}

test('the tracker logs damage from health decreases only', () => {
  const bot = fakeBot()
  const log = createEventLog()
  attachEventTracker(bot, log)
  bot.health = 18; bot.emit('health')
  bot.health = 19; bot.emit('health') // regeneration
  bot.health = 15.5; bot.emit('health')
  assert.deepEqual(log.since(0).map((event) => [event.type, event.amount, event.health]),
    [['damage', 2, 18], ['damage', 3.5, 15.5]])
})

test('death then spawn logs death and respawn; a plain spawn logs nothing', () => {
  const bot = fakeBot()
  const log = createEventLog()
  attachEventTracker(bot, log)
  bot.emit('spawn')
  bot.emit('death')
  bot.emit('spawn')
  assert.deepEqual(log.since(0).map((event) => event.type), ['death', 'respawn'])
  assert.deepEqual(log.since(0)[1].position, { x: 1.5, y: 64, z: 2.5 })
})

test('a pickup of an item that is not the step\'s own is stray, during the step only', () => {
  const bot = fakeBot()
  const log = createEventLog()
  const tracker = attachEventTracker(bot, log)
  const item = (id, name, count) => ({ id, name: 'item', getDroppedItem: () => ({ name, count }) })
  tracker.ownDrops.add(10)

  log.setPhase('travel')
  bot.emit('playerCollect', bot.entity, item(11, 'dirt', 1))
  log.setPhase('step')
  bot.emit('playerCollect', bot.entity, item(10, 'oak_log', 1)) // own drop
  bot.emit('playerCollect', {}, item(12, 'stick', 1)) // somebody else
  bot.emit('playerCollect', bot.entity, item(13, 'gravel', 2))

  const strays = log.since(0)
  assert.equal(strays.length, 1)
  assert.deepEqual([strays[0].type, strays[0].phase, strays[0].items], ['stray_pickup', 'step', { gravel: 2 }])
})

test('interference: a nearby block change or falling block, not the target itself', () => {
  const bot = fakeBot()
  const log = createEventLog()
  const tracker = attachEventTracker(bot, log)
  const target = new Vec3(10, 64, 10)
  const block = (x, y, z, type, name) => ({ type, name, position: new Vec3(x, y, z) })
  const stop = tracker.watchInterference(target)

  bot.emit('blockUpdate', block(10, 64, 10, 1, 'stone'), block(10, 64, 10, 0, 'air')) // the target
  bot.emit('blockUpdate', block(11, 65, 10, 1, 'oak_leaves'), block(11, 65, 10, 0, 'air')) // leaf decay: the leaves vanish
  bot.emit('blockUpdate', block(14, 64, 10, 1, 'stone'), block(14, 64, 10, 0, 'air')) // too far
  bot.emit('blockUpdate', block(11, 64, 10, 1, 'stone'), block(11, 64, 10, 1, 'stone')) // no change
  bot.emit('entitySpawn', { name: 'falling_block', position: new Vec3(9.5, 66, 10.5) })
  bot.emit('entitySpawn', { name: 'falling_block', position: new Vec3(30.5, 66, 10.5) })
  stop()
  bot.emit('blockUpdate', block(11, 64, 11, 1, 'stone'), block(11, 64, 11, 0, 'air'))

  assert.deepEqual(log.since(0).map((event) => [event.type, event.block]),
    [['interference', 'oak_leaves'], ['interference', 'falling_block']])
})

test('rejectOnDeath rejects with the quarantined code and can be disposed', async () => {
  const bot = fakeBot()
  const tracker = attachEventTracker(bot, createEventLog())
  const first = tracker.rejectOnDeath()
  bot.emit('death')
  await assert.rejects(first.promise, { code: 'quarantined' })

  const second = tracker.rejectOnDeath()
  second.dispose()
  assert.equal(bot.listenerCount('death') >= 1, true) // the tracker's own
  bot.emit('death') // a disposed watcher must not reject
  await new Promise((resolve) => setImmediate(resolve))
})
