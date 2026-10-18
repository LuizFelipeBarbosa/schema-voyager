// Live v3 test of the bot's guard (the reflex combat layer): it cuts into a
// /mine, backs off from a creeper, holds busy while it fights an idle bot, and
// POST /guard switches it. Contract: contracts/bot_api.md, "v3.2: survival".
//
// Uses the one bot already running at SV2_BOT_PORT and RCON at SV2_RCON_PORT
// (password RCON_PASSWORD, bot name BOT_USERNAME); starts nothing. Refuses to
// run against the live demo's ports (3000, 25575). Environment variables are
// loaded from $SV2_HOME/.env without overriding the ones already set.
//
// It builds a glass arena around the bot, raises the difficulty to easy (a mob
// despawns on peaceful), uses husks (they don't burn in daylight) and puts the
// world back at the end: no mobs, peaceful, guard mode defend. The arena stays.
//
// Usage: node mc/bot/test/v3_guard_live.js   (exit 0 = every check passed)

const assert = require('node:assert/strict')
const { RconClient, health, loadEnv, post, sleep } = require('./v2_helpers')

const POLL_INTERVAL_MS = 250
const FIGHT_START_TIMEOUT_MS = 8000
const CREEPER_FLEE_TIMEOUT_MS = 5000
const BUSY_RETRY_INTERVAL_MS = 500
const BUSY_RETRY_TIMEOUT_MS = 60000
const NO_FIGHT_WINDOW_MS = 3000
const BLOCK_UPDATE_DELAY_MS = 1000

const short = (value) => JSON.stringify(value)

let failures = 0

// Runs one check and prints PASS or FAIL with the values it saw.
async function check (name, run) {
  try {
    console.log(`PASS ${name}: ${await run()}`)
  } catch (err) {
    failures++
    console.log(`FAIL ${name}: ${err.message}`)
  }
}

async function getStatus (since = 0) {
  const { body } = await post('/status', { since })
  return body
}

const eventTypes = (events) => events.map((event) => event.type)

// Polls /status until `isReady(status)` holds; returns it, or null after timeoutMs.
async function pollStatus (since, timeoutMs, isReady) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const status = await getStatus(since)
    if (isReady(status)) return status
    if (Date.now() > deadline) return null
    await sleep(POLL_INTERVAL_MS)
  }
}

function distance (a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)
}

