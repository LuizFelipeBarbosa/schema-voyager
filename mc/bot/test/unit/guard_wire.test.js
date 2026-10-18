const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const { attachEventTracker, createEventLog } = require('../../lib/events')
const { createActionLock } = require('../../lib/action_lock')

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const found = { threat: { mob: 'zombie' }, decision: 'fight' }

function deferred () {
  const d = {}
  d.promise = new Promise((resolve, reject) => { d.resolve = resolve; d.reject = reject })
  return d
}

function setup (options = {}) {
  const bot = new EventEmitter()
  bot.health = 20
  bot.entity = { position: new Vec3(0, 64, 0) }
  const eventLog = createEventLog()
  const tracker = attachEventTracker(bot, eventLog)
  const calls = []
  const guard = {
    respond: async (f, opts) => { calls.push(['respond', opts.action]); return { decision: f.decision, outcome: 'killed' } }
  }
  const survival = {
    hold: async () => { calls.push(['hold']); return () => calls.push(['release']) },
    onIdleTick: () => calls.push(['idleTick'])
  }
  const lock = createActionLock({
    eventLog,
    interruptBot: () => calls.push(['interruptBot']),
    stopForGuard: () => calls.push(['stopForGuard']),
    guard,
    survival,
    reconnect: () => calls.push(['reconnect']),
    settleMs: 40,
    backstopMs: 120,
    idleGapMs: 80,
    ...options
  })
  const start = (handler, extra = {}) => lock.run({
    path: '/mine', cutIn: true, timeoutMs: 1000, tracker, session: {}, handler, ...extra
  })
  const orphanEvents = () => eventLog.since(0).filter((event) => event.type === 'orphaned_handler')
  return { bot, lock, calls, guard, start, orphanEvents }
}

test('the hold resolves before the handler starts and is released with the lock', async () => {
  const { lock, calls, start } = setup()
  const result = await start((session) => {
    calls.push(['handler', session.signal.aborted])
    return { ok: true }
  })
  await sleep(5)
  assert.deepEqual(result, { kind: 'ok', payload: { ok: true } })
  assert.deepEqual(calls, [['hold'], ['handler', false], ['release']])
  assert.equal(lock.busyDetail(), null)
})

test('a running action answers undefined, a free lock null', async () => {
  const { lock, start } = setup()
  const gate = deferred()
  const running = start(() => gate.promise)
  assert.equal(lock.busyDetail(), undefined)
  gate.resolve({})
  await running
  await sleep(5)
  assert.equal(lock.busyDetail(), null)
})

test('a cut-in aborts the signal, stops for the guard, waits for the handler, then fights', async () => {
  const { lock, calls, start } = setup()
  let signal = null
  const running = start((session) => new Promise((resolve, reject) => {
    signal = session.signal
    signal.addEventListener('abort', () => { calls.push(['handler settles']); reject(signal.reason) })
  }))
  await sleep(5)
  lock.onTick(found)
  const result = await running
  assert.equal(signal.aborted, true)
  assert.equal(signal.reason.code, 'aborted')
  assert.deepEqual(result, { kind: 'interrupted', threat: 'zombie', outcome: 'killed' })
  // The lock, and with it the handler's hold, outlives the fight.
  assert.deepEqual(calls.map((call) => call[0]), ['hold', 'handler settles', 'stopForGuard', 'hold', 'respond', 'release', 'release'])
  assert.equal(calls.some((call) => call[0] === 'interruptBot'), false)
  assert.deepEqual(calls.find((call) => call[0] === 'respond'), ['respond', '/mine'])
  assert.equal(lock.busyDetail(), null)
})

test('the lock stays held through a cut-in fight even though the handler settled at once', async () => {
  const { lock, guard, calls, start } = setup()
  guard.respond = async () => { await sleep(100); return { outcome: 'killed' } }
  const running = start((session) => new Promise((resolve, reject) => {
    session.signal.addEventListener('abort', () => reject(session.signal.reason))
  }))
  await sleep(5)
  lock.onTick(found)
  await sleep(40)
  assert.equal(lock.busyDetail(), undefined)
  lock.onTick(null)
  assert.equal(calls.some((call) => call[0] === 'idleTick'), false)
  assert.equal((await running).kind, 'interrupted')
  await sleep(5)
  assert.equal(lock.busyDetail(), null)
})

test('a handler that settles late is an orphan: recorded, answered, busy until it settles', async () => {
  const { lock, start, orphanEvents } = setup()
  const gate = deferred()
  const running = start(() => gate.promise)
  await sleep(5)
  lock.onTick(found)
  const result = await running
  assert.equal(result.kind, 'interrupted')
  assert.deepEqual(orphanEvents().map(({ path, reconnected }) => ({ path, reconnected })), [{ path: '/mine', reconnected: undefined }])
  assert.equal(lock.busyDetail(), 'orphan')
  gate.resolve({})
  await sleep(5)
  assert.equal(lock.busyDetail(), null)
})

test('the backstop reconnects, records it and releases the lock', async () => {
  const { lock, calls, start, orphanEvents } = setup()
  const running = start(() => new Promise(() => {}))
  await sleep(5)
  lock.onTick(found)
  await running
  assert.equal(lock.busyDetail(), 'orphan')
  await sleep(160)
  assert.equal(calls.filter((call) => call[0] === 'reconnect').length, 1)
  assert.equal(orphanEvents().at(-1).reconnected, true)
  assert.equal(orphanEvents().at(-1).path, '/mine')
  assert.equal(lock.busyDetail(), null)
  assert.ok(calls.some((call) => call[0] === 'release'))
})

