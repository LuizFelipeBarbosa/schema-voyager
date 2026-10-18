// Live v3 test of M3: hunting for food, kill goals and guard goals, typed in
// chat. A second bot, `Tester`, joins the server and types `!goal ...` /
// `!stop`; the checks read the run's goals.jsonl, life.jsonl and episode files
// and the bot's POST /status (events, follow). Contracts: contracts/bot_api.md
// "v3.3: /attack, mobs in /look, /follow" and v3.2 events; docs/v3-interfaces.md
// v3.3 (kill rules, check statuses) and v3.3i (kill and guard goals).
//
// Needs, already running (it starts nothing): the Minecraft server on easy with
// daylight off (SV2_DIFFICULTY=easy), VoyagerBot with BOT_OWNERS=Otopo,Tester,
// and a Python life in play mode with a live LLM writing runs/<run>
// (--mode play). Meant for instance b: SV2_MC_PORT 25567, SV2_RCON_PORT 25577,
// SV2_BOT_PORT 3210. Refuses the live demo's ports (25565, 25575, 3000).
// Environment variables are loaded from $SV2_HOME/.env without overriding the
// ones already set (RCON_PASSWORD is read from there).
//
// It builds a flat glass arena (34 x 14 inside, stone floor) at fixed
// coordinates high above the terrain, force-loads its chunks, teleports
// VoyagerBot and Tester onto it, gives the bot a stone sword, a furnace and
// coal, and gives Tester survival mode with resistance 5 and regeneration (so
// zombies attack it but it can't die). Mobs are summoned over RCON, adult and
// persistent; zombies are fire resistant. At the end (always) it removes the
// arena, kills the mobs, clears Tester's effects, teleports the bot back and
// disconnects Tester. Run it with nothing else queued in the life.
//
// Checks: 1 = "!goal get me 2 cooked beef" with 4 cows around (a kill step
// whose drops are in range, a smelt step, no mismatch, cooked_beef >= 2),
// 2 = "!goal kill 3 zombies" (done, kills >= 3, 3 distinct fight_end killed),
// 3 = "!goal guard me": A the bot follows Tester over 10 blocks, B it defends
// Tester from 3 zombies over 10 more, C it rejoins, D
// "!stop" cancels the goal and follow is null.
//
// Usage: node mc/bot/test/v3_hunt_live.js [--checks 1,2,3] [--run v3-m3-live]
//          (exit 0 = every selected check passed)
// Env: SV2_MC_PORT (25567), SV2_RCON_PORT, SV2_BOT_PORT, RCON_PASSWORD,
//      SV2_HOME (repo root), BOT_USERNAME (VoyagerBot).

const assert = require('node:assert/strict')
const fs = require('fs')
const path = require('path')
const mineflayer = require('mineflayer')
const { RconClient, health, loadEnv, post, sleep } = require('./v2_helpers')

const DEMO_MC_PORT = 25565
const TESTER_NAME = 'Tester'
const MESSAGE_SPACING_MS = 1500
const POLL_INTERVAL_MS = 1000
const SPAWN_TIMEOUT_MS = 60 * 1000
const TELEPORT_TIMEOUT_MS = 10 * 1000
const FIND_GOAL_TIMEOUT_MS = 30 * 1000
const RECORD_TIMEOUT_MS = 30 * 1000
const HUNT_TIMEOUT_MS = 6 * 60 * 1000
const KILL_TIMEOUT_MS = 4 * 60 * 1000
const FOLLOW_TIMEOUT_MS = 2 * 60 * 1000
const STOP_TIMEOUT_MS = 15 * 1000
const BORDER_MARGIN_BLOCKS = 20
const WALK_BLOCKS = 20
const WALK_TIMEOUT_MS = 90 * 1000
const WALK_SAMPLE_MS = 500
const REJOIN_BLOCKS = 5 // the bot is this close to Tester once it is free to follow
const REJOIN_TIMEOUT_MS = 20 * 1000
const DEFEND_TIMEOUT_MS = 60 * 1000
const FINISHED = ['done', 'failed', 'cancelled', 'unsupported']

