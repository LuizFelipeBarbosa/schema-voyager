// Live v2 test: from wherever the bot stands in a natural world, logs ->
// planks -> sticks -> crafting table -> wooden pickaxe -> 3 stone -> stone
// pickaxe (the gate), then best effort toward iron: iron ore, a furnace, 3
// smelted ingots, an iron pickaxe. Every mine, craft and smelt is checked
// exactly against vanilla; a step with quarantining events is retried (up to
// 3 times) and never counted as a mismatch.
//
// Uses the one bot already running at SV2_BOT_PORT and RCON at SV2_RCON_PORT
// (password RCON_PASSWORD); starts nothing. Environment variables are loaded
// from $SV2_HOME/.env without overriding the ones already set.
//
// Usage: node mc/bot/test/v2_iron.js   (exit 0 = gate passed, every exact check matched)

const mcData = require('minecraft-data')('1.21.1')
const {
  RconClient, deltaMatches, health, loadEnv, nearestInLook, post, quarantineReason
} = require('./v2_helpers')

const TIME_LIMIT_MS = 20 * 60 * 1000
const MAX_ATTEMPTS = 3 // per step, when it is quarantined
const MAX_CALLS_PER_GATHER = 40
const MAX_FUEL_CALLS = 8
const SEARCH_DISTANCE = 32
const LOGS_NEEDED = 3
const STONE_NEEDED = 3
const IRON_NEEDED = 3
const FURNACE_COBBLESTONE = 8
// What pathing may place; the same blocks Python's natural mode sends.
const SCAFFOLD = ['dirt', 'cobbled_deepslate', 'netherrack', 'andesite', 'diorite', 'granite', 'tuff']
const DIRECTIONS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
const STAGES = ['start', 'logs', 'wooden_pickaxe', 'stone', 'stone_pickaxe', 'raw_iron', 'cobblestone', 'fuel', 'furnace', 'iron_ingots', 'iron_pickaxe']

// --- Names from minecraft-data -------------------------------------------------

// Overworld logs: every `<species>_log` that has `<species>_planks`.
const LOGS = mcData.itemsArray.map((item) => item.name)
  .filter((name) => name.endsWith('_log') && !name.startsWith('stripped_') && mcData.itemsByName[name.replace(/_log$/, '_planks')])
const planksOf = (log) => log.replace(/_log$/, '_planks')
const isPlanks = (name) => name.endsWith('_planks') && Boolean(mcData.itemsByName[name])

// The ids a recipe's top-left cell accepts, over all recipes of `itemName`.
function topLeftVariants (itemName) {
  const names = mcData.recipes[mcData.itemsByName[itemName].id].map((recipe) => recipe.inShape[0][0])
  return new Set(names.map((id) => mcData.items[id].name))
}
const STONE_TIER = topLeftVariants('stone_pickaxe') // what a stone tool and a furnace take
const isStoneTier = (name) => STONE_TIER.has(name)

// --- State and output ------------------------------------------------------------

const startedAt = Date.now()
const state = { mismatches: [], stage: 'start', inventory: {}, step: 0, searchBlocked: new Set(), nextSearch: 0 }

const short = (value) => JSON.stringify(value)
const count = (name) => state.inventory[name] || 0
const countWhere = (test) => Object.entries(state.inventory).filter(([name]) => test(name)).reduce((sum, [, n]) => sum + n, 0)

function reach (stage) {
  state.stage = stage
  console.log(`-- stage: ${stage}`)
}

function checkTime () {
  if (Date.now() - startedAt > TIME_LIMIT_MS) throw new Error('time limit of 20 minutes reached')
}

async function call (route, body) {
  checkTime()
  const reply = await post(route, body)
  if (reply.body.inventory) state.inventory = reply.body.inventory
  return reply
}

function printStep (label, expected, observed, verdict, body) {
  const travel = body.travel || {}
  const events = (body.events || []).map((event) => `${event.type}@${event.phase}`)
  console.log(`[${String(++state.step).padStart(2, '0')}] ${label} | expected ${short(expected)} observed ${short(observed)} | ${verdict}` +
    ` | travel d=${travel.distance ?? 0} dug=${short(travel.dug || {})} placed=${short(travel.placed || {})}` +
    ` | events=${short(events)}${body.ok === false ? ` | error=${body.error}${body.detail ? ` (${body.detail})` : ''}` : ''}`)
}

function recordMismatch (label, expected, observed) {
  state.mismatches.push({ label, expected, observed })
}

// --- Steps -----------------------------------------------------------------------

