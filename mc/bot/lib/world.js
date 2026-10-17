// Shared helpers: inventory snapshots, block scanning, pathing, drop watching
// and pickup. Used by the action modules and by server.js.

const { Vec3 } = require('vec3')
const { goals } = require('mineflayer-pathfinder')
const { isInCell } = require('./geometry')

const TICK_MS = 50
const SCAN_RADIUS = 16
const PATH_TIMEOUT_MS = 30000
const DROP_RADIUS = 1.5
const AIR_BLOCKS = new Set(['air', 'cave_air', 'void_air'])
const NEARBY_IGNORED = new Set([
  ...AIR_BLOCKS, 'grass_block', 'dirt', 'bedrock', 'barrier', 'short_grass', 'tall_grass'
])
const NEIGHBOUR_OFFSETS = [
  new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 1, 0),
  new Vec3(0, -1, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)
]

function sleepTicks (ticks) {
  return new Promise((resolve) => setTimeout(resolve, ticks * TICK_MS))
}

function round (value, places = 2) {
  const factor = 10 ** places
  return Math.round(value * factor) / factor
}

function positionOf (bot) {
  const { x, y, z } = bot.entity.position
  return { x: round(x), y: round(y), z: round(z) }
}

// --- Inventory -------------------------------------------------------------

// Item name -> total count over armor, main, hotbar and offhand (window 0
// slots 5..45). The 2x2 crafting grid and the cursor are not counted.
function inventoryCounts (bot) {
  const counts = {}
  for (let slot = 5; slot <= 45; slot++) {
    const item = bot.inventory.slots[slot]
    if (item) counts[item.name] = (counts[item.name] || 0) + item.count
  }
  return counts
}

function addCount (counts, name, amount = 1) {
  counts[name] = (counts[name] || 0) + amount
}

function countDelta (before, after) {
  const delta = {}
  for (const name of new Set([...Object.keys(before), ...Object.keys(after)])) {
    const diff = (after[name] || 0) - (before[name] || 0)
    if (diff !== 0) delta[name] = diff
  }
  return delta
}

function countGains (before, after) {
  const gains = {}
  for (const [name, diff] of Object.entries(countDelta(before, after))) {
    if (diff > 0) gains[name] = diff
  }
  return gains
}

// A no-op click with a stale state id makes the server send the whole window
// back (Mineflayer predicts crafting results client-side). Gives up after 2 s.
async function resyncWindow (bot, window) {
  if (!bot._syncWindow) return
  let timer = null
  const timeout = new Promise((resolve) => { timer = setTimeout(resolve, 2000) })
  try {
    await Promise.race([bot._syncWindow(window), timeout])
  } catch (err) {
    console.log(`warn: window resync failed: ${err.message}`)
  } finally {
    clearTimeout(timer)
  }
}

function findInventoryItem (bot, name) {
  return bot.inventory.items().find((item) => item.name === name) || null
}

// Waits until the player inventory has had no slot updates for `quietTicks`
// ticks, giving up after `capTicks`. Server corrections to Mineflayer's
// predicted slots arrive as set_slot / window_items packets.
async function settleInventory (bot, quietTicks = 5, capTicks = 40) {
  const startedAt = Date.now()
  let lastUpdateAt = startedAt
  const onUpdate = () => { lastUpdateAt = Date.now() }
  bot.inventory.on('updateSlot', onUpdate)
  bot._client.on('set_slot', onUpdate)
  bot._client.on('window_items', onUpdate)
  try {
    while (Date.now() - lastUpdateAt < quietTicks * TICK_MS &&
      Date.now() - startedAt < capTicks * TICK_MS) {
      await sleepTicks(1)
    }
  } finally {
    bot.inventory.removeListener('updateSlot', onUpdate)
    bot._client.removeListener('set_slot', onUpdate)
    bot._client.removeListener('window_items', onUpdate)
  }
}

