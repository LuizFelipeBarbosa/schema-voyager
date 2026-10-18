const test = require('node:test')
const assert = require('node:assert/strict')
const { cheapestHarvestTool } = require('../../lib/movements')
const { withDiggingMovements } = require('../../lib/travel')
const { fakeWorldBot, registry } = require('./world_fixture')
const Block = require('prismarine-block')(registry)

const block = (name) => Block.fromStateId(registry.blocksByName[name].defaultState, 0)

function tool (name, durabilityUsed = 0) {
  const item = registry.itemsByName[name]
  return { name, type: item.id, maxDurability: item.maxDurability, durabilityUsed }
}

function botWith (items, held = null) {
  const bot = fakeWorldBot()
  bot.inventory = { items: () => items }
  bot.heldItem = held
  bot.unequipped = false
  bot.unequip = async () => { bot.unequipped = true }
  return bot
}

const pickaxes = () => [tool('iron_pickaxe'), tool('wooden_pickaxe'), tool('stone_pickaxe')]

test('stone is dug with the lowest-tier pickaxe that harvests it', () => {
  const choose = cheapestHarvestTool(botWith(pickaxes()))
  assert.equal(choose(block('stone')).name, 'wooden_pickaxe')
  assert.equal(choose(block('iron_ore')).name, 'stone_pickaxe') // wood cannot harvest it
  assert.equal(choose(block('diamond_ore')).name, 'iron_pickaxe')
})

test('a block the hand harvests is dug by hand unless a tool is much faster', () => {
  const held = tool('iron_pickaxe')
  const bareHand = botWith(pickaxes(), held)
  assert.equal(cheapestHarvestTool(bareHand)(block('dirt')), null)
  assert.ok(bareHand.unequipped, 'no filler item: the held tool is put away')

  const withFiller = botWith([...pickaxes(), { name: 'dirt', type: registry.itemsByName.dirt.id }], held)
  assert.equal(cheapestHarvestTool(withFiller)(block('dirt')).name, 'dirt')

  const withAxe = botWith([...pickaxes(), tool('iron_axe')])
  assert.equal(cheapestHarvestTool(withAxe)(block('oak_log')).name, 'iron_axe') // 6x faster than the hand
})

test('a tool that the next block would break is never used', () => {
  const nearlyBroken = tool('wooden_pickaxe', registry.itemsByName.wooden_pickaxe.maxDurability - 1)
  const choose = cheapestHarvestTool(botWith([nearlyBroken, tool('iron_pickaxe')]))
  assert.equal(choose(block('stone')).name, 'iron_pickaxe')
  assert.equal(cheapestHarvestTool(botWith([nearlyBroken]))(block('stone')), null)
})

test('withDiggingMovements swaps the tool choice in and restores it, even on failure', async () => {
  const bot = botWith(pickaxes())
  bot.registry = registry
  bot.pathfinder = { movements: 'v1', bestHarvestTool: 'original', setMovements (m) { this.movements = m } }
  await withDiggingMovements(bot, {}, async () => {
    assert.notEqual(bot.pathfinder.bestHarvestTool, 'original')
    assert.equal(bot.pathfinder.bestHarvestTool(block('stone')).name, 'wooden_pickaxe')
  })
  assert.equal(bot.pathfinder.bestHarvestTool, 'original')
  await assert.rejects(withDiggingMovements(bot, {}, async () => { throw new Error('boom') }))
  assert.equal(bot.pathfinder.bestHarvestTool, 'original')
  assert.equal(bot.pathfinder.movements, 'v1')
})
