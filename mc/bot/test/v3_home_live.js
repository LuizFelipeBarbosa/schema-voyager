// Live v3 test of M5: the bot's home, its chest and composite goals, typed in
// chat. A second bot, `Tester`, joins the server and types `!goal ...`; the
// checks read the run's goals.jsonl, life.jsonl, life_events.jsonl and episode
// files, the bot's POST /observe, and the chest's real contents over RCON.
// Contracts: docs/v3-interfaces.md v3.5a-v3.5f and contracts/bot_api.md "v3.5:
// chests and home stations".
//
// Needs, already running (it starts nothing): the Minecraft server (peaceful,
// daylight off), VoyagerBot with BOT_OWNERS=Otopo,Tester, and a Python life in
// play mode with a live LLM writing runs/<run> (--mode play), resumed from a
// world with two houses the bot built: the first one is its home, and the
// life's `home_set` event (the --resume migration) says where. Meant for
// instance a: SV2_MC_PORT 25566, SV2_RCON_PORT 25576, SV2_BOT_PORT 3200.
// Refuses the live demo's ports (25565, 25575, 3000). Environment variables are
// loaded from $SV2_HOME/.env without overriding the ones already set.
//
// It teleports the bot to the yard south of the home's door and Tester next to
// it, and gives them what each check needs. At the end (always) it kills the
// cows it summoned, removes the forceload, clears Tester's effects and
// disconnects Tester.
//
// Checks: 1 = "!goal get me 4 sticks" with a chest, 128 coal and a full
// inventory: the bot places the chest in the yard (`home_placed`), stores junk
// and coal in it (`stored`, nothing tossed) and the chest really holds them,
// 2 = "!goal get me 8 torches" with no coal held: a `withdraw` step takes the
// coal out of the chest, 3 = "!goal get ready for night": a composite goal whose
// children run in order, each checked, and whose parent ends done.
//
// Usage: node mc/bot/test/v3_home_live.js [--checks 1,2,3] [--run v3-m5-live] [--south 4]
//          (exit 0 = every selected check passed; --south: blocks south of the door the bot stands)
// Env: SV2_MC_PORT (25566), SV2_RCON_PORT, SV2_BOT_PORT, RCON_PASSWORD,
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
const HOME_SET_TIMEOUT_MS = 60 * 1000
const TELEPORT_TIMEOUT_MS = 15 * 1000
const FIND_GOAL_TIMEOUT_MS = 30 * 1000
const RECORD_TIMEOUT_MS = 30 * 1000
const STORE_TIMEOUT_MS = 6 * 60 * 1000
const TORCH_TIMEOUT_MS = 6 * 60 * 1000
const COMPOSITE_TIMEOUT_MS = 25 * 60 * 1000
const FINISHED = ['done', 'failed', 'cancelled', 'unsupported']

const MAX_FREE_SLOTS = 2 // the bot stores when free_slots < 4; this leaves no doubt
const COW_TAG = 'm5cow'
const ADULT_COW = `{Age:0,PersistenceRequired:1b,Tags:["${COW_TAG}"]}`

// Items no rule names, by stack size; the check gives at most MAX_JUNK_STACKS of them.
const MAX_JUNK_STACKS = 18
const JUNK = {
  poppy: 64, dandelion: 64, oak_sapling: 64, birch_sapling: 64, wheat_seeds: 64, bone: 64, string: 64, feather: 64,
  spider_eye: 64, flint: 64, kelp: 64, cactus: 64, azure_bluet: 64, oxeye_daisy: 64, cornflower: 64, allium: 64,
  red_tulip: 64, pink_tulip: 64, white_tulip: 64
}
// Food stacks fill the rest of the inventory: the bot never stores or tosses food.
const FOOD = ['bread', 'cooked_beef', 'cooked_porkchop', 'cooked_mutton', 'cooked_chicken', 'baked_potato', 'apple', 'carrot', 'cooked_cod', 'cooked_salmon', 'pumpkin_pie', 'golden_carrot']

const short = (value) => JSON.stringify(value)

let failures = 0

