// Live v3 test of the bot's building endpoints POST /place, POST /clear and
// POST /scan: a 3 x 3 floor and a 3-high pillar, read back by /scan, then torches
// and a door, then everything cleared again. Contract: contracts/bot_api.md,
// "v3.4: building actions".
//
// Uses the one bot already running at SV2_BOT_PORT and RCON at SV2_RCON_PORT
// (password RCON_PASSWORD, bot name BOT_USERNAME); starts nothing. Refuses to
// run against the live demo's ports (3000, 25575). Environment variables are
// loaded from $SV2_HOME/.env without overriding the ones already set.
//
// Everything is relative to where the bot stands: it levels a small site east of
// the bot (air above, stone below), gives the bot the blocks it needs, builds,
// clears, and leaves the site empty (air over a stone floor). Repeatable.
//
// Usage: node mc/bot/test/v3_place_live.js   (exit 0 = every check passed)

const assert = require('node:assert/strict')
const { RconClient, health, loadEnv, post, sleep } = require('./v2_helpers')

const POLL_INTERVAL_MS = 250
const POLL_TIMEOUT_MS = 8000
const BUSY_FIRST_RETRY_MS = 250
const BUSY_RETRY_INTERVAL_MS = 500
const BUSY_RETRY_TIMEOUT_MS = 60000
const AIR_NAMES = ['air', 'cave_air', 'void_air']
const SCAN_SIZE = [4, 3, 3] // the build: a 3 x 3 floor and a pillar at x 3, z 1

const short = (value) => JSON.stringify(value)

let failures = 0

// Runs one check and prints PASS or FAIL with the values it saw.
async function check (name, run) {
  try {
    console.log(`PASS ${name}: ${await run()}`)
  } catch (err) {
    failures++
    console.log(`FAIL ${name}: ${err.message}`)
  }
}

// POSTs, and retries a 409 busy the way the Python client does: after 0.25 s, then every 0.5 s, up to 60 s.
async function postRetryingBusy (path, body) {
  const deadline = Date.now() + BUSY_RETRY_TIMEOUT_MS
  let response = await post(path, body)
  for (let wait = BUSY_FIRST_RETRY_MS; response.status === 409 && Date.now() < deadline; wait = BUSY_RETRY_INTERVAL_MS) {
    await sleep(wait)
    response = await post(path, body)
  }
  return response
}

async function observe () {
  const { body } = await postRetryingBusy('/observe', {})
  return body
}

// Polls /observe until `isReady(observation)` holds; throws after 8 s.
async function waitForObservation (what, isReady) {
  const deadline = Date.now() + POLL_TIMEOUT_MS
  for (;;) {
    const observation = await observe()
    if (isReady(observation)) return observation
    if (Date.now() > deadline) throw new Error(`gave up waiting for ${what}; inventory is ${short(observation.inventory)}`)
    await sleep(POLL_INTERVAL_MS)
  }
}

// Polls /scan of the levelled region until its bottom layer is stone and the layers above are air:
// the bot's world lags the RCON fills. Throws, with the last scan, after 8 s.
async function waitForSite (origin, size) {
  const deadline = Date.now() + POLL_TIMEOUT_MS
  for (;;) {
    const { body: scanned } = await postRetryingBusy('/scan', { origin, size })
    const isLevelled = scanned.ok && scanned.layers.every((layer, y) =>
      layer.every((row) => row.every((name) => (y === 0 ? name === 'stone' : AIR_NAMES.includes(name)))))
    if (isLevelled) return
    if (Date.now() > deadline) throw new Error(`the site was not levelled after 8 s; /scan showed ${short(scanned)}`)
    await sleep(POLL_INTERVAL_MS)
  }
}

