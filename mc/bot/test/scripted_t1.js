// Gate G2: scripted T1 (wooden pickaxe from an empty inventory) through the
// bot HTTP API only. Each run rebuilds two oak trees over RCON, resets the
// bot, mines 3 oak logs and crafts planks x3, stick, crafting_table and
// wooden_pickaxe, asserting exact vanilla inventory deltas. After the runs it
// checks the /observe and /explore response shapes once.
//
// Usage: node mc/bot/test/scripted_t1.js [--runs 3]   (exit 0 = all pass)

const {
  createChecker, post, rcon, sameCounts, sleep, startServer, stopServer, waitForConnected
} = require('./helpers')

const USERNAME = 'T1Bot'
const API_PORT = 3101
const LOGS_NEEDED = 3
const MAX_MINE_CALLS = 6
const TREES = [{ x: 7, z: 2 }, { x: -6, z: -6 }] // 7.3 and 8.5 blocks from (0, 0)
const LEAVES_PER_TREE = 3 * 3 * 2 - 1 // 3x3 cap over two layers around the top log

const CRAFTS = [
  { item: 'oak_planks', times: 3, delta: { oak_log: -3, oak_planks: 12 }, usesTable: false },
  { item: 'stick', times: 1, delta: { oak_planks: -2, stick: 4 }, usesTable: false },
  { item: 'crafting_table', times: 1, delta: { oak_planks: -4, crafting_table: 1 }, usesTable: false },
  { item: 'wooden_pickaxe', times: 1, delta: { oak_planks: -3, stick: -2, wooden_pickaxe: 1 }, usesTable: true }
]
const FINAL_INVENTORY = { oak_planks: 3, stick: 2, crafting_table: 1, wooden_pickaxe: 1 }

function parseRuns () {
  const index = process.argv.indexOf('--runs')
  const runs = index >= 0 ? Number(process.argv[index + 1]) : 3
  if (!Number.isInteger(runs) || runs < 1) throw new Error('--runs must be a positive integer')
  return runs
}

// Clears the area around (0, 0) and plants two trees: a 4-high oak_log column
// on the grass with a persistent leaves cap around and above the top log.
function buildArena () {
  rcon('gamerule randomTickSpeed 0')
  rcon('gamerule doDaylightCycle false')
  rcon('time set day')
  rcon('kill @e[type=item]')
  rcon('fill -12 -60 -12 12 -50 12 minecraft:air')
  for (const { x, z } of TREES) {
    rcon(`fill ${x} -60 ${z} ${x} -57 ${z} minecraft:oak_log`)
    rcon(`fill ${x - 1} -57 ${z - 1} ${x + 1} -56 ${z + 1} minecraft:oak_leaves[persistent=true] replace minecraft:air`)
  }
  rcon(`clear ${USERNAME}`)
  rcon(`tp ${USERNAME} 0.5 -60 0.5`)
}

async function runOnce (runNumber, checker) {
  const tag = `run ${runNumber}`
  buildArena()
  const reset = await post(API_PORT, '/reset_episode')
  checker.check(`${tag}: reset ok with empty inventory`, reset.body.ok && sameCounts(reset.body.inventory, {}), reset.body)

  const mineMs = []
  let inventory = {}
  for (let call = 0; call < MAX_MINE_CALLS && (inventory.oak_log || 0) < LOGS_NEEDED; call++) {
    const mined = await post(API_PORT, '/mine', { block: 'oak_log', tool: null })
    const r = mined.body
    mineMs.push(mined.ms)
    checker.check(`${tag}: mine ${call + 1} ok`, mined.status === 200 && r.ok, { status: mined.status, error: r.error, detail: r.detail })
    checker.check(`${tag}: mine ${call + 1} block_removed`, r.block_removed === true, r.block_removed)
    checker.check(`${tag}: mine ${call + 1} drops`, sameCounts(r.drops, { oak_log: 1 }), r.drops)
    inventory = r.inventory || {}
  }
  checker.check(`${tag}: mined ${LOGS_NEEDED} logs in ${mineMs.length} calls`, sameCounts(inventory, { oak_log: LOGS_NEEDED }), inventory)

  const craftMs = {}
  for (const step of CRAFTS) {
    const crafted = await post(API_PORT, '/craft', { item: step.item, times: step.times })
    const r = crafted.body
    craftMs[step.item] = crafted.ms
    const name = `${tag}: craft ${step.item} x${step.times}`
    checker.check(`${name} ok`, crafted.status === 200 && r.ok, { status: crafted.status, error: r.error, detail: r.detail, missing: r.missing })
    checker.check(`${name} delta`, sameCounts(r.inventory_delta, step.delta), r.inventory_delta)
    checker.check(`${name} used_table`, r.used_table === step.usesTable, r.used_table)
    if (step.usesTable) checker.check(`${name} table_retrieved`, r.table_retrieved === true, r.table_retrieved)
    inventory = r.inventory || {}
  }
  checker.check(`${tag}: ends with wooden_pickaxe 1`, (inventory.wooden_pickaxe || 0) === 1, inventory)
  checker.check(`${tag}: final inventory`, sameCounts(inventory, FINAL_INVENTORY), inventory)

  // Verification only, after the run: back at (0, 0) both trees are inside
  // the 16-block scan, so the world can be checked for collateral damage.
  const nearby = (await observeFromOrigin()).nearby_blocks || {}
  checker.check(`${tag}: no leaves broken`, nearby.oak_leaves?.count === TREES.length * LEAVES_PER_TREE, nearby.oak_leaves)
  checker.check(`${tag}: only the mined logs are gone`, nearby.oak_log?.count === TREES.length * 4 - LOGS_NEEDED, nearby.oak_log)

  return { run: runNumber, mine_calls: mineMs.length, mine_ms: mineMs, craft_ms: craftMs, final_inventory: inventory }
}

