const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { deposit, readChest, validateChest, validateDeposit, validateWithdraw, withdraw } = require('../../lib/chest')
const { fakeWorldBot, registry } = require('./world_fixture')

const CHEST_POS = [5, 76, 36]
const CHEST_SLOTS = 27
const stackOf = (name, count) => ({ name, count, type: registry.itemsByName[name].id, maxDurability: registry.itemsByName[name].maxDurability })

function newAbort () {
  const controller = new AbortController()
  const abort = () => controller.abort(Object.assign(new Error('aborted'), { code: 'aborted' }))
  return { signal: controller.signal, abort }
}

// Waits shrink to nothing, so the inventory settle doesn't slow the tests.
function useFastTimers (t) {
  const realSetTimeout = global.setTimeout
  t.mock.method(global, 'setTimeout', (fn, ms, ...args) => realSetTimeout(fn, Math.min(ms, 1), ...args))
}

// A bot beside a chest block (or `blockName`) with a window like mineflayer's: container slots first,
// then the bot's slots 9..44. `inventory` and `chest` are lists of [name, count], one stack each.
// `walksTo` is where the pathfinder ends; `failOn` makes window.deposit/withdraw throw.
function chestBot ({ blockName = 'chest', inventory = [], chest = [], heldSlot = null, walksTo = [5, 76, 34], failOn = null } = {}) {
  const bot = fakeWorldBot({ [CHEST_POS.join(',')]: blockName })
  const slots = new Array(46).fill(null)
  inventory.forEach(([name, count], i) => { slots[9 + i] = stackOf(name, count) })
  const chestSlots = new Array(CHEST_SLOTS).fill(null)
  chest.forEach(([name, count], i) => { chestSlots[i] = stackOf(name, count) })
  const log = { opens: 0, closes: 0, moves: [] }

  // Moves up to `count` of `type` from `from` into `into` (arrays of stacks), merging into partial stacks.
  const move = (from, into, type, count) => {
    const stackSize = registry.items[type].stackSize
    for (let i = 0; i < from.length && count > 0; i++) {
      if (from[i]?.type !== type) continue
      const amount = Math.min(from[i].count, count)
      let left = amount
      for (let j = 0; j < into.length && left > 0; j++) {
        if (into[j]?.type === type && into[j].count < stackSize) {
          const put = Math.min(left, stackSize - into[j].count)
          into[j].count += put
          left -= put
        } else if (!into[j]) {
          into[j] = { ...from[i], count: Math.min(left, stackSize) }
          left -= into[j].count
        }
      }
      from[i].count -= amount
      if (from[i].count === 0) from[i] = null
      count -= amount
    }
  }
  const inventoryView = () => slots.slice(9, 45)
  const sync = (view) => view.forEach((stack, i) => { slots[9 + i] = stack })
  const transfer = (kind, from, into) => async (type, meta, count) => {
    log.moves.push({ kind, name: registry.items[type].name, count })
    if (failOn === kind) throw new Error('click failed')
    const source = from()
    const dest = into()
    move(source, dest, type, count)
    if (kind === 'deposit') { sync(source) } else { sync(dest) }
  }
  const window = {
    inventoryStart: CHEST_SLOTS,
    inventoryEnd: CHEST_SLOTS + 36,
    get slots () { return [...chestSlots, ...inventoryView()] },
    containerItems: () => chestSlots.filter(Boolean),
    containerCount: (type) => chestSlots.filter((s) => s?.type === type).reduce((sum, s) => sum + s.count, 0),
    deposit: transfer('deposit', inventoryView, () => chestSlots),
    withdraw: transfer('withdraw', () => chestSlots, inventoryView)
  }

  bot.entity.onGround = true
  bot.on = () => {}
  bot.removeListener = () => {}
  bot.clearControlStates = () => {}
  bot.dig = async () => {}
  bot.quickBarSlot = heldSlot === null ? 0 : heldSlot - 36
  bot.inventory = {
    slots,
    items: () => slots.filter(Boolean),
    emptySlotCount: () => slots.slice(9, 45).filter((s) => !s).length,
    on () {},
    removeListener () {}
  }
  bot._client = { on () {}, removeListener () {} }
  bot.pathfinder = {
    movements: 'previous',
    setMovements (movements) { bot.pathfinder.movements = movements },
    setGoal () {},
    goto: async () => { bot.entity.position = new Vec3(...walksTo).offset(0.5, 0, 0.5) }
  }
  bot.openContainer = async () => { log.opens++; bot.currentWindow = window; return window }
  bot.closeWindow = async () => { log.closes++; bot.currentWindow = null }
  return { bot, log, chestSlots, slots }
}

const session = (extra = {}) => ({ episode: { walked: 0 }, ...extra })