// One /mine with an exact check of what dropped and the tool held. Returns
// 'match', 'mismatch', 'quarantined' (given up after retries) or the bot's
// error code.
async function mineStep (label, request, expectedDrops) {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    const { body } = await call('/mine', { ...request, dig_through: true, max_distance: SEARCH_DISTANCE, scaffold: SCAFFOLD })
    const expected = body.ok ? expectedDrops(body) : null
    const quarantined = quarantineReason(body)
    if (!body.ok && !quarantined) {
      printStep(label, expected, null, 'FAILED', body)
      return body.error
    }
    if (quarantined) {
      printStep(label, expected, body.drops, `QUARANTINED (${quarantined}), attempt ${attempt}`, body)
      continue
    }
    const exact = deltaMatches(body.drops, { fixed: expected }) &&
      body.block_removed === true && body.tool_used === request.tool
    printStep(label, { drops: expected, tool_used: request.tool }, { drops: body.drops, tool_used: body.tool_used, block_removed: body.block_removed },
      exact ? 'MATCH' : 'MISMATCH', body)
    if (!exact) recordMismatch(label, expected, body.drops)
    return exact ? 'match' : 'mismatch'
  }
  return 'quarantined'
}

// /craft or /smelt with an exact check of the inventory delta: `spec` is
// {fixed, tag} for deltaMatches. A quarantined step whose delta is already
// right counts as done; one that changed nothing is retried.
async function stationStep (label, route, request, spec, extraCheck = () => true) {
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    const { body } = await call(route, request)
    const quarantined = quarantineReason(body, { stationStep: true })
    const delta = body.inventory_delta || {}
    if (!body.ok && !quarantined) {
      printStep(label, spec.fixed, delta, 'FAILED', body)
      return body.error
    }
    if (quarantined) {
      const done = body.ok && deltaMatches(delta, spec)
      printStep(label, spec.fixed, delta, `QUARANTINED (${quarantined}), attempt ${attempt}${done ? ', delta is right' : ''}`, body)
      if (done) return 'match'
      continue
    }
    const exact = deltaMatches(delta, spec) && extraCheck(body)
    printStep(label, spec.fixed, delta, exact ? 'MATCH' : 'MISMATCH', body)
    if (!exact) recordMismatch(label, spec, delta)
    return exact ? 'match' : 'mismatch'
  }
  return 'quarantined'
}

const craftStep = (item, times, spec) => stationStep(`craft ${item} x${times}`, '/craft', { item, times }, spec)

// --- Finding things ----------------------------------------------------------------

// Looks around for `blocks` and walks 32 blocks toward the nearest hit (or the
// next unexplored direction when none is in sight).
async function searchFor (blocks) {
  const { body: seen } = await call('/look', { blocks, radius: 64 })
  const nearest = nearestInLook(seen.directions)
  let direction = nearest && !state.searchBlocked.has(nearest.direction) ? nearest.direction : null
  for (let i = 0; !direction && i < DIRECTIONS.length; i++) {
    const candidate = DIRECTIONS[state.nextSearch++ % DIRECTIONS.length]
    if (!state.searchBlocked.has(candidate)) direction = candidate
  }
  if (!direction) { state.searchBlocked.clear(); direction = DIRECTIONS[state.nextSearch++ % DIRECTIONS.length] }
  const { body } = await call('/explore', { direction, distance: SEARCH_DISTANCE, dig_through: true, scaffold: SCAFFOLD })
  const label = `explore ${direction} (look: ${nearest ? `${nearest.name} ${nearest.distance} ${nearest.direction}` : 'nothing'})`
  printStep(label, null, body.position, body.ok ? 'MOVED' : 'STUCK', body)
  if (body.ok) state.searchBlocked.clear()
  else state.searchBlocked.add(direction)
}

// Mines until `have()` reaches `needed`, searching when nothing is in range.
async function gather (label, request, expectedDrops, have, needed, maxCalls = MAX_CALLS_PER_GATHER) {
  for (let calls = 0; have() < needed; calls++) {
    if (calls >= maxCalls) return false
    const outcome = await mineStep(`${label} ${have() + 1}/${needed}`, request, expectedDrops)
    if (outcome === 'missing_tool') throw new Error(`${request.tool} is missing`)
    if (['not_found', 'hazard', 'unreachable'].includes(outcome)) await searchFor(request.blocks)
  }
  return true
}

// --- The run -----------------------------------------------------------------------

async function gate () {
  const logRequest = { blocks: LOGS, tool: null }
  const logsHeld = () => countWhere((name) => LOGS.includes(name))
  if (!await gather('mine log', logRequest, (body) => ({ [body.block]: 1 }), logsHeld, LOGS_NEEDED)) return false
  reach('logs')

  // Planks come out in the log's species.
  for (const log of LOGS.filter((name) => count(name) > 0)) {
    const logs = count(log)
    const result = await craftStep(planksOf(log), logs, { fixed: { [log]: -logs, [planksOf(log)]: 4 * logs } })
    if (result !== 'match') return false
  }
  const withPlanks = (fixed, total) => ({ fixed, tag: { test: isPlanks, total } })
  if (await craftStep('stick', 1, withPlanks({ stick: 4 }, -2)) !== 'match') return false
  if (await craftStep('crafting_table', 1, withPlanks({ crafting_table: 1 }, -4)) !== 'match') return false
  if (await craftStep('wooden_pickaxe', 1, withPlanks({ wooden_pickaxe: 1, stick: -2 }, -3)) !== 'match') return false
  reach('wooden_pickaxe')

  const stoneRequest = { blocks: ['stone'], tool: 'wooden_pickaxe' }
  const stoneHeld = () => countWhere(isStoneTier)
  if (!await gather('mine stone', stoneRequest, () => ({ cobblestone: 1 }), stoneHeld, STONE_NEEDED)) return false
  reach('stone')
  const stoneTier = { test: isStoneTier, total: -3 }
  if (await craftStep('stone_pickaxe', 1, { fixed: { stone_pickaxe: 1, stick: -2 }, tag: stoneTier }) !== 'match') return false
  reach('stone_pickaxe')
  return true
}

