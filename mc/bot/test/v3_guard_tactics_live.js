// Live comparison of the guard's fight tactics against husks: the bare guard
// (tactics []) against DEFAULT_TACTICS (crit, shield), alternating fight by
// fight so that drift hits both sets equally. Contract: contracts/bot_api.md,
// "v3.6: duels", subsection "`POST /guard` takes `tactics`".
//
// Uses the one bot already running at SV2_BOT_PORT and RCON at SV2_RCON_PORT
// (password RCON_PASSWORD, bot name BOT_USERNAME); starts nothing. Refuses to
// run against the live demo's ports (3000, 25575). Environment variables are
// loaded from $SV2_HOME/.env without overriding the ones already set.
//
// Same arena as v3_guard_live.js (a glass box over a stone floor at y 200, over
// the bot's own x and z), difficulty easy, husks (they don't burn in daylight).
// At the end the world is put back: arena removed, bot teleported back, no
// mobs, peaceful, guard mode defend with tactics crit, shield.
//
// Usage: node mc/bot/test/v3_guard_tactics_live.js [--fights 10] [--out runs/<name>]
// Exit 0 when the run completed (whatever the verdict), 1 on a setup error.

const fs = require('node:fs')
const path = require('node:path')
const { RconClient, health, loadEnv, post, sleep } = require('./v2_helpers')

const POLL_INTERVAL_MS = 250
const FIGHT_START_TIMEOUT_MS = 8000
const FIGHT_END_TIMEOUT_MS = 30000
const BETWEEN_FIGHTS_MS = 1500
const ARENA_Y = 200 // the floor's altitude: above any terrain, below the 319 build limit
const ARENA_RADIUS = 15
const ARENA_HEIGHT = 7 // floor to ceiling: the glass box below spans ~-1 to ~5
const TELEPORT_TIMEOUT_MS = 5000
const ARRIVED_BLOCKS = 1
const HUSK_DISTANCE = 5
const SETS = [[], ['crit', 'shield']]
const MAX_DURATION_INCREASE = 0.2
const MAX_DAMAGE_INCREASE = 1.0

function parseArgs (argv) {
  const args = { fights: 10, out: path.join('runs', `v3-guard-tactics-${new Date().toISOString().replace(/[:.]/g, '-')}`) }
  for (let i = 0; i < argv.length; i += 2) {
    if (argv[i] === '--fights') args.fights = Number(argv[i + 1])
    else if (argv[i] === '--out') args.out = argv[i + 1]
    else throw new Error(`unknown argument: ${argv[i]}`)
  }
  if (!Number.isInteger(args.fights) || args.fights < 1) throw new Error('--fights must be a positive integer')
  return args
}

async function getStatus (since = 0) {
  const { body } = await post('/status', { since })
  return body
}

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

const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)
const mean = (values) => values.reduce((sum, value) => sum + value, 0) / values.length
const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b)
  const middle = sorted.length >> 1
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
}

// The table's numbers for one set's fights, and the verdict's inputs.
function summarize (fights) {
  const ended = fights.filter((fight) => fight.outcome !== 'no_fight' && fight.outcome !== 'timeout')
  const others = {}
  for (const fight of fights) if (fight.outcome !== 'killed') others[fight.outcome] = (others[fight.outcome] || 0) + 1
  return {
    fights: fights.length,
    killed: fights.filter((fight) => fight.outcome === 'killed').length,
    others,
    meanDuration: ended.length ? mean(ended.map((fight) => fight.duration_s)) : NaN,
    medianDuration: ended.length ? median(ended.map((fight) => fight.duration_s)) : NaN,
    meanDamage: ended.length ? mean(ended.map((fight) => fight.damage_taken)) : NaN,
    deaths: fights.filter((fight) => fight.outcome === 'died' || fight.alive === false).length
  }
}

