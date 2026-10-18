const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { DUEL_TIMEOUT_S, validateDuel, runDuel } = require('../../lib/duel')

const OPPONENT_ID = 7
const BOT_ID = 1

function fakeBot ({ opponentMetadata = {} } = {}) {
  const bot = new EventEmitter()
  const opponentEntity = { id: OPPONENT_ID, name: 'Rival', metadata: opponentMetadata }
  Object.assign(bot, {
    username: 'Voyager',
    health: 20,
    entity: { id: BOT_ID },
    players: { Rival: { entity: opponentEntity } },
    _client: new EventEmitter()
  })
  return { bot, opponentEntity }
}

function fakeEventLog () {
  const events = []
  return { events, record: (type, fields) => events.push({ type, ...fields }) }
}

const fakeCreateTactics = (bot, names) => ({ names, attach () {}, detach () {} })

// A fight that returns when shouldStop says so, or after `ms`.
function waitingFight ({ onStart = () => {}, ms = 5000 } = {}) {
  const calls = []
  const fight = async (bot, target, options) => {
    calls.push({ target, options })
    onStart(bot, calls.length)
    const until = Date.now() + ms
    while (!options.shouldStop() && Date.now() < until) await new Promise((resolve) => setTimeout(resolve, 10))
    return { outcome: 'lost' }
  }
  fight.calls = calls
  return fight
}

const listenerCounts = (bot) => ({
  bot: bot.eventNames().map((name) => [name, bot.listenerCount(name)]),
  client: bot._client.eventNames().map((name) => [name, bot._client.listenerCount(name)])
})

function run (bot, body, options) {
  const eventLog = options.eventLog ?? fakeEventLog()
  return runDuel(bot, { player: 'Rival', ...body }, { eventLog, createTactics: fakeCreateTactics, ...options }).then((result) => ({ result, eventLog }))
}

test('validateDuel: rejects bad fields, accepts a good body', () => {
  assert.match(validateDuel({}), /player/)
  assert.match(validateDuel({ player: '' }), /player/)
  assert.match(validateDuel({ player: 'Rival', tactics: ['nope'] }), /nope/)
  for (const timeout of [4, 301, 'x', NaN]) assert.match(validateDuel({ player: 'Rival', timeout_s: timeout }), /timeout_s/)
  assert.match(validateDuel({ player: 'Rival', duel_id: 'x'.repeat(65) }), /duel_id/)
  assert.equal(validateDuel({ player: 'Rival', tactics: [], timeout_s: 5, duel_id: 'x'.repeat(64) }), null)
  assert.equal(validateDuel({ player: 'Rival' }), null)
  assert.equal(DUEL_TIMEOUT_S, 60)
})

test('runDuel: the opponent dying ends it killed, with one duel_end equal to the result', async () => {
  const { bot } = fakeBot()
  const fight = waitingFight({ onStart: (b) => setTimeout(() => b.emit('entityDead', { id: OPPONENT_ID }), 30) })
  const { result, eventLog } = await run(bot, { duel_id: 'd1' }, { fight })
  assert.equal(result.reason, 'killed')
  assert.equal(result.winner, 'Voyager')
  assert.equal(result.duel_id, 'd1')
  assert.equal(result.engagements, 1)
  assert.equal(fight.calls.length, 1)
  assert.deepEqual(eventLog.events, [{ type: 'duel_end', ...result }])
})

test('runDuel: the bot dying records duel_end inside the death emit', async () => {
  const { bot } = fakeBot()
  const fight = waitingFight()
  const eventLog = fakeEventLog()
  const promise = run(bot, {}, { fight, eventLog })
  await new Promise((resolve) => setTimeout(resolve, 30))
  bot.health = 0
  bot.emit('death')
  assert.equal(eventLog.events.length, 1)
  assert.equal(eventLog.events[0].reason, 'died')
  assert.equal(eventLog.events[0].winner, 'Rival')
  const { result } = await promise
  assert.deepEqual(eventLog.events, [{ type: 'duel_end', ...result }])
  assert.equal(result.health_end, 0)
  assert.equal(result.duel_id, null)
})

test('runDuel: a fight that keeps returning lost re-engages until the deadline', async () => {
  const { bot } = fakeBot()
  const fight = async () => ({ outcome: 'lost' })
  const counted = []
  const { result, eventLog } = await run(bot, { timeout_s: 0.45 }, { fight: async (...args) => { counted.push(1); return fight(...args) } })
  assert.equal(result.reason, 'timeout')
  assert.equal(result.winner, null)
  assert.equal(result.engagements, counted.length)
  assert.ok(result.engagements >= 3)
  assert.equal(eventLog.events.length, 1)
})

