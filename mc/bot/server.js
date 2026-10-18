// SchemaVoyager bot server: one Mineflayer bot exposed over HTTP.
// The HTTP contract lives in contracts/bot_api.md.

const express = require('express')
const mineflayer = require('mineflayer')
const { pathfinder, Movements } = require('mineflayer-pathfinder')
const { mine } = require('./lib/mine')
const { craft } = require('./lib/craft')
const { explore, newSpiral, trackWorldBorder } = require('./lib/explore')
const { probe } = require('./lib/probe')
const { smelt } = require('./lib/smelt')
const { toss, validateToss } = require('./lib/toss')
const { createUnreachableMemory, look } = require('./lib/targets')
const { attachEventTracker, createEventLog } = require('./lib/events')
const { attachChatCommands } = require('./lib/chat_commands')
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
const TOSS_TIMEOUT_MS = 20000 // the 10 s walk plus the tosses and the settle
const SMELT_TIMEOUT_BASE_MS = 30000
const SMELT_TIMEOUT_PER_ITEM_MS = 12000

let bot = null
let connected = false
// Events outlive reconnects (seq increases for the process's lifetime); the
// tracker and the border follow the current bot.
const eventLog = createEventLog()
let tracker = null
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
  tracker = attachEventTracker(current, eventLog)
  worldBorder = trackWorldBorder(current._client)

  current.once('spawn', () => {
    connected = true
    current.pathfinder.setMovements(worldPreservingMovements(current))
    if (!episode.origin) episode.origin = current.entity.position.clone()
    startViewer(current)
    log(`spawned at ${JSON.stringify(positionOf(current))}`)
  })
  current.on('move', (oldPos) => {
    const { x, z } = current.entity.position
    const step = Math.hypot(x - oldPos.x, z - oldPos.z)
    if (step < MAX_WALK_STEP) episode.walked += step
  })
  current.on('kicked', (reason) => log(`kicked: ${JSON.stringify(reason)}`))
  current.on('error', (err) => log(`bot error: ${err.message}`))
  attachChatCommands(current, eventLog)
  current.once('end', (reason) => {
    connected = false
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
    if (connected && !busy && current.entity) current.emit('move', current.entity.position.clone())
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
      : null)
  },
  craft: (body) => {
    if (!isName(body.item)) return '"item" must be a non-empty string'
    if (body.times != null && !(Number.isInteger(body.times) && body.times > 0)) return '"times" must be a positive integer'
    return null
  },
  explore: (body) => {
    const directions = [...DIRECTIONS, 'up']
    if (body.direction != null && !directions.includes(body.direction)) return `"direction" must be one of ${directions.join(', ')}`
    if (body.distance != null && !isPositiveNumber(body.distance)) return '"distance" must be a positive number'
    return validateDigOptions(body)
  },
  look: (body) => {
    if (!isNameList(body.blocks) || body.blocks.length === 0) return '"blocks" must be a non-empty list of strings'
    return body.radius != null && !isPositiveNumber(body.radius) ? '"radius" must be a positive number' : null
  },
  smelt: (body) => {
    if (!isName(body.ingredient) || !isName(body.item) || !isName(body.fuel)) return '"ingredient", "item" and "fuel" must be non-empty strings'
    return body.times != null && !(Number.isInteger(body.times) && body.times > 0) ? '"times" must be a positive integer' : null
  },
  probe: (body) => {
    const { grid } = body
    const isSquare = Array.isArray(grid) && (grid.length === 2 || grid.length === 3) && grid.every((row) => Array.isArray(row) && row.length === grid.length)
    if (!isSquare) return '"grid" must be a 2x2 or 3x3 list of rows'
    return grid.flat().every((cell) => cell === null || isName(cell)) ? null : 'grid cells must be item names or null'
  },
  status: (body) => (body.since != null && !Number.isInteger(body.since) ? '"since" must be an integer' : null),
  toss: validateToss,
  chat: (body) => (typeof body.message === 'string' ? null : '"message" must be a string')
}

let busy = false

function withTimeout (promise, ms) {
  let timer = null
  const timeout = new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Object.assign(new Error(`action timed out after ${ms} ms`), { code: 'timeout' })), ms)
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

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
// gets the body and the session {episode, tracker, border}. A death during
// the action ends it with `quarantined`.
function action (path, validate, handler, { timeoutMs = ACTION_TIMEOUT_MS } = {}) {
  app.post(path, async (req, res) => {
    const body = req.body || {}
    const respond = responder(path, body, res)

    const problem = validate(body)
    if (problem) return respond(400, { ok: false, error: 'bad_request', detail: problem })
    if (busy) return respond(409, { ok: false, error: 'busy' })
    if (!connected) return respond(200, { ok: false, error: 'not_connected' })

    busy = true
    const startSeq = eventLog.lastSeq()
    const respondWithEvents = (payload) => respond(200, {
      ...payload,
      position: bot.entity ? positionOf(bot) : null,
      events: eventLog.since(startSeq)
    })
    tracker.beginAction()
    const death = tracker.rejectOnDeath()
    try {
      const session = { episode, tracker, border: worldBorder, unreachable }
      const limitMs = typeof timeoutMs === 'function' ? timeoutMs(body) : timeoutMs
      const running = Promise.resolve().then(() => handler(body, session))
      respondWithEvents(await withTimeout(Promise.race([running, death.promise]), limitMs))
    } catch (err) {
      if (!connected) {
        respond(200, { ok: false, error: 'not_connected' })
      } else if (err.code === 'quarantined') {
        interruptBot()
        respondWithEvents({ ok: false, error: 'quarantined', blocks_walked: walked() })
      } else if (err.code === 'timeout') {
        interruptBot()
        respondWithEvents({ ok: false, error: 'timeout', blocks_walked: walked() })
      } else {
        respond(500, { ok: false, error: 'internal', detail: err.stack })
      }
    } finally {
      death.dispose()
      busy = false
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
    last_seq: eventLog.lastSeq()
  })
}
app.post('/status', status)
app.get('/status', status)

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
action('/mine', validators.mine, async (body, session) => ({ ...await mine(bot, body, session), blocks_walked: walked() }))
action('/craft', validators.craft, async (body, session) => ({ ...await craft(bot, body, session), blocks_walked: walked() }))
action('/explore', validators.explore, async (body, session) => ({ ...await explore(bot, session, body), blocks_walked: walked() }))
action('/look', validators.look, (body) => look(bot, body))
action('/smelt', validators.smelt, async (body, session) => ({ ...await smelt(bot, body, session), blocks_walked: walked() }), {
  timeoutMs: (body) => SMELT_TIMEOUT_BASE_MS + SMELT_TIMEOUT_PER_ITEM_MS * (body.times ?? 1)
})
action('/probe', validators.probe, (body, session) => probe(bot, body, session))
action('/toss', validators.toss, (body, session) => toss(bot, body, session), { timeoutMs: TOSS_TIMEOUT_MS })
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