function isPosition (value) {
  return value && ['x', 'y', 'z'].every((axis) => typeof value[axis] === 'number')
}

async function observeFromOrigin () {
  rcon(`tp ${USERNAME} 0.5 -60 0.5`)
  await sleep(500)
  return (await post(API_PORT, '/observe')).body
}

// /observe and /explore response shapes.
async function checkObserveAndExplore (checker) {
  const observed = (await post(API_PORT, '/observe')).body
  const entries = Object.values(observed.nearby_blocks || {})
  checker.check('observe: shape', observed.ok === true && isPosition(observed.position) &&
    typeof observed.inventory === 'object' && typeof observed.blocks_walked === 'number' &&
    entries.length > 0 && entries.every((e) => Number.isInteger(e.count) &&
      (e.nearest === null || (isPosition(e.nearest) && typeof e.distance === 'number'))), observed)
  checker.check('observe: no excluded blocks listed', !['air', 'grass_block', 'dirt', 'bedrock']
    .some((name) => name in (observed.nearby_blocks || {})), Object.keys(observed.nearby_blocks || {}))

  const explored = (await post(API_PORT, '/explore')).body
  checker.check('explore: shape', explored.ok === true && explored.error === null &&
    isPosition(explored.position) && typeof explored.blocks_walked === 'number', explored)
  checker.check('explore: blocks_walked grew', explored.blocks_walked > observed.blocks_walked,
    { before: observed.blocks_walked, after: explored.blocks_walked })
  // First spiral step from the episode origin (0.5, 0.5) goes 10 blocks east.
  const p = explored.position || {}
  checker.check('explore: first spiral step ends near (10.5, 0.5)', Math.hypot(p.x - 10.5, p.z - 0.5) <= 1.5, p)
  return { observe_keys: Object.keys(observed), nearby_blocks: observed.nearby_blocks, explore: explored }
}

async function main () {
  const runs = parseRuns()
  const checker = createChecker()
  const results = []
  let shapes = null
  const server = startServer({ username: USERNAME, apiPort: API_PORT })
  try {
    await waitForConnected(API_PORT)
    for (let run = 1; run <= runs; run++) {
      const failuresBefore = checker.failures.length
      const result = await runOnce(run, checker)
      result.pass = checker.failures.length === failuresBefore
      results.push(result)
    }
    shapes = await checkObserveAndExplore(checker)
  } catch (err) {
    checker.check('no exceptions', false, err.stack)
  } finally {
    stopServer(server)
  }

  await sleep(200) // let the last server log lines flush
  const passedRuns = results.filter((r) => r.pass).length
  const pass = checker.failures.length === 0 && passedRuns === runs
  console.log(JSON.stringify({
    gate: 'G2',
    test: 'scripted_t1',
    pass,
    runs_passed: `${passedRuns}/${runs}`,
    runs: results,
    observe_explore: shapes,
    failures: checker.failures
  }, null, 2))
  process.exit(pass ? 0 : 1)
}

main()
