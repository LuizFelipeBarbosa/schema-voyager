const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const { craft } = require('../../lib/craft')
const { smelt } = require('../../lib/smelt')
const { digNiche, placeHeldOrUseHome, placeStation } = require('../../lib/station')
const { fakeWorldBot } = require('./world_fixture')

const keyOf = (pos) => `${pos.x},${pos.y},${pos.z}`
const smeltBody = { ingredient: 'raw_iron', item: 'iron_ingot', times: 1, fuel: 'coal' }

// A bot at the bottom of a 1 x 1 shaft in solid stone: air at (0, 10..20, 0) and `extraAir`,
// stone floor. `blocks` overrides cells ("x,y,z": name). `lateDrop`: the niche's drop stays on
// the ground (entity 99) until the first placement, then is picked up. `log` records what the tests assert on.
function shaftBot ({ x = 0.5, blocks = {}, extraAir = [], inventory = { furnace: 1 }, lateDrop = false } = {}) {
  const world = { ...blocks }
  for (let y = 10; y <= 20; y++) world[`0,${y},0`] = 'air'
  for (const cell of extraAir) world[cell] = 'air'
  const bot = fakeWorldBot(world, { defaultBlock: 'stone' })
  const events = new EventEmitter()
  bot.on = events.on.bind(events)
  bot.emit = events.emit.bind(events)
  bot.removeListener = events.removeListener.bind(events)
  bot._client = new EventEmitter()
  bot.entity.position = new Vec3(x, 10, 0.5)
  bot.entity.onGround = true
  bot.heldItem = null

  const slots = new Array(46).fill(null)
  const give = (name, count) => {
    const held = slots.find((slot) => slot?.name === name)
    if (held) held.count += count
    else slots[slots.findIndex((slot, i) => i >= 9 && !slot)] = { name, count, type: bot.registry.itemsByName[name].id, ...(name === 'wooden_pickaxe' && { maxDurability: 59 }) }
  }
  const take = (name, count) => { slots.find((slot) => slot?.name === name).count -= count }
  for (const [name, count] of Object.entries({ wooden_pickaxe: 1, ...inventory })) give(name, count)
  bot.inventory = {
    slots,
    items: () => slots.filter(Boolean),
    count: (id) => slots.filter((slot) => slot?.type === id).reduce((sum, slot) => sum + slot.count, 0),
    on () {},
    removeListener () {}
  }
  bot.equip = async () => {}
  bot.unequip = async () => {}
  bot.lookAt = async () => {}
  bot.setControlState = (control, on) => { if (control === 'forward' && on) bot.entity.position = new Vec3(Math.floor(bot.entity.position.x) + 0.5, 10, 0.5) }
  bot.clearControlStates = () => {}
  bot.recipesFor = (id) => require('prismarine-recipe')(bot.registry).Recipe.find(id, null)
  bot.recipesAll = bot.recipesFor
  bot.findBlock = ({ matching, maxDistance }) => {
    const [pos] = bot.findBlocks({ matching, maxDistance, count: 1 })
    return pos ? bot.blockAt(pos) : null
  }
  const log = { digs: [], placed: [], goals: [], phases: [], crafted: 0 }
  bot.pathfinder = {
    movements: 'previous',
    setMovements (movements) { bot.pathfinder.movements = movements },
    setGoal () {},
    bestHarvestTool: () => null,
    goto: async (goal) => { log.goals.push(goal); if (goal.x !== undefined) bot.entity.position = new Vec3(goal.x, goal.y, goal.z) }
  }
  bot.craft = async (recipe) => {
    log.crafted++
    for (const { id, count } of recipe.delta) {
      const name = bot.registry.items[id].name
      if (count < 0) take(name, -count)
      else give(name, count)
    }
  }
  const air = bot.registry.blocksByName.air.defaultState
  bot.dig = async (block) => {
    log.digs.push(block.position.clone())
    world[keyOf(block.position)] = 'air'
    bot._client.emit('block_change', { location: block.position, type: air })
    if (block.name === 'stone') {
      if (!lateDrop) return give('cobblestone', 1)
      const at = block.position.offset(0.5, 0.5, 0.5)
      bot.entities[99] = { id: 99, name: 'item', position: at, getDroppedItem: () => ({ name: 'cobblestone', count: 1 }) }
      bot.emit('entitySpawn', bot.entities[99])
    } else if (block.name === 'furnace' || block.name === 'crafting_table') give(block.name, 1)
  }
  bot.placeBlock = async (against, face) => {
    const name = slots.find((slot) => slot?.name === 'crafting_table' || slot?.name === 'furnace').name
    log.placed.push(against.position.plus(face))
    take(name, 1)
    world[keyOf(against.position.plus(face))] = name
    if (bot.entities[99]) { delete bot.entities[99]; give('cobblestone', 1) } // the late pickup, mid-step
  }
  const furnace = { input: null, output: null }
  bot.openFurnace = async () => {
    bot.currentWindow = {
      fuel: 0,
      inputItem: () => furnace.input,
      fuelItem: () => null,
      outputItem: () => furnace.output,
      takeInput () {},
      takeFuel () {},
      takeOutput () { give('iron_ingot', 1); furnace.output = null },
      putInput: async (id, meta, count) => { take(bot.registry.items[id].name, count); furnace.output = { name: 'iron_ingot', count } },
      putFuel: async (id, meta, count) => { take(bot.registry.items[id].name, count) }
    }
    return bot.currentWindow
  }
  bot.closeWindow = async () => { bot.currentWindow = null }
  return { bot, log, world, counts: () => Object.fromEntries(slots.filter(Boolean).map((slot) => [slot.name, slot.count])) }
}

