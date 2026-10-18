// SchemaVoyager bot server: one Mineflayer bot exposed over HTTP.
// The HTTP contract lives in contracts/bot_api.md.

const express = require('express')
const mineflayer = require('mineflayer')
const { pathfinder, Movements } = require('mineflayer-pathfinder')
const { mine } = require('./lib/mine')
const { attack, validateAttack } = require('./lib/attack')
const { sprintAllowed } = require('./lib/movements')
const { craft } = require('./lib/craft')
const { explore, newSpiral, trackWorldBorder } = require('./lib/explore')
const { probe } = require('./lib/probe')
const { smelt } = require('./lib/smelt')
const { validateStation } = require('./lib/station')
const { validateProtect } = require('./lib/protect')
const { clear, place, validateClear, validatePlace } = require('./lib/place')
const { scan, validateScan } = require('./lib/scan')
const { survey, validateSurvey } = require('./lib/survey')
const { goto, validateGoto } = require('./lib/goto')
const { toss, validateToss } = require('./lib/toss')
const { deposit, readChest, validateChest, validateDeposit, validateWithdraw, withdraw } = require('./lib/chest')
const { createUnreachableMemory, look } = require('./lib/targets')
const { attachEventTracker, createEventLog } = require('./lib/events')
const { attachChatCommands } = require('./lib/chat_commands')
const { attachSurvival } = require('./lib/survival')
const { createFollow, validateFollow } = require('./lib/follow')
const { DUEL_TIMEOUT_S, runDuel, validateDuel } = require('./lib/duel')
const { MODES, createGuard } = require('./lib/guard')
const { createActionLock } = require('./lib/action_lock')
const { DIRECTIONS } = require('./lib/geometry')
const { hasOpenSky } = require('./lib/sky')
const { nearbyEntities } = require('./lib/entities')
const { VIEWER_MODES, createViewerControl, localCors } = require('./lib/viewer')
const {
  freeSlots, inventoryCounts, positionOf, round, scanNearbyBlocks, sleepTicks, stopMoving
} = require('./lib/world')

const config = {
  host: process.env.MC_HOST || '127.0.0.1',
  port: Number(process.env.MC_PORT || 25565),
  version: process.env.MC_VERSION || '1.21.1',
  username: process.env.BOT_USERNAME || 'SchemaBot',
  apiPort: Number(process.env.BOT_API_PORT || 3000),
  viewerPort: Number(process.env.VIEWER_PORT ?? 3007), // 0 disables the viewer
  viewerMode: VIEWER_MODES.includes(process.env.VIEWER_MODE) ? process.env.VIEWER_MODE : 'third'
}
const RECONNECT_DELAY_MS = 3000
const ACTION_TIMEOUT_MS = 120000
const MAX_WALK_STEP = 2 // larger per-tick jumps are teleports, not walking
const BUILD_TIMEOUT_MS = 60000 // /place and /clear: the 20 s walk plus the placing or digging
const SURVEY_TIMEOUT_MS = 30000
const GOTO_TIMEOUT_MS = 60000
const CHEST_TIMEOUT_MS = 60000
const TOSS_TIMEOUT_MS = 20000 // the 10 s walk plus the tosses and the settle
const SMELT_TIMEOUT_BASE_MS = 30000
const SMELT_TIMEOUT_PER_ITEM_MS = 12000

let bot = null
let connected = false
// Events outlive reconnects (seq increases for the process's lifetime); the
// tracker and the border follow the current bot.
const eventLog = createEventLog()
let tracker = null
let survival = null
// The guard's mode outlives reconnects; the guard follows the current bot.
let guardMode = process.env.BOT_GUARD || 'defend'
let guard = null
let follow = null
let pvpMovements = null
const GUARD_TICK_EVERY = 4 // physics ticks
let worldBorder = null
// Mine targets that could not be reached; kept across episodes and reconnects.
const unreachable = createUnreachableMemory()
// skippedTargets: mine targets that failed as unreachable or dig_failed this episode.
const episode = { origin: null, spiral: newSpiral(), walked: 0, skippedTargets: new Set() }

