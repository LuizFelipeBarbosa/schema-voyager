// Live v3 test of M1, goals typed in chat. A second bot, `Tester`, joins the
// server and types `!goal ...` / `!cancel`; the checks read the trace panel's
// HTTP API (GET /goals.json, POST /goals), VoyagerBot's chat replies and the
// run's episode files. Contract: contracts/bot_api.md "v3.1: chat commands",
// docs/v3-interfaces.md v3.1b, v3.1f, v3.1g, v3.1h.
//
// Needs the server, VoyagerBot (BOT_OWNERS=Otopo,Tester), the trace panel and a
// Python life in play mode already running; starts nothing. Refuses the live
// demo's ports (25565, panel 8765). Environment variables are loaded from
// $SV2_HOME/.env without overriding the ones already set.
//
// Checks: 1 = a goal posted to the panel (not run by default), 2 = "!goal 4
// sticks", 3 = "!goal build a castle" (unsupported), 4 = "!goal 20 iron
// ingots" then "!cancel".
//
// Usage: node mc/bot/test/v3_chat_live.js [--checks 1,2,3,4] [--run v3-m1-live]
//          [--panel http://127.0.0.1:8767]   (exit 0 = every check passed)
// Env: SV2_MC_PORT (25566), SV2_HOME (repo root), BOT_USERNAME (VoyagerBot).

const fs = require('fs')
const path = require('path')
const mineflayer = require('mineflayer')
const { loadEnv, sleep } = require('./v2_helpers')

const DEMO_MC_PORT = 25565
const DEMO_PANEL_PORT = 8765
const TESTER_NAME = 'Tester'
const MESSAGE_SPACING_MS = 1500
const POLL_INTERVAL_MS = 2000
const SPAWN_TIMEOUT_MS = 60 * 1000
const FIND_GOAL_TIMEOUT_MS = 30 * 1000
const REPLY_TIMEOUT_MS = 60 * 1000
const TORCHES_TIMEOUT_MS = 9 * 60 * 1000
const STICKS_TIMEOUT_MS = 5 * 60 * 1000
const LONG_GOAL_START_TIMEOUT_MS = 4 * 60 * 1000
const CANCEL_TIMEOUT_MS = 3 * 60 * 1000
const FINISHED = ['done', 'failed', 'cancelled', 'unsupported']

let failures = 0

function pass (name, detail) { console.log(`PASS ${name}: ${detail}`) }
function warn (name, detail) { console.log(`WARN ${name}: ${detail}`) }
function fail (name, detail) {
  failures++
  console.log(`FAIL ${name}: ${detail}`)
}