function newSession (log) {
  let phase = 'step'
  const ownDrops = new Set()
  const tracker = { ownDrops, log: { setPhase: (next) => { phase = next; log?.phases.push(next) }, phase: () => phase, record () {} } }
  return { tracker, episode: { walked: 0 } }
}

const withLogs = async (fn) => {
  const lines = []
  const original = console.log
  console.log = (line) => lines.push(line)
  try { return { value: await fn(), lines } } finally { console.log = original }
}

test('the logged shaft: recentring in its cell frees the mined cell the bot stood against, and no niche is dug', async () => {
  const { bot, log } = shaftBot({ x: 0.7, extraAir: ['1,10,0'], inventory: { furnace: 1, raw_iron: 1, coal: 1 } })
  const { value: result } = await withLogs(() => smelt(bot, smeltBody, newSession(log)))

  assert.equal(result.ok, true)
  assert.equal(result.used_furnace, true)
  assert.equal(result.niche, undefined)
  assert.deepEqual(log.placed, [new Vec3(1, 10, 0)])
  assert.deepEqual(log.digs.map(keyOf), ['1,10,0']) // only the furnace dug up again
  assert.equal(bot.entity.position.x, 0.5)
})

test('a shaft with four solid walls: a niche is dug at the bot\'s level, the furnace goes in it, and the answer says where', async () => {
  const { bot, log, counts } = shaftBot({ inventory: { furnace: 1, raw_iron: 1, coal: 1 } })
  const { value: result, lines } = await withLogs(() => smelt(bot, smeltBody, newSession(log)))

  assert.equal(result.ok, true)
  assert.equal(result.used_furnace, true)
  assert.deepEqual(result.niche, [1, 10, 0])
  assert.deepEqual(log.placed, [new Vec3(1, 10, 0)])
  assert.deepEqual(result.travel.dug, { stone: 1 })
  assert.deepEqual(result.travel.picked_up, { cobblestone: 1 })
  assert.deepEqual(result.inventory_delta, { raw_iron: -1, coal: -1, iron_ingot: 1 })
  assert.equal(result.inventory_before_step.cobblestone, 1)
  assert.deepEqual(log.phases, ['step', 'travel', 'step']) // smelt starts its step; the niche dig is travel
  assert.ok(lines.includes('[station] dug a niche at 1 10 0 for a furnace'), lines.join('|'))
  assert.equal(counts().furnace, 1)
})

test('a crafting table is placed in a niche too', async () => {
  const { bot, log } = shaftBot({ inventory: { crafting_table: 1, oak_planks: 3, stick: 2 } })
  const result = await craft(bot, { item: 'wooden_pickaxe' }, newSession(log))

  assert.equal(result.ok, true, result.detail)
  assert.deepEqual(result.niche, [1, 10, 0])
  assert.equal(result.used_table, true)
  assert.equal(result.table_retrieved, true)
  assert.equal(result.inventory_before_step.cobblestone, 1)
  assert.equal(result.inventory_delta.cobblestone, undefined)
})