// Empties the main hand without dropping anything: selects an empty hotbar
// slot, or moves the held stack into a free inventory slot. With a full
// inventory the hand is left as is (Mineflayer would toss the stack).
async function emptyHand (bot) {
  if (!bot.heldItem) return
  if (bot.inventory.firstEmptyInventorySlot() === null) return
  await bot.unequip('hand')
}

// --- Block scanning --------------------------------------------------------

function blockNameAt (bot, pos) {
  const block = bot.registry.blocksByStateId[bot.world.getBlockStateId(pos)]
  return block ? block.name : 'air'
}

function hasAirNeighbour (bot, pos) {
  return NEIGHBOUR_OFFSETS.some((offset) => AIR_BLOCKS.has(blockNameAt(bot, pos.plus(offset))))
}

// Visits every block whose centre lies within SCAN_RADIUS of the bot and
// calls visit(name, pos, distance) for each block not in NEARBY_IGNORED.
function forEachNearbyBlock (bot, visit) {
  const origin = bot.entity.position
  const centre = origin.floored()
  const minY = bot.game.minY
  const maxY = minY + bot.game.height - 1
  for (let dx = -SCAN_RADIUS; dx <= SCAN_RADIUS; dx++) {
    for (let dy = -SCAN_RADIUS; dy <= SCAN_RADIUS; dy++) {
      const y = centre.y + dy
      if (y < minY || y > maxY) continue
      for (let dz = -SCAN_RADIUS; dz <= SCAN_RADIUS; dz++) {
        const pos = new Vec3(centre.x + dx, y, centre.z + dz)
        const distance = origin.distanceTo(pos.offset(0.5, 0.5, 0.5))
        if (distance > SCAN_RADIUS) continue
        const name = blockNameAt(bot, pos)
        if (NEARBY_IGNORED.has(name)) continue
        visit(name, pos, distance)
      }
    }
  }
}

// Block name -> {count, nearest, distance}; `nearest` only considers blocks
// with at least one air neighbour.
function scanNearbyBlocks (bot) {
  const summary = {}
  forEachNearbyBlock(bot, (name, pos, distance) => {
    const entry = summary[name] || (summary[name] = { count: 0, nearest: null, distance: null })
    entry.count++
    if ((entry.nearest === null || distance < entry.distance) && hasAirNeighbour(bot, pos)) {
      entry.nearest = { x: pos.x, y: pos.y, z: pos.z }
      entry.distance = round(distance, 1)
    }
  })
  return summary
}

// The nearest `blockName` with an air neighbour whose position (as
// Vec3#toString) is not in `skipped`.
function findNearestExposedBlock (bot, blockName, skipped = new Set()) {
  let best = null
  let bestDistance = Infinity
  forEachNearbyBlock(bot, (name, pos, distance) => {
    if (name !== blockName || distance >= bestDistance) return
    if (!hasAirNeighbour(bot, pos) || skipped.has(pos.toString())) return
    best = pos
    bestDistance = distance
  })
  return best
}

// --- Movement --------------------------------------------------------------

function stopMoving (bot) {
  bot.pathfinder.setGoal(null)
  bot.clearControlStates()
}

// Paths to `goal`; true when the bot ends inside the goal. pathfinder.goto
// can resolve without reaching the goal (empty path), so the end is checked
// the same way the pathfinder does (feet block, or one above on slabs).
async function gotoGoal (bot, goal, timeoutMs = PATH_TIMEOUT_MS) {
  let timer = null
  const timeout = new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(new Error(`path timeout after ${timeoutMs} ms`)), timeoutMs)
  })
  try {
    await Promise.race([bot.pathfinder.goto(goal), timeout])
  } catch (err) {
    stopMoving(bot)
  } finally {
    clearTimeout(timer)
  }
  const feet = bot.entity.position.floored()
  return goal.isEnd(feet) || goal.isEnd(feet.offset(0, 1, 0))
}

// --- Drops and pickup ------------------------------------------------------

function readDroppedStack (entity) {
  try {
    const item = entity.getDroppedItem()
    return item ? { name: item.name, count: item.count } : null
  } catch (err) {
    return null
  }
}

