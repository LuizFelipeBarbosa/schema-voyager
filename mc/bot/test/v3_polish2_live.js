// Live v3 test of session S15's fixes: the hunger goal (a learned kill rule, then
// a smelt), an unreachable home chest (one stall, then never again), a held
// furnace used underground (a niche) and a stuck bot (a life that stops). The
// checks read the run's store.json, goals.jsonl, life_events.jsonl,
// life_state.json and episode files, the bot's GET /status and POST /observe,
// and the chest's real contents over RCON. Contracts: docs/v3-interfaces.md
// v3.0i "Reaching home" and "Mobs seen", v3.2e (the hunger goal), v3.5c
// (furnishing, storing), "A held station underground", "A stuck bot".
//
// Needs, already running (it starts nothing), for each check:
//   hunger      the Minecraft server on easy, VoyagerBot, and a Python life in
//               learn mode with a live LLM writing runs/<run> (--mode learn).
//               Nothing else is needed.
//   unreachable the s12-houses world, peaceful, daylight off, VoyagerBot with
//               BOT_OWNERS=Otopo,Tester, and a Python life in play mode with a
//               live LLM writing runs/<run> (--mode play), resumed from a run
//               whose home is set (exactly v3_home_live.js's setup).
//   niche       the Minecraft server and VoyagerBot, and NO Python life running (the
//               check calls the bot's API itself; it fails if $SV2_HOME/run/lock-a exists).
//   stuck       a Python life in learn mode with a live LLM writing runs/<run>
//               (--mode learn). The check holds the bot's action lock itself, about 200 s.
// Meant for instance a: SV2_MC_PORT 25566, SV2_RCON_PORT 25576, SV2_BOT_PORT 3200.
// Refuses the live demo's ports (25565, 25575, 3000). Environment variables are
// loaded from $SV2_HOME/.env without overriding the ones already set.
//
// Checks: hunger = waits for a learned kill rule for a food animal (summoning 3
// cows next to the bot if none is learned in time), empties the bot of food,
// gives it Hunger 255 and waits for the system goal for a cooked meat: it ends
// done, with a successful kill step before a successful smelt step whose
// output is that meat.
// unreachable = "!goal get me 4 sticks" places the chest in the yard
// (`home_placed`); the bot is moved 8 blocks south and the chest walled in with
// a glass enclosure (the chest's cell +-4, solid from 1 below to 3 above, never inside the home's footprint); with
// 2 free slots a second goal makes room, the walk to the chest stalls and
// ends in exactly one `home_unreachable` (station chest, action moved), the
// cell in `bad_slots`, `moved` has chest, `stations` none; a third goal then
// attempts no deposit (no `stored`, no `home_unreachable`, the chest unchanged)
// and is faster to its first plan step than the second.
//
// niche = at y 40 (stone on seed 2024) builds a solid 5x5x5 stone block with a 1x1 shaft
// 3 high, puts the bot in it with exactly a furnace, 1 raw_iron and 1 coal, and POSTs
// /smelt (no station, no protect): ok, used_furnace, a `niche` cell next to the bot's feet or
// head, raw_iron -1 and iron_ingot +1 and no other gain (the travel's dug blocks excepted),
// furnace_retrieved. Afterwards it seals the niche with stone and teleports the bot back.
// stuck = gives the bot 20 raw_iron, 3 coal and a furnace and POSTs a 20-ingot /smelt
// straight to the bot (it holds the action lock about 200 s, so the life's own actions get
// 409 busy): exactly one `bot_stuck` event with losses 3 in life_events.jsonl within 330 s,
// and the life process (live.json's pid) gone within 60 s after it. Nothing to restore.
//
// At the end (always) it removes the glass enclosure, kills the cows it summoned,
// clears the effects it gave, removes the forceload and disconnects Tester; seals the niche
// stone block and returns the bot to where it was.
//
// Usage: node mc/bot/test/v3_polish2_live.js [--checks hunger,unreachable,niche,stuck] [--run v3-polish2-live]
//          [--south 4] [--wait-kill-s 600] [--wait-goal-s 480] [--summon-now]
//          (the default checks are hunger,unreachable; niche and stuck need their own setup. --summon-now: skip the wait for a naturally learned kill rule; exit 0 = every selected check passed; --south: blocks south of the home's door the bot stands;
//           --help prints this usage and exits without connecting)
// Env: SV2_MC_PORT (25566), SV2_RCON_PORT, SV2_BOT_PORT, RCON_PASSWORD,
//      SV2_HOME (repo root), BOT_USERNAME (VoyagerBot).

const assert = require('node:assert/strict')
const fs = require('fs')
const path = require('path')

const DEMO_MC_PORT = 25565
const TESTER_NAME = 'Tester'
const MESSAGE_SPACING_MS = 1500
const POLL_INTERVAL_MS = 1000
const SPAWN_TIMEOUT_MS = 60 * 1000
const HOME_SET_TIMEOUT_MS = 60 * 1000
const TELEPORT_TIMEOUT_MS = 15 * 1000
const FIND_GOAL_TIMEOUT_MS = 30 * 1000
const RECORD_TIMEOUT_MS = 30 * 1000
const GOAL_TIMEOUT_MS = 6 * 60 * 1000
const SUMMONED_KILL_WAIT_S = 300 // the life asks about new mobs only between goals
const MOB_REPORT_EVERY_MS = 30 * 1000
const FREE_SLOTS_TIMEOUT_MS = 60 * 1000
const IDLE_TIMEOUT_MS = 400 * 1000
const FINISHED = ['done', 'failed', 'cancelled', 'unsupported']

