// Gate G1: the R2 loot-table spike, end to end through the bot HTTP API.
//
// R2 (datapack file/r2_coal_tool) makes coal_ore drop coal only for a
// stone-or-better pickaxe. Checks, one freshly placed coal_ore per case:
//   R2 on  + wooden_pickaxe -> block removed, drops {}
//   R2 on  + stone_pickaxe  -> drops {coal: 1}
//   R2 off + wooden_pickaxe -> drops {coal: 1}
// plus picked_up and the final inventory. Leaves R2 enabled.
//
// Usage: node mc/bot/test/r2_spike.js   (exit 0 = pass)

const {
  createChecker, post, rcon, sameCounts, setR2, sleep, startServer, stopServer, waitForConnected
} = require('./helpers')

const USERNAME = 'SpikeBot'
const API_PORT = 3100

const CASES = [
  { name: 'R2 on, wooden_pickaxe', r2: true, tool: 'wooden_pickaxe', ore: [5, -60, 1], drops: {} },
  { name: 'R2 on, stone_pickaxe', r2: true, tool: 'stone_pickaxe', ore: [1, -60, 5], drops: { coal: 1 } },
  { name: 'R2 off, wooden_pickaxe', r2: false, tool: 'wooden_pickaxe', ore: [-5, -60, -1], drops: { coal: 1 } }
]

function prepareArena () {
  rcon('gamerule randomTickSpeed 0')
  rcon('gamerule doDaylightCycle false')
  rcon('time set day')
  rcon('fill -12 -60 -12 12 -50 12 minecraft:air')
}

async function runCase (testCase, checker) {
  const [x, y, z] = testCase.ore
  rcon('kill @e[type=item]')
  rcon(`clear ${USERNAME}`)
  rcon(`give ${USERNAME} minecraft:${testCase.tool} 1`)
  rcon(`tp ${USERNAME} 0.5 -60 0.5`)
  rcon(`setblock ${x} ${y} ${z} minecraft:coal_ore`)

  const reset = await post(API_PORT, '/reset_episode')
  checker.check(`${testCase.name}: starts holding only the tool`,
    sameCounts(reset.body.inventory, { [testCase.tool]: 1 }), reset.body.inventory)

  const mined = await post(API_PORT, '/mine', { block: 'coal_ore', tool: testCase.tool })
  const r = mined.body
  const expectedInventory = { [testCase.tool]: 1, ...testCase.drops }
  checker.check(`${testCase.name}: HTTP 200 ok`, mined.status === 200 && r.ok === true, { status: mined.status, error: r.error })
  checker.check(`${testCase.name}: target is the placed ore`, r.target && r.target.x === x && r.target.y === y && r.target.z === z, r.target)
  checker.check(`${testCase.name}: block_removed`, r.block_removed === true, r.block_removed)
  checker.check(`${testCase.name}: drops`, sameCounts(r.drops, testCase.drops), r.drops)
  checker.check(`${testCase.name}: picked_up`, sameCounts(r.picked_up, testCase.drops), r.picked_up)
  checker.check(`${testCase.name}: final inventory`, sameCounts(r.inventory, expectedInventory), r.inventory)

  return {
    case: testCase.name,
    r2: testCase.r2,
    tool: testCase.tool,
    expected_drops: testCase.drops,
    block_removed: r.block_removed,
    drops: r.drops,
    picked_up: r.picked_up,
    inventory: r.inventory,
    mine_ms: mined.ms
  }
}

async function main () {
  const checker = createChecker()
  const results = []
  const server = startServer({ username: USERNAME, apiPort: API_PORT })
  try {
    await waitForConnected(API_PORT)
    prepareArena()
    let r2Enabled = null
    for (const testCase of CASES) {
      if (testCase.r2 !== r2Enabled) {
        await setR2(testCase.r2)
        r2Enabled = testCase.r2
      }
      results.push(await runCase(testCase, checker))
    }
  } catch (err) {
    checker.check('no exceptions', false, err.stack)
  } finally {
    try {
      await setR2(true)
    } catch (err) {
      checker.check('R2 re-enabled', false, err.message)
    }
    stopServer(server)
  }

  await sleep(200) // let the last server log lines flush
  const pass = checker.failures.length === 0
  console.log(JSON.stringify({
    gate: 'G1',
    test: 'r2_spike',
    pass,
    r2_left_enabled: rcon('datapack list enabled').includes('file/r2_coal_tool'),
    cases: results,
    failures: checker.failures
  }, null, 2))
  process.exit(pass ? 0 : 1)
}

main()
