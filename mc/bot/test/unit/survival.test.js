const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { attachSurvival } = require('../../lib/survival')
const { createEventLog } = require('../../lib/events')

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// A bot whose loadPlugin runs the plugin at once, or only on inject_allowed.
function makeBot ({ deferred = false } = {}) {
  const bot = new EventEmitter()
  bot.health = 20
  bot.food = 20
  bot.inventory = { items: () => [] }
  const pending = []
  bot.loadPlugin = (plugin) => {
    if (deferred) pending.push(plugin)
    else plugin(bot, {})
  }
  bot.inject = () => {
    bot.emit('inject_allowed')
    pending.splice(0).forEach((plugin) => plugin(bot, {}))
  }
  return bot
}

// Fakes for the two plugins; `calls` records the order of equipAll and eat.
function makeFakes () {
  const calls = []
  const state = { equipAllMs: 0, armorListener: () => {} }
  const armorManager = (bot) => {
    bot.on('playerCollect', state.armorListener)
    bot.armorManager = {
      equipAll: async () => {
        calls.push('equipAll:start')
        state.equipping = true
        await sleep(state.equipAllMs)
        state.equipping = false
        calls.push('equipAll')
      }
    }
  }
  const autoEat = (bot) => {
    bot.autoEat = {
      options: { startAt: 16, eatingTimeout: 50, bannedFood: ['pufferfish'], equipOldItem: true, offhand: true },
      isEating: false,
      disabled: false,
      disable () { this.disabled = true; calls.push('disable') },
      enable () { this.disabled = false; calls.push('enable') },
      eat: async () => { calls.push('eat') }
    }
  }
  return { calls, state, plugins: { armorManager, autoEat } }
}

function setup ({ deferred = false, now } = {}) {
  const bot = makeBot({ deferred })
  const fakes = makeFakes()
  const eventLog = createEventLog()
  const survival = attachSurvival(bot, eventLog, { ...fakes.plugins, now })
  if (deferred) bot.inject()
  return { bot, eventLog, survival, ...fakes }
}

for (const deferred of [false, true]) {
  test(`removes only armor-manager's playerCollect listener (deferred=${deferred})`, () => {
    const bot = makeBot({ deferred })
    const fakes = makeFakes()
    const other = () => {}
    bot.on('playerCollect', other)
    attachSurvival(bot, createEventLog(), fakes.plugins)
    if (deferred) {
      assert.equal(bot.armorManager, undefined)
      bot.inject()
    }
    assert.deepEqual(bot.listeners('playerCollect'), [other])
    assert.ok(bot.armorManager)
  })
}

test('sets the auto-eat options and keeps the banned food', () => {
  const { bot } = setup({ deferred: true })
  const { startAt, equipOldItem, offhand, bannedFood } = bot.autoEat.options
  assert.deepEqual({ startAt, equipOldItem, offhand, bannedFood }, {
    startAt: 14, equipOldItem: false, offhand: false, bannedFood: ['pufferfish']
  })
})

test('startAt is 17 while hurt and 14 at full health', () => {
  const { bot } = setup()
  bot.health = 15
  bot.emit('health')
  assert.equal(bot.autoEat.options.startAt, 17)
  bot.health = 20
  bot.emit('health')
  assert.equal(bot.autoEat.options.startAt, 14)
})

test('hold disables auto-eat and waits for an eat in progress', async () => {
  const { bot, survival } = setup()
  bot.autoEat.isEating = true
  setTimeout(() => { bot.autoEat.isEating = false }, 30)
  const started = Date.now()
  await survival.hold()
  assert.ok(bot.autoEat.disabled)
  assert.ok(Date.now() - started >= 25)
})

test('hold gives up on an eat after eatingTimeout', async () => {
  const { bot, survival } = setup()
  bot.autoEat.isEating = true
  const started = Date.now()
  await survival.hold()
  const elapsed = Date.now() - started
  assert.ok(elapsed >= 45 && elapsed < 500)
})

test('hold waits for a running equipAll', async () => {
  const { state, survival, bot, calls } = setup()
  state.equipAllMs = 30
  survival.onIdleTick()
  await sleep(0)
  assert.ok(state.equipping)
  await survival.hold()
  assert.ok(!state.equipping)
  assert.ok(bot.autoEat.disabled)
  assert.deepEqual(calls.filter((c) => c.startsWith('equipAll')), ['equipAll:start', 'equipAll'])
})