// The arena: its inside is x 40..73, z 40..53, y 200..205, on a stone
// floor at y 199, in glass. Fixed coordinates, high above any terrain and well
// inside the world border (512 wide around the world spawn, near 0,0).
const ARENA_Y = 200
const INNER_MIN = { x: 40, z: 40 }
const INNER_MAX = { x: 73, z: 53 }
const ARENA_CENTER = '57 200 47'
const ARENA_MIN = `${INNER_MIN.x - 1} ${ARENA_Y - 1} ${INNER_MIN.z - 1}`
const ARENA_MAX = `${INNER_MAX.x + 1} ${ARENA_Y + 6} ${INNER_MAX.z + 1}`
const FLOOR_MAX = `${INNER_MAX.x + 1} ${ARENA_Y - 1} ${INNER_MAX.z + 1}`
const FORCELOAD_MIN = `${INNER_MIN.x - 1} ${INNER_MIN.z - 1}`
const FORCELOAD_MAX = `${INNER_MAX.x + 1} ${INNER_MAX.z + 1}`

const FIRE_RESISTANCE = '{id:"minecraft:fire_resistance",duration:1000000,amplifier:0b,show_particles:0b}'
const ADULT_ZOMBIE = `{IsBaby:0b,PersistenceRequired:1b,active_effects:[${FIRE_RESISTANCE}]}`
const ADULT_COW = '{Age:0,PersistenceRequired:1b}'

const short = (value) => JSON.stringify(value)

let failures = 0

function parseArgs (argv) {
  const options = { checks: [1, 2, 3], run: 'v3-m3-live' }
  for (let i = 0; i < argv.length; i += 2) {
    const value = argv[i + 1]
    if (value === undefined) throw new Error(`${argv[i]} needs a value`)
    if (argv[i] === '--checks') options.checks = value.split(',').map(Number)
    else if (argv[i] === '--run') options.run = value
    else throw new Error(`unknown option ${argv[i]}`)
  }
  return options
}

// Polls `read()` until it returns something truthy; returns that value, or
// null after `timeoutMs`.
async function waitFor (read, timeoutMs, intervalMs = POLL_INTERVAL_MS) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const value = await read()
    if (value) return value
    if (Date.now() > deadline) return null
    await sleep(intervalMs)
  }
}

// The parsed lines of a JSONL file (none if it doesn't exist yet), skipping a half-written last line.
function readJsonl (file) {
  let text = ''
  try { text = fs.readFileSync(file, 'utf8') } catch (err) { return [] }
  return text.split('\n').filter(Boolean).flatMap((line) => {
    try { return [JSON.parse(line)] } catch (err) { return [] }
  })
}

const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)

