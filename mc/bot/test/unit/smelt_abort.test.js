const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { smelt } = require('../../lib/smelt')
const { fakeWorldBot } = require('./world_fixture')

function newAbort () {
  const controller = new AbortController()
  const abort = () => controller.abort(Object.assign(new Error('aborted'), { code: 'aborted' }))
  return { signal: controller.signal, abort }
}

// Waits shrink to nothing, so a smelt's polls and a dig's drop window don't slow the tests.
function useFastTimers (t) {
  const realSetTimeout = global.setTimeout
  t.mock.method(global, 'setTimeout', (fn, ms, ...args) => realSetTimeout(fn, Math.min(ms, 1), ...args))
}

// A bot with a furnace window like mineflayer's. `furnaceContents` is what a
// furnace already holds; the window smelts its input at once on putFuel unless
// `stuck`. `onPutFuel(window)` lets a test act while the furnace is loaded.
function furnaceBot ({ blocks = {}, inventory = {}, furnaceContents = {}, stuck = false, onPutFuel = () => {} } = {}) {
  const bot = fakeWorldBot(blocks)
  const registry = bot.registry
  const slots = new Array(46).fill(null)
  const give = (name, count) => {
    const held = slots.find((slot) => slot?.name === name)
    if (held) held.count += count
    else slots[slots.findIndex((slot, i) => i >= 9 && !slot)] = { name, count, type: registry.itemsByName[name].id }
  }
  for (const [name, count] of Object.entries(inventory)) give(name, count)
  const take = (name, count) => { slots.find((slot) => slot?.name === name).count -= count }

  const contents = { input: null, fuel: null, output: null, ...furnaceContents }
  const log = { opens: 0, closes: 0, clicks: [], digs: 0 }
  const clicking = (name, fn) => async (...args) => { log.clicks.push(name); return fn(...args) }
  const takeSlot = (slot) => () => { give(contents[slot].name, contents[slot].count); contents[slot] = null }
  const window = {
    fuel: 0,
    inputItem: () => contents.input,
    fuelItem: () => contents.fuel,
    outputItem: () => contents.output,
    takeInput: clicking('takeInput', takeSlot('input')),
    takeFuel: clicking('takeFuel', takeSlot('fuel')),
    takeOutput: clicking('takeOutput', takeSlot('output')),
    putInput: clicking('putInput', async (id, meta, count) => {
      const name = registry.items[id].name
      take(name, count)
      contents.input = { name, count }
    }),
    putFuel: clicking('putFuel', async (id, meta, count) => {
      const name = registry.items[id].name
      take(name, count)
      contents.fuel = { name, count }
      if (!stuck) { contents.output = { name: 'iron_ingot', count: contents.input.count }; contents.input = null }
      onPutFuel(window)
    })
  }

  bot.inventory = { slots, items: () => slots.filter(Boolean), on () {}, removeListener () {} }
  bot._client = { on () {}, removeListener () {} }
  bot.heldItem = null
  bot.on = () => {}
  bot.removeListener = () => {}
  bot.clearControlStates = () => {}
  bot.equip = async () => {}
  bot.findBlock = ({ matching, maxDistance }) => {
    const [key] = Object.entries(blocks).find(([k, name]) => name === 'furnace' &&
      matching === registry.blocksByName.furnace.id && new Vec3(...k.split(',').map(Number)).distanceTo(bot.entity.position) <= maxDistance) || []
    return key ? bot.blockAt(new Vec3(...key.split(',').map(Number))) : null
  }
  bot.pathfinder = {
    bestHarvestTool: () => null,
    setGoal () {},
    goto: async (goal) => { bot.entity.position = new Vec3(goal.x, goal.y, goal.z) }
  }
  bot.openFurnace = async () => { log.opens++; bot.currentWindow = window; return window }
  bot.closeWindow = async () => { log.closes++; bot.currentWindow = null }
  bot.placeBlock = async (against, face) => {
    take('furnace', 1)
    blocks[`${against.position.plus(face)}`.replace(/[() ]/g, '')] = 'furnace'
  }
  bot.dig = async (block) => {
    log.digs++
    delete blocks[`${block.position}`.replace(/[() ]/g, '')]
    give('furnace', 1)
  }
  return { bot, window, log, contents, counts: () => Object.fromEntries(slots.filter(Boolean).map((s) => [s.name, s.count])) }
}