async function main () {
  const { fights: fightsPerSet, out } = parseArgs(process.argv.slice(2))
  loadEnv()
  const username = process.env.BOT_USERNAME || 'VoyagerBot'
  const rcon = new RconClient() // refuses the live demo's RCON port
  const status = await health() // refuses the live demo's bot port
  if (!status.connected) throw new Error('the bot is not connected to its server')
  await rcon.connect()
  fs.mkdirSync(out, { recursive: true })

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
  const killMobs = () => rcon.exec(`${atBot} kill @e[type=!player,distance=..48]`)

  // Puts the bot and the arena back to a fresh start, then summons one husk; returns the event seq before it.
  const prepareFight = async () => {
    await rcon.exec(`clear ${username}`)
    await rcon.exec(`effect clear ${username}`)
    await rcon.exec(`item replace entity ${username} weapon.mainhand with minecraft:iron_sword`)
    await rcon.exec(`item replace entity ${username} weapon.offhand with minecraft:shield`)
    await rcon.exec(`effect give ${username} minecraft:instant_health 1 10 true`)
    await rcon.exec(`effect give ${username} minecraft:saturation 1 10 true`)
    await killMobs()
    await teleportBot(arenaX + 0.5, ARENA_Y, arenaZ + 0.5)
    const since = (await getStatus()).last_seq
    await rcon.exec(`${atBot} summon minecraft:husk ~${HUSK_DISTANCE} ~ ~ {PersistenceRequired:1b}`)
    return since
  }

  // One fight of the guard against a husk: the fight_end's numbers, or no_fight / timeout.
  const runFight = async (tactics, index) => {
    await post('/guard', { tactics })
    const since = await prepareFight()
    const record = { set: tactics.join(',') || '[]', index }
    const started = await pollStatus(since, FIGHT_START_TIMEOUT_MS, (s) => s.events.some((event) => event.type === 'fight_start'))
    if (!started) return { ...record, outcome: 'no_fight' }
    const ended = await pollStatus(since, FIGHT_END_TIMEOUT_MS, (s) => s.events.some((event) => event.type === 'fight_end'))
    if (!ended) return { ...record, outcome: 'timeout' }
    const fightEnd = ended.events.find((event) => event.type === 'fight_end')
    return {
      ...record,
      outcome: fightEnd.outcome,
      duration_s: fightEnd.duration_s,
      damage_taken: fightEnd.damage_taken,
      health_after: ended.health,
      alive: ended.alive
    }
  }

  const results = SETS.map(() => [])
  try {
    await rcon.exec('difficulty easy')
    await rcon.exec('time set day')
    // A flat arena, a glass box with a stone floor, as in v3_guard_live.js.
    await rcon.exec(`fill ${arenaMin} ${floorMax} minecraft:stone`)
    await teleportBot(arenaX + 0.5, ARENA_Y, arenaZ + 0.5)
    await rcon.exec(`${atBot} fill ~-15 ~-1 ~-15 ~15 ~5 ~15 minecraft:glass hollow`)
    await rcon.exec(`${atBot} fill ~-15 ~-1 ~-15 ~15 ~-1 ~15 minecraft:stone`)
    await post('/guard', { mode: 'defend' })

    for (let index = 0; index < fightsPerSet; index++) {
      for (const [setIndex, tactics] of SETS.entries()) { // alternating, so drift hits both sets equally
        const fight = await runFight(tactics, index)
        results[setIndex].push(fight)
        fs.appendFileSync(path.join(out, 'fights.jsonl'), `${JSON.stringify(fight)}\n`)
        console.log(JSON.stringify(fight))
        await sleep(BETWEEN_FIGHTS_MS)
      }
    }
  } finally {
    await killMobs().catch(() => {})
    // Back first: the bot's presence keeps the arena's chunks loaded for the fill.
    await teleportBot(start.x, start.y, start.z).catch(() => {})
    await rcon.exec(`fill ${arenaMin} ${arenaMax} minecraft:air`).catch(() => {})
    await rcon.exec('difficulty peaceful').catch(() => {})
    await post('/guard', { mode: 'defend', tactics: ['crit', 'shield'] }).catch(() => {})
    rcon.close()
  }

  const [bare, best] = results.map(summarize)
  const fixed = (value) => (Number.isNaN(value) ? 'n/a' : value.toFixed(2))
  console.log('\nset            fights  killed  deaths  mean_s  median_s  mean_dmg  other outcomes')
  for (const [setIndex, summary] of [bare, best].entries()) {
    console.log([
      `[${SETS[setIndex].join(',')}]`.padEnd(14),
      String(summary.fights).padEnd(7),
      String(summary.killed).padEnd(7),
      String(summary.deaths).padEnd(7),
      fixed(summary.meanDuration).padEnd(7),
      fixed(summary.medianDuration).padEnd(9),
      fixed(summary.meanDamage).padEnd(9),
      JSON.stringify(summary.others)
    ].join(' '))
  }
  const regression = best.meanDuration > bare.meanDuration * (1 + MAX_DURATION_INCREASE) ||
    best.meanDamage > bare.meanDamage + MAX_DAMAGE_INCREASE ||
    best.killed < bare.killed
  console.log(`regression: ${regression ? 'yes' : 'no'}`)
}

main().catch((err) => {
  console.log(`aborted: ${err.message}`)
  process.exit(1)
})
