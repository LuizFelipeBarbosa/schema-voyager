// Shared helpers: inventory snapshots, block scanning, pathing, drop watching
// and pickup. Used by the action modules and by server.js.

const { Vec3 } = require('vec3')
const { goals } = require('mineflayer-pathfinder')
const { isInCell } = require('./geometry')

const TICK_MS = 50
const SCAN_RADIUS = 16
const PATH_TIMEOUT_MS = 30000
const STALL_CHECK_MS = 500
const STALL_PROGRESS_BLOCKS = 1 // moving less than this counts as standing still
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

// Number of empty slots among the main inventory and hotbar (window slots
// 9..44), 0 to 36. Armor, offhand, crafting grid and cursor don't count.
function freeSlots (bot) {
  let free = 0
  for (let slot = 9; slot <= 44; slot++) {
    if (!bot.inventory.slots[slot]) free++
  }
  return free
}

// Item name -> count of what the player window holds outside slots 5..45:
// the output and crafting grid (slots 0..4) and the cursor. Empty once a
// craft or probe has put everything back.
function heldOutsideInventory (bot) {
  const counts = {}
  const held = [...bot.inventory.slots.slice(0, 5), bot.inventory.selectedItem]
  for (const item of held.filter(Boolean)) counts[item.name] = (counts[item.name] || 0) + item.count
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

// Paths to `goal` and says how the attempt ended (`end`), not only whether
// the bot got there:
//   reached      the bot is inside the goal (checked the way the pathfinder
//                does: feet block, or one above on slabs);
//   no_path      the last path_update status was noPath (pathfinder.goto
//                resolves, without an error, on an empty path before it looks
//                at the status, so it may resolve);
//   path_timeout the last status was timeout (the A* search ran out of time);
//   timeout      our own `timeoutMs` ran out;
//   stalled      `failFast`: the bot neither moved a block nor dug for
//                `failFast.stallMs`;
//   no_approach  `approach` ({target, windowMs}): the bot's closest distance to
//                the target block's centre did not improve by a block for
//                `windowMs` (digging does not count as approaching);
//   ended        goto resolved or was rejected (goal changed, path stopped)
//                with the bot outside the goal and no failure status.
// `start` and `closest` are those distances (null without `approach`),
// `resets` counts path_reset events and `thinkMs` sums the A* search times
// (path_update times are cumulative within a search, so each counts once).
// With `signal`, an abort stops the walk and throws `signal.reason` (never a
// false result: a cancelled walk is not unreachable).
async function gotoGoalDetailed (bot, goal, timeoutMs = PATH_TIMEOUT_MS, { failFast = null, approach = null, signal = null } = {}) {
  signal?.throwIfAborted()
  const startedAt = Date.now()
  const targetCentre = approach?.target.offset(0.5, 0.5, 0.5)
  const distanceToTarget = () => bot.entity.position.distanceTo(targetCentre)
  const start = approach ? distanceToTarget() : null
  let closest = start

  let lastStatus = null
  let resets = 0
  let thinkMs = 0
  let runningSearchMs = 0 // latest time of the search still sending partial updates
  const onPathUpdate = (results) => {
    lastStatus = results.status
    if (results.status === 'partial') {
      runningSearchMs = results.time ?? 0
    } else {
      thinkMs += results.time ?? 0
      runningSearchMs = 0
    }
  }
  const onPathReset = () => { resets++ }
  bot.on?.('path_update', onPathUpdate)
  bot.on?.('path_reset', onPathReset)

  let timer = null
  let stallCheck = null
  let onAbort = null
  let ownEnd = null // why we ended the attempt ourselves
  const giveUp = new Promise((resolve, reject) => {
    const stopWith = (end, message) => {
      ownEnd ??= end
      reject(new Error(message))
    }
    if (signal) {
      onAbort = () => reject(signal.reason)
      signal.addEventListener('abort', onAbort, { once: true })
    }
    timer = setTimeout(() => stopWith('timeout', `path timeout after ${timeoutMs} ms`), timeoutMs)
    if (!failFast && !approach) return
    let stallMarkedAt = Date.now()
    let stallMarked = bot.entity.position.clone()
    let approachMarkedAt = Date.now()
    let approachMarked = start
    stallCheck = setInterval(() => {
      if (failFast) {
        if (bot.targetDigBlock || bot.entity.position.distanceTo(stallMarked) >= STALL_PROGRESS_BLOCKS) {
          stallMarkedAt = Date.now()
          stallMarked = bot.entity.position.clone()
        } else if (Date.now() - stallMarkedAt >= failFast.stallMs) {
          return stopWith('stalled', `no progress for ${failFast.stallMs} ms`)
        }
      }
      if (approach) {
        closest = Math.min(closest, distanceToTarget())
        if (approachMarked - closest >= STALL_PROGRESS_BLOCKS) {
          approachMarkedAt = Date.now()
          approachMarked = closest
        } else if (Date.now() - approachMarkedAt >= approach.windowMs) {
          stopWith('no_approach', `no approach for ${approach.windowMs} ms`)
        }
      }
    }, STALL_CHECK_MS)
  })
  try {
    await Promise.race([bot.pathfinder.goto(goal), giveUp])
  } catch (err) {
    stopMoving(bot)
    if (signal?.aborted) throw signal.reason
  } finally {
    clearTimeout(timer)
    clearInterval(stallCheck)
    signal?.removeEventListener('abort', onAbort)
    bot.removeListener?.('path_update', onPathUpdate)
    bot.removeListener?.('path_reset', onPathReset)
  }
  if (approach) closest = Math.min(closest, distanceToTarget())

  const feet = bot.entity.position.floored()
  const reached = goal.isEnd(feet) || goal.isEnd(feet.offset(0, 1, 0))
  let end = ownEnd ?? 'ended'
  if (reached) end = 'reached'
  else if (lastStatus === 'noPath') end = 'no_path'
  else if (lastStatus === 'timeout') end = 'path_timeout'
  return { reached, end, start, closest, resets, thinkMs: Math.round(thinkMs + runningSearchMs), ms: Date.now() - startedAt }
}

// gotoGoalDetailed, reduced to: true when the bot ends inside the goal.
async function gotoGoal (bot, goal, timeoutMs = PATH_TIMEOUT_MS, opts = {}) {
  return (await gotoGoalDetailed(bot, goal, timeoutMs, opts)).reached
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
// 1 s for it to be collected; two retries over whatever is left. An abort
// throws `signal.reason` before the next walk.
async function pickUpEntities (bot, entities, { signal = null } = {}) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const remaining = entities.filter((entity) => bot.entities[entity.id])
    if (remaining.length === 0) return
    for (const entity of remaining) {
      signal?.throwIfAborted()
      if (!bot.entities[entity.id]) continue
      await gotoGoal(bot, new GoalPickupRange(entity.position), 10000, { signal })
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
  freeSlots,
  gotoGoal,
  gotoGoalDetailed,
  hasAirNeighbour,
  heldOutsideInventory,
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
