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
    if (!lastProblem) return bot.blockAt(spot.pos)
    console.log(`warn: ${itemName} not placed at ${spot.pos}: ${lastProblem}`)
    await sleepTicks(RETRY_TICKS)
  }
  throw new Error(`refused at ${spots.length} cells, last: ${lastProblem}`)
}

// Null when the station stands at `spot.pos`, else what went wrong.
async function tryPlace (bot, itemName, spot) {
  try {
    await bot.equip(findInventoryItem(bot, itemName), 'hand')
    await bot.placeBlock(bot.blockAt(spot.pos.minus(spot.faceToCell)), spot.faceToCell)
  } catch (err) {
    return err.message
  }
  const placed = bot.blockAt(spot.pos)
  return placed?.name === itemName ? null : `found ${placed?.name} at ${spot.pos} after placing`
}

// Cells where a station can go: air within 2 blocks of the bot's feet (one
// below to one above), next to a full solid block to place against, not
// overlapping the bot's hitbox. Returns [{pos, faceToCell}], where faceToCell
// is the face of that block the station goes on. Cells on the ground come
// first, then the closest; the others are for a bot standing on a pillar or a
// ledge, with nothing solid at its own level.
function findSpots (bot) {
  const feet = bot.entity.position.floored()
  const botPos = bot.entity.position
  // the face to place on, preferring the top of the block below the cell
  const faces = [new Vec3(0, 1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(0, -1, 0)]
  const candidates = []
  for (let dy = -1; dy <= 1; dy++) {
    for (let dx = -2; dx <= 2; dx++) {
      for (let dz = -2; dz <= 2; dz++) {
        const pos = feet.offset(dx, dy, dz)
        if (!AIR_BLOCKS.has(bot.blockAt(pos)?.name)) continue
        if (dy >= 0 && overlapsBot(bot, pos)) continue
        const faceToCell = faces.find((face) => bot.blockAt(pos.minus(face))?.boundingBox === 'block')
        if (faceToCell) candidates.push({ pos, faceToCell, onGround: faceToCell.y === 1 && dy === 0 })
      }
    }
  }
  const distance = (spot) => botPos.distanceTo(spot.pos.offset(0.5, 0.5, 0.5))
  return candidates.sort((a, b) => (b.onGround - a.onGround) || (distance(a) - distance(b)))
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

module.exports = { acquireTable, findPlacedStation, findSpots, placeStation, retrieveStation }