async function main () {
  loadEnv()
  const username = process.env.BOT_USERNAME || 'VoyagerBot'
  const rcon = new RconClient() // refuses the live demo's RCON port
  const status = await health() // refuses the live demo's bot port
  if (!status.connected) throw new Error('the bot is not connected to its server')
  await rcon.connect()

  try {
    const start = await observe()
    const bx = Math.floor(start.position.x)
    const by = Math.floor(start.position.y)
    const bz = Math.floor(start.position.z)
    const origin = { x: bx + 2, y: by, z: bz - 1 }
    const keepOut = { origin: [origin.x, origin.y, origin.z], size: SCAN_SIZE }
    console.log(`bot at ${short(start.position)}, site origin ${short(origin)}`)

    // Level the site: air above, solid stone below. It is east of the bot, never around it.
    await rcon.exec(`fill ${bx + 1} ${by} ${bz - 2} ${bx + 6} ${by + 4} ${bz + 2} minecraft:air`)
    await rcon.exec(`fill ${bx + 1} ${by - 1} ${bz - 2} ${bx + 6} ${by - 1} ${bz + 2} minecraft:stone`)
    await rcon.exec(`clear ${username}`)
    await rcon.exec(`give ${username} minecraft:oak_planks 32`)
    await rcon.exec(`give ${username} minecraft:torch 2`)
    await rcon.exec(`give ${username} minecraft:oak_door 1`)
    await waitForObservation('the planks, torches and door', (o) =>
      o.inventory.oak_planks === 32 && o.inventory.torch === 2 && o.inventory.oak_door === 1)
    await waitForSite([bx + 1, by - 1, bz - 2], [6, 6, 5]) // the fills' region, ground layer first

    // Cells relative to the site origin.
    const cell = (dx, dy, dz) => ({ x: origin.x + dx, y: origin.y + dy, z: origin.z + dz })
    const floorCells = []
    for (let dz = 0; dz < 3; dz++) for (let dx = 0; dx < 3; dx++) floorCells.push(cell(dx, 0, dz))
    const pillarCells = [cell(3, 0, 1), cell(3, 1, 1), cell(3, 2, 1)]
    const floorTorch = cell(1, 1, 1)
    const wallTorch = cell(4, 1, 1)
    const doorBottom = cell(0, 1, 0)

    const place = (block, at) => postRetryingBusy('/place', { block, ...at, keep_out: keepOut })
    const clear = (at) => postRetryingBusy('/clear', { ...at, keep_out: keepOut })
    const scan = async (size) => (await postRetryingBusy('/scan', { origin: keepOut.origin, size })).body
    const brief = ({ status: httpStatus, body, ms }) =>
      `${httpStatus} ${short({ ok: body.ok, error: body.error, placed: body.placed, cleared: body.cleared, found: body.found, travel: body.travel, position: body.position })} in ${ms} ms`

    // Places the cells in order; each must be freshly placed, with no scaffold left behind.
    // Every cell is tried; the failing ones are listed together.
    const placeAll = async (block, cells) => {
      const seen = []
      const failed = []
      for (const at of cells) {
        const response = await place(block, at)
        const text = `${short(at)} ${brief(response)}`
        seen.push(text)
        try {
          assert.equal(response.body.ok, true, text)
          assert.equal(response.body.placed, true, text)
          assert.equal(response.body.found, block, text)
          assert.deepEqual(response.body.travel.placed, {}, text)
        } catch (err) {
          failed.push(err.message)
        }
      }
      assert.equal(failed.length, 0, `${failed.length} of ${cells.length} cells failed: ${failed.join(' | ')}`)
      return seen.join('; ')
    }

    // Clears the cells in order; each must be dug and read back as air.
    const clearAll = async (cells) => {
      const seen = []
      for (const at of cells) {
        const response = await clear(at)
        const text = `${short(at)} ${brief(response)}`
        seen.push(text)
        assert.equal(response.body.ok, true, text)
        assert.equal(response.body.cleared, true, text)
        assert.ok(AIR_NAMES.includes(response.body.found), text)
      }
      return seen.join('; ')
    }

    const blockAt = (scanned, at) => scanned.layers[at.y - origin.y][at.z - origin.z][at.x - origin.x]

    await check('A1: a 3 x 3 floor of oak_planks', () => placeAll('oak_planks', floorCells))
    await check('A2: a 3-high pillar beside it, bottom-up', () => placeAll('oak_planks', pillarCells))

    await check('B: /scan shows planks at the floor and pillar, air elsewhere', async () => {
      const scanned = await scan(SCAN_SIZE)
      const solid = new Set([...floorCells, ...pillarCells].map((at) => `${at.x},${at.y},${at.z}`))
      const wrong = []
      for (let y = 0; y < SCAN_SIZE[1]; y++) {
        for (let z = 0; z < SCAN_SIZE[2]; z++) {
          for (let x = 0; x < SCAN_SIZE[0]; x++) {
            const expected = solid.has(`${origin.x + x},${origin.y + y},${origin.z + z}`) ? 'oak_planks' : 'air'
            if (scanned.layers[y][z][x] !== expected) wrong.push({ x, y, z, expected, got: scanned.layers[y][z][x] })
          }
        }
      }
      assert.equal(scanned.ok, true, short(scanned))
      assert.deepEqual(wrong, [], `layers ${short(scanned.layers)}`)
      return `layers ${short(scanned.layers)}`
    })

    await check('E1: placing a placed cell again changes nothing', async () => {
      const response = await place('oak_planks', floorCells[0])
      assert.equal(response.body.ok, true, brief(response))
      assert.equal(response.body.placed, false, brief(response))
      return brief(response)
    })

    await check('E2: placing into the stone ground is occupied', async () => {
      const response = await place('oak_planks', cell(1, -1, 1))
      const seen = brief(response)
      assert.equal(response.body.ok, false, seen)
      assert.equal(response.body.error, 'occupied', seen)
      assert.equal(response.body.found, 'stone', seen)
      return seen
    })

    await check('E3: placing glass, which is not held, is missing_item', async () => {
      const response = await place('glass', cell(1, 1, 1))
      const seen = brief(response)
      assert.equal(response.body.ok, false, seen)
      assert.equal(response.body.error, 'missing_item', seen)
      return seen
    })

    await check('E4: a torch on the floor reads back as torch', async () => {
      const response = await place('torch', floorTorch)
      const seen = brief(response)
      assert.equal(response.body.ok, true, seen)
      assert.equal(response.body.found, 'torch', seen)
      return seen
    })

    await check('E5: a torch beside the pillar reads back as wall_torch', async () => {
      const response = await place('torch', wallTorch)
      const seen = brief(response)
      assert.equal(response.body.ok, true, seen)
      assert.equal(response.body.found, 'wall_torch', seen)
      return seen
    })

    await check('E6: an oak_door on the floor fills both its cells', async () => {
      const response = await place('oak_door', doorBottom)
      const seen = brief(response)
      assert.equal(response.body.ok, true, seen)
      assert.equal(response.body.placed, true, seen)
      const doorCells = async () => {
        const scanned = await scan(SCAN_SIZE)
        return [blockAt(scanned, doorBottom), blockAt(scanned, cell(0, 2, 0))]
      }
      const cells = await doorCells()
      const later = await doorCells() // diagnostic only
      assert.deepEqual(cells, ['oak_door', 'oak_door'], `${seen}; scan ${short(cells)}, scanned again ${short(later)}`)
      // The server itself must hold both halves.
      const lower = await rcon.exec(`execute if block ${doorBottom.x} ${doorBottom.y} ${doorBottom.z} minecraft:oak_door[half=lower]`)
      const upper = await rcon.exec(`execute if block ${doorBottom.x} ${doorBottom.y + 1} ${doorBottom.z} minecraft:oak_door[half=upper]`)
      assert.deepEqual([lower, upper].map((answer) => /passed/i.test(answer)), [true, true], `${seen}; server says lower "${lower}", upper "${upper}"`)
      return `${seen}; scan ${short(cells)}`
    })

    await check('C1: /clear the door takes both cells', async () => {
      const seen = await clearAll([doorBottom])
      const scanned = await scan(SCAN_SIZE)
      const cells = [blockAt(scanned, doorBottom), blockAt(scanned, cell(0, 2, 0))]
      assert.ok(cells.every((name) => AIR_NAMES.includes(name)), `${seen}; scan ${short(cells)}`)
      return `${seen}; scan ${short(cells)}`
    })
    await check('C2: /clear both torches', () => clearAll([floorTorch, wallTorch]))
    await check('C3: /clear the pillar, top-down', () => clearAll([...pillarCells].reverse()))
    await check('C4: /clear the floor', () => clearAll(floorCells))

    await check('D: /scan finds only air, wall-torch cell included', async () => {
      const scanned = await scan([SCAN_SIZE[0] + 1, SCAN_SIZE[1], SCAN_SIZE[2]])
      const names = scanned.layers.flat(2)
      assert.equal(scanned.ok, true, short(scanned))
      assert.ok(names.every((name) => AIR_NAMES.includes(name)), `layers ${short(scanned.layers)}`)
      return `${names.length} cells, all air`
    })

    await check('E7: /clear of an air cell is not cleared', async () => {
      const response = await clear(floorCells[0])
      const seen = brief(response)
      assert.equal(response.body.ok, true, seen)
      assert.equal(response.body.cleared, false, seen)
      return seen
    })

    await check('E8: /scan of 33 x 1 x 1 is a bad_request', async () => {
      const response = await post('/scan', { origin: keepOut.origin, size: [33, 1, 1] })
      const seen = `${response.status} ${short({ ok: response.body.ok, error: response.body.error })}`
      assert.equal(response.status, 400, seen)
      assert.equal(response.body.error, 'bad_request', seen)
      return seen
    })
  } catch (err) {
    failures++
    console.log(`FAIL setup: ${err.message}`)
  } finally {
    rcon.close()
  }
  console.log(failures === 0 ? 'ALL PASS' : `${failures} FAILED`)
  process.exit(failures === 0 ? 0 : 1)
}

main().catch((err) => {
  console.log(`aborted: ${err.message}`)
  process.exit(1)
})