test('deposit moves items in request order and stops at chest_full when the next item does not fit', async (t) => {
  useFastTimers(t)
  const filler = Array.from({ length: CHEST_SLOTS - 1 }, () => ['stick', 64])
  const { bot, log } = chestBot({ inventory: [['coal', 30], ['dirt', 64], ['cobblestone', 10]], chest: filler })

  const result = await deposit(bot, { pos: CHEST_POS, items: { coal: 20, dirt: 64, cobblestone: 10 } }, session())

  assert.equal(result.ok, true)
  assert.deepEqual(log.moves.map((m) => m.name), ['coal'])
  assert.deepEqual(result.deposited, { coal: 20 })
  assert.equal(result.chest_full, true)
  assert.equal(result.chest_free_slots, 0)
  assert.equal(result.chest.coal, 20)
  assert.deepEqual(result.inventory, { coal: 10, dirt: 64, cobblestone: 10 })
  assert.equal(result.free_slots, 33)
  assert.equal(log.closes, 1)
})

test('deposit takes everything asked in request order and reports a free chest', async (t) => {
  useFastTimers(t)
  const { bot, log } = chestBot({ inventory: [['dirt', 64], ['dirt', 64], ['coal', 5]] })

  const result = await deposit(bot, { pos: CHEST_POS, items: { coal: 5, dirt: 100, diamond: 3 } }, session())

  assert.deepEqual(log.moves.map((m) => m.name), ['coal', 'dirt'])
  assert.deepEqual(result.deposited, { coal: 5, dirt: 100 })
  assert.deepEqual(result.chest, { coal: 5, dirt: 100 })
  assert.equal(result.chest_full, false)
  assert.equal(result.chest_free_slots, 24)
  assert.deepEqual(Object.keys(result).sort(), ['chest', 'chest_free_slots', 'chest_full', 'deposited', 'free_slots', 'inventory', 'ok', 'travel'])
})

test('deposit never moves the held tool, but the other stacks of that tool', async (t) => {
  useFastTimers(t)
  const { bot, log } = chestBot({ inventory: [['iron_pickaxe', 1], ['iron_pickaxe', 1]], heldSlot: 9 })

  const result = await deposit(bot, { pos: CHEST_POS, items: { iron_pickaxe: 2 } }, session())

  assert.deepEqual(log.moves, [{ kind: 'deposit', name: 'iron_pickaxe', count: 1 }])
  assert.deepEqual(result.deposited, { iron_pickaxe: 1 })
  assert.equal(result.chest_full, false)

  const only = chestBot({ inventory: [['iron_pickaxe', 1]], heldSlot: 9 })
  const none = await deposit(only.bot, { pos: CHEST_POS, items: { iron_pickaxe: 1 } }, session())
  assert.deepEqual(none.deposited, {})
  assert.deepEqual(only.log.moves, [])
})

test('withdraw takes less than asked when the chest has less, and ok stays true', async (t) => {
  useFastTimers(t)
  const { bot } = chestBot({ chest: [['coal', 2], ['dirt', 10]] })

  const result = await withdraw(bot, { pos: CHEST_POS, items: { coal: 5, dirt: 4, gold_ingot: 1 } }, session())

  assert.equal(result.ok, true)
  assert.deepEqual(result.withdrawn, { coal: 2, dirt: 4 })
  assert.deepEqual(result.chest, { dirt: 6 })
  assert.equal(result.chest_free_slots, 26)
  assert.equal(result.inventory_full, false)
  assert.deepEqual(result.inventory, { coal: 2, dirt: 4 })
  assert.equal(result.free_slots, 34)
})

test('withdraw stops at inventory_full: room in a partial stack and one empty slot, then nothing', async (t) => {
  useFastTimers(t)
  const sticks = Array.from({ length: 34 }, () => ['stick', 64])
  const { bot } = chestBot({ inventory: [...sticks, ['coal', 60]], chest: [['coal', 64], ['coal', 36], ['dirt', 5]] })

  const result = await withdraw(bot, { pos: CHEST_POS, items: { coal: 100, dirt: 5 } }, session())

  assert.deepEqual(result.withdrawn, { coal: 68 })
  assert.equal(result.inventory_full, true)
  assert.deepEqual(result.chest, { coal: 32, dirt: 5 })
  assert.equal(result.free_slots, 0)

  const again = await withdraw(bot, { pos: CHEST_POS, items: { dirt: 5 } }, session())
  assert.deepEqual(again.withdrawn, {})
  assert.equal(again.inventory_full, true)
})