function log (message) {
  console.log(`${new Date().toISOString()} ${message}`)
}

// --- Bot lifecycle ---------------------------------------------------------

function connect () {
  log(`connecting to ${config.host}:${config.port} as ${config.username}`)
  const current = mineflayer.createBot({
    host: config.host,
    port: config.port,
    username: config.username,
    version: config.version,
    auth: 'offline',
    // Silences a multi-line stack for the 1.21.1 declare_recipes packet, which
    // minecraft-protocol cannot parse (Mineflayer uses minecraft-data recipes
    // instead). Bot errors are still logged by the 'error' handler below.
    hideErrors: true
  })
  bot = current
  current.loadPlugin(pathfinder)
  current.loadPlugin(require('mineflayer-pvp').plugin)
  survival = attachSurvival(current, eventLog)
  const currentGuard = createGuard(current, { eventLog, mode: guardMode, movements: () => pvpMovements })
  guard = currentGuard
  const currentFollow = createFollow(current, {
    guard: currentGuard, eventLog, isFree: () => actionLock.busyDetail() === null, movements: () => pvpMovements
  })
  follow = currentFollow
  tracker = attachEventTracker(current, eventLog)
  worldBorder = trackWorldBorder(current._client)

  current.once('spawn', () => {
    connected = true
    pvpMovements = worldPreservingMovements(current)
    current.pathfinder.setMovements(pvpMovements)
    current.pvp.movements = pvpMovements
    if (!episode.origin) episode.origin = current.entity.position.clone()
    startViewer(current)
    log(`spawned at ${JSON.stringify(positionOf(current))}`)
  })
  // The difficulty can change after the spawn; mineflayer has updated bot.game.difficulty by now.
  current._client.on('difficulty', () => {
    if (pvpMovements) pvpMovements.allowSprinting = sprintAllowed(current)
  })
  current.on('move', (oldPos) => {
    const { x, z } = current.entity.position
    const step = Math.hypot(x - oldPos.x, z - oldPos.z)
    if (step < MAX_WALK_STEP) episode.walked += step
  })
  current.on('kicked', (reason) => log(`kicked: ${JSON.stringify(reason)}`))
  current.on('error', (err) => log(`bot error: ${err.message}`))
  attachChatCommands(current, eventLog)
  let physicsTicks = 0
  current.on('physicsTick', () => {
    if (!connected || ++physicsTicks % GUARD_TICK_EVERY !== 0) return
    try {
      actionLock.onTick(currentGuard.mode === 'off' ? null : currentGuard.check())
      currentFollow.tick() // after onTick, which may have started a fight
    } catch (err) {
      log(`guard tick failed: ${err.stack}`)
    }
  })
  current.once('end', (reason) => {
    connected = false
    currentGuard.dispose()
    currentFollow.dispose()
    if (current.viewer) current.viewer.close()
    viewerControl.detach(current)
    log(`disconnected (${reason}); reconnecting in ${RECONNECT_DELAY_MS / 1000} s`)
    setTimeout(connect, RECONNECT_DELAY_MS)
  })
}

// Pathing must never change the world or the inventory.
function worldPreservingMovements (current) {
  const movements = new Movements(current)
  movements.canDig = false
  movements.allow1by1towers = false
  movements.scafoldingBlocks = []
  movements.allowSprinting = sprintAllowed(current)
  return movements
}

// prismarine-viewer only sends the camera position on 'move', so a browser
// opened while the bot stands still sees empty sky. Re-send the position
// while idle; the walked-distance listener adds 0 for a zero-length move.
const VIEWER_HEARTBEAT_MS = 1000

const viewerControl = createViewerControl({
  port: config.viewerPort,
  mode: config.viewerMode,
  launch: (current, options) => require('prismarine-viewer').mineflayer(current, options)
})

