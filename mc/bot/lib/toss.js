// POST /toss: drops items to free inventory slots, then walks away from them
// so they are not picked up again inside a later step window.

const { goals } = require('mineflayer-pathfinder')
const { recordTravel } = require('./travel')
const { freeSlots, gotoGoal, inventoryCounts, settleInventory } = require('./world')

const FIRST_SLOT = 9 // main inventory and hotbar: window slots 9..44
const LAST_SLOT = 44
const HOTBAR_OFFSET = 36
const WALK_AWAY_BLOCKS = 4.5
const WALKED_AWAY_BLOCKS = 4
const WALK_TIMEOUT_MS = 10000

// Ends 4.5 or more blocks (horizontal) from `origin`, and not in front of
// `yaw` (the offset has a dot product of 0 or less with the facing vector).
// The heuristic is the distance still missing to the ring, which never
// overestimates the walk.
class WalkAwayGoal extends goals.Goal {
  constructor (origin, yaw) {
    super()
    this.origin = origin
    this.facing = { x: -Math.sin(yaw), z: -Math.cos(yaw) }
  }

  // `node` is a block position; its centre is where the bot stands.
  offsetOf (node) {
    return { x: node.x + 0.5 - this.origin.x, z: node.z + 0.5 - this.origin.z }
  }

  heuristic (node) {
    const offset = this.offsetOf(node)
    return Math.max(0, WALK_AWAY_BLOCKS - Math.hypot(offset.x, offset.z))
  }

  isEnd (node) {
    const offset = this.offsetOf(node)
    return Math.hypot(offset.x, offset.z) >= WALK_AWAY_BLOCKS &&
      offset.x * this.facing.x + offset.z * this.facing.z <= 0
  }
}

// The stacks of `name` in slots 9..44, smallest first, leaving out the held
// slot when it holds a tool.
function tossableStacks (bot, name) {
  const heldSlot = bot.quickBarSlot + HOTBAR_OFFSET
  const stacks = []
  for (let slot = FIRST_SLOT; slot <= LAST_SLOT; slot++) {
    const item = bot.inventory.slots[slot]
    if (!item || item.name !== name) continue
    if (slot === heldSlot && item.maxDurability) continue
    stacks.push(item)
  }
  return stacks.sort((a, b) => a.count - b.count)
}

// Tosses up to `wanted` of `name`; returns how many it threw.
async function tossItems (bot, name, wanted) {
  let remaining = wanted
  for (const stack of tossableStacks(bot, name)) {
    if (remaining <= 0) break
    const amount = Math.min(stack.count, remaining)
    if (amount === stack.count) await bot.tossStack(stack)
    else await bot.toss(stack.type, null, amount)
    remaining -= amount
  }
  return wanted - remaining
}

// Error string for a bad /toss body, else null.
function validateToss (body) {
  const items = body.items
  if (items === null || typeof items !== 'object' || Array.isArray(items) || Object.keys(items).length === 0) {
    return '"items" must be a non-empty object of item name to count'
  }
  const valid = Object.entries(items).every(([name, count]) => name.length > 0 && Number.isInteger(count) && count > 0)
  return valid ? null : '"items" must map non-empty item names to positive integer counts'
}

// `body.items`: item name -> count to drop. `session`: {episode, ...}.
async function toss (bot, body, session) {
  const before = inventoryCounts(bot)
  await bot.look(bot.entity.yaw, 0, true)
  const origin = bot.entity.position.clone()
  const yaw = bot.entity.yaw

  let thrown = 0
  for (const [name, wanted] of Object.entries(body.items)) thrown += await tossItems(bot, name, wanted)

  if (thrown === 0) {
    return {
      ok: true,
      tossed: {},
      inventory: inventoryCounts(bot),
      free_slots: freeSlots(bot),
      walked_away: false,
      travel: recordTravel(bot, session.episode).finish()
    }
  }

  await settleInventory(bot)
  const recording = recordTravel(bot, session.episode)
  await gotoGoal(bot, new WalkAwayGoal(origin, yaw), WALK_TIMEOUT_MS)
  const travel = recording.finish()

  const after = inventoryCounts(bot)
  const tossed = {}
  for (const name of Object.keys(body.items)) {
    const gone = (before[name] || 0) - (after[name] || 0)
    if (gone > 0) tossed[name] = gone
  }
  const here = bot.entity.position
  return {
    ok: true,
    tossed,
    inventory: after,
    free_slots: freeSlots(bot),
    walked_away: Math.hypot(here.x - origin.x, here.z - origin.z) >= WALKED_AWAY_BLOCKS,
    travel
  }
}

module.exports = { WalkAwayGoal, toss, tossableStacks, validateToss }