test('two holds: only the last release enables, then equipAll and eat run once', async () => {
  const { bot, survival, calls } = setup()
  const first = await survival.hold()
  const second = await survival.hold()
  assert.deepEqual(calls, ['disable'])
  first()
  assert.ok(bot.autoEat.disabled)
  second()
  assert.ok(!bot.autoEat.disabled)
  await sleep(10)
  assert.deepEqual(calls, ['disable', 'enable', 'equipAll:start', 'equipAll', 'eat'])
})

test('a hold opened while the last release is still equipping blocks the queued eat', async () => {
  const { bot, survival, state } = setup()
  const holdsAtEat = []
  let open = 0
  bot.autoEat.eat = async () => { holdsAtEat.push(open) }
  state.equipAllMs = 50

  const first = await survival.hold()
  open = 1
  first()
  open = 0
  await sleep(10) // equipAll is still running
  const second = survival.hold()
  open = 1
  await second.then((release) => {
    assert.deepEqual(holdsAtEat, [])
    release()
    open = 0
  })
  await sleep(80)
  assert.deepEqual(holdsAtEat, [0])
})

test('releasing twice is a no-op', async () => {
  const { bot, survival } = setup()
  const first = await survival.hold()
  const second = await survival.hold()
  first()
  first()
  assert.ok(bot.autoEat.disabled)
  second()
  assert.ok(!bot.autoEat.disabled)
})

test('onIdleTick does nothing while held, then runs once per 2 s', async () => {
  let clock = 10000
  const { survival, calls } = setup({ now: () => clock })
  const release = await survival.hold()
  survival.onIdleTick()
  await sleep(5)
  assert.deepEqual(calls, ['disable'])
  release()
  await sleep(10)
  calls.length = 0

  survival.onIdleTick()
  survival.onIdleTick()
  await sleep(10)
  assert.deepEqual(calls, ['equipAll:start', 'equipAll', 'eat'])
  clock += 1999
  survival.onIdleTick()
  await sleep(10)
  assert.equal(calls.filter((c) => c === 'eat').length, 1)
  clock += 1
  survival.onIdleTick()
  await sleep(10)
  assert.equal(calls.filter((c) => c === 'eat').length, 2)
})

test('autoeat_error logs every error except "No food found."', (t) => {
  const { bot } = setup()
  const log = t.mock.method(console, 'log', () => {})
  bot.emit('autoeat_error', new Error('No food found.'))
  assert.equal(log.mock.callCount(), 0)
  bot.emit('autoeat_error', new Error('Eating timed out'))
  assert.deepEqual(log.mock.calls.map((call) => call.arguments), [['survival: autoeat_error: Eating timed out']])
})

test('records ate on autoeat_finished; autoeat_error does not throw', () => {
  const { bot, eventLog } = setup()
  bot.food = 18
  bot.emit('autoeat_finished', { name: 'bread' }, false)
  const [event] = eventLog.since(0)
  assert.equal(event.type, 'ate')
  assert.equal(event.item, 'bread')
  assert.equal(event.food, 18)
  assert.doesNotThrow(() => bot.emit('autoeat_error', new Error('No food found.')))
})

test('before the plugins are injected, hold, release and onIdleTick are safe', async () => {
  const fresh = makeBot({ deferred: true })
  const early = attachSurvival(fresh, createEventLog(), makeFakes().plugins)
  const release = await early.hold()
  early.onIdleTick()
  release()
  await sleep(5)
  assert.equal(fresh.autoEat, undefined)
})

test('the real plugins: armor listener removed, auto-eat options set', () => {
  const bot = makeBot()
  bot._client = new EventEmitter()
  bot.entity = { id: 1 }
  attachSurvival(bot, createEventLog())
  assert.equal(bot.listeners('playerCollect').length, 1) // auto-eat's own
  assert.ok(bot.armorManager.equipAll)
  const { startAt, equipOldItem, offhand } = bot.autoEat.options
  assert.deepEqual({ startAt, equipOldItem, offhand }, { startAt: 14, equipOldItem: false, offhand: false })
})
