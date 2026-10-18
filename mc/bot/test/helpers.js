// Shared plumbing for the live bot tests: RCON, the server child process, and
// HTTP calls. Requires the schema-voyager-mc container to be running.

const { execFileSync, spawn } = require('child_process')
const path = require('path')
const readline = require('readline')

const SERVER_JS = path.join(__dirname, '..', 'server.js')
const R2_DATAPACK = 'file/r2_coal_tool'
const MAX_LOG_LINE = 400

function sleep (ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function rcon (command) {
  return execFileSync('docker', ['exec', 'schema-voyager-mc', 'rcon-cli', command], { encoding: 'utf8' }).trim()
}

async function setR2 (enabled) {
  const reply = rcon(`datapack ${enabled ? 'enable' : 'disable'} "${R2_DATAPACK}"`)
  await sleep(1500) // datapack reload
  const listed = rcon('datapack list enabled').includes(R2_DATAPACK)
  if (listed !== enabled) throw new Error(`R2 datapack should be ${enabled ? 'enabled' : 'disabled'}; rcon said: ${reply}`)
}

// Starts server.js with its own username and API port (viewer off) and echoes
// its log lines, truncated, with a prefix.
function startServer ({ username, apiPort }) {
  const child = spawn(process.execPath, [SERVER_JS], {
    env: { ...process.env, BOT_USERNAME: username, BOT_API_PORT: String(apiPort), VIEWER_PORT: '0' },
    stdio: ['ignore', 'pipe', 'pipe']
  })
  for (const stream of [child.stdout, child.stderr]) {
    readline.createInterface({ input: stream }).on('line', (line) => {
      const shown = line.length > MAX_LOG_LINE ? `${line.slice(0, MAX_LOG_LINE)}...` : line
      console.log(`  [server] ${shown}`)
    })
  }
  return child
}

function stopServer (child) {
  if (child && child.exitCode === null) child.kill('SIGTERM')
}

async function waitForConnected (apiPort, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const health = await (await fetch(`http://127.0.0.1:${apiPort}/health`)).json()
      if (health.connected) return health
    } catch (err) {
      // server not listening yet
    }
    await sleep(500)
  }
  throw new Error(`bot on :${apiPort} did not connect within ${timeoutMs} ms`)
}

// POSTs a JSON body; returns {status, body, ms}.
async function post (apiPort, route, body = {}) {
  const startedAt = Date.now()
  const res = await fetch(`http://127.0.0.1:${apiPort}${route}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body)
  })
  return { status: res.status, body: await res.json(), ms: Date.now() - startedAt }
}

// Same counts, ignoring zero entries.
function sameCounts (actual, expected) {
  const keys = new Set([...Object.keys(actual || {}), ...Object.keys(expected || {})])
  return [...keys].every((key) => ((actual || {})[key] || 0) === ((expected || {})[key] || 0))
}

// Collects named checks; `failures` lists the ones that did not hold.
function createChecker () {
  const failures = []
  return {
    failures,
    check (name, condition, actual) {
      if (!condition) failures.push({ check: name, actual })
      return condition
    }
  }
}

module.exports = {
  createChecker,
  post,
  rcon,
  sameCounts,
  setR2,
  sleep,
  startServer,
  stopServer,
  waitForConnected
}