for (const which of ['stopSignal', 'signal']) {
  test(`runDuel: ${which} aborted mid-fight ends it stopped quickly`, async () => {
    const { bot } = fakeBot()
    const controller = new AbortController()
    const startedAt = Date.now()
    const { result, eventLog } = await run(bot, {}, {
      fight: waitingFight({ onStart: () => setTimeout(() => controller.abort(), 50) }),
      [which]: controller.signal
    })
    assert.equal(result.reason, 'stopped')
    assert.equal(result.winner, null)
    assert.ok(Date.now() - startedAt < 1000)
    assert.equal(eventLog.events.length, 1)
  })
}

test('runDuel: an opponent out of view for noOpponentMs ends it no_opponent', async () => {
  const { bot } = fakeBot()
  bot.players = { Rival: { entity: null } }
  const fight = waitingFight()
  const { result } = await run(bot, {}, { fight, noOpponentMs: 400 })
  assert.equal(result.reason, 'no_opponent')
  assert.equal(result.winner, null)
  assert.equal(result.engagements, 0)
  assert.equal(fight.calls.length, 0)
})

test('runDuel: counts strikes, hits, crits, blocks and both sides damage', async () => {
  const { bot, opponentEntity } = fakeBot({ opponentMetadata: { 9: 20 } })
  const fight = waitingFight({
    onStart: (b) => setTimeout(() => {
      for (let i = 0; i < 3; i++) b.emit('attackedTarget')
      b.emit('entityHurt', opponentEntity)
      b.emit('entityHurt', opponentEntity)
      b.emit('entityHurt', { id: 99 })
      b.emit('entityCriticalEffect', opponentEntity)
      b._client.emit('entity_status', { entityId: BOT_ID, entityStatus: 29 })
      b._client.emit('entity_status', { entityId: OPPONENT_ID, entityStatus: 29 })
      b._client.emit('entity_status', { entityId: OPPONENT_ID, entityStatus: 29 })
      b._client.emit('entity_status', { entityId: OPPONENT_ID, entityStatus: 2 })
      b.health = 15.5
      b.emit('health')
      b.health = 18
      b.emit('health')
      b.health = 17
      b.emit('health')
      opponentEntity.metadata[9] = 14
      b.emit('entityUpdate', opponentEntity)
      opponentEntity.metadata[9] = 16
      b.emit('entityUpdate', opponentEntity)
      opponentEntity.metadata[9] = 12.5
      b.emit('entityUpdate', opponentEntity)
      b.emit('entityDead', { id: OPPONENT_ID })
    }, 20)
  })
  const { result } = await run(bot, {}, { fight })
  assert.equal(result.swings, 3)
  assert.equal(result.hits, 2)
  assert.equal(result.crits, 1)
  assert.equal(result.blocked, 1)
  assert.equal(result.their_blocked, 2)
  assert.equal(result.damage_taken, 5.5) // 20 -> 15.5, then 18 -> 17
  assert.equal(result.damage_dealt, 9.5) // 20 -> 14, 16 -> 12.5
  assert.equal(result.health_end, 17)
})

test('runDuel: damage_dealt is null when the opponent health is never seen', async () => {
  const { bot, opponentEntity } = fakeBot()
  const fight = waitingFight({
    onStart: (b) => setTimeout(() => {
      b.emit('entityUpdate', opponentEntity)
      b.emit('entityDead', { id: OPPONENT_ID })
    }, 20)
  })
  const { result } = await run(bot, {}, { fight })
  assert.equal(result.damage_dealt, null)
})

for (const ending of ['killed', 'died', 'timeout', 'stopped', 'no_opponent']) {
  test(`runDuel: every listener is removed after ${ending}, and fight gets the tactics`, async () => {
    const { bot } = fakeBot()
    const controller = new AbortController()
    const before = listenerCounts(bot)
    const options = { stopSignal: controller.signal }
    const body = {}
    let fight = waitingFight({
      onStart: (b) => {
        if (ending === 'killed') setTimeout(() => b.emit('entityDead', { id: OPPONENT_ID }), 20)
        if (ending === 'died') setTimeout(() => b.emit('death'), 20)
        if (ending === 'stopped') setTimeout(() => controller.abort(), 20)
      }
    })
    if (ending === 'timeout') body.timeout_s = 0.2
    if (ending === 'no_opponent') {
      bot.players.Rival.entity = null
      options.noOpponentMs = 300
    }
    let createdWith = null
    options.createTactics = (b, names) => { createdWith = names; return fakeCreateTactics(b, names) }
    const { result } = await run(bot, { ...body, tactics: [] }, { fight, ...options })
    assert.equal(result.reason, ending)
    assert.deepEqual(listenerCounts(bot), before)
    assert.deepEqual(createdWith, [])
    if (ending !== 'no_opponent') {
      const given = fight.calls[0].options
      assert.equal(given.fleeBelow, 0)
      assert.equal(given.flee, false)
      assert.deepEqual(given.tactics.names, [])
    }
  })
}