const HUNGER_BELOW = 8 // life.py: a food level under this, with nothing edible held, queues a hunger goal
const MAX_FREE_SLOTS = 2 // the bot stores when free_slots < 4; this leaves no doubt
const COW_TAG = 'p2cow'
const ADULT_COW = `{Age:0,PersistenceRequired:1b,Tags:["${COW_TAG}"]}`
const COW_OFFSETS = [[3, 0], [-4, 0], [0, 3]] // blocks from the bot; each cow lands on the surface there

const FOOD_ANIMALS = ['cow', 'pig', 'sheep', 'chicken', 'rabbit']
const COOKED_MEATS = ['cooked_beef', 'cooked_porkchop', 'cooked_mutton', 'cooked_chicken', 'cooked_rabbit']
// Vanilla foods the bot may hold; the hunger check clears all of them.
const EDIBLE = [
  'apple', 'baked_potato', 'beetroot', 'beetroot_soup', 'bread', 'carrot', 'chorus_fruit', 'cooked_beef', 'cooked_chicken',
  'cooked_cod', 'cooked_mutton', 'cooked_porkchop', 'cooked_rabbit', 'cooked_salmon', 'cookie', 'dried_kelp',
  'enchanted_golden_apple', 'golden_apple', 'golden_carrot', 'honey_bottle', 'melon_slice', 'mushroom_stew', 'poisonous_potato',
  'potato', 'pumpkin_pie', 'rabbit_stew', 'beef', 'chicken', 'cod', 'mutton', 'porkchop', 'rabbit', 'salmon', 'tropical_fish',
  'pufferfish', 'rotten_flesh', 'spider_eye', 'suspicious_stew', 'sweet_berries', 'glow_berries', 'cake'
]

// The glass enclosure around the chest (every block in it replaced): the chest's cell +-4 in x and z, from one block below
// the chest's cell to three above it, so the bot can neither step, jump nor fall into it from trees or higher terrain.
const ENCLOSURE_RADIUS = 4
const ENCLOSURE_BELOW = 1
const ENCLOSURE_ABOVE = 3
const BOT_BEYOND_ENCLOSURE = 3 // blocks past the enclosure's south edge where the bot and Tester wait

// Items no rule names, by stack size; the check gives at most MAX_JUNK_STACKS of them.
const MAX_JUNK_STACKS = 18
const JUNK = {
  poppy: 64, dandelion: 64, oak_sapling: 64, birch_sapling: 64, wheat_seeds: 64, bone: 64, string: 64, feather: 64,
  flint: 64, kelp: 64, cactus: 64, azure_bluet: 64, oxeye_daisy: 64, cornflower: 64, allium: 64,
  red_tulip: 64, pink_tulip: 64, white_tulip: 64
}
// Food stacks fill the rest of the inventory: the bot never stores or tosses food.
const FILLER_FOOD = ['bread', 'cooked_beef', 'cooked_porkchop', 'cooked_mutton', 'cooked_chicken', 'baked_potato', 'apple', 'carrot', 'cooked_cod', 'cooked_salmon', 'pumpkin_pie', 'golden_carrot']

const NICHE_Y = 40 // deep in stone on seed 2024
const NICHE_RADIUS = 2 // the stone block is 5x5x5 around the shaft's foot
const NICHE_BELOW = 1
const NICHE_ABOVE = 3 // the block's top; the shaft is 3 high (feet, head, one above)
const SMELT_HTTP_TIMEOUT_MS = 300 * 1000
const STUCK_EVENT_TIMEOUT_MS = 330 * 1000
const LIFE_STOP_TIMEOUT_MS = 60 * 1000
const STUCK_EVENT_KINDS = ['bot_down', 'bot_back', 'bot_stuck']

const short = (value) => JSON.stringify(value)

// --- Pure helpers (unit-tested in unit/polish2_live.test.js) ---

// The boxes ({min: [x, y, z], max: [x, y, z]}, inclusive) of the glass enclosure around `chest` ([x, y, z]): every cell
// of the chest +-4 in x and z, from chest.y - 1 to chest.y + 3, except the chest's own cell and the home's footprint
// (`origin`, `size`: x/z cells origin..origin+size-1, all y). The boxes are disjoint.
function enclosureBoxes (chest, { origin, size }) {
  const [cx, cy, cz] = chest
  const r = ENCLOSURE_RADIUS
  const yMin = cy - ENCLOSURE_BELOW
  const yMax = cy + ENCLOSURE_ABOVE
  const house = { x1: origin[0], x2: origin[0] + size[0] - 1, z1: origin[2], z2: origin[2] + size[2] - 1 }
  const column = { x1: cx, x2: cx, z1: cz, z2: cz }
  const inHouse = cx >= house.x1 && cx <= house.x2 && cz >= house.z1 && cz <= house.z2
  const full = { x1: cx - r, x2: cx + r, z1: cz - r, z2: cz + r }
  const walls = outsideRect(full, house).flatMap((rect) => outsideRect(rect, column))
  const boxes = walls.map((rect) => ({ min: [rect.x1, yMin, rect.z1], max: [rect.x2, yMax, rect.z2] }))
  if (!inHouse) { // the chest's column: glass under the chest and above it, not the chest
    boxes.push({ min: [cx, yMin, cz], max: [cx, cy - 1, cz] }, { min: [cx, cy + 1, cz], max: [cx, yMax, cz] })
  }
  return boxes
}

