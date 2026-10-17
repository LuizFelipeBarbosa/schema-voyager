// Plumbing for the v2 live test: environment, RCON over Node's net module,
// and HTTP calls to the v2 bot. It never starts a server, never uses docker,
// and refuses the live demo's ports. Nothing here prints the environment: the
// .env file holds a key.

const fs = require('fs')
const net = require('net')
const path = require('path')

const DEFAULT_SV2_HOME = '/Users/luiz/Developer/crusoe-hackathon/sv2'
const RCON_HOST = '127.0.0.1'
const RCON_LOGIN = 3
const RCON_EXEC = 2
const RCON_TIMEOUT_MS = 10000
const DEMO_RCON_PORTS = [25575]
const DEMO_BOT_PORTS = [3000]

function sleep (ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// --- Environment -------------------------------------------------------------

// Pure: KEY=VALUE lines of a .env file as an object. Skips comments and blank
// lines, accepts an `export ` prefix and single or double quotes.
function parseEnv (text) {
  const env = {}
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const match = line.replace(/^export\s+/, '').match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/)
    if (!match) continue
    let value = match[2].trim()
    const quote = value[0]
    if ((quote === '"' || quote === "'") && value.endsWith(quote) && value.length >= 2) value = value.slice(1, -1)
    else value = value.replace(/\s+#.*$/, '')
    env[match[1]] = value
  }
  return env
}

// Loads $SV2_HOME/.env into process.env without overriding what is already
// set. Returns how many variables it added (never their names or values).
function loadEnv (sv2Home = process.env.SV2_HOME || DEFAULT_SV2_HOME) {
  let text = ''
  try {
    text = fs.readFileSync(path.join(sv2Home, '.env'), 'utf8')
  } catch (err) {
    return 0
  }
  let added = 0
  for (const [key, value] of Object.entries(parseEnv(text))) {
    if (process.env[key] !== undefined) continue
    process.env[key] = value
    added++
  }
  return added
}

function requirePort (name, forbidden) {
  const port = Number(process.env[name])
  if (!Number.isInteger(port) || port <= 0) throw new Error(`${name} must be set to a port number`)
  if (forbidden.includes(port)) throw new Error(`${name}=${port} is the live demo's port; refusing`)
  return port
}

// --- RCON (Source RCON protocol) ---------------------------------------------

// Packet: int32 length, int32 id, int32 type, body, two NUL bytes; the length
// counts everything after itself.
function encodeRconPacket (id, type, body) {
  const payload = Buffer.from(body, 'utf8')
  const packet = Buffer.alloc(14 + payload.length)
  packet.writeInt32LE(10 + payload.length, 0)
  packet.writeInt32LE(id, 4)
  packet.writeInt32LE(type, 8)
  payload.copy(packet, 12)
  return packet
}

// The complete packets in `buffer` and the bytes left over.
function decodeRconPackets (buffer) {
  const packets = []
  let offset = 0
  while (buffer.length - offset >= 4) {
    const length = buffer.readInt32LE(offset)
    if (buffer.length - offset < 4 + length) break
    packets.push({
      id: buffer.readInt32LE(offset + 4),
      type: buffer.readInt32LE(offset + 8),
      body: buffer.toString('utf8', offset + 12, offset + 4 + length - 2)
    })
    offset += 4 + length
  }
  return { packets, rest: buffer.subarray(offset) }
}

class RconClient {
  constructor ({ port = requirePort('SV2_RCON_PORT', DEMO_RCON_PORTS), password = process.env.RCON_PASSWORD } = {}) {
    if (DEMO_RCON_PORTS.includes(port)) throw new Error(`RCON port ${port} is the live demo's; refusing`)
    if (!password) throw new Error('RCON_PASSWORD is not set')
    this.port = port
    this.password = password
    this.socket = null
    this.pending = Buffer.alloc(0)
    this.waiters = []
    this.nextId = 1
  }

