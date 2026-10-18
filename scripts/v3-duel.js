// Duel runner (docs/v3-interfaces.md "v3.6 Duels"; routes in contracts/bot_api.md "v3.6: duels").
// Runs N duels between two tactic sets on a live instance, through POST /duel on both bots, and
// prints a win-rate table with a Wilson 95% interval and the keep verdict for set A.
//
// Run it through scripts/v3-duel.sh, which takes the instance lock and starts the second bot.
// Usage: node scripts/v3-duel.js --a <set> --b <set> [--n 40] [--timeout 60] [--out runs/<name>]
//        node scripts/v3-duel.js --vs <player> --a <set> [--n 3] [--timeout 120] [--out runs/<name>]
//        add --dry-run to print the plan and one reset without touching anything
// <set>: comma-separated tactic names; "baseline" or "" means [] (the baseline).
// Env: SV2_HOME, SV2_RCON_PORT, RCON_PASSWORD (or $SV2_HOME/.env), SV2_BOT_PORT, SV2_BOT2_PORT,
//      DUEL_BOT_NAME (VoyagerBot), DUEL_BOT2_NAME (VoyagerBot2).

const { execFileSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const { RconClient, loadEnv, sleep } = require('../mc/bot/test/v2_helpers')

const DEMO_BOT_PORTS = [3000, 3007, 8765]

// The arena: inner floor x 44..68, z 35..59 at y 199 (stone), inner air y 200..204, inside a glass box
// from (43,199,34) to (69,205,60). Far above the terrain and well inside the world border (near 0,0).
const BOX_MIN = { x: 43, y: 199, z: 34 }
const BOX_MAX = { x: 69, y: 205, z: 60 }
const FIGHT_Y = 200
const SPOTS = {
  west: { x: 51.5, z: 47.5, yaw: -90 }, // facing east
  east: { x: 61.5, z: 47.5, yaw: 90 } // facing west
}
const BORDER_MARGIN_BLOCKS = 20
const KIT = [
  ['armor.head', 'iron_helmet'], ['armor.chest', 'iron_chestplate'], ['armor.legs', 'iron_leggings'],
  ['armor.feet', 'iron_boots'], ['weapon.offhand', 'shield'], ['hotbar.0', 'iron_sword'], ['hotbar.1', 'golden_apple']
]
const GAMERULE_DEFAULTS = { naturalRegeneration: 'true', doImmediateRespawn: 'false' }
const GAMERULES_FOR_DUELS = { naturalRegeneration: 'false', keepInventory: 'true', doImmediateRespawn: 'true' }

const FORCELOAD_BOX = `${BOX_MIN.x} ${BOX_MIN.z} ${BOX_MAX.x} ${BOX_MAX.z}`
const FILL_BOX = `${BOX_MIN.x} ${BOX_MIN.y} ${BOX_MIN.z} ${BOX_MAX.x} ${BOX_MAX.y} ${BOX_MAX.z}`
const FILL_FLOOR = `${BOX_MIN.x} ${BOX_MIN.y} ${BOX_MIN.z} ${BOX_MAX.x} ${BOX_MIN.y} ${BOX_MAX.z}`
const LOAD_WAIT_MS = 3000
const SETTLE_MS = 1500
const RESET_CHECK_TIMEOUT_MS = 10000
const ALIVE_WAIT_MS = 15000
const DEATH_LOG_WAIT_MS = 1500
const RESET_ATTEMPTS = 3
const SPOT_TOLERANCE_BLOCKS = 1.5
const MAX_INVALID_IN_A_ROW = 5
const HTTP_MARGIN_MS = 30000
const MAX_DUEL_ID_LENGTH = 64

// --- Pure: planning, judging, statistics, the table ------------------------------------------

// "crit,wtap" → ["crit", "wtap"]; "baseline" and "" → [] (the baseline).
function parseTactics (text) {
  return text.split(',').map((name) => name.trim()).filter((name) => name && name !== 'baseline')
}

// Which bot (0 or 1) plays set A and which bot takes the west side. Sides swap every duel, the
// sets swap bots every 2 duels, so 4 duels cover every pairing. Against a person (vs) the bot always plays A.
function planDuel (i, vs = false) {
  return { aBot: vs ? 0 : Math.floor(i / 2) % 2, westBot: i % 2 }
}

const END_REASONS = ['died', 'both_died', 'killed', 'timeout']

// The bot's duel_end: the answer itself when ok, else the event in its events. Returns {end} or {problem}.
function findDuelEnd (answer, duelId) {
  if (!answer || answer.error) return { problem: `no answer (${answer && answer.error})` }
  const { status, body } = answer
  if (body.ok) {
    return body.duel_id === duelId ? { end: body } : { problem: `duel_id mismatch (${body.duel_id} for ${duelId})` }
  }
  const event = (body.events || []).find((candidate) => candidate.type === 'duel_end' && candidate.duel_id === duelId)
  if (event) return { end: event }
  return { problem: `no duel_end (status ${status}, ${body.error || 'unknown'})` }
}

// Judges one duel from the answers {status, body} (or {error}) of the bot playing set A and the bot
// playing set B; answerB is null when A fought a person (--vs), then only A's answer counts.
// diedA / diedB: the bot's event log holds a `death` (a double KO can leave a bot's own duel_end at
// `killed`). Returns {valid, result: 'A'|'B'|'draw'|null, reason, endA, endB}.
function judge (answerA, answerB, duelId, { diedA = false, diedB = false } = {}) {
  const a = findDuelEnd(answerA, duelId)
  const b = answerB === null ? { end: null } : findDuelEnd(answerB, duelId)
  const endA = a.end || null
  const endB = b.end || null
  const invalid = (reason) => ({ valid: false, result: null, reason, endA, endB })
  const valid = (result, reason) => ({ valid: true, result, reason, endA, endB })

  if (a.problem) return invalid(`A: ${a.problem}`)
  if (b.problem) return invalid(`B: ${b.problem}`)
  if (!END_REASONS.includes(endA.reason)) return invalid(`A ended with ${endA.reason}`)
  if (endB && !END_REASONS.includes(endB.reason)) return invalid(`B ended with ${endB.reason}`)

  const hasDied = (end, flag) => flag || end.reason === 'died' || end.reason === 'both_died'
  const aDied = hasDied(endA, diedA)

  if (endB === null) { // against a person
    if (endA.reason === 'both_died') return valid('draw', 'double KO')
    return aDied ? valid('B', 'the player won') : endA.reason === 'killed' ? valid('A', 'the bot won') : valid('draw', 'timeout')
  }
  const bDied = hasDied(endB, diedB)
  if (aDied && bDied) return valid('draw', 'double KO')
  // both_died says the opponent died too: an opponent that survived contradicts it.
  if (endA.reason === 'both_died' || endB.reason === 'both_died') {
    return invalid(`${endA.reason === 'both_died' ? 'A' : 'B'} said both_died, but the other bot did not die`)
  }
  if (aDied || bDied) {
    const [winner, other] = aDied ? ['B', endB] : ['A', endA]
    const note = other.reason === 'killed' ? '' : ` (${winner} did not report killed, it said ${other.reason})`
    return valid(winner, `${winner === 'A' ? 'B' : 'A'} died${note}`)
  }
  if (endA.reason === 'timeout' && endB.reason === 'timeout') return valid('draw', 'timeout')
  return invalid(`A said ${endA.reason} and B said ${endB.reason}, but neither died`)
}

// Wilson score interval for `successes` of `n` (successes may be fractional); [0, 1] when n is 0.
function wilson (successes, n, z = 1.96) {
  if (n === 0) return [0, 1]
  const p = successes / n
  const z2 = z * z
  const denominator = 1 + z2 / n
  const centre = (p + z2 / (2 * n)) / denominator
  const half = (z * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denominator
  return [Math.max(0, centre - half), Math.min(1, centre + half)]
}

function mean (values) {
  const numbers = values.filter((value) => typeof value === 'number')
  return numbers.length ? numbers.reduce((sum, value) => sum + value, 0) / numbers.length : null
}

// One set's row. `result` is 'A' or 'B' (the set's own letter); `ends` are the duel_end of its bot.
function setStats (label, records, letter, ends) {
  const wins = records.filter((record) => record.result === letter).length
  const draws = records.filter((record) => record.result === 'draw').length
  const duels = records.length
  const rate = duels ? (wins + draws / 2) / duels : null
  return {
    label,
    duels,
    wins,
    losses: duels - wins - draws,
    draws,
    rate,
    ci: wilson(wins + draws / 2, duels),
    duration: mean(ends.map((end) => end && end.duration)),
    swings: mean(ends.map((end) => end && end.swings)),
    hits: mean(ends.map((end) => end && end.hits)),
    crits: mean(ends.map((end) => end && end.crits)),
    dealt: mean(ends.map((end) => end && end.damage_dealt)),
    taken: mean(ends.map((end) => end && end.damage_taken))
  }
}

// Records: {plan, valid, result, endA, endB}, one per attempt. Only valid ones count as duels.
// names: {a, b, out, timeout, botNames} label the table.
function summarize (records, { a, b, out = '', timeout = 60, botNames = ['VoyagerBot', 'VoyagerBot2'] }) {
  const duels = records.filter((record) => record.valid)
  const labelA = a === b ? `${a} (A)` : a
  const labelB = a === b ? `${b} (B)` : b
  const aWinsWhere = (select, value) => {
    const where = duels.filter((record) => select(record.plan) === value)
    return { wins: where.filter((record) => record.result === 'A').length, duels: where.length }
  }
  return {
    out,
    timeout,
    duels: duels.length,
    invalid: records.length - duels.length,
    a: setStats(labelA, duels, 'A', duels.map((record) => record.endA)),
    b: setStats(labelB, duels, 'B', duels.map((record) => record.endB)),
    aByBot: botNames.map((name, bot) => ({ name, ...aWinsWhere((plan) => plan.aBot, bot) })),
    aBySide: ['west', 'east'].map((side, bot) => ({ name: side, ...aWinsWhere((plan) => (plan.aBot === plan.westBot ? 0 : 1), bot) }))
  }
}

// Keep rule for set A: a win rate of minRate or more over at least minDuels duels.
function verdict (summary, { minDuels = 40, minRate = 0.6 } = {}) {
  const { duels, rate } = summary.a
  const percent = `${(rate * 100).toFixed(1)}%`
  if (duels < minDuels) return { keep: false, why: `${duels} duels < ${minDuels}` }
  if (rate < minRate - 1e-9) return { keep: false, why: `${duels} duels >= ${minDuels}, win rate ${percent} < ${minRate * 100}%` }
  return { keep: true, why: `${duels} duels >= ${minDuels}, win rate ${percent} >= ${minRate * 100}%` }
}

function formatTable (summary) {
  const fixed = (value, digits = 1) => (value === null ? '-' : value.toFixed(digits))
  const percent = (value) => (value === null ? '-' : `${(value * 100).toFixed(1)}%`)
  const nameWidth = Math.max(8, summary.a.label.length, summary.b.label.length) + 2
  const row = (cells) => cells.map(([text, width], index) => (index === 0 ? text.padEnd(width) : text.padStart(width))).join('')
  const setRow = (s) => row([
    [s.label, nameWidth], [String(s.duels), 5], [String(s.wins), 6], [String(s.losses), 8], [String(s.draws), 7],
    [percent(s.rate), 10], [`[${(s.ci[0] * 100).toFixed(1)}, ${(s.ci[1] * 100).toFixed(1)}]`, 16],
    [s.duration === null ? '-' : `${s.duration.toFixed(1)} s`, 10], [fixed(s.swings), 8], [fixed(s.hits), 6],
    [fixed(s.crits), 7], [fixed(s.dealt), 7], [fixed(s.taken), 7]
  ])
  const header = row([
    ['set', nameWidth], ['duels', 5], ['wins', 6], ['losses', 8], ['draws', 7], ['win rate', 10], ['95% CI', 16],
    ['duration', 10], ['swings', 8], ['hits', 6], ['crits', 7], ['dealt', 7], ['taken', 7]
  ])
  const split = (parts) => parts.map((part) => `${part.name} ${part.wins}/${part.duels}`).join(', ')
  const { keep, why } = verdict(summary)
  return [
    `v3-duel ${summary.out}: A = ${summary.a.label} vs B = ${summary.b.label}, ${summary.duels} duels (${summary.invalid} invalid, re-run), timeout ${summary.timeout} s`,
    header,
    setRow(summary.a),
    setRow(summary.b),
    `A's wins by bot: ${split(summary.aByBot)}; by side: ${split(summary.aBySide)}`,
    `verdict: ${keep ? 'KEEP' : 'DROP'} ${summary.a.label} (${why})`
  ].join('\n')
}

// --- Pure: RCON commands -----------------------------------------------------------------------

// Everything one bot (or player) needs for a duel: the same kit and a spot to stand on.
function resetCommands (name, side) {
  const spot = SPOTS[side]
  return [
    `clear ${name}`,
    `effect clear ${name}`,
    ...KIT.map(([slot, item]) => `item replace entity ${name} ${slot} with minecraft:${item}`),
    `effect give ${name} minecraft:instant_health 1 10 true`,
    `effect give ${name} minecraft:saturation 1 10 true`,
    `tp ${name} ${spot.x} ${FIGHT_Y} ${spot.z} ${spot.yaw} 0`
  ]
}

// Removes what the last duel left lying around inside the arena.
function sweepCommands () {
  const volume = `x=${BOX_MIN.x},y=${BOX_MIN.y},z=${BOX_MIN.z},dx=${BOX_MAX.x - BOX_MIN.x},dy=${BOX_MAX.y - BOX_MIN.y},dz=${BOX_MAX.z - BOX_MIN.z}`
  return ['item', 'arrow'].map((type) => `kill @e[type=minecraft:${type},${volume}]`)
}

// --- Command line ------------------------------------------------------------------------------

const USAGE = `Usage: node scripts/v3-duel.js --a <set> --b <set> [--n 40] [--timeout 60] [--out runs/<name>] [--dry-run]
       node scripts/v3-duel.js --vs <player> --a <set> [--n 3] [--timeout 120] [--out runs/<name>] [--dry-run]
<set>: comma-separated tactic names; "baseline" or "" means [] (the baseline).
Normally run through scripts/v3-duel.sh, which starts the second bot and takes the instance lock.`

function parseArgs (argv) {
  const options = { a: 'baseline', b: 'baseline', vs: null, n: null, timeout: null, out: null, dryRun: false, help: false }
  const withValue = { '--a': 'a', '--b': 'b', '--vs': 'vs', '--n': 'n', '--timeout': 'timeout', '--out': 'out' }
  for (let i = 0; i < argv.length; i++) {
    const key = withValue[argv[i]]
    if (argv[i] === '--dry-run') options.dryRun = true
    else if (argv[i] === '--help' || argv[i] === '-h') options.help = true
    else if (key) {
      if (argv[i + 1] === undefined) throw new Error(`${argv[i]} needs a value`)
      options[key] = argv[++i]
    } else throw new Error(`unknown option ${argv[i]}`)
  }
  options.n = options.n === null ? (options.vs ? 3 : 40) : Number(options.n)
  options.timeout = options.timeout === null ? (options.vs ? 120 : 60) : Number(options.timeout)
  if (!Number.isInteger(options.n) || options.n < 1) throw new Error('--n must be a positive integer')
  if (!(options.timeout >= 5 && options.timeout <= 300)) throw new Error('--timeout must be 5 to 300')
  return options
}

function timestamp () {
  return new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15)
}

// What each duel will do, one line per duel.
function describePlan (n, botNames, vs) {
  return Array.from({ length: n }, (_, i) => {
    const plan = planDuel(i, Boolean(vs))
    const sides = [plan.westBot === 0 ? 'west' : 'east', plan.westBot === 0 ? 'east' : 'west']
    const names = vs ? [botNames[0], vs] : botNames
    return `duel ${i}: ${names[plan.aBot]} plays A on the ${sides[plan.aBot]}, ${names[1 - plan.aBot]} plays B on the ${sides[1 - plan.aBot]}`
  })
}

// --- Live: HTTP, RCON, the arena, the series ---------------------------------------------------

// POST to a bot; never throws: {status, body} or {error}.
async function postBot (port, route, body, timeoutMs) {
  try {
    const res = await fetch(`http://127.0.0.1:${port}${route}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs)
    })
    return { status: res.status, body: await res.json() }
  } catch (err) {
    return { error: err.message }
  }
}

const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)

// Reads the world setting a command prints, e.g. "Gamerule keepInventory is currently set to: true".
async function readSetting (rcon, command, pattern, fallback) {
  const match = (await rcon.exec(command)).match(pattern)
  return match ? match[1].toLowerCase() : fallback
}

async function runSeries (options, ports, botNames, home) {
  const { vs } = options
  const outDir = path.resolve(home, options.out || path.join('runs', `v3-duel-${timestamp()}`))
  const outName = path.basename(outDir)
  const setA = parseTactics(options.a)
  const setB = parseTactics(options.b)
  const bots = vs ? [{ name: botNames[0], port: ports[0] }] : botNames.map((name, bot) => ({ name, port: ports[bot] }))
  const players = vs ? [botNames[0], vs] : botNames // who gets reset; the bots are the ones that fight

  fs.mkdirSync(outDir, { recursive: true })
  fs.writeFileSync(path.join(outDir, 'command.txt'), `node scripts/v3-duel.js ${process.argv.slice(2).join(' ')}\n`)
  fs.writeFileSync(path.join(outDir, 'COMMIT'), `${execFileSync('git', ['-C', home, 'rev-parse', 'HEAD'], { encoding: 'utf8' })}`)

  const rcon = new RconClient({ port: Number(process.env.SV2_RCON_PORT), password: process.env.RCON_PASSWORD })
  await rcon.connect()
  const saved = { gamerules: null, difficulty: null, bots: [], arena: false, forceloaded: false }
  let stopRequested = false
  const onSignal = () => {
    if (stopRequested) process.exit(130)
    stopRequested = true
    console.log('stopping: finishing the teardown (press Ctrl-C again to quit at once)')
    bots.forEach((bot) => postBot(bot.port, '/duel/stop', {}, 5000))
  }
  process.on('SIGINT', onSignal)
  process.on('SIGTERM', onSignal)

  const botStatus = async (bot) => (await postBot(bot.port, '/status', {}, 10000)).body

  // Waits until the bot is alive, at full health and on its spot (a dead bot respawns at once).
  async function atSpot (bot, side) {
    const spot = { x: SPOTS[side].x, y: FIGHT_Y, z: SPOTS[side].z }
    const deadline = Date.now() + RESET_CHECK_TIMEOUT_MS
    while (Date.now() < deadline) {
      const status = await botStatus(bot)
      if (status && status.alive && status.health >= 20 && status.position && distance(status.position, spot) <= SPOT_TOLERANCE_BLOCKS) return true
      await sleep(500)
    }
    return false
  }

  // Waits until every bot is alive: a bot still respawning after a death can't be reset.
  async function allAlive () {
    const deadline = Date.now() + ALIVE_WAIT_MS
    while (Date.now() < deadline) {
      const statuses = await Promise.all(bots.map(botStatus))
      if (statuses.every((status) => status && status.alive)) return true
      await sleep(500)
    }
    return false
  }

  async function resetDuel (plan) {
    for (let attempt = 1; attempt <= RESET_ATTEMPTS; attempt++) {
      if (!await allAlive()) {
        console.log(`reset failed, a bot is not alive (attempt ${attempt} of ${RESET_ATTEMPTS})`)
        continue
      }
      for (const command of sweepCommands()) await rcon.exec(command)
      for (const [index, name] of players.entries()) {
        const side = plan.westBot === index ? 'west' : 'east'
        for (const command of resetCommands(name, side)) await rcon.exec(command)
      }
      await sleep(SETTLE_MS)
      const ready = await Promise.all(bots.map((bot, index) => atSpot(bot, plan.westBot === index ? 'west' : 'east')))
      if (ready.every(Boolean)) return
      console.log(`reset failed (attempt ${attempt} of ${RESET_ATTEMPTS})`)
    }
    throw new Error(`the reset failed ${RESET_ATTEMPTS} times in a row; aborting the series`)
  }

  // Sends /duel to the bot playing each set at the same moment; returns the answers and, from the
  // bots' event logs, whether each set's bot died, both by set (A, B).
  async function fightDuel (plan, duelId) {
    const sets = [setA, setB]
    const botFor = [plan.aBot, 1 - plan.aBot]
    const fight = (set) => {
      const bot = bots[botFor[set]]
      const opponent = vs || botNames[1 - botFor[set]]
      return postBot(bot.port, '/duel', { player: opponent, tactics: sets[set], timeout_s: options.timeout, duel_id: duelId }, options.timeout * 1000 + HTTP_MARGIN_MS)
    }
    const lastSeqs = await Promise.all(bots.map(async (bot) => (await botStatus(bot))?.last_seq))
    const answers = await Promise.all(vs ? [fight(0)] : [fight(0), fight(1)])
    await Promise.all(bots.map((bot) => postBot(bot.port, '/duel/stop', {}, 5000)))
    await sleep(DEATH_LOG_WAIT_MS) // a bot's own death can come after its duel ended
    const died = await Promise.all(answers.map(async (_, set) => {
      const bot = bots[botFor[set]]
      const since = lastSeqs[botFor[set]]
      if (since === undefined) return false
      const events = (await postBot(bot.port, '/status', { since }, 10000)).body?.events ?? []
      return events.some((event) => event.type === 'death')
    }))
    return { answers, died }
  }

  const records = []
  const jsonl = path.join(outDir, 'duels.jsonl')
  try {
    // Arena: refuse unless it is well inside the world border (centred near 0,0).
    const borderReply = await rcon.exec('worldborder get')
    const width = Number((borderReply.match(/[\d.]+/) || [])[0])
    const farthest = Math.max(...[BOX_MIN.x, BOX_MIN.z, BOX_MAX.x, BOX_MAX.z].map(Math.abs))
    if (!(width > 0)) throw new Error(`could not read the world border from "${borderReply}"`)
    if (farthest > width / 2 - BORDER_MARGIN_BLOCKS) throw new Error(`the arena reaches ${farthest} blocks from 0,0, within ${BORDER_MARGIN_BLOCKS} of the world border (${width} wide); refusing`)

    // Series setup, recorded as it goes so the teardown puts back what was changed.
    saved.gamerules = {}
    for (const rule of Object.keys(GAMERULES_FOR_DUELS)) {
      saved.gamerules[rule] = await readSetting(rcon, `gamerule ${rule}`, /:\s*(true|false)\s*$/i, GAMERULE_DEFAULTS[rule] || 'false')
    }
    saved.difficulty = await readSetting(rcon, 'difficulty', /difficulty is (\w+)/i, 'peaceful')
    for (const bot of bots) {
      const status = await botStatus(bot)
      saved.bots.push({ bot, position: status.position, guard: status.guard && status.guard.mode })
      await postBot(bot.port, '/guard', { mode: 'off' }, 10000)
    }
    await rcon.exec(`forceload add ${FORCELOAD_BOX}`)
    saved.forceloaded = true
    await sleep(LOAD_WAIT_MS)
    saved.arena = true
    await rcon.exec(`fill ${FILL_BOX} minecraft:glass hollow`)
    await rcon.exec(`fill ${FILL_FLOOR} minecraft:stone`)
    for (const [rule, value] of Object.entries(GAMERULES_FOR_DUELS)) await rcon.exec(`gamerule ${rule} ${value}`)
    await rcon.exec('difficulty peaceful')

    for (let i = 0, invalidInARow = 0, retries = 0; i < options.n && !stopRequested;) {
      const plan = planDuel(i, Boolean(vs))
      const duelId = `${outName.slice(-40)}-${i}${retries ? `-r${retries}` : ''}`.slice(0, MAX_DUEL_ID_LENGTH)
      await resetDuel(plan)
      const { answers: [answerA, answerB = null], died: [diedA, diedB = false] } = await fightDuel(plan, duelId)
      const outcome = judge(answerA, answerB, duelId, { diedA, diedB })
      const record = { i, duel_id: duelId, plan, diedA, diedB, ...outcome }
      records.push(record)
      fs.appendFileSync(jsonl, `${JSON.stringify(record)}\n`)
      console.log(`${duelId}: ${outcome.valid ? `${outcome.result} (${outcome.reason})` : `INVALID, ${outcome.reason}`}`)
      if (outcome.valid) { i++; invalidInARow = 0; retries = 0 } else {
        retries++
        if (++invalidInARow >= MAX_INVALID_IN_A_ROW) throw new Error(`${MAX_INVALID_IN_A_ROW} invalid duels in a row; aborting the series`)
      }
    }
  } finally {
    await teardown(rcon, saved)
    rcon.close()
  }

  const summary = summarize(records, { a: setA.join(',') || 'baseline', b: vs ? `player ${vs}` : setB.join(',') || 'baseline', out: path.relative(home, outDir), timeout: options.timeout, botNames })
  const table = formatTable(summary)
  fs.writeFileSync(path.join(outDir, 'summary.json'), `${JSON.stringify({ summary, verdict: verdict(summary) }, null, 2)}\n`)
  fs.writeFileSync(path.join(outDir, 'table.txt'), `${table}\n`)
  console.log(table)
}

// Puts everything back, step by step: one failing step doesn't stop the others.
async function teardown (rcon, saved) {
  const steps = [
    ...saved.bots.filter((entry) => entry.position).map((entry) => () => rcon.exec(`tp ${entry.bot.name} ${entry.position.x} ${entry.position.y} ${entry.position.z}`)),
    ...(saved.arena ? [() => rcon.exec(`fill ${FILL_BOX} minecraft:air`)] : []),
    ...(saved.forceloaded ? [() => rcon.exec(`forceload remove ${FORCELOAD_BOX}`)] : []),
    ...Object.entries(saved.gamerules || {}).map(([rule, value]) => () => rcon.exec(`gamerule ${rule} ${value}`)),
    ...(saved.difficulty ? [() => rcon.exec(`difficulty ${saved.difficulty}`)] : []),
    ...saved.bots.filter((entry) => entry.guard).map((entry) => () => postBot(entry.bot.port, '/guard', { mode: entry.guard }, 10000))
  ]
  for (const step of steps) {
    try { await step() } catch (err) { console.log(`teardown step failed: ${err.message}`) }
  }
}

async function main () {
  const options = parseArgs(process.argv.slice(2))
  if (options.help) return console.log(USAGE)
  const botNames = [process.env.DUEL_BOT_NAME || 'VoyagerBot', process.env.DUEL_BOT2_NAME || 'VoyagerBot2']

  if (options.dryRun) {
    console.log(describePlan(options.n, botNames, options.vs).join('\n'))
    console.log('\none reset (the west side, then the east side):')
    console.log([...sweepCommands(), ...resetCommands(botNames[0], 'west'), ...resetCommands(options.vs || botNames[1], 'east')].join('\n'))
    return
  }

  const home = process.env.SV2_HOME || path.resolve(__dirname, '..')
  loadEnv(home)
  const ports = [Number(process.env.SV2_BOT_PORT), Number(process.env.SV2_BOT2_PORT)]
  for (const port of options.vs ? ports.slice(0, 1) : ports) {
    if (!(port > 0)) throw new Error('SV2_BOT_PORT (and SV2_BOT2_PORT without --vs) must be set')
    if (DEMO_BOT_PORTS.includes(port)) throw new Error(`bot port ${port} belongs to the live demo; refusing`)
  }
  await runSeries(options, ports, botNames, home)
}

module.exports = { parseTactics, planDuel, judge, wilson, summarize, verdict, formatTable, resetCommands, sweepCommands, parseArgs }

if (require.main === module) {
  main().catch((err) => {
    console.error(`error: ${err.message}`)
    process.exit(1)
  })
}
