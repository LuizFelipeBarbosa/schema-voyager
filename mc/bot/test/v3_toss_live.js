// Live v3 test of POST /toss and the free_slots field of /observe: tossing the
// smallest stacks first frees slots, the bot walks away from what it dropped
// and does not pick it up again. Contract: contracts/bot_api.md, "v3.0: /toss
// and free_slots".
//
// Unlike the v2 live tests it does NOT clear the inventory at the end: it
// leaves the bot holding exactly 2112 dirt (33 full stacks) and 5 poppies, two
// free slots, which is the nearly-full state a life starts from afterwards.
//
// Uses the one bot already running at SV2_BOT_PORT and RCON at SV2_RCON_PORT
// (password RCON_PASSWORD, bot name BOT_USERNAME); starts nothing. Refuses to
// run against the live demo's ports (3000, 25575). Environment variables are
// loaded from $SV2_HOME/.env without overriding the ones already set.
//
// Usage: node mc/bot/test/v3_toss_live.js   (exit 0 = every check passed)

const assert = require('node:assert/strict')
const { RconClient, health, loadEnv, post, sleep } = require('./v2_helpers')

const POLL_INTERVAL_MS = 250
const POLL_TIMEOUT_MS = 5000
const PICKUP_DELAY_PASSED_MS = 3000
const START_DIRT = 1920 // 30 stacks, 6 free slots
const LIFE_START_DIRT = 2112 // 33 stacks, with 5 poppies: 2 free slots

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

async function observe () {
  const { body } = await post('/observe', {})
  return body
}

// Polls /observe until `isReady(observation)` holds; throws after 5 s.
async function waitForObservation (what, isReady) {
  const deadline = Date.now() + POLL_TIMEOUT_MS
  for (;;) {
    const observation = await observe()
    if (isReady(observation)) return observation
    if (Date.now() > deadline) {
      throw new Error(`gave up waiting for ${what}; inventory is ${short(observation.inventory)}`)
    }
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
    // Start from 30 stacks of dirt and nothing on the ground.
    await rcon.exec(`clear ${username}`)
    await rcon.exec('kill @e[type=minecraft:item]')
    await rcon.exec(`give ${username} minecraft:dirt ${START_DIRT}`)
    await waitForObservation(`dirt ${START_DIRT}`, (o) => o.inventory.dirt === START_DIRT)

    await check('A: 30 stacks of dirt leave 6 free slots', async () => {
      const observed = await observe()
      assert.deepEqual(observed.inventory, { dirt: START_DIRT })
      assert.equal(observed.free_slots, 6)
      return `inventory ${short(observed.inventory)}, free_slots ${observed.free_slots}`
    })

    // poppy is not held: it is skipped, not an error.
    const { body: toss } = await post('/toss', { items: { dirt: 100, poppy: 3 } }) // 1820 dirt = 28 stacks + 1 of 28
    console.log(`toss response: ${short(toss)}`)

    await check('B: toss 100 dirt, skip poppy, walk away', async () => {
      assert.equal(toss.ok, true)
      assert.deepEqual(toss.tossed, { dirt: 100 })
      assert.equal(toss.inventory.dirt, 1820)
      assert.equal(toss.free_slots, 7)
      assert.equal(toss.walked_away, true)
      assert.ok(toss.travel.distance >= 4, `travel.distance ${toss.travel.distance} < 4`)
      assert.deepEqual(toss.events.filter((event) => event.type === 'stray_pickup'), [])
      assert.ok(toss.events.every((event) => event.phase === 'travel'), `events ${short(toss.events)}`)
      return `tossed ${short(toss.tossed)}, free_slots ${toss.free_slots}, distance ${toss.travel.distance}, ${toss.events.length} events`
    })

    await check('C: an immediate observe agrees with the toss response', async () => {
      const observed = await observe()
      assert.deepEqual(observed.inventory, toss.inventory)
      assert.equal(observed.free_slots, toss.free_slots)
      return `inventory ${short(observed.inventory)}, free_slots ${observed.free_slots}`
    })

    await sleep(PICKUP_DELAY_PASSED_MS) // past the 2 s pickup delay
    await check('D: the bot did not pick the stacks up again', async () => {
      const observed = await observe()
      assert.equal(observed.inventory.dirt, 1820)
      return `dirt ${observed.inventory.dirt}, free_slots ${observed.free_slots}`
    })

    // The API cannot select a hotbar slot, so which item the bot holds is not
    // under the test's control; the unit tests cover it.
    console.log('SKIP held-tool (covered by unit tests)')

    // Prepare the life's start: 33 stacks of dirt (RCON give fills the partial
    // stack first) and 5 poppies.
    await rcon.exec('kill @e[type=minecraft:item]')
    await rcon.exec(`give ${username} minecraft:dirt ${LIFE_START_DIRT - 1820}`)
    await rcon.exec(`give ${username} minecraft:poppy 5`)
    await waitForObservation('the life-start inventory', (o) => o.inventory.dirt === LIFE_START_DIRT && o.inventory.poppy === 5)

    await check('F: life-start inventory is 2112 dirt and 5 poppies with 2 free slots', async () => {
      const observed = await observe()
      assert.deepEqual(observed.inventory, { dirt: LIFE_START_DIRT, poppy: 5 })
      assert.equal(observed.free_slots, 2)
      return `inventory ${short(observed.inventory)}, free_slots ${observed.free_slots}`
    })
  } catch (err) {
    failures++
    console.log(`FAIL setup: ${err.message}`)
  } finally {
    rcon.close() // the inventory stays as it is: the life starts from it
  }
  console.log(failures === 0 ? 'ALL PASS' : `${failures} FAILED`)
  process.exit(failures === 0 ? 0 : 1)
}

main().catch((err) => {
  console.log(`aborted: ${err.message}`)
  process.exit(1)
})
