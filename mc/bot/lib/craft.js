// POST /craft: pick an affordable recipe, get a crafting table if the recipe
// needs one (placing and retrieving the bot's own if none is near), craft,
// and report the inventory change once the server has confirmed it. When only
// a mix of tag variants can pay (planks of two kinds in one pickaxe), the bot
// fills the grid itself. See contracts/bot_api.md.

const { craftMixedOnce, planMixedGrid } = require('./grid')
const { acquireTable, retrieveStation } = require('./station')
const { emptyTravel } = require('./travel')
const { countDelta, inventoryCounts, resyncWindow, settleInventory } = require('./world')

async function craft (bot, { item: itemName, times = 1 }, { tracker }) {
  const before = inventoryCounts(bot)
  tracker.log.setPhase('step') // a craft has no travel: its step starts now
  const result = { item: itemName, times, used_table: false, table_retrieved: null }
  const finish = (error, extra = {}) => {
    const inventory = inventoryCounts(bot)
    return {
      ok: !error,
      error: error || null,
      ...result,
      ...extra,
      inventory_delta: countDelta(before, inventory),
      inventory,
      travel: emptyTravel(),
      inventory_before_step: before
    }
  }

  const itemInfo = bot.registry.itemsByName[itemName]
  if (!itemInfo) return finish('missing_ingredients', { missing: {}, detail: `unknown item ${itemName}` })
  const recipe = pickRecipe(bot, itemInfo.id, times)
  const mixedPlan = recipe ? null : planMixedGrid(bot, itemInfo.id, times)
  if (!recipe && !mixedPlan) return finish('missing_ingredients', { missing: cheapestMissing(bot, itemInfo.id, times) })

  let table = null
  let placedTablePos = null
  if ((recipe || mixedPlan.recipe).requiresTable) {
    result.used_table = true
    const acquired = await acquireTable(bot)
    if (acquired.error) return finish(acquired.error, acquired.detail ? { detail: acquired.detail } : {})
    table = acquired.table
    placedTablePos = acquired.placedPos
  }

  let craftError = null
  try {
    if (recipe) {
      await bot.craft(recipe, times, table)
    } else {
      for (let run = 0; run < times; run++) await craftMixedOnce(bot, table, itemName)
    }
  } catch (err) {
    craftError = err.message
  }
  await resyncWindow(bot, bot.inventory)
  if (placedTablePos) {
    result.table_retrieved = await retrieveStation(bot, placedTablePos, 'crafting_table', before.crafting_table || 0, {
      onDrop: (entity) => tracker.ownDrops.add(entity.id)
    })
  }
  await settleInventory(bot)
  if (!craftError) return finish(null)
  if (!recipe) {
    console.log(`warn: mixed-variant craft of ${itemName} failed: ${craftError}`)
    return finish('missing_ingredients', { missing: {}, detail: 'mixed_species' })
  }
  return finish('craft_failed', { detail: craftError })
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

module.exports = { craft }