test('a death during the fight answers quarantined', async () => {
  const { bot, lock, guard, calls, start } = setup()
  const fightGate = deferred()
  guard.respond = (f) => { calls.push(['respond']); return fightGate.promise }
  const running = start((session) => new Promise((resolve, reject) => {
    session.signal.addEventListener('abort', () => reject(session.signal.reason))
  }))
  await sleep(5)
  lock.onTick(found)
  await sleep(10)
  assert.ok(calls.some((call) => call[0] === 'respond'))
  bot.emit('death')
  const result = await running
  assert.equal(result.kind, 'quarantined')
  assert.ok(calls.some((call) => call[0] === 'interruptBot'))
})

test('a death during the settle wait answers quarantined', async () => {
  const { bot, lock, calls, start } = setup({ settleMs: 500 })
  const running = start(() => new Promise(() => {}))
  await sleep(5)
  lock.onTick(found)
  await sleep(5)
  bot.emit('death')
  assert.equal((await running).kind, 'quarantined')
  assert.equal(calls.some((call) => call[0] === 'respond'), false)
})

test('a death while the handler runs answers quarantined and aborts the signal', async () => {
  const { bot, lock, start } = setup()
  let signal = null
  const gate = deferred()
  const running = start((session) => { signal = session.signal; return gate.promise })
  await sleep(5)
  bot.emit('death')
  assert.equal((await running).kind, 'quarantined')
  assert.equal(signal.aborted, true)
  assert.equal(lock.busyDetail(), 'orphan')
  gate.resolve({})
  await sleep(5)
  assert.equal(lock.busyDetail(), null)
})

test('a timeout aborts the signal, calls interruptBot and holds the lock until the handler settles', async () => {
  const { lock, calls, start } = setup()
  const gate = deferred()
  let signal = null
  const result = await start((session) => { signal = session.signal; return gate.promise }, { timeoutMs: 20 })
  assert.equal(result.kind, 'timeout')
  assert.equal(signal.aborted, true)
  assert.equal(calls.filter((call) => call[0] === 'interruptBot').length, 1)
  assert.equal(lock.busyDetail(), 'orphan')
  gate.resolve({})
  await sleep(5)
  assert.equal(lock.busyDetail(), null)
})

test('an endpoint that is not cut into ignores a threat', async () => {
  const { lock, calls, start } = setup()
  const gate = deferred()
  const running = start(() => gate.promise, { path: '/observe', cutIn: false })
  await sleep(5)
  assert.equal(lock.onTick(found), undefined)
  assert.equal(lock.busyDetail(), undefined)
  gate.resolve({ ok: true })
  assert.equal((await running).kind, 'ok')
  assert.equal(calls.some((call) => call[0] === 'stopForGuard' || call[0] === 'respond'), false)
})

test('a handler error is thrown to the caller and the lock is released', async () => {
  const { lock, start } = setup()
  await assert.rejects(start(() => { throw new Error('boom') }), /boom/)
  await sleep(5)
  assert.equal(lock.busyDetail(), null)
})

test('an idle fight holds the guard state, hold/release surround it, and no second fight starts within the gap', async () => {
  const { lock, guard, calls } = setup()
  const fightGate = deferred()
  guard.respond = async (f, opts) => { calls.push(['respond', opts.action]); await fightGate.promise }
  const fight = lock.onTick(found)
  assert.equal(lock.busyDetail(), 'guard')
  assert.equal(lock.onTick(found), undefined) // never two fights at once
  await sleep(5)
  fightGate.resolve()
  await fight
  assert.deepEqual(calls, [['hold'], ['respond', null], ['release']])
  assert.equal(lock.busyDetail(), null)
  assert.equal(lock.onTick(found), undefined) // within idleGapMs
  await sleep(100)
  assert.ok(lock.onTick(found) instanceof Promise)
})

test('an orphan does not stop an idle fight, and its detail wins', async () => {
  const { lock, guard, start } = setup()
  const gate = deferred()
  const fightGate = deferred()
  let fights = 0
  guard.respond = async () => { fights++; await fightGate.promise; return { outcome: 'killed' } }
  const running = start(() => gate.promise)
  await sleep(5)
  guard.respond = async () => ({ outcome: 'killed' })
  lock.onTick(found)
  await running
  guard.respond = async () => { fights++; await fightGate.promise }
  const fight = lock.onTick(found)
  assert.ok(fight instanceof Promise)
  assert.equal(lock.busyDetail(), 'orphan')
  fightGate.resolve()
  await fight
  assert.equal(fights, 1)
})

test('an idle tick with nothing to do calls onIdleTick, but not during an action or a fight', async () => {
  const { lock, calls, guard, start } = setup()
  lock.onTick(null)
  assert.equal(calls.filter((call) => call[0] === 'idleTick').length, 1)
  const gate = deferred()
  const running = start(() => gate.promise)
  await sleep(5)
  lock.onTick(null)
  assert.equal(calls.filter((call) => call[0] === 'idleTick').length, 1)
  gate.resolve({})
  await running
  await sleep(5)
  const fightGate = deferred()
  guard.respond = async () => { await fightGate.promise }
  const fight = lock.onTick(found)
  lock.onTick(null)
  assert.equal(calls.filter((call) => call[0] === 'idleTick').length, 1)
  fightGate.resolve()
  await fight
})

test('rejectOnThreat rejects once with code interrupted and the found attached', async () => {
  const bot = new EventEmitter()
  bot.health = 20
  const tracker = attachEventTracker(bot, createEventLog())
  const threat = tracker.rejectOnThreat()
  threat.reject(found)
  threat.reject({ threat: { mob: 'other' } })
  await assert.rejects(threat.promise, (err) => err.code === 'interrupted' && err.found === found)
  const disposed = tracker.rejectOnThreat()
  disposed.dispose()
  disposed.reject(found)
})
