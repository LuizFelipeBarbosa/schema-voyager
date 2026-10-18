const test = require('node:test')
const assert = require('node:assert/strict')
const { parseTactics, planDuel, judge, wilson, summarize, verdict, formatTable } = require('../../../../scripts/v3-duel.js')

const ID = 'd-1'
const end = (reason, extra = {}) => ({ duel_id: ID, reason, duration: 10, swings: 12, hits: 8, crits: 4, damage_dealt: 20, damage_taken: 15, ...extra })
const ok = (reason, extra) => ({ status: 200, body: { ok: true, ...end(reason, extra) } })

test('planDuel covers the four pairings once and repeats every 4 duels', () => {
  const pairings = [0, 1, 2, 3].map((i) => JSON.stringify(planDuel(i)))
  assert.equal(new Set(pairings).size, 4)
  for (let i = 0; i < 8; i++) assert.deepEqual(planDuel(i), planDuel(i + 4))
  assert.deepEqual(planDuel(0), { aBot: 0, westBot: 0 })
  assert.deepEqual(planDuel(3), { aBot: 1, westBot: 1 })
})

test('judge: a death decides the duel', () => {
  const aWins = judge(ok('killed'), ok('died'), ID)
  assert.deepEqual([aWins.valid, aWins.result], [true, 'A'])
  const bWins = judge(ok('died'), ok('killed'), ID)
  assert.deepEqual([bWins.valid, bWins.result], [true, 'B'])
  assert.equal(aWins.endA.reason, 'killed')
})

test('judge: a death without the other bot\'s killed is still a win, with a note', () => {
  const result = judge(ok('timeout'), ok('died'), ID)
  assert.deepEqual([result.valid, result.result], [true, 'A'])
  assert.match(result.reason, /did not report killed/)
})

test('judge: both timeout is a draw', () => {
  const result = judge(ok('timeout'), ok('timeout'), ID)
  assert.deepEqual([result.valid, result.result], [true, 'draw'])
})

test('judge: inconsistent or missing answers are invalid, with a reason', () => {
  const busy = { status: 409, body: { ok: false, error: 'busy' } }
  const cases = {
    'both died': [ok('died'), ok('died')],
    'a missing duel_end': [ok('timeout'), { status: 200, body: { ok: false, error: 'not_found', events: [] } }],
    'a 409': [busy, ok('timeout')],
    'an http error': [ok('timeout'), { error: 'fetch failed' }],
    'a duel_id mismatch': [ok('timeout'), ok('timeout', { duel_id: 'other' })],
    'killed without a death': [ok('killed'), ok('timeout')],
    'both killed': [ok('killed'), ok('killed')],
    stopped: [ok('stopped'), ok('timeout')]
  }
  for (const [name, [a, b]] of Object.entries(cases)) {
    const result = judge(a, b, ID)
    assert.equal(result.valid, false, name)
    assert.equal(result.result, null, name)
    assert.ok(result.reason.length > 0, name)
  }
})

test('judge: a quarantined answer still carries the duel_end in its events', () => {
  const quarantined = { status: 200, body: { ok: false, error: 'quarantined', events: [{ type: 'death' }, { type: 'duel_end', ...end('died') }] } }
  const result = judge(quarantined, ok('killed'), ID)
  assert.deepEqual([result.valid, result.result], [true, 'B'])
  assert.equal(result.endA.reason, 'died')
})

test('judge --vs: only the bot\'s answer counts', () => {
  assert.equal(judge(ok('died'), null, ID).result, 'B')
  assert.equal(judge(ok('killed'), null, ID).result, 'A')
  assert.equal(judge(ok('timeout'), null, ID).result, 'draw')
  assert.equal(judge({ error: 'timeout' }, null, ID).valid, false)
})

test('wilson', () => {
  assert.deepEqual(wilson(0, 0), [0, 1])
  const [low, high] = wilson(20, 40)
  assert.ok(Math.abs(low - 0.352) < 0.0005 && Math.abs(high - 0.648) < 0.0005, `${low} ${high}`)
  const [low26, high26] = wilson(26, 40)
  assert.ok(Math.abs(low26 - 0.495) < 0.001 && Math.abs(high26 - 0.779) < 0.001, `${low26} ${high26}`)
  const [fracLow, fracHigh] = wilson(26.5, 40)
  assert.ok(fracLow > wilson(26, 40)[0] && fracHigh > wilson(26, 40)[1])
  assert.deepEqual(wilson(0, 10)[0], 0)
  assert.equal(wilson(10, 10)[1], 1)
})