function startViewer (current) {
  if (!config.viewerPort) return
  try {
    viewerControl.attach(current)
  } catch (err) {
    log(`viewer failed to start: ${err.message}`)
    return
  }
  const heartbeat = setInterval(() => {
    if (connected && actionLock.busyDetail() === null && current.entity) current.emit('move', current.entity.position.clone())
  }, VIEWER_HEARTBEAT_MS)
  current.once('end', () => clearInterval(heartbeat))
}

// Stops whatever the bot is doing (used on reset and on action timeout).
function interruptBot () {
  stopMoving(bot)
  if (bot.targetDigBlock) bot.stopDigging()
  if (bot.currentWindow) bot.closeWindow(bot.currentWindow)
}

// --- Actions ---------------------------------------------------------------

function walked () {
  return round(episode.walked, 1)
}

function observe () {
  return {
    ok: true,
    position: positionOf(bot),
    inventory: inventoryCounts(bot),
    free_slots: freeSlots(bot),
    nearby_blocks: scanNearbyBlocks(bot),
    nearby_entities: nearbyEntities(bot),
    blocks_walked: walked(),
    sky: hasOpenSky(bot)
  }
}

async function resetEpisode () {
  follow?.stop()
  interruptBot()
  // Settle first so a teleport sent just before this call is applied before
  // the new origin is taken.
  await sleepTicks(10)
  episode.origin = bot.entity.position.clone()
  episode.spiral = newSpiral()
  episode.walked = 0
  episode.skippedTargets.clear()
  return observe()
}

// Vanilla kicks a non-op player for chat spam once a score reaches 200: +20
// per message, -1 per tick. A kick mid-episode reconnects the bot with a
// broken inventory view, so narration that would take the score past
// CHAT_SCORE_LIMIT is dropped instead (as in a fast replan loop).
const CHAT_SCORE_PER_MESSAGE = 20
const CHAT_SCORE_LIMIT = 160
const chatScore = { value: 0, at: Date.now() }

function chat ({ message }) {
  const now = Date.now()
  chatScore.value = Math.max(0, chatScore.value - (now - chatScore.at) / 50)
  chatScore.at = now
  if (chatScore.value + CHAT_SCORE_PER_MESSAGE > CHAT_SCORE_LIMIT) {
    log(`chat dropped to stay under the spam kick: ${message}`)
    return { ok: true, dropped: true }
  }
  chatScore.value += CHAT_SCORE_PER_MESSAGE
  bot.chat(message.slice(0, 250))
  return { ok: true }
}

// --- HTTP ------------------------------------------------------------------

const isName = (value) => typeof value === 'string' && value.length > 0
const isNameList = (value) => Array.isArray(value) && value.every(isName)
const isPositiveNumber = (value) => typeof value === 'number' && Number.isFinite(value) && value > 0

function validateDigOptions (body) {
  if (body.dig_through != null && typeof body.dig_through !== 'boolean') return '"dig_through" must be a boolean'
  if (body.scaffold != null && !isNameList(body.scaffold)) return '"scaffold" must be a list of item names'
  return null
}