// Starts recording item entities that spawn near `blockPos`: within
// DROP_RADIUS of the block centre, or with `inCell` only those that spawn in
// the block's own cell (so drops of neighbouring digs, leaf decay or gravel
// are not counted). Each entity's stack is taken from its first item metadata
// (later merges between drops would otherwise double count). `onDrop(entity)`
// is called for every recorded entity as it spawns, `onLost(entity)` for those
// whose stack could not be read. stop() returns {counts, entities}.
function watchDrops (bot, blockPos, { inCell = false, onDrop = null, onLost = null } = {}) {
  const centre = blockPos.offset(0.5, 0.5, 0.5)
  const seen = new Map() // entity id -> {entity, stack}

  const onSpawn = (entity) => {
    if (entity.name !== 'item') return
    const belongs = inCell ? isInCell(entity.position, blockPos) : entity.position.distanceTo(centre) <= DROP_RADIUS
    if (!belongs) return
    seen.set(entity.id, { entity, stack: readDroppedStack(entity) })
    if (onDrop) onDrop(entity)
  }
  const onUpdate = (entity) => {
    const record = seen.get(entity.id)
    if (record && !record.stack) record.stack = readDroppedStack(entity)
  }
  bot.on('entitySpawn', onSpawn)
  bot.on('entityUpdate', onUpdate)

  return {
    stop () {
      bot.removeListener('entitySpawn', onSpawn)
      bot.removeListener('entityUpdate', onUpdate)
      const counts = {}
      const entities = []
      for (const record of seen.values()) {
        const stack = record.stack || readDroppedStack(record.entity)
        if (!stack) {
          console.log(`warn: item entity ${record.entity.id} near ${blockPos} has no item metadata`)
          if (onLost) onLost(record.entity)
          continue
        }
        addCount(counts, stack.name, stack.count)
        entities.push(record.entity)
      }
      return { counts, entities }
    }
  }
}

function waitUntilGone (bot, entity, timeoutMs) {
  if (!bot.entities[entity.id]) return Promise.resolve()
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(timer)
      bot.removeListener('entityGone', onGone)
      resolve()
    }
    const onGone = (gone) => { if (gone.id === entity.id) finish() }
    const timer = setTimeout(finish, timeoutMs)
    bot.on('entityGone', onGone)
  })
}

// Ends on a cell from which the player's pickup box (hitbox grown by 1 block
// sideways and 0.5 vertically) reaches an item lying at `pos`.
class GoalPickupRange extends goals.Goal {
  constructor (pos) {
    super()
    this.pos = pos.clone()
  }

  heuristic (node) {
    return Math.hypot(node.x + 0.5 - this.pos.x, node.z + 0.5 - this.pos.z) + Math.abs(node.y - this.pos.y)
  }

  isEnd (node) {
    const above = this.pos.y - node.y
    return Math.abs(node.x + 0.5 - this.pos.x) < 1.1 && Math.abs(node.z + 0.5 - this.pos.z) < 1.1 &&
      above > -0.5 && above < 2
  }
}

// Tolerant pickup: walk next to each entity that still exists and wait up to
// 1 s for it to be collected; two retries over whatever is left.
async function pickUpEntities (bot, entities) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const remaining = entities.filter((entity) => bot.entities[entity.id])
    if (remaining.length === 0) return
    for (const entity of remaining) {
      if (!bot.entities[entity.id]) continue
      await gotoGoal(bot, new GoalPickupRange(entity.position), 10000)
      await waitUntilGone(bot, entity, 1000)
    }
  }
}

module.exports = {
  AIR_BLOCKS,
  TICK_MS,
  addCount,
  blockNameAt,
  countDelta,
  countGains,
  emptyHand,
  findInventoryItem,
  findNearestExposedBlock,
  gotoGoal,
  hasAirNeighbour,
  inventoryCounts,
  pickUpEntities,
  positionOf,
  readDroppedStack,
  resyncWindow,
  round,
  scanNearbyBlocks,
  settleInventory,
  sleepTicks,
  stopMoving,
  watchDrops
}
