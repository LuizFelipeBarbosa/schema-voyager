// Live checks for the two bot bugs behind D's noisy layout-8 holdout run:
//   table: when the server refuses the closest cells for the crafting table
//          (armor stands stand in the four cells next to the bot), /craft
//          places it at another cell, crafts, and picks the table back up.
//   mine:  a log out of reach fails as unreachable once, the next /mine
//          mines the next nearest log, /reset_episode clears the skip list,
//          and once the skipped log is the only one left it is tried again
//          rather than reported not_found.
//   chat:  a burst of narration (D's instant replans sent one line each and
//          got the bot kicked for spam) is queued and all of it is sent,
//          paced so that vanilla does not kick the bot.
//
// Starts its own bot (FixBot on 3102, viewer off), clears the arena interior
// and works 12 blocks from spawn, away from SchemaBot. Run it only while no
// episode is running; the next episode rebuilds the arena.
//
// Usage: node mc/bot/test/layout8_fixes.js   (exit 0 = pass)

const readline = require('readline')
const {
  createChecker, post, rcon, sameCounts, sleep, startServer, stopServer, waitForConnected
} = require('./helpers')

const USERNAME = 'FixBot'
const API_PORT = 3102
const CX = 12
const CZ = 12
const TAG = 'layout8_fixes'
const NEXT_TO_BOT = [[1, 0], [-1, 0], [0, 1], [0, -1]]
const OUT_OF_REACH_LOG = [CX + 1, -55, CZ] // 5 blocks up, nothing to stand on: 5.6 blocks from the bot
const GROUND_LOG = [CX + 6, -60, CZ] // reachable, 6.0 blocks from the bot
const FAR_LOG = [CX - 7, -60, CZ] // reachable, the farthest
const CHAT_BURST = 20 // vanilla would kick after 10 in quick succession

function clearArena () {
  rcon(`kill @e[type=armor_stand,tag=${TAG}]`)
  rcon('kill @e[type=item]')
  rcon('fill -24 -60 -24 24 -50 24 minecraft:air')
}

function resetBot (items) {
  rcon(`clear ${USERNAME}`)
  for (const [item, count] of Object.entries(items)) rcon(`give ${USERNAME} minecraft:${item} ${count}`)
  rcon(`tp ${USERNAME} ${CX + 0.5} -60 ${CZ + 0.5} 0 0`)
}

const samePos = (target, [x, y, z]) => Boolean(target) && target.x === x && target.y === y && target.z === z

async function tableCase (checker, refusals) {
  clearArena()
  resetBot({ crafting_table: 1, oak_planks: 3, stick: 2 })
  for (const [dx, dz] of NEXT_TO_BOT) {
    rcon(`summon minecraft:armor_stand ${CX + dx + 0.5} -60 ${CZ + dz + 0.5} {NoGravity:1b,Invulnerable:1b,Tags:["${TAG}"]}`)
  }
  await sleep(500)
  await post(API_PORT, '/reset_episode')

  refusals.length = 0
  const crafted = await post(API_PORT, '/craft', { item: 'wooden_pickaxe', times: 1 })
  const r = crafted.body
  checker.check('table: craft ok', r.ok === true, { error: r.error, detail: r.detail })
  checker.check('table: the closest cells were refused first', refusals.length >= 1, refusals)
  checker.check('table: used and retrieved the table', r.used_table === true && r.table_retrieved === true, r)
  checker.check('table: delta', sameCounts(r.inventory_delta, { oak_planks: -3, stick: -2, wooden_pickaxe: 1 }), r.inventory_delta)
  rcon(`kill @e[type=armor_stand,tag=${TAG}]`)
  return { ms: crafted.ms, refused_cells: refusals.length, refusals: [...refusals] }
}

async function mineCase (checker) {
  clearArena()
  rcon(`setblock ${OUT_OF_REACH_LOG.join(' ')} minecraft:oak_log`)
  rcon(`setblock ${GROUND_LOG.join(' ')} minecraft:oak_log`)
  rcon(`setblock ${FAR_LOG.join(' ')} minecraft:oak_log`)
  resetBot({})
  await sleep(500)
  await post(API_PORT, '/reset_episode')

  const first = (await post(API_PORT, '/mine', { block: 'oak_log', tool: null })).body
  checker.check('mine: the out-of-reach log is tried first and is unreachable',
    first.error === 'unreachable' && samePos(first.target, OUT_OF_REACH_LOG), first)
  const second = (await post(API_PORT, '/mine', { block: 'oak_log', tool: null })).body
  checker.check('mine: the next call mines the ground log instead',
    second.ok === true && samePos(second.target, GROUND_LOG) && sameCounts(second.drops, { oak_log: 1 }), second)

  await post(API_PORT, '/reset_episode')
  const third = (await post(API_PORT, '/mine', { block: 'oak_log', tool: null })).body
  checker.check('mine: after /reset_episode the out-of-reach log is a target again',
    samePos(third.target, OUT_OF_REACH_LOG), third)
  const fourth = (await post(API_PORT, '/mine', { block: 'oak_log', tool: null })).body
  checker.check('mine: then the far log', fourth.ok === true && samePos(fourth.target, FAR_LOG), fourth)
  const fifth = (await post(API_PORT, '/mine', { block: 'oak_log', tool: null })).body
  checker.check('mine: with only the skipped log left, it is tried again, not reported not_found',
    fifth.error === 'unreachable' && samePos(fifth.target, OUT_OF_REACH_LOG), fifth)
  const calls = [first, second, third, fourth, fifth]
  return { errors: calls.map((call) => call.error), targets: calls.map((call) => call.target) }
}

async function chatCase (checker, kicks) {
  const startedAt = Date.now()
  for (let i = 1; i <= CHAT_BURST; i++) await post(API_PORT, '/chat', { message: `layout8_fixes chat ${i}/${CHAT_BURST}` })
  let health = null
  while (Date.now() - startedAt < 30000) {
    health = await (await fetch(`http://127.0.0.1:${API_PORT}/health`)).json()
    if (health.chat_queued === 0) break
    await sleep(500)
  }
  await sleep(2000) // a kick would arrive within a tick or two of the last message
  const drainedMs = Date.now() - startedAt - 2000
  checker.check('chat: every message was sent', health.chat_queued === 0, health)
  checker.check('chat: paced, not sent at once', drainedMs >= 8000, drainedMs)
  checker.check('chat: not kicked', kicks.length === 0, kicks)
  return { messages: CHAT_BURST, drained_ms: drainedMs }
}

async function main () {
  const checker = createChecker()
  const results = {}
  const refusals = []
  const kicks = []
  const server = startServer({ username: USERNAME, apiPort: API_PORT })
  readline.createInterface({ input: server.stdout }).on('line', (line) => {
    if (line.includes('crafting table not placed')) refusals.push(line)
    if (line.includes('kicked:')) kicks.push(line)
  })
  try {
    await waitForConnected(API_PORT)
    results.table = await tableCase(checker, refusals)
    results.mine = await mineCase(checker)
    results.chat = await chatCase(checker, kicks)
  } catch (err) {
    checker.check('no exceptions', false, err.stack)
  } finally {
    stopServer(server)
    try {
      clearArena() // arena builds do not remove armor stands
    } catch (err) {
      checker.check('cleanup', false, err.message)
    }
  }

  await sleep(200) // let the last server log lines flush
  const pass = checker.failures.length === 0
  console.log(JSON.stringify({ test: 'layout8_fixes', pass, results, failures: checker.failures }, null, 2))
  process.exit(pass ? 0 : 1)
}

main()