async function main () {
  loadEnv()
  const username = process.env.BOT_USERNAME || 'VoyagerBot'
  const rcon = new RconClient() // refuses the live demo's RCON port
  const status = await health() // refuses the live demo's bot port
  if (!status.connected) throw new Error('the bot is not connected to its server')
  await rcon.connect()

  const atBot = `execute at ${username} run`
  const killMobs = () => rcon.exec(`${atBot} kill @e[type=!player,type=!item,distance=..48]`)
  const topUpHealth = () => rcon.exec(`effect give ${username} minecraft:instant_health 1 5`)
  // Kills leftover mobs and tops up health, so that a check starts clean.
  const resetBetweenChecks = async () => {
    await killMobs()
    await topUpHealth()
    await sleep(1000)
  }

  try {
    await rcon.exec('difficulty easy')
    await rcon.exec('time set day')
    await post('/guard', { mode: 'defend' })
    await rcon.exec(`clear ${username}`)
    await rcon.exec(`give ${username} minecraft:iron_sword`)
    await rcon.exec(`effect clear ${username}`)
    // A flat arena, a glass box with a stone floor: the bot's surroundings may
    // be rock, water or ice, where a mob is out of sight, a target out of reach
    // and a dug-out space floods.
    await rcon.exec(`${atBot} fill ~-6 ~-1 ~-6 ~14 ~5 ~6 minecraft:glass hollow`)
    await rcon.exec(`${atBot} fill ~-6 ~-1 ~-6 ~14 ~-1 ~6 minecraft:stone`)
    await resetBetweenChecks()

    await check('1: a husk cuts into a /mine, and the retried /mine finishes', async () => {
      await rcon.exec(`${atBot} setblock ~10 ~ ~ minecraft:oak_log`)
      await sleep(BLOCK_UPDATE_DELAY_MS) // the bot must have received the block
      const mining = post('/mine', { block: 'oak_log' })
      await sleep(750)
      await rcon.exec(`${atBot} summon minecraft:husk ~3 ~ ~`)
      const { body: interrupted } = await mining
      const types = eventTypes(interrupted.events)
      const seen = `response ${short({ ok: interrupted.ok, error: interrupted.error, detail: interrupted.detail })}, events ${short(types)}`

      assert.equal(interrupted.ok, false, seen)
      assert.equal(interrupted.error, 'interrupted', seen)
      assert.equal(interrupted.detail.threat, 'husk', seen)
      assert.ok(['killed', 'fled'].includes(interrupted.detail.outcome), seen)
      for (const type of ['threat', 'fight_start', 'fight_end']) assert.ok(types.includes(type), `no ${type}: ${seen}`)
      if (interrupted.detail.outcome === 'fled') assert.ok(types.includes('fled'), `no fled: ${seen}`)

      await resetBetweenChecks()
      const { status: httpStatus, body: retried } = await post('/mine', { block: 'oak_log' })
      const retriedSeen = `retry ${httpStatus} ${short({ ok: retried.ok, error: retried.error, detail: retried.detail, block_removed: retried.block_removed, drops: retried.drops, picked_up: retried.picked_up })}`
      assert.equal(retried.ok, true, retriedSeen)
      assert.equal(retried.block_removed, true, retriedSeen)
      assert.equal(retried.drops.oak_log, 1, retriedSeen)
      assert.equal(retried.picked_up.oak_log, 1, retriedSeen)
      return `${seen}; ${retriedSeen}`
    })

    await resetBetweenChecks()
    await check('2: the bot backs off from a creeper', async () => {
      const before = (await getStatus()).position
      const since = (await getStatus()).last_seq
      await rcon.exec(`${atBot} summon minecraft:creeper ~4 ~ ~`)

      const fledStatus = await pollStatus(since, CREEPER_FLEE_TIMEOUT_MS, (s) => s.events.some((event) => event.type === 'fled'))
      await sleep(2000) // backing off
      const later = await getStatus(since)
      await killMobs() // before it explodes
      assert.ok(fledStatus, `no fled event within ${CREEPER_FLEE_TIMEOUT_MS} ms; events ${short(later.events)}`)
      const fled = fledStatus.events.find((event) => event.type === 'fled')
      const threat = later.events.find((event) => event.type === 'threat')
      assert.equal(fled.reason, 'creeper', `fled ${short(fled)}`)
      assert.equal(threat.mob, 'creeper', `threat ${short(threat)}`)
      const laterDistance = later.nearby_entities.creeper?.nearest
      assert.ok(laterDistance > threat.distance, `creeper at ${laterDistance} after ${threat.distance}; events ${short(later.events)}`)
      return `threat distance ${threat.distance}, fled ${short(fled)}, creeper ${laterDistance} blocks away 2 s later, bot moved ${distance(before, later.position).toFixed(1)}`
    })

    await resetBetweenChecks()
    await check('3: an idle fight holds busy, and a retried /observe gets in', async () => {
      const since = (await getStatus()).last_seq
      await rcon.exec(`${atBot} summon minecraft:husk ~3 ~ ~`)
      const fighting = await pollStatus(since, FIGHT_START_TIMEOUT_MS, (s) => s.guard.fighting === 'husk' || s.events.some((event) => event.type === 'fight_start'))
      assert.ok(fighting, `no fight within ${FIGHT_START_TIMEOUT_MS} ms; status ${short(await getStatus(since))}`)

      const first = await post('/observe', {})
      const firstSeen = `first /observe ${first.status} ${short({ ok: first.body.ok, error: first.body.error, detail: first.body.detail })}`
      assert.equal(first.status, 409, firstSeen)
      assert.deepEqual({ error: first.body.error, detail: first.body.detail }, { error: 'busy', detail: 'guard' }, firstSeen)

      const deadline = Date.now() + BUSY_RETRY_TIMEOUT_MS
      let retries = 0
      let observed
      do {
        await sleep(BUSY_RETRY_INTERVAL_MS)
        observed = await post('/observe', {})
        retries++
      } while (observed.status === 409 && Date.now() < deadline)
      assert.equal(observed.status, 200, `${firstSeen}; still ${observed.status} ${short(observed.body)} after ${retries} retries`)
      assert.equal(observed.body.ok, true, short(observed.body))

      const events = (await getStatus(since)).events
      const fightEnd = events.find((event) => event.type === 'fight_end')
      assert.ok(fightEnd, `no fight_end; events ${short(events)}`)
      return `${firstSeen}; /observe ok after ${retries} retries; fight_end ${short(fightEnd)}`
    })

    await resetBetweenChecks()
    await check('4: POST /guard validates, switches off and back on', async () => {
      const bogus = await post('/guard', { mode: 'bogus' })
      assert.equal(bogus.status, 400, `bogus mode answered ${bogus.status} ${short(bogus.body)}`)

      const off = await post('/guard', { mode: 'off' })
      assert.deepEqual(off.body, { ok: true, mode: 'off' })
      assert.equal((await getStatus()).guard.mode, 'off')

      const since = (await getStatus()).last_seq
      await rcon.exec(`${atBot} summon minecraft:husk ~3 ~ ~`)
      const fought = await pollStatus(since, NO_FIGHT_WINDOW_MS, (s) => s.events.some((event) => event.type === 'fight_start'))
      await killMobs()
      assert.equal(fought, null, `fight_start with the guard off: ${short(fought && fought.events)}`)

      const on = await post('/guard', { mode: 'defend' })
      assert.deepEqual(on.body, { ok: true, mode: 'defend' })
      assert.equal((await getStatus()).guard.mode, 'defend')
      return `bogus ${bogus.status}, off ${short(off.body)}, no fight_start in ${NO_FIGHT_WINDOW_MS} ms, back to ${short(on.body)}`
    })
  } catch (err) {
    failures++
    console.log(`FAIL setup: ${err.message}`)
  } finally {
    await killMobs().catch(() => {})
    await rcon.exec('difficulty peaceful').catch(() => {})
    await post('/guard', { mode: 'defend' }).catch(() => {})
    rcon.close()
  }
  console.log(failures === 0 ? 'ALL PASS' : `${failures} FAILED`)
  process.exit(failures === 0 ? 0 : 1)
}

main().catch((err) => {
  console.log(`aborted: ${err.message}`)
  process.exit(1)
})
