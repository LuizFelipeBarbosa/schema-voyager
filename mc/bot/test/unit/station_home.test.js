const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { craft } = require('../../lib/craft')
const { smelt } = require('../../lib/smelt')
const { validateProtect } = require('../../lib/protect')
const { findSpots, validateStation } = require('../../lib/station')
const { fakeWorldBot } = require('./world_fixture')

const HOME = [10, 10, 0]
const keyOf = (pos) => `${pos.x},${pos.y},${pos.z}`
const EVERYTHING_NEAR = [{ origin: [-5, 5, -5], size: [11, 10, 11] }] // protects every cell findSpots looks at

// A bot over a stone floor with an inventory, a walk (`walk(bot, goal)` moves it; the default arrives)
// and what a crafting table and a furnace window do. `log` records what the test asserts on.
function homeBot ({ blocks = {}, inventory = {}, furnaceContents = {}, walk = (bot, goal) => { bot.entity.position = new Vec3(goal.x, goal.y, goal.z) } } = {}) {
  const world = { ...blocks }
  for (let x = -5; x <= 5; x++) for (let z = -5; z <= 5; z++) world[`${x},9,${z}`] = 'stone'
  const bot = fakeWorldBot(world)
  const slots = new Array(46).fill(null)
  const give = (name, count) => {
    const held = slots.find((slot) => slot?.name === name)
    if (held) held.count += count
    else slots[slots.findIndex((slot, i) => i >= 9 && !slot)] = { name, count, type: bot.registry.itemsByName[name].id }
  }
  for (const [name, count] of Object.entries(inventory)) give(name, count)
  const take = (name, count) => { slots.find((slot) => slot?.name === name).count -= count }

  const log = { goals: [], movements: [], crafted: [], digs: 0, placed: 0, opens: [] }
  const contents = { input: null, fuel: null, output: null, ...furnaceContents }
  const takeSlot = (slot) => () => { give(contents[slot].name, contents[slot].count); contents[slot] = null }
  const window = {
    fuel: 0,
    inputItem: () => contents.input,
    fuelItem: () => contents.fuel,
    outputItem: () => contents.output,
    takeInput: takeSlot('input'),
    takeFuel: takeSlot('fuel'),
    takeOutput: takeSlot('output'),
    putInput: async (id, meta, count) => { take(bot.registry.items[id].name, count); contents.input = { name: bot.registry.items[id].name, count } },
    putFuel: async (id, meta, count) => {
      take(bot.registry.items[id].name, count)
      contents.fuel = { name: bot.registry.items[id].name, count }
      contents.output = { name: 'iron_ingot', count: contents.input.count }
      contents.input = null
    }
  }

  bot.entity.onGround = true
  bot.inventory = {
    slots,
    items: () => slots.filter(Boolean),
    count: (id) => slots.filter((slot) => slot?.type === id).reduce((sum, slot) => sum + slot.count, 0),
    on () {},
    removeListener () {}
  }
  bot._client = { on () {}, removeListener () {} }
  bot.on = () => {}
  bot.removeListener = () => {}
  bot.clearControlStates = () => {}
  bot.heldItem = null
  bot.equip = async () => {}
  bot.recipesFor = (id) => require('prismarine-recipe')(bot.registry).Recipe.find(id, null)
  bot.recipesAll = bot.recipesFor
  bot.findBlock = ({ matching, maxDistance }) => {
    const [pos] = bot.findBlocks({ matching, maxDistance, count: 1 })
    return pos ? bot.blockAt(pos) : null
  }
  bot.pathfinder = {
    movements: 'previous',
    setMovements (movements) { bot.pathfinder.movements = movements; log.movements.push(movements) },
    setGoal () {},
    bestHarvestTool: () => null,
    goto: async (goal) => { log.goals.push(goal); return walk(bot, goal) }
  }
  bot.craft = async (recipe, count, table) => {
    log.crafted.push(table?.position)
    for (const { id, count: change } of recipe.delta) {
      const name = bot.registry.items[id].name
      if (change < 0) take(name, -change)
      else give(name, change)
    }
  }
  bot.openFurnace = async (block) => { log.opens.push(block.position); bot.currentWindow = window; return window }
  bot.closeWindow = async () => { bot.currentWindow = null }
  bot.placeBlock = async (against, face) => {
    log.placed++
    const name = slots.find((slot) => slot?.name === 'crafting_table' || slot?.name === 'furnace').name
    take(name, 1)
    world[keyOf(against.position.plus(face))] = name
  }
  bot.dig = async (block) => {
    log.digs++
    delete world[keyOf(block.position)]
    give(block.name, 1)
  }
  return { bot, log, world, contents, counts: () => Object.fromEntries(slots.filter(Boolean).map((slot) => [slot.name, slot.count])) }
}