// Best effort past the gate; returns when something does not work out.
async function ironStage () {
  const tool = 'stone_pickaxe'
  const rawIron = () => count('raw_iron')
  if (!await gather('mine iron', { blocks: ['iron_ore', 'deepslate_iron_ore'], tool }, () => ({ raw_iron: 1 }), rawIron, IRON_NEEDED)) return
  reach('raw_iron')

  const stoneHeld = () => countWhere(isStoneTier)
  if (!await gather('mine cobblestone', { blocks: ['stone'], tool }, () => ({ cobblestone: 1 }), stoneHeld, FURNACE_COBBLESTONE)) return
  reach('cobblestone')

  const fuel = await findFuel(tool)
  if (!fuel) return
  reach('fuel')

  const furnace = await craftStep('furnace', 1, { fixed: { furnace: 1 }, tag: { test: isStoneTier, total: -FURNACE_COBBLESTONE } })
  if (furnace !== 'match') return
  reach('furnace')

  // The fuel is not part of the exact check: how much burns is the server's.
  const smelted = await stationStep(`smelt raw_iron x${IRON_NEEDED} with ${fuel}`, '/smelt',
    { ingredient: 'raw_iron', item: 'iron_ingot', times: IRON_NEEDED, fuel },
    { fixed: { raw_iron: -IRON_NEEDED, iron_ingot: IRON_NEEDED }, ignore: [fuel] },
    (body) => body.furnace_retrieved === true)
  if (smelted !== 'match') return
  reach('iron_ingots')

  if (count('stick') < 2) {
    const sticks = await craftStep('stick', 1, { fixed: { stick: 4 }, tag: { test: isPlanks, total: -2 } })
    if (sticks !== 'match') return
  }
  const pickaxe = await craftStep('iron_pickaxe', 1, { fixed: { iron_pickaxe: 1, iron_ingot: -IRON_NEEDED, stick: -2 } })
  if (pickaxe === 'match') reach('iron_pickaxe')
}

// Coal if some is in reach; else three planks of one kind. Returns the item name.
async function findFuel (tool) {
  const coalRequest = { blocks: ['coal_ore', 'deepslate_coal_ore'], tool }
  if (await gather('mine coal', coalRequest, () => ({ coal: 1 }), () => count('coal'), 1, MAX_FUEL_CALLS)) return 'coal'
  const spare = Object.keys(state.inventory).find((name) => isPlanks(name) && count(name) >= IRON_NEEDED)
  if (spare) return spare
  const log = LOGS.find((name) => count(name) > 0)
  if (!log) {
    const logsHeld = () => countWhere((name) => LOGS.includes(name))
    if (!await gather('mine log for fuel', { blocks: LOGS, tool: null }, (body) => ({ [body.block]: 1 }), logsHeld, 1)) return null
  }
  const mined = LOGS.find((name) => count(name) > 0)
  const crafted = await craftStep(planksOf(mined), 1, { fixed: { [mined]: -1, [planksOf(mined)]: 4 } })
  return crafted === 'match' ? planksOf(mined) : null
}

async function main () {
  loadEnv()
  const username = process.env.BOT_USERNAME || 'VoyagerBot'
  const status = await health()
  if (!status.connected) throw new Error('the bot is not connected to its server')

  const rcon = new RconClient()
  await rcon.connect()
  try {
    console.log(`rcon: ${await rcon.exec(`clear ${username}`)}`)
  } finally {
    rcon.close()
  }
  const reset = await call('/reset_episode', {})
  if (!reset.body.ok) throw new Error(`/reset_episode failed: ${short(reset.body)}`)
  console.log(`start: position ${short(reset.body.position)}, inventory ${short(state.inventory)}`)

  if (await gate()) await ironStage()
}

main()
  .catch((err) => console.log(`aborted: ${err.message}`))
  .then(() => {
    const stageReached = state.stage
    const gatePassed = STAGES.indexOf(stageReached) >= STAGES.indexOf('stone_pickaxe')
    const exact = state.mismatches.length === 0
    for (const mismatch of state.mismatches) console.log(`mismatch: ${mismatch.label} expected ${short(mismatch.expected)} observed ${short(mismatch.observed)}`)
    console.log(gatePassed && exact ? 'GATE PASS' : `GATE FAIL (${gatePassed ? `${state.mismatches.length} exact checks mismatched` : 'stopped before the stone pickaxe'})`)
    console.log(`iron: reached ${stageReached}`)
    console.log(`took ${Math.round((Date.now() - startedAt) / 1000)} s`)
    process.exit(gatePassed && exact ? 0 : 1)
  })