// The parts of rectangle `rect` ({x1, x2, z1, z2}, inclusive) that lie outside rectangle `cut`: up to four rectangles.
function outsideRect (rect, cut) {
  const overlaps = rect.x1 <= cut.x2 && rect.x2 >= cut.x1 && rect.z1 <= cut.z2 && rect.z2 >= cut.z1
  if (!overlaps) return [rect]
  const parts = []
  if (rect.x1 < cut.x1) parts.push({ ...rect, x2: cut.x1 - 1 }) // west of the cut
  if (rect.x2 > cut.x2) parts.push({ ...rect, x1: cut.x2 + 1 }) // east of the cut
  const x1 = Math.max(rect.x1, cut.x1)
  const x2 = Math.min(rect.x2, cut.x2)
  if (rect.z1 < cut.z1) parts.push({ x1, x2, z1: rect.z1, z2: cut.z1 - 1 }) // north of the cut
  if (rect.z2 > cut.z2) parts.push({ x1, x2, z1: cut.z2 + 1, z2: rect.z2 }) // south of the cut
  return parts
}

// The names in an inventory ({item: count}) the bot could eat, held in a positive count.
function edibleHeld (inventory) {
  return Object.keys(inventory || {}).filter((name) => EDIBLE.includes(name) && inventory[name] > 0)
}

// The system goal (from goals.jsonl's latest states) for a cooked meat that is not in `knownIds`, or undefined.
function pickHungerGoal (goals, knownIds = new Set()) {
  return goals.find((goal) => goal.source === 'system' && !knownIds.has(goal.id) &&
    (COOKED_MEATS.includes(goal.item) || COOKED_MEATS.some((meat) => String(goal.text || '').includes(meat))))
}

// Whether an episode's events hold a successful kill step before a successful smelt step whose output is `meat`.
function killBeforeSmelt (events, meat) {
  const steps = events.filter((event) => event.kind === 'step' && event.ok && event.action_obj)
  const kill = steps.findIndex((step) => step.action_obj.kind === 'kill')
  const smelt = steps.findIndex((step) => step.action_obj.kind === 'smelt' && step.action_obj.target === meat)
  return { ok: kill !== -1 && smelt !== -1 && kill < smelt, kill, smelt }
}

// The food-animal kill rule in a parsed store.json ({rules: [...]}), or undefined.
function findFoodKillRule (store) {
  return ((store && store.rules) || []).find((rule) => rule.action && rule.action.type === 'kill' && FOOD_ANIMALS.includes(rule.action.mob))
}

// The RCON commands that build the niche check's shaft at the cell [x, y, z]: a solid stone block, then a 1x1 air column
// from y to y + 2. `seal` refills the whole block with stone.
function shaftCommands ([x, y, z]) {
  const r = NICHE_RADIUS
  const seal = `fill ${x - r} ${y - NICHE_BELOW} ${z - r} ${x + r} ${y + NICHE_ABOVE} ${z + r} minecraft:stone`
  return { build: [seal, `fill ${x} ${y} ${z} ${x} ${y + 2} ${z} minecraft:air`], seal }
}

// Whether `niche` is a cell next to the bot's feet cell [x, y, z] or the one above it (its head): one block away on one axis.
function nicheNextToBot (niche, feet) {
  if (!Array.isArray(niche) || niche.length !== 3 || !niche.every(Number.isInteger)) return false
  const [x, y, z] = feet
  if (sameCell(niche, [x, y, z]) || sameCell(niche, [x, y + 1, z])) return false // the bot's own cells
  return [[x, y, z], [x, y + 1, z]].some((cell) => cell.reduce((sum, n, i) => sum + Math.abs(n - niche[i]), 0) === 1)
}

// The items an answer's inventory change gained that the smelt does not explain: all but `output`, the fuel and the
// furnace it took back, and the blocks the travel dug (`dug`: {block: count}) or what they drop (stone drops cobblestone).
function strayGains (delta, output, dug = {}) {
  const explained = new Set([output, 'furnace', ...Object.keys(dug)])
  if (dug.stone) explained.add('cobblestone')
  return Object.keys(delta || {}).filter((name) => delta[name] > 0 && !explained.has(name))
}

const USAGE = `Usage: node mc/bot/test/v3_polish2_live.js [--checks hunger,unreachable,niche,stuck] [--run v3-polish2-live]
         [--south 4] [--wait-kill-s 600] [--wait-goal-s 480] [--summon-now]
Live check of S15's fixes (see the header of this file for what it needs). Exit 0 = every selected check passed.`

