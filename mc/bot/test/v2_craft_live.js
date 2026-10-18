// Live v2 test of the crafting grid: crafts, probes and refused crafts must
// leave nothing in the crafting grid or on the cursor, and report exactly what
// the inventory gained and lost. Each case starts from a cleared inventory
// that RCON fills.
//
// Uses the one bot already running at SV2_BOT_PORT and RCON at SV2_RCON_PORT
// (password RCON_PASSWORD, bot name BOT_USERNAME); starts nothing. Refuses to
// run against the live demo's ports (3000, 25575). Environment variables are
// loaded from $SV2_HOME/.env without overriding the ones already set.
//
// Usage: node mc/bot/test/v2_craft_live.js   (exit 0 = every case passed)

const assert = require('node:assert/strict')
const { RconClient, health, loadEnv, post, sleep } = require('./v2_helpers')

const GIVE_SETTLE_MS = 1500

const short = (value) => JSON.stringify(value)

// Clears the bot's inventory, gives it `items`, and returns the inventory the
// bot sees once it has caught up.
async function startWith (rcon, username, items) {
  await rcon.exec(`clear ${username}`)
  for (const [name, count] of Object.entries(items)) await rcon.exec(`give ${username} minecraft:${name} ${count}`)
  await sleep(GIVE_SETTLE_MS)
  const { body } = await post('/observe', {})
  assert.deepEqual(body.inventory, items, 'the starting inventory is not what was given')
}

// Checks every answer must pass: nothing left in the grid or on the cursor,
// and the inventory the bot reports afterwards matches the delta.
async function assertNothingLeft (before, reply) {
  assert.deepEqual(reply.held_outside_inventory, {}, 'items left in the crafting grid or on the cursor')
  const { body: observed } = await post('/observe', {})
  const expected = { ...before }
  for (const [name, change] of Object.entries(reply.inventory_delta)) expected[name] = (expected[name] || 0) + change
  for (const name of Object.keys(expected)) if (expected[name] === 0) delete expected[name]
  assert.deepEqual(observed.inventory, expected, 'a later observe disagrees with the reported delta')
}

const CASES = [
  {
    name: '(a) 2 runs of sticks from 6 birch planks',
    items: { birch_planks: 6 },
    async run (before) {
      const { body } = await post('/craft', { item: 'stick', times: 2 })
      assert.equal(body.ok, true, short(body))
      assert.deepEqual(body.inventory_delta, { birch_planks: -4, stick: 8 })
      await assertNothingLeft(before, body)
    }
  },
  {
    name: '(b) sticks from 1 birch + 1 spruce planks',
    items: { birch_planks: 1, spruce_planks: 1 },
    async run (before) {
      const { body } = await post('/craft', { item: 'stick', times: 1 })
      assert.equal(body.ok, true, short(body))
      assert.deepEqual(body.inventory_delta, { birch_planks: -1, spruce_planks: -1, stick: 4 })
      await assertNothingLeft(before, body)
    }
  },
  {
    name: '(c) ladder from 7 sticks and a crafting table',
    items: { stick: 7, crafting_table: 1 },
    async run (before) {
      const { body } = await post('/craft', { item: 'ladder', times: 1 })
      assert.equal(body.ok, true, short(body))
      assert.equal(body.used_table, true)
      assert.deepEqual(body.inventory_delta, { stick: -7, ladder: 3 })
      await assertNothingLeft(before, body)
    }
  },
  {
    name: '(d) a craft that cannot be paid changes nothing',
    items: { birch_planks: 1 },
    async run (before) {
      const { body } = await post('/craft', { item: 'stick', times: 1 })
      assert.equal(body.ok, false, short(body))
      assert.equal(body.error, 'missing_ingredients')
      assert.deepEqual(body.inventory_delta, {})
      await assertNothingLeft(before, body)
    }
  },
  {
    name: '(e) probing a stick grid changes nothing',
    items: { birch_planks: 4 },
    async run (before) {
      const { body } = await post('/probe', { grid: [['birch_planks', null], ['birch_planks', null]] })
      assert.equal(body.ok, true, short(body))
      assert.deepEqual(body.output, { item: 'stick', count: 4 })
      assert.deepEqual(body.inventory_delta, {})
      await assertNothingLeft(before, body)
    }
  }
]

async function main () {
  loadEnv()
  const username = process.env.BOT_USERNAME || 'VoyagerBot'
  const rcon = new RconClient() // refuses the live demo's RCON port
  const status = await health() // refuses the live demo's bot port
  if (!status.connected) throw new Error('the bot is not connected to its server')
  await rcon.connect()

  let failures = 0
  try {
    for (const testCase of CASES) {
      try {
        await startWith(rcon, username, testCase.items)
        await testCase.run(testCase.items)
        console.log(`PASS ${testCase.name}`)
      } catch (err) {
        failures++
        console.log(`FAIL ${testCase.name}: ${err.message}`)
      }
    }
  } finally {
    await rcon.exec(`clear ${username}`).catch(() => {})
    rcon.close()
  }
  console.log(failures === 0 ? 'ALL PASS' : `${failures} FAILED`)
  process.exit(failures === 0 ? 0 : 1)
}

main().catch((err) => {
  console.log(`aborted: ${err.message}`)
  process.exit(1)
})