function parseArgs (argv) {
  const options = { checks: [2, 3, 4], run: 'v3-m1-live', panel: 'http://127.0.0.1:8767' }
  for (let i = 0; i < argv.length; i += 2) {
    const value = argv[i + 1]
    if (value === undefined) throw new Error(`${argv[i]} needs a value`)
    if (argv[i] === '--checks') options.checks = value.split(',').map(Number)
    else if (argv[i] === '--run') options.run = value
    else if (argv[i] === '--panel') options.panel = value.replace(/\/$/, '')
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

async function main () {
  const home = process.env.SV2_HOME || path.resolve(__dirname, '..', '..', '..')
  loadEnv(home)
  const mcPort = Number(process.env.SV2_MC_PORT || 25566)
  const voyagerName = process.env.BOT_USERNAME || 'VoyagerBot'
  const { checks, run, panel } = parseArgs(process.argv.slice(2))
  if (mcPort === DEMO_MC_PORT) throw new Error(`SV2_MC_PORT=${mcPort} is the live demo's port; refusing`)
  if (new URL(panel).port === String(DEMO_PANEL_PORT)) throw new Error(`panel ${panel} is the live demo's; refusing`)
  const episodesDir = path.join(home, 'runs', run, 'episodes')

  // --- Panel and episode files ---

  async function listGoals () {
    const res = await fetch(`${panel}/goals.json?run=${encodeURIComponent(run)}`)
    if (res.status !== 200) throw new Error(`GET /goals.json answered ${res.status}`)
    const body = await res.json()
    if (body.run !== run) throw new Error(`GET /goals.json answered run ${body.run}, not ${run}`)
    return body.goals
  }

  async function getGoal (id) {
    return (await listGoals()).find((goal) => goal.id === id)
  }

  // Waits for a goal with this text whose id is not in `knownIds`; null on timeout.
  async function findNewGoal (text, knownIds) {
    return waitFor(async () => (await listGoals()).reverse().find((goal) => goal.text === text && !knownIds.has(goal.id)), FIND_GOAL_TIMEOUT_MS)
  }

  // Polls until the goal is in one of `statuses`; returns it (null on timeout).
  async function waitForStatus (id, statuses, timeoutMs) {
    return waitFor(async () => {
      const goal = await getGoal(id)
      return goal && statuses.includes(goal.status) ? goal : null
    }, timeoutMs)
  }

  // The events of the episode whose `goal` event has this goal id (null if none yet).
  function readEpisode (goalId) {
    let files = []
    try { files = fs.readdirSync(episodesDir).filter((name) => name.endsWith('.jsonl')) } catch (err) { return null }
    for (const file of files) {
      const events = fs.readFileSync(path.join(episodesDir, file), 'utf8').split('\n').filter(Boolean).flatMap((line) => {
        try { return [JSON.parse(line)] } catch (err) { return [] } // a half-written last line
      })
      if (events.some((event) => event.kind === 'goal' && event.goal_id === goalId)) return events
    }
    return null
  }

  const countSteps = (goalId) => (readEpisode(goalId) || []).filter((event) => event.kind === 'step').length

  // --- Tester: sends chat, hears VoyagerBot ---

  const heard = [] // {at, text} from VoyagerBot
  const tester = mineflayer.createBot({
    host: '127.0.0.1',
    port: mcPort,
    username: TESTER_NAME,
    version: process.env.MC_VERSION || '1.21.1',
    auth: 'offline'
  })
  tester.on('chat', (username, message) => {
    if (username === voyagerName) heard.push({ at: Date.now(), text: message })
  })
  tester.on('error', (err) => console.log(`tester error: ${err.message}`))

  let lastSentAt = 0
  async function say (message) {
    await sleep(Math.max(0, lastSentAt + MESSAGE_SPACING_MS - Date.now()))
    lastSentAt = Date.now()
    tester.chat(message)
    console.log(`Tester says: ${message}`)
  }

  // Waits for a VoyagerBot message that matches, heard at or after `since`.
  async function hearReply (matches, since, timeoutMs = REPLY_TIMEOUT_MS) {
    return waitFor(() => heard.find((message) => message.at >= since && matches(message.text)), timeoutMs, 250)
  }

  // Says `!goal <text>` and returns the new goal from the panel (null if it never shows).
  async function sayGoal (text) {
    const knownIds = new Set((await listGoals()).map((goal) => goal.id))
    const saidAt = Date.now()
    await say(`!goal ${text}`)
    const goal = await findNewGoal(text, knownIds)
    return { goal, saidAt }
  }

  // Waits for the goal to be `done`, printing PASS or FAIL for the check.
  async function expectDone (name, id, timeoutMs) {
    const ended = await waitForStatus(id, FINISHED, timeoutMs)
    if (!ended) return fail(name, `${id} not finished within ${timeoutMs / 1000} s`)
    if (ended.status === 'done') return pass(name, `${id} done, target_count ${ended.target_count}`)
    fail(name, `${id} ${ended.status}: ${ended.reason}`)
  }

  // --- Checks ---

  async function check1 () {
    const name = '1: a goal posted to the panel runs to done'
    const res = await fetch(`${panel}/goals`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text: 'get me 10 torches', run })
    })
    if (res.status !== 201) return fail(name, `POST /goals answered ${res.status}`)
    const { id } = await res.json()
    if (!id) return fail(name, 'POST /goals did not answer a goal id')
    await expectDone(name, id, TORCHES_TIMEOUT_MS)
  }

  async function check2 () {
    const name = '2: "!goal 4 sticks" is queued and done'
    const { goal, saidAt } = await sayGoal('4 sticks')
    if (!goal) return fail(name, 'no goal with text "4 sticks" appeared on the panel')
    const queued = await hearReply((text) => text.includes(`Queued ${goal.id}`), saidAt)
    if (queued) pass(name, `heard "${queued.text}"`)
    else warn(name, `did not hear "Queued ${goal.id}" within ${REPLY_TIMEOUT_MS / 1000} s`)
    await expectDone(name, goal.id, STICKS_TIMEOUT_MS)
  }

  async function check3 () {
    const name = '3: "!goal build a castle" is unsupported'
    const { goal, saidAt } = await sayGoal('build a castle')
    if (!goal) return fail(name, 'no goal with text "build a castle" appeared on the panel')
    const reply = await hearReply((text) => text.includes("can't build"), saidAt)
    const unsupported = await waitForStatus(goal.id, FINISHED, REPLY_TIMEOUT_MS)
    if (!reply) return fail(name, `${goal.id}: did not hear "can't build" within ${REPLY_TIMEOUT_MS / 1000} s`)
    if (!unsupported || unsupported.status !== 'unsupported' || !unsupported.reason) {
      return fail(name, `${goal.id}: panel shows ${JSON.stringify(unsupported && { status: unsupported.status, reason: unsupported.reason })}`)
    }
    pass(name, `heard "${reply.text}"; ${goal.id} unsupported: ${unsupported.reason}`)
  }

  async function check4 () {
    const name = '4: "!cancel" interrupts a running user goal'
    const { goal } = await sayGoal('20 iron ingots')
    if (!goal) return fail(name, 'no goal with text "20 iron ingots" appeared on the panel')

    const running = await waitFor(async () => {
      const current = await getGoal(goal.id)
      if (FINISHED.includes(current.status)) return current
      return current.status === 'running' && countSteps(goal.id) >= 2 ? current : null
    }, LONG_GOAL_START_TIMEOUT_MS)
    if (!running) return fail(name, `${goal.id} not running with 2 steps within ${LONG_GOAL_START_TIMEOUT_MS / 1000} s`)
    if (FINISHED.includes(running.status)) return fail(name, `${goal.id} ended before the cancel: ${running.status}: ${running.reason}`)

    const stepsAtCancel = countSteps(goal.id)
    const cancelledAt = Date.now()
    await say('!cancel')
    const interrupted = await waitFor(() => {
      return (readEpisode(goal.id) || []).find((event) => event.kind === 'goal_interrupted' && event.reason === 'cancelled')
    }, CANCEL_TIMEOUT_MS)
    if (!interrupted) return fail(name, `no goal_interrupted with reason cancelled for ${goal.id} within ${CANCEL_TIMEOUT_MS / 1000} s`)

    const secondsToInterrupt = ((Date.now() - cancelledAt) / 1000).toFixed(1)
    const stepsAfter = countSteps(goal.id)
    const heardCancelled = heard.some((message) => message.at >= cancelledAt && message.text.includes(`Cancelled ${goal.id}`))
    const final = await waitForStatus(goal.id, ['cancelled'], REPLY_TIMEOUT_MS)
    const detail = `${goal.id}: steps at cancel ${stepsAtCancel}, after ${stepsAfter}, ${secondsToInterrupt} s to goal_interrupted, "Cancelled ${goal.id}" reply ${heardCancelled ? 'heard' : 'not heard'}`
    if (!final) return fail(name, `panel does not show ${goal.id} cancelled; ${detail}`)
    if (stepsAfter - stepsAtCancel > 1) return fail(name, `more than 1 step after the cancel; ${detail}`)
    pass(name, detail)
  }

  // --- Run ---

  try {
    await listGoals() // fail fast, with a clear message, if the panel or run is wrong
    const spawned = await new Promise((resolve) => {
      tester.once('spawn', () => resolve(true))
      setTimeout(() => resolve(false), SPAWN_TIMEOUT_MS)
    })
    if (!spawned) throw new Error(`${TESTER_NAME} did not spawn on port ${mcPort} within ${SPAWN_TIMEOUT_MS / 1000} s`)
    console.log(`${TESTER_NAME} spawned on port ${mcPort}; run ${run}, panel ${panel}, checks ${checks.join(',')}`)

    const available = { 1: check1, 2: check2, 3: check3, 4: check4 }
    for (const number of checks) {
      if (!available[number]) throw new Error(`unknown check ${number}`)
      await available[number]()
    }
  } finally {
    tester.quit()
    await sleep(500)
  }
  console.log(failures === 0 ? 'ALL PASS' : `${failures} FAILED`)
  process.exit(failures === 0 ? 0 : 1)
}

main().catch((err) => {
  console.log(`FAIL ${err.message}`)
  process.exit(1)
})