const validators = {
  none: () => null,
  mine: (body) => {
    if (body.blocks != null) {
      if (!isNameList(body.blocks) || body.blocks.length === 0) return '"blocks" must be a non-empty list of strings'
    } else if (!isName(body.block)) {
      return '"block" must be a non-empty string'
    }
    if (body.tool != null && !isName(body.tool)) return '"tool" must be a string or null'
    return validateDigOptions(body) || (body.max_distance != null && !isPositiveNumber(body.max_distance)
      ? '"max_distance" must be a positive number'
      : validateProtect(body.protect))
  },
  attack: validateAttack,
  craft: (body) => {
    if (!isName(body.item)) return '"item" must be a non-empty string'
    if (body.times != null && !(Number.isInteger(body.times) && body.times > 0)) return '"times" must be a positive integer'
    return validateStation(body.station) || validateProtect(body.protect)
  },
  explore: (body) => {
    const directions = [...DIRECTIONS, 'up']
    if (body.direction != null && !directions.includes(body.direction)) return `"direction" must be one of ${directions.join(', ')}`
    if (body.distance != null && !isPositiveNumber(body.distance)) return '"distance" must be a positive number'
    return validateDigOptions(body) || validateProtect(body.protect)
  },
  look: (body) => {
    if (body.blocks != null && !isNameList(body.blocks)) return '"blocks" must be a list of strings'
    if (body.mobs != null && !isNameList(body.mobs)) return '"mobs" must be a list of strings'
    if (body.mobs?.includes('player')) return '"mobs" cannot contain "player"'
    if (!body.blocks?.length && !body.mobs?.length) return '"blocks" or "mobs" must be a non-empty list of strings'
    return body.radius != null && !isPositiveNumber(body.radius) ? '"radius" must be a positive number' : validateProtect(body.protect)
  },
  smelt: (body) => {
    if (!isName(body.ingredient) || !isName(body.item) || !isName(body.fuel)) return '"ingredient", "item" and "fuel" must be non-empty strings'
    if (body.times != null && !(Number.isInteger(body.times) && body.times > 0)) return '"times" must be a positive integer'
    return validateStation(body.station) || validateProtect(body.protect)
  },
  probe: (body) => {
    const { grid } = body
    const isSquare = Array.isArray(grid) && (grid.length === 2 || grid.length === 3) && grid.every((row) => Array.isArray(row) && row.length === grid.length)
    if (!isSquare) return '"grid" must be a 2x2 or 3x3 list of rows'
    return grid.flat().every((cell) => cell === null || isName(cell)) ? null : 'grid cells must be item names or null'
  },
  status: (body) => (body.since != null && !Number.isInteger(body.since) ? '"since" must be an integer' : null),
  toss: validateToss,
  place: validatePlace,
  clear: validateClear,
  scan: validateScan,
  survey: validateSurvey,
  goto: validateGoto,
  deposit: validateDeposit,
  withdraw: validateWithdraw,
  chest: validateChest,
  chat: (body) => (typeof body.message === 'string' ? null : '"message" must be a string')
}

const actionLock = createActionLock({
  eventLog,
  interruptBot: () => interruptBot(),
  stopForGuard: () => {
    stopMoving(bot)
    if (bot.targetDigBlock) bot.stopDigging()
  },
  guard: { respond: (found, options) => guard.respond(found, options) },
  survival: { hold: () => survival.hold(), onIdleTick: () => survival.onIdleTick() },
  reconnect: () => bot.quit()
})

// Answers `res` and writes the request's one log line.
function responder (path, body, res) {
  const startedAt = Date.now()
  return (status, payload) => {
    res.status(status).json(payload)
    log(`POST ${path} ${JSON.stringify(body)} -> ${status} in ${Date.now() - startedAt} ms ${JSON.stringify(payload)}`)
  }
}

// Registers a POST action: validation, one-at-a-time lock, connection check,
// timeout (`timeoutMs`: a number, or a function of the body), the events of
// the call added to the response, and one log line per request. The handler
// gets the body and the session {episode, tracker, border, unreachable,
// signal}. A death during the action ends it with `quarantined`; the guard
// cuts into the `cutIn` ones (see lib/action_lock.js) and the answer is
// `interrupted`.
function action (path, validate, handler, { timeoutMs = ACTION_TIMEOUT_MS, cutIn = false } = {}) {
  app.post(path, async (req, res) => {
    const body = req.body || {}
    const respond = responder(path, body, res)

    const problem = validate(body)
    if (problem) return respond(400, { ok: false, error: 'bad_request', detail: problem })
    const busyDetail = actionLock.busyDetail()
    if (busyDetail !== null) return respond(409, { ok: false, error: 'busy', detail: busyDetail })
    if (!connected) return respond(200, { ok: false, error: 'not_connected' })

    const startSeq = eventLog.lastSeq()
    const respondWithEvents = (payload) => respond(200, {
      ...payload,
      position: bot.entity ? positionOf(bot) : null,
      events: eventLog.since(startSeq)
    })
    tracker.beginAction()
    try {
      const limitMs = typeof timeoutMs === 'function' ? timeoutMs(body) : timeoutMs
      const session = { episode, tracker, border: worldBorder, unreachable }
      const result = await actionLock.run({
        path, cutIn, timeoutMs: limitMs, tracker, session, handler: (withSignal) => handler(body, withSignal)
      })
      if (!connected) {
        respond(200, { ok: false, error: 'not_connected' })
      } else if (result.kind === 'ok') {
        respondWithEvents(result.payload)
      } else if (result.kind === 'interrupted') {
        respondWithEvents({
          ok: false, error: 'interrupted', detail: { threat: result.threat, outcome: result.outcome }, blocks_walked: walked()
        })
      } else {
        respondWithEvents({ ok: false, error: result.kind, blocks_walked: walked() })
      }
    } catch (err) {
      if (!connected) respond(200, { ok: false, error: 'not_connected' })
      else respond(500, { ok: false, error: 'internal', detail: err.stack })
    }
  })
}

