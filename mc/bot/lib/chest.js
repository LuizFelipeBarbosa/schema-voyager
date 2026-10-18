// POST /deposit, /withdraw and /chest: walk next to a chest outside the walls, open it, move items, close it.
// See contracts/bot_api.md (v3.5).

const { goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const { withTimeout } = require('./grid')
const { validateProtect } = require('./protect')
const { tossableStacks, validateToss } = require('./toss')
const { withWalkingMovements } = require('./goto')
const { recordTravel } = require('./travel')
const { freeSlots, gotoGoal, inventoryCounts, settleInventory } = require('./world')

const WALK_MS = 45000
const WALK_RANGE = 3 // a chest opens from 4.5 blocks; 3 spares a step that a canopy or ledge can block
const WALK_STALL_MS = 8000 // no progress this long answers unreachable instead of waiting out WALK_MS
const OPEN_CHEST_TIMEOUT_MS = 5000

function validatePos (pos) {
  return Array.isArray(pos) && pos.length === 3 && pos.every(Number.isInteger) ? null : '"pos" must be three integers'
}

function validateChest (body) {
  return validatePos(body.pos) || validateProtect(body.protect)
}

function validateDeposit (body) {
  return validatePos(body.pos) || validateToss(body) || validateProtect(body.protect)
}

const validateWithdraw = validateDeposit

// Walks next to the chest at `body.pos`, opens it and runs `useChest(window)`, whose result (merged
// with `chest`, `chest_free_slots` and `travel`) is the answer. The window is closed whatever happens.
// With `reportInventory` the answer also has `inventory` and `free_slots`; the bot's inventory only shows
// the moved items once the window is closed and the server resends it, so they are read after that.
async function withChest (bot, body, { episode, signal = null }, useChest, { reportInventory = false } = {}) {
  signal?.throwIfAborted()
  const [x, y, z] = body.pos
  const recording = recordTravel(bot, episode)
  let arrived
  try {
    arrived = await withWalkingMovements(bot, () => gotoGoal(bot, new goals.GoalNear(x, y, z, WALK_RANGE), WALK_MS, { signal, failFast: { stallMs: WALK_STALL_MS } }))
  } catch (err) {
    recording.finish()
    throw err
  }
  await recording.settle()
  const travel = recording.finish()
  if (!arrived) return { ok: false, error: 'unreachable', travel }

  const block = bot.blockAt(new Vec3(x, y, z))
  if (block?.name !== 'chest') return { ok: false, error: 'not_found', detail: { found: block?.name ?? null }, travel }

  const window = await withTimeout(bot.openContainer(block), OPEN_CHEST_TIMEOUT_MS, 'the chest did not open')
  let answer
  try {
    const moved = await useChest(window)
    answer = {
      ok: true,
      ...moved,
      chest: countItems(window.containerItems()),
      chest_free_slots: window.slots.slice(0, window.inventoryStart).filter((slot) => !slot).length,
      travel
    }
  } finally {
    await bot.closeWindow(window)
  }
  if (!reportInventory) return answer
  await settleInventory(bot)
  return { ...answer, inventory: inventoryCounts(bot), free_slots: freeSlots(bot) }
}

function countItems (stacks) {
  const counts = {}
  for (const stack of stacks) counts[stack.name] = (counts[stack.name] || 0) + stack.count
  return counts
}

// How many more of the item `type` the slots [start, end) of `window` take: room in its partial
// stacks, and a full stack for each empty slot.
function roomFor (bot, window, type, start, end) {
  const stackSize = bot.registry.items[type].stackSize
  let room = 0
  for (const stack of window.slots.slice(start, end)) {
    if (!stack) room += stackSize
    else if (stack.type === type) room += stackSize - stack.count
  }
  return room
}

async function deposit (bot, body, session) {
  return withChest(bot, body, session, async (window) => {
    const deposited = {}
    let chestFull = false
    for (const [name, requested] of Object.entries(body.items)) {
      session.signal?.throwIfAborted()
      const type = bot.registry.itemsByName[name]?.id
      if (type === undefined) continue
      const have = tossableStacks(bot, name).reduce((sum, stack) => sum + stack.count, 0)
      const wanted = Math.min(requested, have)
      const amount = Math.min(wanted, roomFor(bot, window, type, 0, window.inventoryStart))
      if (amount > 0) {
        await window.deposit(type, null, amount)
        deposited[name] = amount
      }
      if (amount < wanted) {
        chestFull = true
        break
      }
    }
    return { deposited, chest_full: chestFull }
  }, { reportInventory: true })
}

async function withdraw (bot, body, session) {
  return withChest(bot, body, session, async (window) => {
    const withdrawn = {}
    let inventoryFull = false
    for (const [name, requested] of Object.entries(body.items)) {
      session.signal?.throwIfAborted()
      const type = bot.registry.itemsByName[name]?.id
      if (type === undefined) continue
      const have = window.containerCount(type)
      const wanted = Math.min(requested, have)
      // mineflayer's withdraw refuses a full inventory even when a partial stack has room.
      const room = bot.inventory.emptySlotCount() === 0 ? 0 : roomFor(bot, window, type, window.inventoryStart, window.inventoryEnd)
      const amount = Math.min(wanted, room)
      if (amount > 0) {
        await window.withdraw(type, null, amount)
        withdrawn[name] = amount
      }
      if (amount < wanted) {
        inventoryFull = true
        break
      }
    }
    return { withdrawn, inventory_full: inventoryFull }
  }, { reportInventory: true })
}

async function readChest (bot, body, session) {
  return withChest(bot, body, session, async () => ({}))
}

module.exports = { deposit, readChest, validateChest, validateDeposit, validateWithdraw, withdraw }