function newSession () {
  const records = []
  const tracker = { log: { setPhase () {}, record: (type, data) => records.push({ type, data }) }, ownDrops: new Set() }
  return { session: { tracker, episode: { walked: 0 } }, records }
}

const pickaxe = { item: 'wooden_pickaxe' }
const pickaxeMaterials = { oak_planks: 3, stick: 2 }
const smeltBody = { ingredient: 'raw_iron', item: 'iron_ingot', times: 1, fuel: 'coal' }

test('findSpots skips a cell inside a protect box, and finds none when the boxes cover them all', () => {
  const { bot } = homeBot()
  assert.ok(findSpots(bot).some((spot) => spot.pos.x === 1 && spot.pos.z === 0))
  const withBox = findSpots(bot, [{ origin: [1, 10, 0], size: [1, 1, 1] }])
  assert.equal(withBox.some((spot) => spot.pos.x === 1 && spot.pos.y === 10 && spot.pos.z === 0), false)
  assert.ok(withBox.length > 0)
  assert.deepEqual(findSpots(bot, EVERYTHING_NEAR), [])
})

test('a craft with no table near or held walks to the given station and uses it without digging it up', async () => {
  const { bot, log } = homeBot({ blocks: { [HOME.join(',')]: 'crafting_table' }, inventory: pickaxeMaterials })
  const { session } = newSession()

  const result = await craft(bot, { ...pickaxe, station: HOME }, session)

  assert.equal(result.ok, true)
  assert.equal(result.used_table, true)
  assert.equal(result.used_home_station, true)
  assert.equal(result.table_retrieved, null)
  assert.equal(log.digs, 0)
  assert.equal(log.placed, 0)
  assert.deepEqual(log.crafted, [new Vec3(...HOME)])
  const [goal] = log.goals
  assert.deepEqual([goal.constructor.name, goal.x, goal.y, goal.z, goal.rangeSq], ['GoalNear', 10, 10, 0, 9])
  const [movements] = log.movements
  assert.deepEqual([movements.canDig, movements.canOpenDoors, movements.scafoldingBlocks], [false, false, []])
  assert.equal(bot.pathfinder.movements, 'previous')
  assert.equal(result.travel.distance, 0)
  assert.equal(result.station_gone, undefined)
})

test('a held table is placed and dug up when a spot is free, and the home station is not walked to', async () => {
  const { bot, log } = homeBot({ blocks: { [HOME.join(',')]: 'crafting_table' }, inventory: { ...pickaxeMaterials, crafting_table: 1 } })
  const { session } = newSession()

  const result = await craft(bot, { ...pickaxe, station: HOME }, session)

  assert.equal(result.ok, true)
  assert.equal(result.used_home_station, false)
  assert.equal(result.table_retrieved, true)
  assert.equal(log.placed, 1)
  assert.equal(log.digs, 1)
  assert.deepEqual(log.goals, [])
})

test('with every spot inside a protect box, a held table is not placed and the home station is used', async () => {
  const { bot, log } = homeBot({ blocks: { [HOME.join(',')]: 'crafting_table' }, inventory: { ...pickaxeMaterials, crafting_table: 1 } })
  const { session } = newSession()

  const result = await craft(bot, { ...pickaxe, station: HOME, protect: EVERYTHING_NEAR }, session)

  assert.equal(result.ok, true)
  assert.equal(result.used_home_station, true)
  assert.equal(result.table_retrieved, null)
  assert.equal(log.placed, 0)
  assert.equal(log.digs, 0)
})

test('with every spot protected and no station given, the craft reports the missing table', async () => {
  const { bot } = homeBot({ inventory: { ...pickaxeMaterials, crafting_table: 1 } })
  const { session } = newSession()

  const result = await craft(bot, { ...pickaxe, protect: EVERYTHING_NEAR }, session)

  assert.equal(result.error, 'craft_failed') // placing is still tried, as without protect
  assert.equal(result.used_home_station, false)
})

test('a given station whose cell holds something else is reported gone', async () => {
  const { bot, log } = homeBot({ blocks: { [HOME.join(',')]: 'stone' }, inventory: pickaxeMaterials })
  const { session } = newSession()

  const result = await craft(bot, { ...pickaxe, station: HOME }, session)

  assert.equal(result.error, 'missing_crafting_table')
  assert.equal(result.station_gone, true)
  assert.equal(result.used_home_station, false)
  assert.deepEqual(log.goals, [])
})

test('no station given and no table is the plain missing_crafting_table', async () => {
  const { bot } = homeBot({ inventory: pickaxeMaterials })
  const result = await craft(bot, pickaxe, newSession().session)
  assert.equal(result.error, 'missing_crafting_table')
  assert.equal(result.station_gone, undefined)
  assert.equal(result.used_home_station, false)
})

