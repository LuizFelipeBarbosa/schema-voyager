const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const { retrieveStation } = require('../../lib/station')
const { fakeWorldBot, registry } = require('./world_fixture')

const POS = new Vec3(0, 10, 1)

function tool (name, durabilityUsed = 0) {
  const item = registry.itemsByName[name]
  return { name, type: item.id, count: 1, maxDurability: item.maxDurability, durabilityUsed }
}

// A bot standing by a placed `stationName`. `onDig(bot)` plays what the dig does
// to the inventory; `bot.equipped` lists what was equipped for it.
function botWithStation (stationName, items, onDig = () => {}) {
  const bot = Object.assign(new EventEmitter(), fakeWorldBot({ [`${POS.x},${POS.y},${POS.z}`]: stationName }))
  bot.items = [...items]
  bot.inventory = new EventEmitter()
  bot.inventory.items = () => bot.items
  Object.defineProperty(bot.inventory, 'slots', { get: () => [...Array(5).fill(null), ...bot.items] })
  bot._client = new EventEmitter()
  bot.pathfinder.bestHarvestTool = () => bot.items.find((item) => item.name.endsWith('_pickaxe')) || null
  bot.equipped = []
  bot.heldItem = null
  bot.equip = async (item) => { bot.equipped.push(item.name); bot.heldItem = item }
  bot.unequip = async () => { bot.heldItem = null }
  bot.dig = async () => {
    bot.items.push({ name: stationName, type: registry.itemsByName[stationName].id, count: 1 })
    onDig(bot)
  }
  return bot
}

const removeItem = (name) => (bot) => { bot.items = bot.items.filter((item) => item.name !== name) }

test('a held tool that is gone from the inventory after the dig is reported as broken', async () => {
  const bot = botWithStation('furnace', [tool('wooden_pickaxe', 58)], removeItem('wooden_pickaxe'))
  const outcome = await retrieveStation(bot, POS, 'furnace', 0, { withBestTool: true })
  assert.deepEqual(outcome, { retrieved: true, toolsBroken: ['wooden_pickaxe'] })
})

test('retrieved is computed as before when the tool broke', async () => {
  const bot = botWithStation('furnace', [tool('wooden_pickaxe', 58)], (b) => { removeItem('wooden_pickaxe')(b); removeItem('furnace')(b) })
  const outcome = await retrieveStation(bot, POS, 'furnace', 1, { withBestTool: true })
  assert.deepEqual(outcome, { retrieved: false, toolsBroken: ['wooden_pickaxe'] })
})

test('a dig that leaves the tool reports nothing broken', async () => {
  const bot = botWithStation('furnace', [tool('wooden_pickaxe')])
  const outcome = await retrieveStation(bot, POS, 'furnace', 0, { withBestTool: true })
  assert.deepEqual(outcome, { retrieved: true, toolsBroken: [] })
})

test('the furnace dig uses the pickaxe with durability to spare, not the one on its last point', async () => {
  const lastPoint = tool('stone_pickaxe', registry.itemsByName.stone_pickaxe.maxDurability - 1)
  const bot = botWithStation('furnace', [lastPoint, tool('wooden_pickaxe')])
  await retrieveStation(bot, POS, 'furnace', 0, { withBestTool: true })
  assert.deepEqual(bot.equipped, ['wooden_pickaxe'])
})

test('with only a worn pickaxe, the furnace dig falls back to the best harvest tool', async () => {
  const worn = tool('wooden_pickaxe', registry.itemsByName.wooden_pickaxe.maxDurability - 1)
  const bot = botWithStation('furnace', [worn], removeItem('wooden_pickaxe'))
  const outcome = await retrieveStation(bot, POS, 'furnace', 0, { withBestTool: true })
  assert.deepEqual(bot.equipped, ['wooden_pickaxe'])
  assert.deepEqual(outcome.toolsBroken, ['wooden_pickaxe'])
})

test('the crafting table dig equips no pickaxe, which the hand does not need', async () => {
  const bot = botWithStation('crafting_table', [tool('wooden_pickaxe'), tool('iron_pickaxe')])
  const outcome = await retrieveStation(bot, POS, 'crafting_table', 0)
  assert.deepEqual(bot.equipped, [])
  assert.deepEqual(outcome, { retrieved: true, toolsBroken: [] })
})