// n duels where A wins `wins`, draws `draws`, the rest B wins; plans follow planDuel.
function records (n, wins, draws = 0) {
  return Array.from({ length: n }, (_, i) => ({
    i,
    plan: planDuel(i),
    valid: true,
    result: i < wins ? 'A' : i < wins + draws ? 'draw' : 'B',
    endA: end('timeout', { crits: 4 }),
    endB: end('timeout', { crits: 0, damage_dealt: null })
  }))
}
const names = { a: 'crit', b: 'baseline', out: 'runs/x', timeout: 60 }

test('summarize: draws count half, invalid records are only counted', () => {
  const all = [...records(40, 20, 4), { i: 0, plan: planDuel(0), valid: false, result: null, endA: null, endB: null }]
  const summary = summarize(all, names)
  assert.equal(summary.duels, 40)
  assert.equal(summary.invalid, 1)
  assert.equal(summary.a.wins, 20)
  assert.equal(summary.a.draws, 4)
  assert.equal(summary.a.losses, 16)
  assert.equal(summary.a.rate, 22 / 40)
  assert.equal(summary.b.wins, 16)
  assert.equal(summary.b.rate, 18 / 40)
  assert.equal(summary.a.crits, 4)
  assert.equal(summary.b.crits, 0)
  assert.equal(summary.b.dealt, null)
})

test('summarize: A\'s wins by bot and by side add up', () => {
  const summary = summarize(records(40, 25, 3), names)
  const sum = (parts, key) => parts.reduce((total, part) => total + part[key], 0)
  assert.equal(sum(summary.aByBot, 'wins'), 25)
  assert.equal(sum(summary.aBySide, 'wins'), 25)
  assert.equal(sum(summary.aByBot, 'duels'), 40)
  assert.equal(sum(summary.aBySide, 'duels'), 40)
  assert.deepEqual(summary.aByBot.map((part) => part.duels), [20, 20])
  assert.deepEqual(summary.aBySide.map((part) => part.duels), [20, 20])
})

test('verdict: 24 of 40 keeps, 23 of 40 drops, 30 duels at 70% drop for too few duels', () => {
  assert.equal(verdict(summarize(records(40, 24), names)).keep, true)
  assert.equal(verdict(summarize(records(40, 23), names)).keep, false)
  assert.equal(verdict(summarize(records(40, 22, 4), names)).keep, true) // 24/40 with draws
  const few = verdict(summarize(records(30, 21), names))
  assert.equal(few.keep, false)
  assert.match(few.why, /30 duels < 40/)
  assert.equal(verdict(summarize(records(30, 21), names), { minDuels: 30 }).keep, true)
})

test('formatTable shows the sets, the counts and the verdict', () => {
  const table = formatTable(summarize([...records(40, 26, 2), { valid: false, plan: planDuel(0) }], names))
  assert.match(table, /A = crit vs B = baseline, 40 duels \(1 invalid, re-run\), timeout 60 s/)
  assert.match(table, /^crit +40 +26 +12 +2 +67.5%/m)
  assert.match(table, /^baseline +40 +12 +26 +2 +32.5%/m)
  assert.match(table, /A's wins by bot: VoyagerBot \d+\/20, VoyagerBot2 \d+\/20; by side: west \d+\/20, east \d+\/20/)
  assert.match(table, /verdict: KEEP crit \(40 duels >= 40, win rate 67.5% >= 60%\)/)
  assert.match(formatTable(summarize(records(40, 10), names)), /verdict: DROP crit/)
})

test('formatTable tells equal sets apart (the A/A test)', () => {
  const table = formatTable(summarize(records(40, 20), { ...names, a: 'baseline' }))
  assert.match(table, /baseline \(A\)/)
  assert.match(table, /baseline \(B\)/)
})

test('parseTactics', () => {
  assert.deepEqual(parseTactics('baseline'), [])
  assert.deepEqual(parseTactics(''), [])
  assert.deepEqual(parseTactics('crit,wtap'), ['crit', 'wtap'])
  assert.deepEqual(parseTactics(' crit '), ['crit'])
})

test('planDuel: against a person the bot always plays A, and the sides still swap', () => {
  const plans = [0, 1, 2, 3].map((i) => planDuel(i, true))
  assert.deepEqual(plans.map((plan) => plan.aBot), [0, 0, 0, 0])
  assert.deepEqual(plans.map((plan) => plan.westBot), [0, 1, 0, 1])
})