const app = express()
app.use(localCors)
app.use(express.json())

app.get('/health', (req, res) => {
  res.json({ ok: true, connected, username: config.username, version: config.version })
})

// Answers while an action runs: it only reads. GET takes ?since=, for the panel's live position.
function status (req, res) {
  const body = req.method === 'GET' && req.query.since != null ? { since: Number(req.query.since) } : req.body || {}
  const respond = responder('/status', body, res)
  const problem = validators.status(body)
  if (problem) return respond(400, { ok: false, error: 'bad_request', detail: problem })
  const ready = connected && bot.entity
  respond(200, {
    ok: true,
    connected,
    alive: Boolean(ready) && bot.health > 0,
    health: ready ? bot.health : null,
    food: ready ? bot.food : null,
    position: ready ? positionOf(bot) : null,
    nearby_entities: ready ? nearbyEntities(bot) : {},
    events: eventLog.since(body.since ?? 0),
    last_seq: eventLog.lastSeq(),
    guard: guard ? guard.status() : { mode: guardMode, fighting: null },
    follow: follow ? follow.status() : null
  })
}
app.post('/status', status)
app.get('/status', status)

// Sets the guard's mode; doesn't take the action lock, and works without a bot.
app.post('/guard', (req, res) => {
  const body = req.body || {}
  const respond = responder('/guard', body, res)
  try {
    if (guard) guard.setMode(body.mode)
    else if (!MODES.includes(body.mode)) throw new Error(`unknown guard mode: ${body.mode}`)
  } catch (err) {
    return respond(400, { ok: false, error: 'bad_request', detail: err.message })
  }
  guardMode = body.mode
  respond(200, { ok: true, mode: guardMode })
})

// Follows a player, or stops following; doesn't take the action lock and never answers 409.
app.post('/follow', (req, res) => {
  const body = req.body || {}
  const respond = responder('/follow', body, res)
  const problem = validateFollow(body)
  if (problem) return respond(400, { ok: false, error: 'bad_request', detail: problem })
  if (!connected || !bot.entity) return respond(200, { ok: false, error: 'not_connected' })
  respond(200, follow.start(body.player, body.distance ?? undefined))
})

app.post('/follow/stop', (req, res) => {
  const respond = responder('/follow/stop', req.body || {}, res)
  respond(200, { ok: true, player: follow ? follow.stop() : null })
})

// Restarts the viewer in another camera mode; doesn't take the action lock.
app.post('/viewer', async (req, res) => {
  const body = req.body || {}
  const respond = responder('/viewer', body, res)
  if (!VIEWER_MODES.includes(body.mode)) {
    return respond(400, { ok: false, error: 'bad_request', detail: `"mode" must be one of ${VIEWER_MODES.join(', ')}` })
  }
  if (!config.viewerPort) return respond(200, { ok: false, error: 'viewer_disabled' })
  try {
    await viewerControl.setMode(body.mode)
  } catch (err) {
    return respond(500, { ok: false, error: 'internal', detail: err.message })
  }
  respond(200, { ok: true, mode: viewerControl.mode })
})