function newSession (extra = {}) {
  const records = []
  let phase = null
  const tracker = {
    log: { setPhase: (p) => { phase = p }, record: (type, data) => records.push({ type, data, phase }) },
    ownDrops: new Set()
  }
  return { session: { tracker, episode: { walked: 0 }, ...extra }, records }
}

const body = { ingredient: 'raw_iron', item: 'iron_ingot', times: 1, fuel: 'coal' }

function floorBlocks () {
  const blocks = {}
  for (let x = -5; x <= 5; x++) for (let z = -5; z <= 5; z++) blocks[`${x},9,${z}`] = 'stone'
  return blocks
}

test('a furnace with leftovers is drained before the shortfall check and the step is flagged', async (t) => {
  useFastTimers(t)
  const { bot, log } = furnaceBot({
    blocks: { '2,10,0': 'furnace' },
    furnaceContents: { input: { name: 'raw_iron', count: 1 }, fuel: { name: 'coal', count: 1 } }
  })
  const { session, records } = newSession()

  const result = await smelt(bot, body, session)

  assert.equal(result.error, null) // short without the drain: the bot held neither ingredient
  assert.deepEqual(result.inventory_before_step, { raw_iron: 1, coal: 1 })
  assert.deepEqual(records, [{ type: 'interference', data: { block: 'furnace', position: { x: 2, y: 10, z: 0 } }, phase: 'step' }])
  assert.equal(result.output_taken, 1)
  assert.equal(log.opens, 2)
})

test('an empty furnace is no interference and leaves the inventory as it was', async (t) => {
  useFastTimers(t)
  const { bot } = furnaceBot({ blocks: { '2,10,0': 'furnace' }, inventory: { raw_iron: 1, coal: 1 } })
  const { session, records } = newSession()

  const result = await smelt(bot, body, session)

  assert.equal(result.error, null)
  assert.deepEqual(records, [])
  assert.deepEqual(result.inventory_before_step, { raw_iron: 1, coal: 1 })
})

test('an abort in the furnace loop takes the items back, closes the window, remembers the placed furnace', async (t) => {
  useFastTimers(t)
  const { signal, abort } = newAbort()
  const { bot, log, counts } = furnaceBot({
    blocks: floorBlocks(), inventory: { raw_iron: 1, coal: 1, furnace: 1 }, stuck: true, onPutFuel: abort
  })
  const { session } = newSession({ signal })

  await assert.rejects(smelt(bot, body, session), { code: 'aborted' })

  assert.deepEqual(counts(), { raw_iron: 1, coal: 1, furnace: 0 }) // the furnace item was placed
  assert.equal(log.closes, 1)
  assert.equal(log.digs, 0) // the furnace is left standing, retrieveStation never ran
  assert.equal(session.episode.standingStations.length, 1)
  assert.equal(session.episode.standingStations[0].name, 'furnace')
  assert.equal(bot.blockAt(session.episode.standingStations[0].position).name, 'furnace')
})

test('an abort with the window already closed throws without clicking it', async (t) => {
  useFastTimers(t)
  const { signal, abort } = newAbort()
  const { bot, log } = furnaceBot({
    blocks: { '2,10,0': 'furnace' },
    inventory: { raw_iron: 1, coal: 1 },
    stuck: true,
    onPutFuel: () => { abort(); bot.currentWindow = null } // interruptBot() closed it
  })
  const { session } = newSession({ signal })

  await assert.rejects(smelt(bot, body, session), { code: 'aborted' })

  assert.deepEqual(log.clicks, ['putInput', 'putFuel'])
  assert.equal(log.closes, 1) // only the drain's own close
})

test('a remembered furnace 10 blocks away is walked to, used, dug up and forgotten', async (t) => {
  useFastTimers(t)
  const { bot, log } = furnaceBot({ blocks: { '10,10,0': 'furnace' }, inventory: { raw_iron: 1, coal: 1 } })
  const { session } = newSession()
  session.episode.standingStations = [{ name: 'furnace', position: new Vec3(10, 10, 0) }]

  const result = await smelt(bot, body, session)

  assert.equal(result.error, null)
  assert.equal(bot.entity.position.x, 10) // walked to it
  assert.equal(log.digs, 1)
  assert.equal(result.furnace_retrieved, true)
  assert.deepEqual(session.episode.standingStations, [])
  assert.equal(result.inventory_delta.furnace, undefined)
  assert.equal(result.inventory_delta.iron_ingot, 1)
})