function parseArgs (argv) {
  const options = { checks: [1, 2, 3], run: 'v3-m5-live', south: 4 }
  for (let i = 0; i < argv.length; i += 2) {
    const value = argv[i + 1]
    if (value === undefined) throw new Error(`${argv[i]} needs a value`)
    if (argv[i] === '--checks') options.checks = value.split(',').map(Number)
    else if (argv[i] === '--run') options.run = value
    else if (argv[i] === '--south') options.south = Number(value)
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
  const sv2Home = process.env.SV2_HOME || path.resolve(__dirname, '..', '..', '..')
  loadEnv(sv2Home)
  const mcPort = Number(process.env.SV2_MC_PORT || 25566)
  const voyagerName = process.env.BOT_USERNAME || 'VoyagerBot'
  const { checks, run, south } = parseArgs(process.argv.slice(2))
  if (mcPort === DEMO_MC_PORT) throw new Error(`SV2_MC_PORT=${mcPort} is the live demo's port; refusing`)
  const runDir = path.join(sv2Home, 'runs', run)
  const rcon = new RconClient() // refuses the live demo's RCON port
  const botStatus = await health() // refuses the live demo's bot port
  if (!botStatus.connected) throw new Error('the bot is not connected to its server')
  await rcon.connect()

  // --- Run files ---

  const readGoalLines = () => readJsonl(path.join(runDir, 'goals.jsonl'))
  // The latest line per goal id in goals.jsonl (each line is a goal's full state), oldest id first.
  const readGoals = () => [...new Map(readGoalLines().map((goal) => [goal.id, goal])).values()]
  const readLifeEvents = () => readJsonl(path.join(runDir, 'life_events.jsonl'))
  const readRecords = () => readJsonl(path.join(runDir, 'life.jsonl'))

  // Waits for a goal with this text whose id is not in `knownIds`.
  async function findNewGoal (text, knownIds) {
    const goal = await waitFor(() => readGoals().reverse().find((g) => g.text === text && !knownIds.has(g.id)), FIND_GOAL_TIMEOUT_MS)
    assert.ok(goal, `no goal with text "${text}" appeared in goals.jsonl within ${FIND_GOAL_TIMEOUT_MS / 1000} s`)
    return goal
  }

  // Waits for the goal to reach one of `statuses`; fails at any other end.
  async function waitForGoal (id, statuses, timeoutMs) {
    const goal = await waitFor(() => {
      const current = readGoals().find((g) => g.id === id)
      return current && (statuses.includes(current.status) || FINISHED.includes(current.status)) ? current : null
    }, timeoutMs)
    assert.ok(goal, `${id} not ${statuses.join(' or ')} within ${timeoutMs / 1000} s`)
    assert.ok(statuses.includes(goal.status), `${id} ${goal.status}: ${goal.reason}`)
    return goal
  }

  // The life's record of a finished goal in life.jsonl (written when the goal ends: life.py _save).
  async function waitForGoalRecord (id) {
    const record = await waitFor(() => readRecords().reverse().find((r) => r.goal_id === id), RECORD_TIMEOUT_MS)
    assert.ok(record, `no life.jsonl record for ${id} within ${RECORD_TIMEOUT_MS / 1000} s`)
    return record
  }

  // The events of the episodes holding this goal's `goal` event (episode.py writes them), in file order.
  function readGoalEpisodeEvents (goalId) {
    const episodesDir = path.join(runDir, 'episodes')
    let files = []
    try { files = fs.readdirSync(episodesDir).filter((name) => name.endsWith('.jsonl')) } catch (err) { return [] }
    return files.flatMap((file) => {
      const events = readJsonl(path.join(episodesDir, file))
      return events.some((event) => event.kind === 'goal' && event.goal_id === goalId) ? events : []
    })
  }

  // --- The bot and the chest, read directly ---

  async function observeBot () {
    const { body } = await post('/observe', {})
    return body
  }

  // The chest's real contents ({item: count}) from the server's block data.
  async function readChest ([x, y, z]) {
    const reply = await rcon.exec(`data get block ${x} ${y} ${z} Items`)
    assert.ok(/block data/.test(reply), `no chest data at ${x} ${y} ${z}: ${reply}`)
    const contents = {}
    for (const [, fields] of reply.matchAll(/\{([^{}]*)\}/g)) {
      const id = (fields.match(/id: "minecraft:(\w+)"/) || [])[1]
      const count = Number((fields.match(/count: (\d+)/) || [])[1])
      if (id) contents[id] = (contents[id] || 0) + count
    }
    return contents
  }

  // --- Tester: sends chat ---

  const tester = mineflayer.createBot({
    host: '127.0.0.1',
    port: mcPort,
    username: TESTER_NAME,
    version: process.env.MC_VERSION || '1.21.1',
    auth: 'offline'
  })
  tester.on('error', (err) => console.log(`tester error: ${err.message}`))

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

  // --- The home ---

  // The home the life migrated or set (its `home_set` event), and where the bot stands: --south blocks south of the
  // door's outside cell, with Tester next to it.
  const homeSet = await waitFor(() => readLifeEvents().find((event) => event.kind === 'home_set'), HOME_SET_TIMEOUT_MS)
  if (!homeSet) throw new Error(`no home_set event in ${runDir}/life_events.jsonl within ${HOME_SET_TIMEOUT_MS / 1000} s`)
  const { origin, size, door } = homeSet.home
  if (!door) throw new Error('the home has no door')
  const standX = door[0]
  const standZ = door[2] + south

  // Teleports the bot to the ground at standX, standZ and Tester next to it; returns the bot's position.
  async function standInYard () {
    await rcon.exec(`forceload add ${standX} ${standZ}`)
    await sleep(2000) // the chunk loads before the height is read
    const onGround = `execute positioned ${standX + 0.5} 200 ${standZ + 0.5} positioned over motion_blocking_no_leaves run`
    await rcon.exec(`${onGround} tp ${voyagerName} ~ ~ ~`)
    await rcon.exec(`${onGround} tp ${TESTER_NAME} ~2 ~ ~`)
    const arrived = await waitFor(async () => {
      const { position } = await observeBot()
      return distance(position, { x: standX + 0.5, y: position.y, z: standZ + 0.5 }) <= 1.5 ? position : null
    }, TELEPORT_TIMEOUT_MS, 500)
    if (!arrived) throw new Error(`${voyagerName} did not arrive at ${standX} ${standZ} within ${TELEPORT_TIMEOUT_MS / 1000} s`)
    await sleep(1500) // Tester lands too
    return arrived
  }

  // True when the cell is in the ring one block outside the house's footprint, outside the site box.
  function inRingOutside ([x, y, z]) {
    const insideFootprint = x >= origin[0] && x < origin[0] + size[0] && z >= origin[2] && z < origin[2] + size[2]
    const inRing = x >= origin[0] - 1 && x <= origin[0] + size[0] && z >= origin[2] - 1 && z <= origin[2] + size[2]
    return inRing && !insideFootprint
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

  // State the checks hand on: where the life put the chest.
  let chestPos = null

  async function check1 () {
    const position = await standInYard()
    console.log(`  bot at ${short(position)}; home origin ${short(origin)} size ${short(size)} door ${short(door)}`)

    // A chest, two stacks of coal, junk and food until the bot has 2 free slots or fewer.
    await rcon.exec(`give ${voyagerName} minecraft:chest 1`)
    await rcon.exec(`give ${voyagerName} minecraft:coal 128`)
    const junkNames = Object.keys(JUNK).slice(0, MAX_JUNK_STACKS)
    for (const name of junkNames) await rcon.exec(`give ${voyagerName} minecraft:${name} ${JUNK[name]}`)
    let freeSlots = (await observeBot()).free_slots
    for (const food of FOOD) {
      if (freeSlots <= MAX_FREE_SLOTS) break
      await rcon.exec(`give ${voyagerName} minecraft:${food} 64`)
      freeSlots = (await observeBot()).free_slots
    }
    assert.ok(freeSlots <= MAX_FREE_SLOTS, `free_slots ${freeSlots} after filling the inventory`)
    console.log(`  inventory ready: free_slots ${freeSlots}`)

    const eventsFrom = readLifeEvents().length
    const goal = await sayGoal('get me 4 sticks')
    await waitForGoal(goal.id, ['done'], STORE_TIMEOUT_MS)
    const record = await waitForGoalRecord(goal.id)
    const events = readLifeEvents().slice(eventsFrom)

    const placed = events.find((event) => event.kind === 'home_placed' && event.station === 'chest')
    assert.ok(placed, `no home_placed event with station chest; events ${short(events.map((e) => e.kind))}`)
    chestPos = placed.pos
    assert.ok(inRingOutside(placed.pos), `the chest at ${short(placed.pos)} is not in the ring outside the house (origin ${short(origin)}, size ${short(size)})`)

    const stored = readGoalEpisodeEvents(goal.id).find((event) => event.kind === 'stored')
    assert.ok(stored, `no stored event in the episode of ${goal.id}`)
    assert.deepEqual(record.stored, stored.items, `the goal record's stored differs from the event's items`)
    const storedNames = Object.keys(stored.items)
    assert.ok(storedNames.includes('coal'), `stored has no coal: ${short(stored.items)}`)
    assert.equal(stored.items.coal, 64, `stored coal is ${stored.items.coal}, not the 64 surplus`)
    assert.ok(storedNames.some((name) => name in JUNK), `stored has no junk item: ${short(stored.items)}`)

    const chest = await readChest(placed.pos)
    for (const [name, count] of Object.entries(stored.items)) {
      assert.ok((chest[name] || 0) >= count, `the chest holds ${chest[name] || 0} ${name}, stored says ${count}`)
    }
    const tossedStored = Object.keys(record.tossed || {}).filter((name) => name in stored.items)
    assert.deepEqual(tossedStored, [], `items both stored and tossed: ${short(record.tossed)}`)
    return `${goal.id} done; home_placed chest at ${short(placed.pos)}; stored ${short(stored.items)} (free slots ${stored.free_slots_before} -> ${stored.free_slots_after}); chest holds ${short(chest)}; tossed ${short(record.tossed)}`
  }

  async function check2 () {
    assert.ok(chestPos, 'check 2 needs the chest check 1 placed (run --checks 1,2)')
    await standInYard()
    await rcon.exec(`clear ${voyagerName} minecraft:coal`)
    await rcon.exec(`clear ${voyagerName} minecraft:charcoal`)
    const torchesBefore = (await observeBot()).inventory.torch || 0
    const chestBefore = await readChest(chestPos)
    assert.ok((chestBefore.coal || 0) > 0, `the chest has no coal: ${short(chestBefore)}`)

    const goal = await sayGoal('get me 8 torches')
    await waitForGoal(goal.id, ['done'], TORCH_TIMEOUT_MS)
    await waitForGoalRecord(goal.id)

    const steps = readGoalEpisodeEvents(goal.id).filter((event) => event.kind === 'step')
    const withdraws = steps.filter((step) => step.action_obj && step.action_obj.kind === 'withdraw' && step.action_obj.target === 'coal')
    assert.ok(withdraws.length >= 1, `no withdraw step for coal; steps ${short(steps.map((s) => s.action))}`)
    const succeeded = withdraws.filter((step) => step.ok)
    assert.ok(succeeded.length >= 1, `no withdraw step succeeded: ${short(withdraws.map((s) => [s.action, s.ok, s.error]))}`)
    const asked = succeeded.reduce((sum, step) => sum + step.action_obj.times, 0)

    const torchesAfter = (await observeBot()).inventory.torch || 0
    const chestAfter = await readChest(chestPos)
    const taken = chestBefore.coal - (chestAfter.coal || 0)
    assert.ok(torchesAfter - torchesBefore >= 8, `torches ${torchesBefore} -> ${torchesAfter}`)
    assert.ok(taken >= 1, `the chest's coal did not go down: ${chestBefore.coal} -> ${chestAfter.coal || 0}`)
    assert.equal(taken, asked, `the chest's coal went down by ${taken}, the withdraw steps asked ${asked}`)
    return `${goal.id} done; withdraw steps ${short(succeeded.map((s) => s.action))}; torches ${torchesBefore} -> ${torchesAfter}; chest coal ${chestBefore.coal} -> ${chestAfter.coal || 0}`
  }

  async function check3 () {
    const position = await standInYard()
    // Four cows 6 to 8 blocks south of the bot: outside the home's yard.
    for (const [dx, dz] of [[-4, 7], [4, 7], [-2, 9], [2, 6]]) {
      await rcon.exec(`execute at ${voyagerName} run summon minecraft:cow ~${dx} ~3 ~${dz} ${ADULT_COW}`)
    }
    console.log(`  4 cows summoned south of ${short(position)}`)

    const goal = await sayGoal('get ready for night')
    const parent = await waitFor(() => {
      const current = readGoals().find((g) => g.id === goal.id)
      return current && current.kind === 'composite' && current.children.length >= 2 ? current : null
    }, FIND_GOAL_TIMEOUT_MS)
    assert.ok(parent, `${goal.id} did not become a composite goal with 2+ children: ${short(readGoals().find((g) => g.id === goal.id))}`)
    console.log(`  ${goal.id} composite, children ${short(parent.children)}`)

    const ended = await waitFor(() => readGoals().find((g) => g.id === goal.id && FINISHED.includes(g.status)), COMPOSITE_TIMEOUT_MS, 5000)
    assert.ok(ended, `${goal.id} not finished within ${COMPOSITE_TIMEOUT_MS / 60000} min`)
    await sleep(2000) // the life writes the parent's end event just after

    const lines = readGoalLines()
    const final = readGoals()
    const finalParent = final.find((g) => g.id === goal.id)
    const childLines = finalParent.children.map((id) => {
      const own = lines.map((line, index) => ({ line, index })).filter(({ line }) => line.id === id)
      const firstRunning = own.find(({ line }) => line.status === 'running')
      const lastTerminal = [...own].reverse().find(({ line }) => FINISHED.includes(line.status))
      return { id, goal: final.find((g) => g.id === id), firstRunning: firstRunning ? firstRunning.index : null, lastTerminal: lastTerminal ? lastTerminal.index : null }
    })
    const ran = childLines.filter((child) => child.firstRunning !== null)
    const records = readRecords()
    const evidence = childLines.map(({ goal: child }) => `  ${child.id} "${child.text}" ${child.kind} ${child.status}${child.reason ? ` (${child.reason})` : ''}${child.replanned ? ' replanned' : ''}`)
    console.log(`  children of ${goal.id}:\n${evidence.join('\n')}`)

    for (let i = 1; i < ran.length; i++) {
      assert.ok(ran[i - 1].lastTerminal !== null && ran[i].firstRunning > ran[i - 1].lastTerminal, `${ran[i].id} ran before ${ran[i - 1].id} ended`)
    }
    for (const child of ran) {
      const record = records.find((r) => r.goal_id === child.id)
      assert.ok(record, `${child.id} ran but has no life.jsonl record`)
      assert.ok(record.outcome, `${child.id}'s record has no outcome: ${short(record)}`)
    }
    const parentEnd = readLifeEvents().find((event) => event.kind === 'goal_parent_end' && event.goal_id === goal.id)
    assert.ok(parentEnd, `no goal_parent_end event for ${goal.id}`)
    assert.equal(parentEnd.status, 'done', `the parent ended ${parentEnd.status}: ${finalParent.reason}`)
    assert.equal(finalParent.status, 'done')
    const replanned = readLifeEvents().filter((event) => event.kind === 'goal_replanned' && event.goal_id === goal.id)
    return `${goal.id} composite, ${ran.length} of ${finalParent.children.length} children ran in order, outcomes ${short(ran.map((c) => records.find((r) => r.goal_id === c.id).outcome))}, parent done${replanned.length ? `, replanned ${short(replanned)}` : ''}`
  }

  // --- Run ---

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

    const names = { 1: '1: junk and coal go into the home chest placed in the yard', 2: '2: "get me 8 torches" withdraws the stored coal', 3: '3: "get ready for night" runs its children in order and ends done' }
    for (const number of checks) await check(names[number], available[number])
  } catch (err) {
    failures++
    console.log(`FAIL setup: ${err.message}`)
  } finally {
    await say('!stop').catch(() => {}) // the goals left open
    await rcon.exec(`kill @e[type=cow,tag=${COW_TAG}]`).catch(() => {})
    await rcon.exec(`forceload remove ${standX} ${standZ}`).catch(() => {})
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
