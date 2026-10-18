const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { WalkAwayGoal, toss, tossableStacks, validateToss } = require('../../lib/toss')
const { freeSlots } = require('../../lib/world')
const { fakeWorldBot } = require('./world_fixture')

function stack (name, count, maxDurability = null) {
  return { name, type: name.length, count, maxDurability }
}

// A fake bot whose tosses remove items from `inventory.slots`. `slots` maps
// window slot -> stack. `pathfinder.goto` walks to `walkTo` (if given) and
// then runs `afterWalk`.
function tossBot (slots, { quickBarSlot = 0, walkTo = null, afterWalk = null } = {}) {
  const bot = fakeWorldBot()
  bot.entity.yaw = 0 // facing -z
  bot.quickBarSlot = quickBarSlot
  bot.inventory = { slots: new Array(46).fill(null), items: () => [], on () {}, removeListener () {} }
  for (const [slot, item] of Object.entries(slots)) bot.inventory.slots[slot] = { ...item }
  bot._client = { on () {}, removeListener () {} }
  bot.on = () => {}
  bot.removeListener = () => {}
  bot.dig = async () => {}
  bot.looks = []
  bot.tosses = []
  bot.look = async (yaw, pitch) => { bot.looks.push([yaw, pitch]) }
  bot.tossStack = async (item) => {
    bot.tosses.push([item.name, item.count])
    bot.inventory.slots[bot.inventory.slots.indexOf(item)] = null
  }
  bot.toss = async (type, metadata, count) => {
    const item = bot.inventory.slots.find((s) => s && s.type === type)
    bot.tosses.push([item.name, count])
    item.count -= count
  }
  bot.walks = 0
  bot.pathfinder.goto = async () => {
    bot.walks++
    if (walkTo) bot.entity.position = walkTo
    if (afterWalk) afterWalk(bot)
  }
  bot.pathfinder.setGoal = () => {}
  return bot
}

const session = { episode: { walked: 0 } }

test('freeSlots counts only the main and hotbar slots', () => {
  const bot = tossBot({ 0: stack('dirt', 1), 5: stack('dirt', 1), 9: stack('dirt', 1), 44: stack('dirt', 1), 45: stack('dirt', 1) })
  assert.equal(freeSlots(bot), 34)
  assert.equal(freeSlots(tossBot({})), 36)
})

test('tossableStacks lists the smallest stacks first and skips a held tool', () => {
  const bot = tossBot({ 9: stack('dirt', 30), 10: stack('dirt', 5), 36: stack('dirt', 64) })
  assert.deepEqual(tossableStacks(bot, 'dirt').map((s) => s.count), [5, 30, 64])
  const tools = tossBot({ 36: stack('iron_pickaxe', 1, 250), 37: stack('iron_pickaxe', 1, 250) }, { quickBarSlot: 0 })
  assert.deepEqual(tossableStacks(tools, 'iron_pickaxe').length, 1)
  const heldNonTool = tossBot({ 36: stack('dirt', 3) }, { quickBarSlot: 0 })
  assert.equal(tossableStacks(heldNonTool, 'dirt').length, 1)
})

test('toss drops the smallest stacks first, a partial count from the last one', async () => {
  const bot = tossBot({ 9: stack('dirt', 30), 10: stack('dirt', 5), 11: stack('dirt', 64), 12: stack('granite', 7) },
    { walkTo: new Vec3(0.5, 10, 6.5) })
  const result = await toss(bot, { items: { dirt: 40 } }, session)
  assert.deepEqual(bot.tosses, [['dirt', 5], ['dirt', 30], ['dirt', 5]])
  assert.deepEqual(result.tossed, { dirt: 40 })
})

test('toss takes a partial count from a stack and leaves unnamed items alone', async () => {
  const bot = tossBot({ 9: stack('dirt', 30), 10: stack('granite', 7) }, { walkTo: new Vec3(0.5, 10, 6.5) })
  const result = await toss(bot, { items: { dirt: 12, cobblestone: 3 } }, session)
  assert.deepEqual(bot.tosses, [['dirt', 12]])
  assert.deepEqual(result.tossed, { dirt: 12 })
  assert.deepEqual(result.inventory, { dirt: 18, granite: 7 })
  assert.equal(result.free_slots, 34)
  assert.deepEqual(bot.looks, [[0, 0]])
})

test('toss never throws the held tool but does throw a held non-tool', async () => {
  const toolBot = tossBot({ 36: stack('iron_pickaxe', 1, 250) })
  const none = await toss(toolBot, { items: { iron_pickaxe: 1 } }, session)
  assert.deepEqual(toolBot.tosses, [])
  assert.deepEqual(none.tossed, {})

  const dirtBot = tossBot({ 36: stack('dirt', 4) }, { walkTo: new Vec3(0.5, 10, 6.5) })
  const some = await toss(dirtBot, { items: { dirt: 4 } }, session)
  assert.deepEqual(some.tossed, { dirt: 4 })
})

test('nothing to toss: no walk, walked_away false, empty travel', async () => {
  const bot = tossBot({ 9: stack('dirt', 3) })
  const result = await toss(bot, { items: { granite: 5 } }, session)
  assert.equal(bot.walks, 0)
  assert.equal(result.ok, true)
  assert.equal(result.walked_away, false)
  assert.deepEqual(result.tossed, {})
  assert.deepEqual(result.travel, { distance: 0, dug: {}, placed: {}, picked_up: {}, placed_cells: [] })
})

test('tossed is measured after the walk: a stack picked up again is not counted', async () => {
  const bot = tossBot({ 9: stack('dirt', 10) }, {
    walkTo: new Vec3(0.5, 10, 6.5),
    afterWalk: (b) => { b.inventory.slots[9] = stack('dirt', 4) }
  })
  const result = await toss(bot, { items: { dirt: 10 } }, session)
  assert.deepEqual(result.tossed, { dirt: 6 })
  assert.deepEqual(result.travel.picked_up, { dirt: 4 })
  assert.equal(result.walked_away, true)
})

test('walked_away is false when the walk falls short', async () => {
  const bot = tossBot({ 9: stack('dirt', 10) }, { walkTo: new Vec3(0.5, 10, 2.5) })
  const result = await toss(bot, { items: { dirt: 10 } }, session)
  assert.equal(result.walked_away, false)
})

test('the walk-away goal ends 4.5 blocks out, beside or behind the bot, never in front', () => {
  const goal = new WalkAwayGoal(new Vec3(0.5, 10, 0.5), 0) // facing -z
  const end = (x, z) => goal.isEnd(new Vec3(x, 10, z))
  assert.equal(end(0, 5), true) // behind: offset (0, 5)
  assert.equal(end(0, 4), false) // 4.0 from the toss point: inside the ring
  assert.equal(end(5, 0), true) // beside: dot product 0
  assert.equal(end(0, -5), false) // in front
  assert.equal(end(4, -1), false) // far enough, slightly in front
  assert.equal(end(0, 0), false)
  assert.equal(goal.heuristic(new Vec3(0, 10, 0)), 4.5)
  assert.equal(goal.heuristic(new Vec3(0, 10, 9)), 0)
})

test('the toss validator wants a non-empty object of names to positive integers', () => {
  assert.equal(validateToss({ items: { dirt: 40, granite: 1 } }), null)
  for (const items of [undefined, null, [], 'dirt', {}, { dirt: 0 }, { dirt: -2 }, { dirt: 1.5 }, { dirt: '3' }, { '': 3 }]) {
    assert.equal(typeof validateToss({ items }), 'string', JSON.stringify(items))
  }
  assert.equal(typeof validateToss({}), 'string')
})
