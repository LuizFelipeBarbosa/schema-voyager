// POST /craft: pick an affordable recipe, get a crafting table if the recipe
// needs one (placing and retrieving the bot's own if none is near), craft,
// and report the inventory change once the server has confirmed it.
// See contracts/bot_api.md.

const { Vec3 } = require('vec3')
const {
  AIR_BLOCKS, countDelta, findInventoryItem, inventoryCounts, pickUpEntities,
  settleInventory, sleepTicks, watchDrops
} = require('./world')

const TABLE_SEARCH_RADIUS = 4
const DROP_WINDOW_TICKS = 20
const MAX_TABLE_CELLS = 6 // distinct cells tried before giving up on placing a table
const TABLE_RETRY_TICKS = 5

async function craft (bot, { item: itemName, times = 1 }) {
  const before = inventoryCounts(bot)
  const result = { item: itemName, times, used_table: false, table_retrieved: null }
  const finish = (error, extra = {}) => {
    const inventory = inventoryCounts(bot)
    return {
      ok: !error,
      error: error || null,
      ...result,
      ...extra,
      inventory_delta: countDelta(before, inventory),
      inventory
    }
  }

  const itemInfo = bot.registry.itemsByName[itemName]
  if (!itemInfo) return finish('missing_ingredients', { missing: {}, detail: `unknown item ${itemName}` })
  const recipe = pickRecipe(bot, itemInfo.id, times)
  if (!recipe) return finish('missing_ingredients', { missing: cheapestMissing(bot, itemInfo.id, times) })

  let table = null
  let placedTablePos = null
  if (recipe.requiresTable) {
    result.used_table = true
    table = bot.findBlock({ matching: bot.registry.blocksByName.crafting_table.id, maxDistance: TABLE_SEARCH_RADIUS })
    if (!table) {
      if (!findInventoryItem(bot, 'crafting_table')) return finish('missing_crafting_table')
      try {
        table = await placeTable(bot)
        placedTablePos = table.position
      } catch (err) {
        return finish('craft_failed', { detail: `could not place a crafting table: ${err.message}` })
      }
    }
  }

  let craftError = null
  try {
    await bot.craft(recipe, times, table)
  } catch (err) {
    craftError = err.message
  }
  await resyncInventory(bot)
  if (placedTablePos) {
    result.table_retrieved = await retrieveTable(bot, placedTablePos, before.crafting_table || 0)
  }
  await settleInventory(bot)
  return craftError ? finish('craft_failed', { detail: craftError }) : finish(null)
}

// --- Recipes ---------------------------------------------------------------

// Recipes needing a table are considered too (so a missing table is reported
// as such); a recipe that works in the 2x2 grid is preferred.
function pickRecipe (bot, itemId, times) {
  const affordable = bot.recipesFor(itemId, null, 1, true)
    .filter((recipe) => canAfford(bot, recipe, times))
  return affordable.find((recipe) => !recipe.requiresTable) || affordable[0] || null
}

function canAfford (bot, recipe, times) {
  return recipe.delta.every((entry) =>
    entry.count >= 0 || bot.inventory.count(entry.id, entry.metadata) + entry.count * times >= 0)
}

// What the recipe with the smallest total shortfall still lacks. Ties between
// tag variants (one recipe per plank type, say) go to the earliest registry
// items, which makes oak the default.
function cheapestMissing (bot, itemId, times) {
  let best = null
  for (const recipe of bot.recipesAll(itemId, null, true)) {
    const missing = {}
    let total = 0
    let idSum = 0
    for (const entry of recipe.delta) {
      if (entry.count >= 0) continue
      const shortfall = -entry.count * times - bot.inventory.count(entry.id, entry.metadata)
      if (shortfall <= 0) continue
      const name = bot.registry.items[entry.id].name
      missing[name] = (missing[name] || 0) + shortfall
      total += shortfall
      idSum += entry.id
    }
    if (!best || total < best.total || (total === best.total && idSum < best.idSum)) {
      best = { missing, total, idSum }
    }
  }
  return best ? best.missing : {}
}

// Mineflayer predicts craft results client-side. A no-op click with a stale
// state id makes the server send the whole player inventory back.
async function resyncInventory (bot) {
  if (!bot._syncWindow) return
  let timer = null
  const timeout = new Promise((resolve) => { timer = setTimeout(resolve, 2000) })
  try {
    await Promise.race([bot._syncWindow(bot.inventory), timeout])
  } catch (err) {
    console.log(`warn: inventory resync failed: ${err.message}`)
  } finally {
    clearTimeout(timer)
  }
}

// --- Crafting table --------------------------------------------------------

// The server can refuse a cell that looks free here (say, an entity is in the
// way), and asking again for the same cell gets the same answer. So each
// refusal moves on to the next closest cell, a few ticks later.
async function placeTable (bot) {
  const spots = findTableSpots(bot).slice(0, MAX_TABLE_CELLS)
  if (spots.length === 0) throw new Error('no free spot next to the bot')
  let lastProblem = null
  for (const spot of spots) {
    lastProblem = await tryPlaceTable(bot, spot)
    if (!lastProblem) return bot.blockAt(spot)
    console.log(`warn: crafting table not placed at ${spot}: ${lastProblem}`)
    await sleepTicks(TABLE_RETRY_TICKS)
  }
  throw new Error(`refused at ${spots.length} cells, last: ${lastProblem}`)
}

// Null when the table stands at `spot`, else what went wrong.
async function tryPlaceTable (bot, spot) {
  try {
    await bot.equip(findInventoryItem(bot, 'crafting_table'), 'hand')
    await bot.placeBlock(bot.blockAt(spot.offset(0, -1, 0)), new Vec3(0, 1, 0))
  } catch (err) {
    return err.message
  }
  const placed = bot.blockAt(spot)
  return placed?.name === 'crafting_table' ? null : `found ${placed?.name} at ${spot} after placing`
}

// Air cells at the bot's feet level, on a full solid block, that do not
// overlap the bot's hitbox; closest first, at most 2 blocks out.
function findTableSpots (bot) {
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

// Digs the table the bot placed and picks it back up. True when the bot holds
// as many crafting tables as before the call.
async function retrieveTable (bot, pos, tablesBefore) {
  const watcher = watchDrops(bot, pos)
  try {
    await bot.dig(bot.blockAt(pos), true)
    await sleepTicks(DROP_WINDOW_TICKS)
  } catch (err) {
    console.log(`warn: could not dig the placed crafting table: ${err.message}`)
  }
  const { entities } = watcher.stop()
  await pickUpEntities(bot, entities)
  await settleInventory(bot)
  return (inventoryCounts(bot).crafting_table || 0) >= tablesBefore
}

module.exports = { craft }