test('a walk that does not get there answers unreachable with station_unreachable and travel', async () => {
  const { bot, log } = homeBot({
    blocks: { [HOME.join(',')]: 'crafting_table' },
    inventory: pickaxeMaterials,
    walk: async () => { throw new Error('no path') }
  })
  const { session } = newSession()

  const result = await craft(bot, { ...pickaxe, station: HOME }, session)

  assert.equal(result.ok, false)
  assert.equal(result.error, 'unreachable')
  assert.equal(result.station_unreachable, true)
  assert.equal(result.used_home_station, false)
  assert.ok(result.travel)
  assert.deepEqual(log.crafted, [])
})

test('a walk that makes no progress for 8 s answers unreachable long before the 45 s timeout', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] })
  const { bot } = homeBot({
    blocks: { [HOME.join(',')]: 'crafting_table' },
    inventory: pickaxeMaterials,
    walk: () => new Promise(() => {}) // the bot stands still and the pathfinder never answers
  })
  const { session } = newSession()

  let elapsed = 0
  let finishedAt = null
  const pending = craft(bot, { ...pickaxe, station: HOME }, session).finally(() => { finishedAt = elapsed })
  while (finishedAt === null && elapsed < 45000) {
    t.mock.timers.tick(500)
    elapsed += 500
    await new Promise((resolve) => setImmediate(resolve))
  }
  const result = await pending
  assert.ok(finishedAt <= 9000, `answered after ${finishedAt} ms`)

  assert.deepEqual([result.error, result.station_unreachable], ['unreachable', true])
  assert.ok(result.travel)
})

test('digging up a remembered table is not reported as a gain of one table', async () => {
  const { bot } = homeBot({ blocks: { '2,10,0': 'crafting_table' }, inventory: pickaxeMaterials })
  const { session } = newSession()
  session.episode.standingStations = [{ name: 'crafting_table', position: new Vec3(2, 10, 0) }]

  const result = await craft(bot, pickaxe, session)

  assert.equal(result.ok, true)
  assert.equal(result.table_retrieved, true)
  assert.equal(result.inventory.crafting_table, 1)
  assert.equal(result.inventory_delta.crafting_table, undefined)
  assert.equal(result.inventory_delta.wooden_pickaxe, 1)
})

test('a smelt with no furnace near or held uses the given one, drains it first and leaves it standing', async () => {
  const { bot, log, counts } = homeBot({
    blocks: { [HOME.join(',')]: 'furnace' },
    inventory: { raw_iron: 1, coal: 1 },
    furnaceContents: { output: { name: 'iron_ingot', count: 2 } }
  })
  const { session, records } = newSession()

  const result = await smelt(bot, { ...smeltBody, station: HOME }, session)

  assert.equal(result.ok, true)
  assert.equal(result.used_furnace, true)
  assert.equal(result.used_home_station, true)
  assert.equal(result.furnace_retrieved, null)
  assert.equal(log.digs, 0)
  assert.equal(log.opens.length, 2) // drained, then used
  assert.deepEqual(records, [{ type: 'interference', data: { block: 'furnace', position: { x: 10, y: 10, z: 0 } } }])
  assert.deepEqual(result.inventory_before_step, { raw_iron: 1, coal: 1, iron_ingot: 2 })
  assert.deepEqual(result.inventory_delta, { raw_iron: -1, iron_ingot: 1 }) // the fake furnace burns no coal
  assert.equal(counts().iron_ingot, 3)
})

test('a smelt answers unreachable when the walk to the home furnace fails, and station_gone when the cell is empty', async () => {
  const unreachable = homeBot({ blocks: { [HOME.join(',')]: 'furnace' }, inventory: { raw_iron: 1, coal: 1 }, walk: async () => { throw new Error('no path') } })
  const stuck = await smelt(unreachable.bot, { ...smeltBody, station: HOME }, newSession().session)
  assert.deepEqual([stuck.error, stuck.station_unreachable, stuck.used_home_station], ['unreachable', true, false])

  const gone = homeBot({ inventory: { raw_iron: 1, coal: 1 } })
  const missing = await smelt(gone.bot, { ...smeltBody, station: HOME }, newSession().session)
  assert.deepEqual([missing.error, missing.station_gone], ['missing_furnace', true]) // the loaded cell holds air, which is something else
})

test('validateStation takes three integers or nothing', () => {
  assert.equal(validateStation(undefined), null)
  assert.equal(validateStation(null), null)
  assert.equal(validateStation([1, 64, -3]), null)
  for (const bad of [[1, 2], [1, 2, 3, 4], [1.5, 2, 3], ['1', 2, 3], 'home', { x: 1 }]) {
    assert.equal(typeof validateStation(bad), 'string', JSON.stringify(bad))
  }
})

test('the protect given with a station is validated as everywhere else', () => {
  assert.equal(validateProtect([{ origin: [0, 0, 0], size: [2, 2, 2] }]), null)
  assert.equal(typeof validateProtect([{ origin: [0, 0], size: [2, 2, 2] }]), 'string')
  assert.equal(typeof validateProtect('box'), 'string')
})