action('/observe', validators.none, () => observe())
action('/mine', validators.mine, async (body, session) => ({ ...await mine(bot, body, session), blocks_walked: walked() }), { cutIn: true })
action('/attack', validators.attack, async (body, session) => ({ ...await attack(bot, body, { ...session, guard, movements: pvpMovements }), blocks_walked: walked() }), { cutIn: true })
// A duel runs under the action lock; /duel/stop is no action, so it answers while the duel holds the lock.
let duelStop = null // the running duel's AbortController, or null
action('/duel', validateDuel, async (body, session) => {
  if (body.player === bot.username || !bot.players[body.player]) {
    return { ok: false, error: 'not_found', detail: `player "${body.player}" is not online or is the bot itself` }
  }
  duelStop = new AbortController()
  try {
    return { ok: true, ...await runDuel(bot, body, { eventLog, movements: pvpMovements, signal: session.signal, stopSignal: duelStop.signal }) }
  } finally {
    duelStop = null
  }
}, { timeoutMs: (body) => ((body.timeout_s ?? DUEL_TIMEOUT_S) + 15) * 1000 })
app.post('/duel/stop', (req, res) => {
  const respond = responder('/duel/stop', req.body || {}, res)
  const stopped = duelStop !== null
  duelStop?.abort()
  respond(200, { ok: true, stopped })
})
action('/craft', validators.craft, async (body, session) => ({ ...await craft(bot, body, session), blocks_walked: walked() }), { cutIn: true })
action('/explore', validators.explore, async (body, session) => ({ ...await explore(bot, session, body), blocks_walked: walked() }), { cutIn: true })
action('/look', validators.look, (body) => look(bot, { ...body, blocks: body.blocks ?? [], mobs: body.mobs ?? [] }))
action('/smelt', validators.smelt, async (body, session) => ({ ...await smelt(bot, body, session), blocks_walked: walked() }), {
  timeoutMs: (body) => SMELT_TIMEOUT_BASE_MS + SMELT_TIMEOUT_PER_ITEM_MS * (body.times ?? 1),
  cutIn: true
})
action('/probe', validators.probe, (body, session) => probe(bot, body, session))
action('/toss', validators.toss, (body, session) => toss(bot, body, session), { timeoutMs: TOSS_TIMEOUT_MS })
action('/place', validators.place, (body, session) => place(bot, body, session), { timeoutMs: BUILD_TIMEOUT_MS, cutIn: true })
action('/clear', validators.clear, (body, session) => clear(bot, body, session), { timeoutMs: BUILD_TIMEOUT_MS, cutIn: true })
action('/scan', validators.scan, (body) => scan(bot, body))
action('/survey', validators.survey, (body) => survey(bot, body), { timeoutMs: SURVEY_TIMEOUT_MS })
action('/goto', validators.goto, (body, session) => goto(bot, body, session), { timeoutMs: GOTO_TIMEOUT_MS, cutIn: true })
action('/deposit', validators.deposit, (body, session) => deposit(bot, body, session), { timeoutMs: CHEST_TIMEOUT_MS, cutIn: true })
action('/withdraw', validators.withdraw, (body, session) => withdraw(bot, body, session), { timeoutMs: CHEST_TIMEOUT_MS, cutIn: true })
action('/chest', validators.chest, (body, session) => readChest(bot, body, session), { timeoutMs: CHEST_TIMEOUT_MS, cutIn: true })
action('/chat', validators.chat, (body) => chat(body))
action('/reset_episode', validators.none, () => resetEpisode())

// Malformed JSON bodies.
app.use((err, req, res, next) => {
  log(`${req.method} ${req.path} -> 400 ${err.message}`)
  res.status(400).json({ ok: false, error: 'bad_request', detail: err.message })
})

process.on('unhandledRejection', (err) => log(`unhandled rejection: ${err && err.stack ? err.stack : err}`))
process.on('uncaughtException', (err) => log(`uncaught exception: ${err.stack}`))

app.listen(config.apiPort, () => log(`bot API listening on :${config.apiPort}`))
connect()
