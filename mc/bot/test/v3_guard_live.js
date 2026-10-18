// Live v3 test of the bot's guard (the reflex combat layer): it cuts into a
// /mine, backs off from a creeper, holds busy while it fights an idle bot, and
// POST /guard switches it. Contract: contracts/bot_api.md, "v3.2: survival".
//
// Uses the one bot already running at SV2_BOT_PORT and RCON at SV2_RCON_PORT
// (password RCON_PASSWORD, bot name BOT_USERNAME); starts nothing. Refuses to
// run against the live demo's ports (3000, 25575). Environment variables are
// loaded from $SV2_HOME/.env without overriding the ones already set.
//
// It teleports the bot to a flat stone floor high above the terrain (over its
// own x and z), builds a glass arena there, raises the difficulty to easy (a
// mob despawns on peaceful), uses husks (they don't burn in daylight) and puts
// the world back at the end: arena removed, bot teleported back, no mobs,
// peaceful, guard mode defend.
//
// Usage: node mc/bot/test/v3_guard_live.js   (exit 0 = every check passed)

const assert = require('node:assert/strict')
const { RconClient, health, loadEnv, post, sleep } = require('./v2_helpers')

const POLL_INTERVAL_MS = 250
const FIGHT_START_TIMEOUT_MS = 8000
const CREEPER_FLEE_TIMEOUT_MS = 5000
const BUSY_FIRST_RETRY_MS = 250
const BUSY_RETRY_INTERVAL_MS = 500
const CREEPER_MIN_MOVE_BLOCKS = 3
const NO_REFIGHT_WINDOW_MS = 3000
const BUSY_RETRY_TIMEOUT_MS = 60000
const NO_FIGHT_WINDOW_MS = 3000
const BLOCK_UPDATE_DELAY_MS = 1000
const ARENA_Y = 200 // the floor's altitude: above any terrain, below the 319 build limit
const ARENA_RADIUS = 15
const ARENA_HEIGHT = 7 // floor to ceiling: the glass box below spans ~-1 to ~5
const TELEPORT_TIMEOUT_MS = 5000
const ARRIVED_BLOCKS = 1

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

// POSTs, and retries a 409 busy the way the Python client does: after 0.25 s, then every 0.5 s, up to 60 s.
async function postRetryingBusy (path, body) {
  const deadline = Date.now() + BUSY_RETRY_TIMEOUT_MS
  let response = await post(path, body)
  for (let wait = BUSY_FIRST_RETRY_MS; response.status === 409 && Date.now() < deadline; wait = BUSY_RETRY_INTERVAL_MS) {
    await sleep(wait)
    response = await post(path, body)
  }
  return response
}