function parseArgs (argv) {
  const options = { checks: ['hunger', 'unreachable'], run: 'v3-polish2-live', south: 4, waitKillS: 600, waitGoalS: 480, summonNow: false }
  for (let i = 0; i < argv.length; i += 2) {
    if (argv[i] === '--summon-now') {
      options.summonNow = true
      i--
      continue
    }
    const value = argv[i + 1]
    if (value === undefined) throw new Error(`${argv[i]} needs a value`)
    if (argv[i] === '--checks') options.checks = value.split(',')
    else if (argv[i] === '--run') options.run = value
    else if (argv[i] === '--south') options.south = Number(value)
    else if (argv[i] === '--wait-kill-s') options.waitKillS = Number(value)
    else if (argv[i] === '--wait-goal-s') options.waitGoalS = Number(value)
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

function readJson (file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')) } catch (err) { return null }
}

const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)
const sameCell = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length === 3 && a.every((n, i) => n === b[i])

let failures = 0
let sleep // set in main, once the helpers are required

async function main () {
  const argv = process.argv.slice(2)
  if (argv.includes('--help') || argv.includes('-h')) {
    console.log(USAGE)
    process.exit(0)
  }
  const mineflayer = require('mineflayer')
  const helpers = require('./v2_helpers')
  const { RconClient, health, loadEnv, post, botUrl } = helpers
  sleep = helpers.sleep

  const sv2Home = process.env.SV2_HOME || path.resolve(__dirname, '..', '..', '..')
  loadEnv(sv2Home)
  const mcPort = Number(process.env.SV2_MC_PORT || 25566)
  const voyagerName = process.env.BOT_USERNAME || 'VoyagerBot'
  const { checks, run, south, waitKillS, waitGoalS, summonNow } = parseArgs(argv)
  for (const name of checks) if (!['hunger', 'unreachable', 'niche', 'stuck'].includes(name)) throw new Error(`unknown check ${name}`)
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
  const readPlaces = () => (readJson(path.join(runDir, 'life_state.json')) || {}).places || {}

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

  // --- The bot, read directly ---

  async function observeBot () {
    const { body } = await post('/observe', {})
    return body
  }

  async function botStatusNow () {
    return (await fetch(botUrl('/status'))).json()
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

  const summonedCows = []

  async function hungerCheck () {
    const startedAt = Date.now()
    const readRule = () => findFoodKillRule(readJson(path.join(runDir, 'store.json')))

    // 1. A kill rule for a food animal, learned or after cows are put next to the bot.
    // Prints which mobs the life has recorded, every 30 s while a wait goes on.
    let lastReportAt = 0
    const waitForRule = (seconds) => waitFor(() => {
      if (Date.now() - lastReportAt >= MOB_REPORT_EVERY_MS) {
        lastReportAt = Date.now()
        const state = readJson(path.join(runDir, 'life_state.json')) || {}
        console.log(`  observed_mobs ${short(Object.keys(state.observed_mobs || {}))}, asked_mobs ${short(state.asked_mobs || [])}`)
      }
      return readRule()
    }, seconds * 1000, 5000)

    let rule = summonNow ? null : await waitForRule(waitKillS)
    let how = 'learned naturally'
    if (!rule) {
      // The bot may be in a tunnel: bring it to the surface, then put cows on the surface around it.
      console.log(`  bot at ${short((await botStatusNow()).position)}`)
      await rcon.exec(`execute as ${voyagerName} at @s positioned over motion_blocking_no_leaves run tp @s ~ ~ ~`)
      await sleep(2000)
      console.log(`  bot on the surface at ${short((await botStatusNow()).position)}`)
      for (const [dx, dz] of COW_OFFSETS) {
        const reply = await rcon.exec(`execute at ${voyagerName} positioned ~${dx} ~ ~${dz} positioned over motion_blocking_no_leaves run summon minecraft:cow ~ ~ ~ ${ADULT_COW}`)
        console.log(`  summon cow at ~${dx} ~${dz}: ${reply}`)
      }
      summonedCows.push(COW_TAG)
      const count = await rcon.exec(`execute if entity @e[type=cow,tag=${COW_TAG}]`)
      console.log(`  cows tagged ${COW_TAG}: ${count}`)
      rule = await waitForRule(SUMMONED_KILL_WAIT_S)
      how = 'after summoning 3 cows'
      assert.ok(rule, `no kill rule for ${FOOD_ANIMALS.join('/')} ${summonNow ? '' : `within ${waitKillS} s, nor `}${SUMMONED_KILL_WAIT_S} s after summoning cows`)
    }
    console.log(`  kill rule ${rule.id} (${rule.action.mob}) ${how}, after ${Math.round((Date.now() - startedAt) / 1000)} s`)

    // 2. Nothing edible held: the life queues a hunger goal only then.
    const knownIds = new Set(readGoals().map((goal) => goal.id))
    for (const name of edibleHeld((await observeBot()).inventory)) await rcon.exec(`clear ${voyagerName} minecraft:${name}`)
    const stillHeld = edibleHeld((await observeBot()).inventory)
    assert.deepEqual(stillHeld, [], `food still held after clearing: ${short(stillHeld)}`)

    // 3. Hunger until the food level is under HUNGER_BELOW.
    await rcon.exec(`effect give ${voyagerName} minecraft:hunger 30 255`)
    const hungry = await waitFor(async () => {
      const { food } = await botStatusNow()
      return food !== null && food < HUNGER_BELOW ? food : null
    }, 60 * 1000, 1000)
    assert.ok(hungry !== null, `food did not fall under ${HUNGER_BELOW} within 60 s of Hunger 255`)
    const hungryAt = Date.now()

    // 4. The hunger goal ends done, with a kill step before the smelt step.
    const found = await waitFor(() => pickHungerGoal(readGoals(), knownIds), waitGoalS * 1000, 2000)
    assert.ok(found, `no system goal for a cooked meat appeared within ${waitGoalS} s`)
    const goal = await waitFor(() => {
      const current = readGoals().find((g) => g.id === found.id)
      return current && FINISHED.includes(current.status) ? current : null
    }, Math.max(1000, waitGoalS * 1000 - (Date.now() - hungryAt)), 3000)
    assert.ok(goal, `${found.id} (${found.text}) did not end within ${waitGoalS} s`)
    await waitForGoalRecord(goal.id)
    const events = readGoalEpisodeEvents(goal.id)
    const steps = events.filter((event) => event.kind === 'step').map((step) => [step.action_obj && step.action_obj.kind, step.action_obj && step.action_obj.target, step.ok])
    const seconds = Math.round((Date.now() - hungryAt) / 1000)
    console.log(`  ${goal.id} "${goal.text}" ${goal.status} after ${seconds} s; steps (kind, target, ok) ${short(steps)}`)
    assert.equal(goal.status, 'done', `${goal.id} ${goal.status}: ${goal.reason}`)
    const meat = COOKED_MEATS.find((name) => name === goal.item || String(goal.text).includes(name))
    const order = killBeforeSmelt(events, meat)
    assert.ok(order.ok, `no successful kill step before a successful smelt of ${meat} (kill step ${order.kill}, smelt step ${order.smelt} among the successful ones)`)
    return `${goal.id} "${goal.text}" done in ${seconds} s (${how}); kill step before smelt of ${meat}`
  }

  // --- unreachable ---

  let tester = null
  let lastSentAt = 0
  async function say (message) {
    await sleep(Math.max(0, lastSentAt + MESSAGE_SPACING_MS - Date.now()))
    lastSentAt = Date.now()
    tester.chat(message)
    console.log(`Tester says: ${message}`)
  }

  // Says `!goal <text>` and returns the new goal from goals.jsonl, and when it was said.
  async function sayGoal (text) {
    const knownIds = new Set(readGoals().map((goal) => goal.id))
    await say(`!goal ${text}`)
    const saidAt = Date.now() / 1000
    return { goal: await findNewGoal(text, knownIds), saidAt }
  }

  async function joinTester () {
    tester = mineflayer.createBot({ host: '127.0.0.1', port: mcPort, username: TESTER_NAME, version: process.env.MC_VERSION || '1.21.1', auth: 'offline' })
    tester.on('error', (err) => console.log(`tester error: ${err.message}`))
    const spawned = await new Promise((resolve) => {
      tester.once('spawn', () => resolve(true))
      setTimeout(() => resolve(false), SPAWN_TIMEOUT_MS)
    })
    if (!spawned) throw new Error(`${TESTER_NAME} did not spawn on port ${mcPort} within ${SPAWN_TIMEOUT_MS / 1000} s`)
    await rcon.exec(`gamemode survival ${TESTER_NAME}`)
    await rcon.exec(`effect give ${TESTER_NAME} minecraft:resistance 1000000 4 true`)
  }

  let forceloaded = null // [x, z] of the chunk kept loaded around the bot
  let placedBoxes = []

  // Teleports the bot to the ground at x, z and Tester next to it; returns the bot's position.
  async function standAt (x, z) {
    if (forceloaded) await rcon.exec(`forceload remove ${forceloaded[0]} ${forceloaded[1]}`)
    forceloaded = [x, z]
    await rcon.exec(`forceload add ${x} ${z}`)
    await sleep(2000) // the chunk loads before the height is read
    const onGround = `execute positioned ${x + 0.5} 200 ${z + 0.5} positioned over motion_blocking_no_leaves run`
    await rcon.exec(`${onGround} tp ${voyagerName} ~ ~ ~`)
    await rcon.exec(`${onGround} tp ${TESTER_NAME} ~2 ~ ~`)
    const arrived = await waitFor(async () => {
      const { position } = await observeBot()
      return distance(position, { x: x + 0.5, y: position.y, z: z + 0.5 }) <= 1.5 ? position : null
    }, TELEPORT_TIMEOUT_MS, 500)
    if (!arrived) throw new Error(`${voyagerName} did not arrive at ${x} ${z} within ${TELEPORT_TIMEOUT_MS / 1000} s`)
    await sleep(1500) // Tester lands too
    return arrived
  }

  // The bot's free slots; /observe has none while the bot is busy, so it asks again.
  async function readFreeSlots () {
    const freeSlots = await waitFor(async () => {
      const { free_slots: value } = await observeBot()
      return typeof value === 'number' ? { value } : null
    }, FREE_SLOTS_TIMEOUT_MS, 2000)
    assert.ok(freeSlots, `no numeric free_slots from /observe within ${FREE_SLOTS_TIMEOUT_MS / 1000} s`)
    return freeSlots.value
  }

  // Waits until no goal is queued or running; prints the system goals that ran meanwhile.
  async function waitUntilIdle () {
    const idle = await waitFor(() => !readGoals().some((g) => ['queued', 'running'].includes(g.status)), IDLE_TIMEOUT_MS, 5000)
    assert.ok(idle, `goals still queued or running after ${IDLE_TIMEOUT_MS / 1000} s: ${short(readGoals().filter((g) => ['queued', 'running'].includes(g.status)).map((g) => [g.id, g.text, g.status]))}`)
    const system = readGoals().filter((g) => g.source === 'system')
    console.log(`  idle; system goals so far ${short(system.map((g) => [g.id, g.text, g.status]))}`)
  }

  // Gives junk (then food) until the bot has MAX_FREE_SLOTS free slots or fewer; returns the free slots.
  async function fillInventory () {
    for (const name of Object.keys(JUNK).slice(0, MAX_JUNK_STACKS)) await rcon.exec(`give ${voyagerName} minecraft:${name} ${JUNK[name]}`)
    let freeSlots = await readFreeSlots()
    for (const food of FILLER_FOOD) {
      if (freeSlots <= MAX_FREE_SLOTS) break
      await rcon.exec(`give ${voyagerName} minecraft:${food} 64`)
      freeSlots = await readFreeSlots()
    }
    assert.ok(freeSlots <= MAX_FREE_SLOTS, `free_slots ${freeSlots} after filling the inventory`)
    return freeSlots
  }

  // How long a goal took from when Tester said it to its first plan step (null if it has none).
  function secondsToFirstPlan (goal, saidAt) {
    const plan = readGoalEpisodeEvents(goal.id).find((event) => event.kind === 'plan')
    return plan ? Math.round((plan.t - saidAt) * 10) / 10 : null
  }

  // Runs a "get me 4 sticks" goal with the inventory nearly full; returns the goal, the new life events and its timings.
  async function runSticksGoal () {
    const freeSlots = await fillInventory()
    const eventsFrom = readLifeEvents().length
    const { goal, saidAt } = await sayGoal('get me 4 sticks')
    await waitForGoal(goal.id, ['done'], GOAL_TIMEOUT_MS)
    await waitForGoalRecord(goal.id)
    return { goal, freeSlots, events: readLifeEvents().slice(eventsFrom), toFirstPlan: secondsToFirstPlan(goal, saidAt) }
  }

  async function unreachableCheck () {
    await joinTester()
    const homeSet = await waitFor(() => readLifeEvents().find((event) => event.kind === 'home_set'), HOME_SET_TIMEOUT_MS)
    assert.ok(homeSet, `no home_set event in ${runDir}/life_events.jsonl within ${HOME_SET_TIMEOUT_MS / 1000} s`)
    const home = homeSet.home
    assert.ok(home.door, 'the home has no door')
    const footprint = (readPlaces().home) || home

    // 1. The first goal: the chest is placed in the yard.
    const yard = [home.door[0], home.door[2] + south]
    await standAt(...yard)
    await rcon.exec(`give ${voyagerName} minecraft:chest 1`)
    const first = await runSticksGoal()
    const placed = first.events.find((event) => event.kind === 'home_placed' && event.station === 'chest')
    assert.ok(placed, `no home_placed event with station chest; events ${short(first.events.map((e) => e.kind))}`)
    const chestCell = placed.pos
    console.log(`  ${first.goal.id} done; chest placed at ${short(chestCell)}`)

    // 2. Bot and Tester wait outside, south of a solid glass enclosure around the chest.
    await standAt(yard[0], chestCell[2] + ENCLOSURE_RADIUS + BOT_BEYOND_ENCLOSURE)
    console.log(`  bot at ${short((await observeBot()).position)}, Tester at ${short(tester.entity.position)}`)
    placedBoxes = enclosureBoxes(chestCell, footprint)
    // every block, not only air: short grass and flowers in the yard are walkable and would leave gaps
    for (const { min, max } of placedBoxes) console.log(`  fill ${min.join(' ')} ${max.join(' ')}: ${await rcon.exec(`fill ${min.join(' ')} ${max.join(' ')} minecraft:glass`)}`)
    console.log(`  glass enclosure around ${short(chestCell)}: ${placedBoxes.length} boxes, outside the footprint ${short(footprint)}`)
    const chestBefore = await readChest(chestCell)

    // 3. The second goal stalls once and moves the chest.
    const second = await runSticksGoal()
    const unreachable = second.events.filter((event) => event.kind === 'home_unreachable')
    assert.equal(unreachable.length, 1, `${unreachable.length} home_unreachable events in the second goal: ${short(unreachable)}`)
    assert.equal(unreachable[0].station, 'chest', `home_unreachable for ${unreachable[0].station}`)
    assert.ok(sameCell(unreachable[0].pos, chestCell), `home_unreachable at ${short(unreachable[0].pos)}, the chest is at ${short(chestCell)}`)
    assert.equal(unreachable[0].action, 'moved', `home_unreachable action ${unreachable[0].action}`)
    const places = readPlaces()
    assert.ok((places.bad_slots || []).some((cell) => sameCell(cell, chestCell)), `bad_slots ${short(places.bad_slots)} lacks ${short(chestCell)}`)
    assert.ok((places.moved || []).includes('chest'), `moved ${short(places.moved)} lacks chest`)
    assert.ok(!(places.stations || {}).chest, `stations still has a chest: ${short(places.stations)}`)
    const later = second.events.filter((event) => event.kind === 'home_slot_bad' || (event.kind === 'home_placed'))
    console.log(`  ${second.goal.id} done; home_unreachable ${short(unreachable[0])}; first plan step after ${second.toFirstPlan} s; other furnishing events ${short(later)}`)

    // 4. The third goal does not try the walled-in chest again (the life may furnish a new chest elsewhere).
    await waitUntilIdle() // the moved chest allows one more "a chest for my home" goal, which may be running
    const chestBeforeThird = await readChest(chestCell)
    const third = await runSticksGoal()
    const again = third.events.filter((event) => event.kind === 'home_unreachable')
    assert.equal(again.length, 0, `a further home_unreachable in the third goal: ${short(again)}`)
    const chestAfter = await readChest(chestCell)
    assert.deepEqual(chestAfter, chestBeforeThird, `the walled-in chest changed: ${short(chestBeforeThird)} -> ${short(chestAfter)}`)
    // `stored` events carry no position: the unchanged chest above is the proof that none went into C.
    const thirdEpisode = readGoalEpisodeEvents(third.goal.id)
    const report = {
      home_placed: third.events.filter((e) => e.kind === 'home_placed'),
      home_slot_bad: readLifeEvents().filter((e) => e.kind === 'home_slot_bad'),
      stored: thirdEpisode.filter((e) => e.kind === 'stored').map((e) => e.items),
      tossed: thirdEpisode.filter((e) => e.kind === 'tossed').map((e) => e.items)
    }
    console.log(`  ${third.goal.id} done; first plan step after ${third.toFirstPlan} s (second goal: ${second.toFirstPlan} s); ${short(report)}`)
    return `${second.goal.id} ended with one home_unreachable (moved) at ${short(chestCell)}; ${third.goal.id} tried no deposit; time to first plan step ${second.toFirstPlan} s (second) vs ${third.toFirstPlan} s (third)`
  }

  // --- niche ---

  let nicheSite = null // {cell, bot position to return to}, set once the shaft exists

  async function nicheCheck () {
    assert.ok(!fs.existsSync(path.join(sv2Home, 'run', 'lock-a')), `${path.join(sv2Home, 'run', 'lock-a')} exists: stop the life first`)
    const back = (await botStatusNow()).position
    const cell = [Math.floor(back.x), NICHE_Y, Math.floor(back.z)]
    const shaft = shaftCommands(cell)
    const [x, y, z] = cell
    console.log(`  bot at ${short(back)}; shaft at ${short(cell)}`)

    nicheSite = { shaft, back }
    for (const command of shaft.build) console.log(`  ${command}: ${await rcon.exec(command)}`)
    await rcon.exec(`clear ${voyagerName} minecraft:furnace`)
    for (const stack of ['furnace 1', 'raw_iron 1', 'coal 1']) await rcon.exec(`give ${voyagerName} minecraft:${stack}`)
    await rcon.exec(`tp ${voyagerName} ${x + 0.5} ${y} ${z + 0.5}`)
    await sleep(2000)

    const { status, body } = await post('/smelt', { ingredient: 'raw_iron', item: 'iron_ingot', times: 1, fuel: 'coal' })
    const keys = ['ok', 'error', 'used_furnace', 'furnace_retrieved', 'niche', 'inventory_delta', 'inventory_before_step', 'inventory', 'travel', 'fuel_used', 'position']
    console.log(`  /smelt ${status}: ${short(Object.fromEntries(keys.filter((key) => key in body).map((key) => [key, body[key]])))}`)
    console.log(`  travel.dug ${short((body.travel || {}).dug)}`)
    assert.equal(body.ok, true, `/smelt not ok: ${body.error}`)
    assert.equal(body.used_furnace, true, 'used_furnace is not true')
    const feet = [x, y, z] // the bot's cell: the shaft's foot
    assert.ok(nicheNextToBot(body.niche, feet), `niche ${short(body.niche)} is not a 3-integer cell next to the feet ${short(feet)} or head`)
    const delta = body.inventory_delta
    assert.ok(delta, 'no inventory_delta in the answer')
    assert.equal(delta.raw_iron, -1, `raw_iron change ${delta.raw_iron}`)
    assert.equal(delta.iron_ingot, 1, `iron_ingot change ${delta.iron_ingot}`)
    const stray = strayGains(delta, 'iron_ingot', (body.travel || {}).dug)
    assert.deepEqual(stray, [], `unexplained gains ${short(stray)} in ${short(delta)}`)
    assert.equal(body.furnace_retrieved, true, 'furnace_retrieved is not true')
    return `smelted raw_iron to iron_ingot in a niche at ${short(body.niche)} beside ${short(feet)}; furnace back; delta ${short(delta)}`
  }

  // --- stuck ---

  async function stuckCheck () {
    const readLiveJson = () => readJson(path.join(runDir, 'live.json'))
    const lifeAlive = () => {
      const live = readLiveJson()
      if (!live) return false
      try { process.kill(live.pid, 0); return true } catch (err) { return err.code === 'EPERM' }
    }
    assert.ok(lifeAlive(), `no running life: ${path.join(runDir, 'live.json')} is missing or names a dead pid`)
    const eventsFrom = readLifeEvents().length
    const readEvents = () => readLifeEvents().slice(eventsFrom)

    for (const stack of ['raw_iron 20', 'coal 3', 'furnace 1']) await rcon.exec(`give ${voyagerName} minecraft:${stack}`)
    const startedAt = Date.now()
    const smelting = fetch(botUrl('/smelt'), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ ingredient: 'raw_iron', item: 'iron_ingot', times: 20, fuel: 'coal' }),
      signal: AbortSignal.timeout(SMELT_HTTP_TIMEOUT_MS)
    }).then((res) => res.json()).catch((err) => ({ ok: false, error: err.message }))
    console.log('  /smelt of 20 raw_iron started; the life\'s own actions get 409 busy meanwhile')

    const seen = new Set()
    const printNewEvents = () => readEvents().forEach((event) => {
      if (!STUCK_EVENT_KINDS.includes(event.kind) || seen.has(event)) return
      seen.add(event)
      console.log(`  ${event.kind} goal ${event.goal_id} error "${String(event.error || '').slice(0, 80)}" t ${event.t} (+${Math.round((Date.now() - startedAt) / 1000)} s)`)
    })
    const stuckEvent = await waitFor(() => { printNewEvents(); return readEvents().find((event) => event.kind === 'bot_stuck') }, STUCK_EVENT_TIMEOUT_MS, 2000)
    assert.ok(stuckEvent, `no bot_stuck event within ${STUCK_EVENT_TIMEOUT_MS / 1000} s`)
    const stuckAt = Date.now()
    const stuckEvents = readEvents().filter((event) => event.kind === 'bot_stuck')
    assert.equal(stuckEvents.length, 1, `${stuckEvents.length} bot_stuck events`)
    assert.equal(stuckEvent.losses, 3, `bot_stuck losses ${stuckEvent.losses}`)
    const stopped = await waitFor(() => !lifeAlive(), LIFE_STOP_TIMEOUT_MS, 1000)
    assert.ok(stopped, `the life (live.json ${short(readLiveJson())}) still runs ${LIFE_STOP_TIMEOUT_MS / 1000} s after bot_stuck`)
    const stoppedAfter = Math.round((Date.now() - stuckAt) / 1000)
    printNewEvents()

    // The smelt keeps the action lock for about 200 s: its answer is informational.
    const answer = await Promise.race([smelting, sleep(SMELT_HTTP_TIMEOUT_MS).then(() => null)])
    console.log(`  /smelt answer: ${answer ? short({ ok: answer.ok, smelted: answer.smelted, error: answer.error }) : 'not yet'}`)
    console.log('  nothing to restore: the life has stopped')
    return `one bot_stuck (goal ${stuckEvent.goal_id}, losses 3), the life gone ${stoppedAfter} s later`
  }

  // --- Run ---

  try {
    console.log(`run ${run}, port ${mcPort}, checks ${checks.join(',')}`)
    const names = { hunger: 'hunger: a hungry bot kills for food, then smelts it', unreachable: 'unreachable: a walled-in chest costs one stall, then is left alone', niche: 'niche: a held furnace is used underground in a dug niche', stuck: 'stuck: a bot busy for good stops the life with bot_stuck' }
    const available = { hunger: hungerCheck, unreachable: unreachableCheck, niche: nicheCheck, stuck: stuckCheck }
    for (const name of checks) await check(names[name], available[name])
  } catch (err) {
    failures++
    console.log(`FAIL setup: ${err.message}`)
  } finally {
    if (tester) await say('!stop').catch(() => {}) // the goals left open
    for (const { min, max } of placedBoxes) console.log(`  clean up fill ${min.join(' ')} ${max.join(' ')}: ${await rcon.exec(`fill ${min.join(' ')} ${max.join(' ')} minecraft:air replace minecraft:glass`).catch((err) => err.message)}`)
    if (nicheSite) { // seal the niche, then return the bot
      console.log(`  clean up ${nicheSite.shaft.seal}: ${await rcon.exec(nicheSite.shaft.seal).catch((err) => err.message)}`)
      const { x, y, z } = nicheSite.back
      await rcon.exec(`tp ${voyagerName} ${x} ${y} ${z}`).catch(() => {})
    }
    await rcon.exec(`kill @e[type=cow,tag=${COW_TAG}]`).catch(() => {})
    if (forceloaded) await rcon.exec(`forceload remove ${forceloaded[0]} ${forceloaded[1]}`).catch(() => {})
    await rcon.exec(`effect clear ${voyagerName} minecraft:hunger`).catch(() => {})
    await rcon.exec(`effect clear ${TESTER_NAME}`).catch(() => {})
    if (tester) tester.quit()
    await sleep(500)
    rcon.close()
  }
  console.log(failures === 0 ? 'ALL PASS' : `${failures} FAILED`)
  process.exit(failures === 0 ? 0 : 1)
}

module.exports = { shaftCommands, nicheNextToBot, strayGains, enclosureBoxes, outsideRect, edibleHeld, pickHungerGoal, killBeforeSmelt, findFoodKillRule, COOKED_MEATS }

if (require.main === module) {
  main().catch((err) => {
    console.log(`aborted: ${err.message}`)
    process.exit(1)
  })
}
