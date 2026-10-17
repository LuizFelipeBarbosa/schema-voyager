// SchemaVoyager bot server: one Mineflayer bot exposed over HTTP.
// The HTTP contract lives in contracts/bot_api.md.

const express = require('express')
const mineflayer = require('mineflayer')
const { pathfinder, Movements } = require('mineflayer-pathfinder')
const { mine } = require('./lib/mine')
const { craft } = require('./lib/craft')
const { explore, newSpiral } = require('./lib/explore')
const {
  inventoryCounts, positionOf, round, scanNearbyBlocks, sleepTicks, stopMoving
} = require('./lib/world')

const config = {
  host: process.env.MC_HOST || '127.0.0.1',
  port: Number(process.env.MC_PORT || 25565),
  version: process.env.MC_VERSION || '1.21.1',
  username: process.env.BOT_USERNAME || 'SchemaBot',
  apiPort: Number(process.env.BOT_API_PORT || 3000),
  viewerPort: Number(process.env.VIEWER_PORT ?? 3007) // 0 disables the viewer
}
const RECONNECT_DELAY_MS = 3000
const ACTION_TIMEOUT_MS = 120000
const MAX_WALK_STEP = 2 // larger per-tick jumps are teleports, not walking

let bot = null
let connected = false
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
  current.once('end', (reason) => {
    connected = false
    if (current.viewer) current.viewer.close()
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

function startViewer (current) {
  if (!config.viewerPort) return
  try {
    require('prismarine-viewer').mineflayer(current, { port: config.viewerPort, firstPerson: false, viewDistance: 4 })
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
    nearby_blocks: scanNearbyBlocks(bot),
    blocks_walked: walked()
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

const validators = {
  none: () => null,
  mine: (body) => {
    if (!isName(body.block)) return '"block" must be a non-empty string'
    if (body.tool != null && !isName(body.tool)) return '"tool" must be a string or null'
    return null
  },
  craft: (body) => {
    if (!isName(body.item)) return '"item" must be a non-empty string'
    if (body.times != null && !(Number.isInteger(body.times) && body.times > 0)) return '"times" must be a positive integer'
    return null
  },
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

// Registers a POST action: validation, one-at-a-time lock, connection check,
// timeout, and one log line per request.
function action (path, validate, handler) {
  app.post(path, async (req, res) => {
    const startedAt = Date.now()
    const body = req.body || {}
    const respond = (status, payload) => {
      res.status(status).json(payload)
      log(`POST ${path} ${JSON.stringify(body)} -> ${status} in ${Date.now() - startedAt} ms ${JSON.stringify(payload)}`)
    }

    const problem = validate(body)
    if (problem) return respond(400, { ok: false, error: 'bad_request', detail: problem })
    if (busy) return respond(409, { ok: false, error: 'busy' })
    if (!connected) return respond(200, { ok: false, error: 'not_connected' })

    busy = true
    try {
      respond(200, await withTimeout(Promise.resolve().then(() => handler(body)), ACTION_TIMEOUT_MS))
    } catch (err) {
      if (!connected) {
        respond(200, { ok: false, error: 'not_connected' })
      } else if (err.code === 'timeout') {
        interruptBot()
        respond(200, { ok: false, error: 'timeout', blocks_walked: walked() })
      } else {
        respond(500, { ok: false, error: 'internal', detail: err.stack })
      }
    } finally {
      busy = false
    }
  })
}

const app = express()
app.use(express.json())

app.get('/health', (req, res) => {
  res.json({ ok: true, connected, username: config.username, version: config.version })
})

action('/observe', validators.none, () => observe())
action('/mine', validators.mine, async (body) => ({ ...await mine(bot, body, episode.skippedTargets), blocks_walked: walked() }))
action('/craft', validators.craft, async (body) => ({ ...await craft(bot, body), blocks_walked: walked() }))
action('/explore', validators.none, async () => ({ ...await explore(bot, episode), blocks_walked: walked() }))
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