async function main () {
  loadEnv()
  const username = process.env.BOT_USERNAME || 'VoyagerBot'
  const rcon = new RconClient() // refuses the live demo's RCON port
  const status = await health() // refuses the live demo's bot port
  if (!status.connected) throw new Error('the bot is not connected to its server')
  await rcon.connect()

  const start = (await getStatus()).position
  const arenaX = Math.floor(start.x) // the bot's chunk is loaded, so the arena's is
  const arenaZ = Math.floor(start.z)
  const arenaMin = `${arenaX - ARENA_RADIUS} ${ARENA_Y - 1} ${arenaZ - ARENA_RADIUS}`
  const arenaMax = `${arenaX + ARENA_RADIUS} ${ARENA_Y - 1 + ARENA_HEIGHT - 1} ${arenaZ + ARENA_RADIUS}`
  const floorMax = `${arenaX + ARENA_RADIUS} ${ARENA_Y - 1} ${arenaZ + ARENA_RADIUS}`
  const teleportBot = async (x, y, z) => {
    await rcon.exec(`tp ${username} ${x} ${y} ${z}`)
    const arrived = await pollStatus(0, TELEPORT_TIMEOUT_MS, (s) => distance(s.position, { x, y, z }) <= ARRIVED_BLOCKS)
    if (!arrived) throw new Error(`the bot did not arrive at ${x} ${y} ${z} within ${TELEPORT_TIMEOUT_MS} ms`)
  }

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
    // A flat arena, a glass box with a stone floor, at a known spot: the bot's
    // surroundings may be rock, water, ice or a tree, where a mob is out of
    // sight, a target out of reach and a dug-out space floods.
    // 31 x 31 and 6 high: under 6000 blocks, well under the 32768 of one fill. Big enough to back off in.
    await rcon.exec(`fill ${arenaMin} ${floorMax} minecraft:stone`)
    await teleportBot(arenaX + 0.5, ARENA_Y, arenaZ + 0.5)
    await rcon.exec(`${atBot} fill ~-15 ~-1 ~-15 ~15 ~5 ~15 minecraft:glass hollow`)
    await rcon.exec(`${atBot} fill ~-15 ~-1 ~-15 ~15 ~-1 ~15 minecraft:stone`)
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
      const { status: httpStatus, body: retried } = await postRetryingBusy('/mine', { block: 'oak_log' })
      const retriedSeen = `retry ${httpStatus} ${short({ ok: retried.ok, error: retried.error, detail: retried.detail, block_removed: retried.block_removed, drops: retried.drops, picked_up: retried.picked_up })}`
      assert.equal(retried.ok, true, retriedSeen)
      assert.equal(retried.block_removed, true, retriedSeen)
      assert.equal(retried.drops.oak_log, 1, retriedSeen)
      assert.equal(retried.picked_up.oak_log, 1, retriedSeen)
      return `${seen}; ${retriedSeen}`
    })

    await resetBetweenChecks()
    await check('2: the bot backs off from a creeper and never fights it', async () => {
      const before = (await getStatus()).position
      const since = (await getStatus()).last_seq
      await rcon.exec(`${atBot} summon minecraft:creeper ~4 ~ ~`)

      // The glass may corner the bot, so the creeper's distance proves nothing: the bot must have moved.
      const backedOff = await pollStatus(since, CREEPER_FLEE_TIMEOUT_MS, (s) =>
        s.events.some((event) => event.type === 'fled' && event.reason === 'creeper') &&
        distance(before, s.position) >= CREEPER_MIN_MOVE_BLOCKS)
      const later = await getStatus(since)
      await killMobs() // before it explodes
      const seen = `bot moved ${distance(before, later.position).toFixed(1)}; events ${short(later.events)}`
      assert.ok(backedOff, `no fled (creeper) and ${CREEPER_MIN_MOVE_BLOCKS} blocks moved within ${CREEPER_FLEE_TIMEOUT_MS} ms; ${seen}`)
      const fought = later.events.find((event) => event.type === 'fight_start' && event.target === 'creeper')
      assert.equal(fought, undefined, `fought a creeper; ${seen}`)
      return seen
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

      const observed = await postRetryingBusy('/observe', {})
      assert.equal(observed.status, 200, `${firstSeen}; still ${observed.status} ${short(observed.body)} after retrying`)
      assert.equal(observed.body.ok, true, short(observed.body))

      const events = (await getStatus(since)).events
      const fightEnd = events.find((event) => event.type === 'fight_end')
      assert.ok(fightEnd, `no fight_end; events ${short(events)}`)
      return `${firstSeen}; /observe ok after retrying; fight_end ${short(fightEnd)}`
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

    await resetBetweenChecks()
    await check('5: a killed husk is not fought again', async () => {
      const since = (await getStatus()).last_seq
      await rcon.exec(`${atBot} summon minecraft:husk ~3 ~ ~`)
      const ended = await pollStatus(since, FIGHT_START_TIMEOUT_MS + BUSY_RETRY_TIMEOUT_MS, (s) => s.events.some((event) => event.type === 'fight_end'))
      assert.ok(ended, 'no fight_end')
      const killed = ended.events.find((event) => event.type === 'fight_end' && event.outcome === 'killed')
      assert.ok(killed, `the husk was not killed; events ${short(ended.events)}`)
      const first = ended.events.find((event) => event.type === 'fight_start')

      await sleep(NO_REFIGHT_WINDOW_MS)
      const events = (await getStatus(since)).events
      const refights = events.filter((event) => event.type === 'fight_start' && event.id === first.id && event.seq > killed.seq)
      assert.equal(refights.length, 0, `fought husk ${first.id} again: ${short(refights)}`)
      return `husk ${first.id} killed at seq ${killed.seq}, no new fight_start with it in ${NO_REFIGHT_WINDOW_MS} ms`
    })
  } catch (err) {
    failures++
    console.log(`FAIL setup: ${err.message}`)
  } finally {
    await killMobs().catch(() => {})
    // Back first: the bot's presence keeps the arena's chunks loaded for the fill.
    await teleportBot(start.x, start.y, start.z).catch(() => {})
    await rcon.exec(`fill ${arenaMin} ${arenaMax} minecraft:air`).catch(() => {})
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