// Each case poisons the first candidate, (1, 10, 0); the niche goes elsewhere.
const REJECTED = {
  'water beside the cell': { '2,10,0': 'water' },
  'lava below the cell': { '1,9,0': 'lava' },
  'water above the cell': { '1,11,0': 'water' },
  'gravel above the cell': { '1,11,0': 'gravel', '1,12,0': 'gravel' },
  'an infested block in the cell (the quickest to dig)': { '1,10,0': 'infested_stone' },
  'a chest in the cell': { '1,10,0': 'chest' },
  'a furnace in the cell': { '1,10,0': 'furnace' }
}
for (const [name, blocks] of Object.entries(REJECTED)) {
  test(`a niche is not dug with ${name}`, async () => {
    const { bot, log } = shaftBot({ blocks })
    const dug = await withLogs(() => digNiche(bot, [], 'furnace', null))

    assert.ok(dug.value, 'some other cell is dug')
    assert.notDeepEqual(dug.value.cell, [1, 10, 0])
    assert.equal(log.digs.length, 1)
    assert.ok(log.digs[0].y >= 10)
  })
}

test('a protected cell is never dug, and when every side is protected nothing is: the floor is never touched', async () => {
  const { bot, log } = shaftBot()
  const east = [{ origin: [1, 10, 0], size: [1, 2, 1] }]
  const dug = await withLogs(() => digNiche(bot, east, 'furnace', null))
  assert.notEqual(dug.value.cell[0], 1)

  const around = [{ origin: [-1, 10, -1], size: [3, 2, 3] }]
  log.digs.length = 0
  const none = await withLogs(() => digNiche(bot, around, 'furnace', null))
  assert.equal(none.value, null)
  assert.deepEqual(log.digs, [])
})

test('with the floor the only diggable thing, placeStation fails as it always did', async () => {
  const { bot, log } = shaftBot({ inventory: { furnace: 1 } })
  const walls = [{ origin: [-1, 10, -1], size: [3, 2, 3] }]
  await assert.rejects(placeStation(bot, 'furnace', { protect: walls }), /no free spot next to the bot/)
  assert.deepEqual(log.digs, [])
})

test('a bot in a shaft with a home station set places its own furnace instead of walking home', async () => {
  const { bot, log } = shaftBot({ blocks: { '10,10,0': 'furnace' } })
  const { value: acquired } = await withLogs(() => placeHeldOrUseHome(bot, 'furnace', { station: [10, 10, 0], protect: [], episode: { walked: 0 }, tracker: newSession(log).tracker }))

  assert.equal(acquired.error, undefined)
  assert.equal(acquired.usedHome, undefined)
  assert.deepEqual(acquired.niche, [1, 10, 0])
  assert.deepEqual(log.goals, [])
})

test('with every neighbour protected, the home station is walked to as before', async () => {
  const { bot, log } = shaftBot({ blocks: { '10,10,0': 'furnace' } })
  const walls = [{ origin: [-2, 9, -2], size: [5, 4, 5] }]
  const acquired = await placeHeldOrUseHome(bot, 'furnace', { station: [10, 10, 0], protect: walls, episode: { walked: 0 } })

  assert.equal(acquired.usedHome, true)
  assert.equal(log.goals.length, 1)
  assert.deepEqual(log.digs, [])
})

test('a niche drop that is picked up late is not in the smelt\'s delta, and is not an own drop', async () => {
  const { bot, log } = shaftBot({ lateDrop: true, inventory: { furnace: 1, raw_iron: 1, coal: 1 } })
  const session = newSession(log)
  const { value: result } = await withLogs(() => smelt(bot, smeltBody, session))

  assert.equal(result.ok, true)
  assert.deepEqual(result.niche, [1, 10, 0])
  assert.deepEqual(result.inventory_delta, { raw_iron: -1, coal: -1, iron_ingot: 1 })
  assert.equal(result.inventory.cobblestone, 1)
  assert.equal(session.tracker.ownDrops.size, 0)
})

test('a niche drop that is picked up late is not a partial_delta in the craft', async () => {
  const { bot, log } = shaftBot({ lateDrop: true, inventory: { crafting_table: 1, oak_planks: 3, stick: 2 } })
  const session = newSession(log)
  const result = await craft(bot, { item: 'wooden_pickaxe' }, session)

  assert.equal(result.ok, true, result.detail)
  assert.equal(result.inventory_delta.cobblestone, undefined)
  assert.equal(result.inventory_delta.wooden_pickaxe, 1)
  assert.equal(session.tracker.ownDrops.size, 0)
})