  async connect () {
    this.socket = net.connect(this.port, RCON_HOST)
    await new Promise((resolve, reject) => {
      this.socket.once('connect', resolve)
      this.socket.once('error', reject)
    })
    this.socket.on('data', (chunk) => this.onData(chunk))
    const id = this.nextId++
    this.socket.write(encodeRconPacket(id, RCON_LOGIN, this.password))
    const reply = await this.waitForPacket((packet) => packet.type === RCON_EXEC)
    if (reply.id === -1) throw new Error('RCON login refused')
  }

  onData (chunk) {
    const { packets, rest } = decodeRconPackets(Buffer.concat([this.pending, chunk]))
    this.pending = rest
    for (const packet of packets) {
      const index = this.waiters.findIndex((waiter) => waiter.matches(packet))
      if (index >= 0) this.waiters.splice(index, 1)[0].resolve(packet)
    }
  }

  waitForPacket (matches) {
    return new Promise((resolve, reject) => {
      const waiter = { matches, resolve: (packet) => { clearTimeout(timer); resolve(packet) } }
      const timer = setTimeout(() => {
        this.waiters.splice(this.waiters.indexOf(waiter), 1)
        reject(new Error('RCON timed out'))
      }, RCON_TIMEOUT_MS)
      this.waiters.push(waiter)
    })
  }

  async exec (command) {
    const id = this.nextId++
    const reply = this.waitForPacket((packet) => packet.id === id)
    this.socket.write(encodeRconPacket(id, RCON_EXEC, command))
    return (await reply).body
  }

  close () {
    if (this.socket) this.socket.destroy()
  }
}

// --- Checks on bot responses -----------------------------------------------------

const QUARANTINE_EVENTS = new Set(['damage', 'death', 'respawn', 'target_despawned', 'interference'])

// Why a response says nothing about the rules (null if it does): the bot died
// or was hurt, a block moved under it, or (craft and smelt only) it picked up
// an item that was not its own.
function quarantineReason (body, { stationStep = false } = {}) {
  if (body.error === 'quarantined') return 'quarantined'
  const event = (body.events || []).find((candidate) =>
    QUARANTINE_EVENTS.has(candidate.type) || (stationStep && candidate.type === 'stray_pickup'))
  return event ? event.type : null
}

// Exact check of an inventory delta: every `fixed` entry equals its value, and
// the entries matching `tag.test` (a variant family: any planks, any
// cobblestone) add up to `tag.total`, all on the side of the sign of the total.
// Entries named in `ignore` (a smelt's fuel) are not checked.
function deltaMatches (delta, { fixed = {}, tag = null, ignore = [] } = {}) {
  const rest = { ...delta }
  for (const name of ignore) delete rest[name]
  for (const [name, value] of Object.entries(fixed)) {
    if ((rest[name] || 0) !== value) return false
    delete rest[name]
  }
  if (tag) {
    let sum = 0
    for (const [name, value] of Object.entries(rest)) {
      if (!tag.test(name)) continue
      if (Math.sign(value) !== Math.sign(tag.total)) return false
      sum += value
      delete rest[name]
    }
    if (sum !== tag.total) return false
  }
  return Object.keys(rest).length === 0
}

// The nearest block over a /look reply's directions: {direction, name, distance}, or null.
function nearestInLook (directions) {
  let best = null
  for (const [direction, found] of Object.entries(directions || {})) {
    for (const [name, hit] of Object.entries(found)) {
      if (!best || hit.distance < best.distance) best = { direction, name, distance: hit.distance }
    }
  }
  return best
}

// --- HTTP ----------------------------------------------------------------------

function botUrl (route) {
  return `http://127.0.0.1:${requirePort('SV2_BOT_PORT', DEMO_BOT_PORTS)}${route}`
}

// POSTs a JSON body to the bot; returns {status, body, ms}.
async function post (route, body = {}) {
  const startedAt = Date.now()
  const res = await fetch(botUrl(route), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body)
  })
  return { status: res.status, body: await res.json(), ms: Date.now() - startedAt }
}

async function health () {
  return (await fetch(botUrl('/health'))).json()
}

module.exports = {
  DEFAULT_SV2_HOME,
  RconClient,
  botUrl,
  decodeRconPackets,
  deltaMatches,
  encodeRconPacket,
  health,
  loadEnv,
  nearestInLook,
  parseEnv,
  post,
  quarantineReason,
  requirePort,
  sleep
}