test('chest reads the content without moving anything', async (t) => {
  useFastTimers(t)
  const { bot, log } = chestBot({ chest: [['coal', 2], ['coal', 3]] })

  const result = await readChest(bot, { pos: CHEST_POS }, session())

  assert.equal(result.ok, true)
  assert.deepEqual(result.chest, { coal: 5 })
  assert.equal(result.chest_free_slots, 25)
  assert.deepEqual(log.moves, [])
  assert.equal(log.closes, 1)
  assert.deepEqual(Object.keys(result).sort(), ['chest', 'chest_free_slots', 'ok', 'travel'])
})

test('not_found tells what stands at pos, and nothing is opened', async (t) => {
  useFastTimers(t)
  const { bot, log } = chestBot({ blockName: 'barrel' })
  const result = await readChest(bot, { pos: CHEST_POS }, session())
  assert.deepEqual({ ok: result.ok, error: result.error, detail: result.detail }, { ok: false, error: 'not_found', detail: { found: 'barrel' } })
  assert.ok(result.travel)
  assert.equal(log.opens, 0)

  bot.blockAt = () => null
  const unloaded = await deposit(bot, { pos: CHEST_POS, items: { coal: 1 } }, session())
  assert.deepEqual(unloaded.detail, { found: null })
})

test('unreachable when the walk ends out of range, with travel and no window', async (t) => {
  useFastTimers(t)
  const { bot, log } = chestBot({ walksTo: [5, 76, 20] })
  const result = await withdraw(bot, { pos: CHEST_POS, items: { coal: 1 } }, session())
  assert.equal(result.ok, false)
  assert.equal(result.error, 'unreachable')
  assert.ok(result.travel)
  assert.equal(log.opens, 0)
  assert.equal(bot.pathfinder.movements, 'previous')
})

test('the window is closed when a move fails', async (t) => {
  useFastTimers(t)
  const { bot, log } = chestBot({ inventory: [['coal', 5]], failOn: 'deposit' })
  await assert.rejects(deposit(bot, { pos: CHEST_POS, items: { coal: 5 } }, session()), /click failed/)
  assert.equal(log.closes, 1)
})

test('an abort between moves closes the window and throws the signal reason', async (t) => {
  useFastTimers(t)
  const { bot, log } = chestBot({ chest: [['coal', 5], ['dirt', 5]] })
  const { signal, abort } = newAbort()
  const window = await bot.openContainer()
  const move = window.withdraw
  window.withdraw = async (...args) => { await move(...args); abort() }
  log.opens = 0

  await assert.rejects(withdraw(bot, { pos: CHEST_POS, items: { coal: 5, dirt: 5 } }, session({ signal })), { code: 'aborted' })

  assert.deepEqual(log.moves.map((m) => m.name), ['coal'])
  assert.equal(log.closes, 1)
})

test('an abort before the walk throws without opening anything', async () => {
  const { bot, log } = chestBot()
  const { signal, abort } = newAbort()
  abort()
  await assert.rejects(readChest(bot, { pos: CHEST_POS }, session({ signal })), { code: 'aborted' })
  assert.equal(log.opens, 0)
})

test('validators accept a good body and reject a bad pos, items or protect', () => {
  const good = { pos: CHEST_POS, items: { coal: 1 }, protect: [{ origin: [0, 0, 0], size: [1, 1, 1] }] }
  assert.equal(validateDeposit(good), null)
  assert.equal(validateWithdraw(good), null)
  assert.equal(validateChest({ pos: CHEST_POS }), null)

  const badPos = [{}, { pos: [1, 2] }, { pos: [1, 2, 3, 4] }, { pos: [1.5, 2, 3] }, { pos: ['1', 2, 3] }, { pos: 'x' }]
  for (const bad of badPos) {
    assert.equal(typeof validateChest(bad), 'string', JSON.stringify(bad))
    assert.equal(typeof validateDeposit({ ...bad, items: { coal: 1 } }), 'string', JSON.stringify(bad))
    assert.equal(typeof validateWithdraw({ ...bad, items: { coal: 1 } }), 'string', JSON.stringify(bad))
  }
  for (const items of [undefined, {}, [], { coal: 0 }, { coal: 1.5 }, { '': 1 }]) {
    assert.equal(typeof validateDeposit({ pos: CHEST_POS, items }), 'string', JSON.stringify(items))
    assert.equal(typeof validateWithdraw({ pos: CHEST_POS, items }), 'string', JSON.stringify(items))
  }
  const badProtect = { pos: CHEST_POS, items: { coal: 1 }, protect: [{ origin: [0, 0], size: [1, 1, 1] }] }
  assert.equal(typeof validateDeposit(badProtect), 'string')
  assert.equal(typeof validateWithdraw(badProtect), 'string')
  assert.equal(typeof validateChest({ pos: CHEST_POS, protect: 'x' }), 'string')
})