async function main () {
  const home = process.env.SV2_HOME || path.resolve(__dirname, '..', '..', '..')
  loadEnv(home)
  const mcPort = Number(process.env.SV2_MC_PORT || 25567)
  const voyagerName = process.env.BOT_USERNAME || 'VoyagerBot'
  const { checks, run } = parseArgs(process.argv.slice(2))
  if (mcPort === DEMO_MC_PORT) throw new Error(`SV2_MC_PORT=${mcPort} is the live demo's port; refusing`)
  const runDir = path.join(home, 'runs', run)
  const rcon = new RconClient() // refuses the live demo's RCON port
  const botStatus = await health() // refuses the live demo's bot port
  if (!botStatus.connected) throw new Error('the bot is not connected to its server')
  await rcon.connect()

  // The border is centred on the world spawn, near 0,0: every arena corner must be well inside it.
  const borderReply = await rcon.exec('worldborder get')
  const borderWidth = Number((borderReply.match(/[\d.]+/) || [])[0])
  const farthestCorner = Math.max(INNER_MIN.x - 1, INNER_MIN.z - 1, INNER_MAX.x + 1, INNER_MAX.z + 1)
  if (!(borderWidth > 0)) throw new Error(`could not read the world border from "${borderReply}"`)
  if (farthestCorner > borderWidth / 2 - BORDER_MARGIN_BLOCKS) {
    throw new Error(`the arena reaches ${farthestCorner} blocks from 0,0, within ${BORDER_MARGIN_BLOCKS} of the world border (${borderWidth} wide); refusing`)
  }

  // --- Bot status and its events ---

  const botEvents = [] // every /status event seen so far, in order
  let lastSeq = 0

  // POSTs /status (it takes no lock), keeping the new events; returns the status.
  async function pollStatus () {
    const { body } = await post('/status', { since: lastSeq })
    botEvents.push(...body.events)
    lastSeq = body.last_seq
    return body
  }

  // --- Run files ---

  // The latest line per goal id in goals.jsonl (goals.py: each line is a goal's full state), oldest id first.
  const readGoals = () => [...new Map(readJsonl(path.join(runDir, 'goals.jsonl')).map((goal) => [goal.id, goal])).values()]

  // Waits for a goal with this text whose id is not in `knownIds`.
  async function findNewGoal (text, knownIds) {
    const goal = await waitFor(() => readGoals().reverse().find((g) => g.text === text && !knownIds.has(g.id)), FIND_GOAL_TIMEOUT_MS)
    assert.ok(goal, `no goal with text "${text}" appeared in goals.jsonl within ${FIND_GOAL_TIMEOUT_MS / 1000} s`)
    return goal
  }

  // Waits for the goal to reach one of `statuses` (polling /status as it goes, to keep its events).
  async function waitForGoal (id, statuses, timeoutMs) {
    const goal = await waitFor(async () => {
      await pollStatus()
      const current = readGoals().find((g) => g.id === id)
      return current && (statuses.includes(current.status) || FINISHED.includes(current.status)) ? current : null
    }, timeoutMs)
    assert.ok(goal, `${id} not ${statuses.join(' or ')} within ${timeoutMs / 1000} s`)
    assert.ok(statuses.includes(goal.status), `${id} ${goal.status}: ${goal.reason}`)
    return goal
  }

  // The life's record of a finished goal in life.jsonl (written when the goal ends: life.py _save).
  async function waitForGoalRecord (id) {
    const record = await waitFor(() => readJsonl(path.join(runDir, 'life.jsonl')).reverse().find((r) => r.goal_id === id), RECORD_TIMEOUT_MS)
    assert.ok(record, `no life.jsonl record for ${id} within ${RECORD_TIMEOUT_MS / 1000} s`)
    return record
  }

  // The step events of the episodes holding this goal's `goal` event (episode.py _act_once writes them).
  function readGoalSteps (goalId) {
    const episodesDir = path.join(runDir, 'episodes')
    let files = []
    try { files = fs.readdirSync(episodesDir).filter((name) => name.endsWith('.jsonl')) } catch (err) { return [] }
    return files.flatMap((file) => {
      const events = readJsonl(path.join(episodesDir, file))
      return events.some((event) => event.kind === 'goal' && event.goal_id === goalId) ? events.filter((event) => event.kind === 'step') : []
    })
  }

  // --- Tester: sends chat, stands and walks in the arena ---

  const tester = mineflayer.createBot({
    host: '127.0.0.1',
    port: mcPort,
    username: TESTER_NAME,
    version: process.env.MC_VERSION || '1.21.1',
    auth: 'offline'
  })
  tester.on('error', (err) => console.log(`tester error: ${err.message}`))
  let testerDied = false
  tester.on('death', () => { testerDied = true })

  let lastSentAt = 0
  async function say (message) {
    await sleep(Math.max(0, lastSentAt + MESSAGE_SPACING_MS - Date.now()))
    lastSentAt = Date.now()
    tester.chat(message)
    console.log(`Tester says: ${message}`)
  }

  // Says `!goal <text>` and returns the new goal from goals.jsonl.
  async function sayGoal (text) {
    const knownIds = new Set(readGoals().map((goal) => goal.id))
    await say(`!goal ${text}`)
    return findNewGoal(text, knownIds)
  }

  // --- The arena ---

  // Runs `command` with the arena's centre as the position, so `distance=` and `~` mean the arena.
  const inArena = (command) => rcon.exec(`execute positioned ${ARENA_CENTER} run ${command}`)
  const atPlayer = (name, command) => rcon.exec(`execute at ${name} run ${command}`)
  const killMobs = () => inArena('kill @e[type=!player,distance=..40]')

  async function teleportBot (x, y, z) {
    await rcon.exec(`tp ${voyagerName} ${x} ${y} ${z}`)
    const arrived = await waitFor(async () => distance((await pollStatus()).position, { x, y, z }) <= 1.5, TELEPORT_TIMEOUT_MS, 250)
    if (!arrived) throw new Error(`${voyagerName} did not arrive at ${x} ${y} ${z} within ${TELEPORT_TIMEOUT_MS / 1000} s`)
  }

  async function teleportTester (x, y, z) {
    await rcon.exec(`tp ${TESTER_NAME} ${x} ${y} ${z}`)
    const arrived = await waitFor(() => tester.entity && distance(tester.entity.position, { x, y, z }) <= 1.5, TELEPORT_TIMEOUT_MS, 250)
    if (!arrived) throw new Error(`${TESTER_NAME} did not arrive at ${x} ${y} ${z} within ${TELEPORT_TIMEOUT_MS / 1000} s`)
  }

  // Puts the bot and Tester in the arena, mobs gone, the bot healed and fed to full, so a check starts clean.
  // `botDx` and `testerDx` are the x offsets from the arena's west edge. Throws if the bot dies meanwhile.
  async function resetBetween (botDx, testerDx) {
    const eventsFrom = botEvents.length
    await killMobs()
    await teleportBot(INNER_MIN.x + botDx, ARENA_Y, INNER_MIN.z + 7.5)
    await teleportTester(INNER_MIN.x + testerDx, ARENA_Y, INNER_MIN.z + 5.5)
    await rcon.exec(`effect give ${voyagerName} minecraft:instant_health 1 5 true`)
    await rcon.exec(`effect give ${voyagerName} minecraft:saturation 10 255 true`) // full, so auto-eat leaves the cooked beef alone
    await sleep(1500)
    await pollStatus()
    const death = botEvents.slice(eventsFrom).find((event) => event.type === 'death')
    if (death) throw new Error(`the bot died during setup: ${short(death)}`)
  }

  // --- Checks ---

  // Runs one check and prints PASS or FAIL with the evidence.
  async function check (name, runCheck) {
    try {
      console.log(`PASS ${name}: ${await runCheck()}`)
    } catch (err) {
      failures++
      console.log(`FAIL ${name}: ${err.message}`)
    }
  }

  async function check1 () {
    await resetBetween(10.5, 8.5)
    // No meat to smelt or eat instead of hunting: the planner would skip the kill.
    for (const meat of ['cooked_beef', 'beef', 'porkchop', 'chicken', 'mutton', 'cooked_porkchop', 'cooked_chicken', 'cooked_mutton']) {
      await rcon.exec(`clear ${voyagerName} minecraft:${meat}`)
    }
    const cows = ['~4 ~ ~3', '~-4 ~ ~3', '~5 ~ ~-3', '~-5 ~ ~-4']
    for (const offset of cows) await atPlayer(voyagerName, `summon minecraft:cow ${offset} ${ADULT_COW}`)

    const goal = await sayGoal('get me 2 cooked beef')
    await waitForGoal(goal.id, ['done'], HUNT_TIMEOUT_MS)

    const steps = readGoalSteps(goal.id)
    const killSteps = steps.filter((step) => step.action_obj && step.action_obj.kind === 'kill')
    const smeltSteps = steps.filter((step) => step.action_obj && step.action_obj.kind === 'smelt')
    const inRange = killSteps.filter((step) => step.check === 'stochastic' && step.check_detail === 'drops in range')
    const matched = smeltSteps.filter((step) => step.check === 'match')
    const mismatches = steps.filter((step) => step.check === 'mismatch')
    const evidence = [...inRange, ...matched, ...mismatches].map((step) => `  step ${step.index} ${step.action} check ${step.check} (${step.check_detail}) drops ${short(step.drops)} ok ${step.ok}`)
    const lastInventory = [...steps].reverse().map((step) => step.inventory).find(Boolean) || {}
    const cooked = lastInventory.cooked_beef || 0
    const summary = `${goal.id} done; ${steps.length} steps, ${killSteps.length} kill, ${smeltSteps.length} smelt; ${inRange.length} kill stochastic "drops in range", ${matched.length} smelt match, ${mismatches.length} mismatch; cooked_beef ${cooked}`
    if (evidence.length) console.log(`  evidence for ${goal.id}:\n${evidence.join('\n')}`)

    assert.ok(inRange.length >= 1, `no kill step with check stochastic and "drops in range"; ${summary}`)
    assert.ok(matched.length >= 1, `no smelt step that matched; ${summary}`)
    assert.equal(mismatches.length, 0, `${mismatches.length} step check(s) mismatch; ${summary}`)
    assert.ok(cooked >= 2, `cooked_beef below 2 in the last step's inventory; ${summary}`)
    return summary
  }

  async function check2 () {
    await resetBetween(10.5, 8.5)
    const eventsFrom = botEvents.length
    const zombies = ['~7 ~ ~', '~-5 ~ ~-4', '~4 ~ ~5']
    for (const offset of zombies) await atPlayer(voyagerName, `summon minecraft:zombie ${offset} ${ADULT_ZOMBIE}`)

    const goal = await sayGoal('kill 3 zombies')
    const done = await waitForGoal(goal.id, ['done'], KILL_TIMEOUT_MS)
    const record = await waitForGoalRecord(goal.id)

    const killed = botEvents.slice(eventsFrom).filter((event) => event.type === 'fight_end' && event.target === 'zombie' && event.outcome === 'killed')
    const killedIds = new Set(killed.map((event) => event.id))
    const summary = `${goal.id} ${done.status}, record kills ${record.kills}, fight_end killed zombie ids ${short([...killedIds])}`
    assert.ok(record.kills >= 3, `the record's kills is below 3; ${summary}`)
    assert.ok(killedIds.size >= 3, `fewer than 3 distinct fight_end killed zombie events; ${summary}`)
    return summary
  }

  async function check3 () {
    await resetBetween(8.5, 4.5)
    const eventsFrom = botEvents.length
    const goal = await sayGoal('guard me')
    const following = await waitFor(async () => (await pollStatus()).follow?.player === TESTER_NAME, FOLLOW_TIMEOUT_MS)
    assert.ok(following, `${goal.id}: /status.follow.player never became ${TESTER_NAME} within ${FOLLOW_TIMEOUT_MS / 1000} s`)

    const testerState = () => (testerDied || tester.health <= 0 ? 'DIED' : `alive (health ${tester.health})`)
    const eventsSince = (type) => botEvents.slice(eventsFrom).filter((event) => event.type === type)
    const walkedEvidence = []

    // Walks Tester `blocks` along +x, sampling the bot's follow.distance; returns the blocks walked and the max distance.
    async function walkTester (blocks) {
      const startX = tester.entity.position.x
      let maxDistance = 0
      const deadline = Date.now() + WALK_TIMEOUT_MS
      while (tester.entity.position.x - startX < blocks && Date.now() < deadline) {
        await tester.lookAt(tester.entity.position.offset(5, 1.6, 0), true)
        tester.setControlState('forward', true)
        await sleep(WALK_SAMPLE_MS)
        const { follow } = await pollStatus()
        maxDistance = Math.max(maxDistance, follow && follow.distance !== null ? follow.distance : Infinity)
      }
      tester.setControlState('forward', false)
      return { walked: tester.entity.position.x - startX, maxDistance }
    }

    // Waits until the bot is within REJOIN_BLOCKS of Tester; returns the last distance seen (null if it never was).
    async function waitForClose (timeoutMs) {
      let last = null
      const close = await waitFor(async () => {
        const { follow } = await pollStatus()
        last = follow ? follow.distance : null
        return last !== null && last <= REJOIN_BLOCKS
      }, timeoutMs, 500)
      return close ? last : null
    }

    // A: follows. Tester walks with no mobs around.
    const walkA = await walkTester(WALK_BLOCKS / 2)
    const closeA = await waitForClose(REJOIN_TIMEOUT_MS)
    walkedEvidence.push(walkA.walked)
    const partA = `A follows: walked ${walkA.walked.toFixed(1)}, max follow.distance ${walkA.maxDistance.toFixed(1)} (info), after the walk ${closeA === null ? `never <= ${REJOIN_BLOCKS}` : closeA.toFixed(1)}`
    console.log(`  ${partA}`)
    assert.ok(closeA !== null, partA)

    // B: defends. Zombies appear ahead of Tester, away from the bot behind it.
    for (const offset of ['~4 ~ ~1', '~4 ~ ~-2', '~5 ~ ~3']) await atPlayer(TESTER_NAME, `summon minecraft:zombie ${offset} ${ADULT_ZOMBIE}`)
    const walkBResult = await walkTester(WALK_BLOCKS / 2)
    const zombiesGone = await waitFor(async () => {
      await pollStatus()
      return !/passed/i.test(await inArena('execute if entity @e[type=zombie,distance=..40]'))
    }, DEFEND_TIMEOUT_MS)
    walkedEvidence.push(walkBResult.walked)
    const threats = eventsSince('threat').filter((event) => ['player_near', 'player_attacked'].includes(event.reason))
    const kills = eventsSince('fight_end').filter((event) => event.target === 'zombie' && event.outcome === 'killed')
    const partB = `B defends: walked ${walkBResult.walked.toFixed(1)}, threats ${short(threats.map((event) => event.reason))}, zombie fight_end killed ${kills.length}, zombies ${zombiesGone ? 'all gone' : 'left'}, Tester ${testerState()}`
    console.log(`  ${partB}`)
    assert.ok(threats.length >= 1, partB)
    assert.ok(zombiesGone, partB)
    assert.ok(!testerDied && tester.health > 0, partB)

    // C: rejoins once the fights are over.
    const closeC = await waitForClose(REJOIN_TIMEOUT_MS)
    const partC = `C rejoins: follow.distance ${closeC === null ? `never <= ${REJOIN_BLOCKS} within ${REJOIN_TIMEOUT_MS / 1000} s` : closeC.toFixed(1)}`
    console.log(`  ${partC}`)
    assert.ok(closeC !== null, partC)

    // D: !stop ends it.
    await say('!stop')
    const stopped = await waitFor(async () => {
      const { follow } = await pollStatus()
      return readGoals().find((g) => g.id === goal.id).status === 'cancelled' && follow === null
    }, STOP_TIMEOUT_MS, 500)
    const partD = `D !stop: ${goal.id} ${readGoals().find((g) => g.id === goal.id).status}, follow ${short((await pollStatus()).follow)}`
    console.log(`  ${partD}`)
    assert.ok(stopped, `${partD} (wanted cancelled and null within ${STOP_TIMEOUT_MS / 1000} s)`)
    const total = walkedEvidence.reduce((sum, blocks) => sum + blocks, 0)
    assert.ok(total >= WALK_BLOCKS - 2, `Tester walked only ${total.toFixed(1)} blocks in all`)
    return `${goal.id}: A, B, C, D all held; walked ${total.toFixed(1)} blocks in all`
  }

  // --- Run ---

  const start = (await pollStatus()).position
  const available = { 1: check1, 2: check2, 3: check3 }
  try {
    for (const number of checks) if (!available[number]) throw new Error(`unknown check ${number}`)
    const spawned = await new Promise((resolve) => {
      tester.once('spawn', () => resolve(true))
      setTimeout(() => resolve(false), SPAWN_TIMEOUT_MS)
    })
    if (!spawned) throw new Error(`${TESTER_NAME} did not spawn on port ${mcPort} within ${SPAWN_TIMEOUT_MS / 1000} s`)
    console.log(`${TESTER_NAME} spawned on port ${mcPort}; run ${run}, checks ${checks.join(',')}`)

    await rcon.exec(`gamemode survival ${TESTER_NAME}`)
    await rcon.exec(`effect give ${TESTER_NAME} minecraft:resistance 1000000 4 true`)
    await rcon.exec(`effect give ${TESTER_NAME} minecraft:regeneration 1000000 4 true`)
    await rcon.exec(`forceload add ${FORCELOAD_MIN} ${FORCELOAD_MAX}`)
    await sleep(3000) // the chunks load before the fill
    await rcon.exec(`fill ${ARENA_MIN} ${ARENA_MAX} minecraft:glass hollow`)
    await rcon.exec(`fill ${ARENA_MIN} ${FLOOR_MAX} minecraft:stone`)
    await rcon.exec(`give ${voyagerName} minecraft:stone_sword`)
    await rcon.exec(`give ${voyagerName} minecraft:furnace`)
    await rcon.exec(`give ${voyagerName} minecraft:coal 8`)

    for (const number of checks) {
      const names = { 1: '1: hunting: "get me 2 cooked beef" kills a cow and smelts its beef', 2: '2: "kill 3 zombies" is done with 3 kills', 3: '3: "guard me" defends Tester on a 20-block walk, "!stop" ends it' }
      await check(names[number], available[number])
    }
  } catch (err) {
    failures++
    console.log(`FAIL setup: ${err.message}`)
  } finally {
    tester.setControlState('forward', false)
    await say('!stop').catch(() => {}) // the goals left open
    await post('/follow/stop', {}).catch(() => {})
    await killMobs().catch(() => {})
    // Back first: the bot's presence keeps the arena's chunks loaded for the fill.
    await teleportBot(start.x, start.y, start.z).catch(() => {})
    await rcon.exec(`fill ${ARENA_MIN} ${ARENA_MAX} minecraft:air`).catch(() => {})
    await rcon.exec(`forceload remove ${FORCELOAD_MIN} ${FORCELOAD_MAX}`).catch(() => {})
    await rcon.exec(`effect clear ${TESTER_NAME}`).catch(() => {})
    tester.quit()
    await sleep(500)
    rcon.close()
  }
  console.log(failures === 0 ? 'ALL PASS' : `${failures} FAILED`)
  process.exit(failures === 0 ? 0 : 1)
}

main().catch((err) => {
  console.log(`aborted: ${err.message}`)
  process.exit(1)
})
