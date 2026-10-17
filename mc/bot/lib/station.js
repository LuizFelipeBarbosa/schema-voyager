// Placing a crafting table or a furnace from the inventory next to the bot
// and digging it back up afterwards.

const { Vec3 } = require('vec3')
const {
  AIR_BLOCKS, findInventoryItem, inventoryCounts, pickUpEntities, settleInventory, sleepTicks, watchDrops
} = require('./world')

const SEARCH_RADIUS = 4
const DROP_WINDOW_TICKS = 20
const MAX_CELLS = 6 // distinct cells tried before giving up on placing
const RETRY_TICKS = 5

// A placed `blockName` within SEARCH_RADIUS of the bot, or null.
function findPlacedStation (bot, blockName) {
  return bot.findBlock({ matching: bot.registry.blocksByName[blockName].id, maxDistance: SEARCH_RADIUS })
}

// A crafting table to use: a placed one within reach, else one placed from the
// inventory. Returns {table, placedPos} (placedPos is set when the bot placed
// it and must dig it up again) or {error, detail}.
async function acquireTable (bot) {
  const nearby = findPlacedStation(bot, 'crafting_table')
  if (nearby) return { table: nearby, placedPos: null }
  if (!findInventoryItem(bot, 'crafting_table')) return { error: 'missing_crafting_table' }
  try {
    const table = await placeStation(bot, 'crafting_table')
    return { table, placedPos: table.position }
  } catch (err) {
    return { error: 'craft_failed', detail: `could not place a crafting table: ${err.message}` }
  }
}

// The server can refuse a cell that looks free here (say, an entity is in the
// way), and asking again for the same cell gets the same answer. So each
// refusal moves on to the next closest cell, a few ticks later.
async function placeStation (bot, itemName) {
  const spots = findSpots(bot).slice(0, MAX_CELLS)
  if (spots.length === 0) throw new Error('no free spot next to the bot')
  let lastProblem = null
  for (const spot of spots) {
    lastProblem = await tryPlace(bot, itemName, spot)
    if (!lastProblem) return bot.blockAt(spot)
    console.log(`warn: ${itemName} not placed at ${spot}: ${lastProblem}`)
    await sleepTicks(RETRY_TICKS)
  }
  throw new Error(`refused at ${spots.length} cells, last: ${lastProblem}`)
}

// Null when the station stands at `spot`, else what went wrong.
async function tryPlace (bot, itemName, spot) {
  try {
    await bot.equip(findInventoryItem(bot, itemName), 'hand')
    await bot.placeBlock(bot.blockAt(spot.offset(0, -1, 0)), new Vec3(0, 1, 0))
  } catch (err) {
    return err.message
  }
  const placed = bot.blockAt(spot)
  return placed?.name === itemName ? null : `found ${placed?.name} at ${spot} after placing`
}

// Air cells at the bot's feet level, on a full solid block, that do not
// overlap the bot's hitbox; closest first, at most 2 blocks out.
function findSpots (bot) {
  const feet = bot.entity.position.floored()
  const candidates = []
  for (let dx = -2; dx <= 2; dx++) {
    for (let dz = -2; dz <= 2; dz++) {
      const pos = feet.offset(dx, 0, dz)
      if (!AIR_BLOCKS.has(bot.blockAt(pos)?.name)) continue
      if (bot.blockAt(pos.offset(0, -1, 0))?.boundingBox !== 'block') continue
      if (overlapsBot(bot, pos)) continue
      candidates.push(pos)
    }
  }
  const botPos = bot.entity.position
  candidates.sort((a, b) => botPos.distanceTo(a.offset(0.5, 0, 0.5)) - botPos.distanceTo(b.offset(0.5, 0, 0.5)))
  return candidates
}

function overlapsBot (bot, cell) {
  const { x, z } = bot.entity.position
  const halfWidth = 0.3 + 0.05 // player hitbox plus a small margin
  return cell.x < x + halfWidth && cell.x + 1 > x - halfWidth &&
    cell.z < z + halfWidth && cell.z + 1 > z - halfWidth
}

// Digs the station the bot placed and picks it back up. True when the bot
// holds as many `itemName` as before the call. With `withBestTool`, the best
// tool in the inventory is held for the dig (a furnace only drops when
// broken with a pickaxe). `onDrop` sees each item entity the dig drops.
async function retrieveStation (bot, pos, itemName, countBefore, { withBestTool = false, onDrop = null } = {}) {
  const watcher = watchDrops(bot, pos, { onDrop })
  try {
    const block = bot.blockAt(pos)
    const tool = withBestTool ? bot.pathfinder.bestHarvestTool(block) : null
    if (tool) await bot.equip(tool, 'hand')
    await bot.dig(block, true)
    await sleepTicks(DROP_WINDOW_TICKS)
  } catch (err) {
    console.log(`warn: could not dig the placed ${itemName}: ${err.message}`)
  }
  const { entities } = watcher.stop()
  await pickUpEntities(bot, entities)
  await settleInventory(bot)
  return (inventoryCounts(bot)[itemName] || 0) >= countBefore
}

module.exports = { acquireTable, findPlacedStation, placeStation, retrieveStation }
