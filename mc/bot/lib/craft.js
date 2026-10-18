// POST /craft: pick an affordable recipe, get a crafting table if the recipe
// needs one (placing and retrieving the bot's own if none is near), craft,
// and report the inventory change once the server has confirmed it. When only
// a mix of tag variants can pay (planks of two kinds in one pickaxe), the bot
// fills the grid itself. See contracts/bot_api.md.

const { clearCraftingGrids, craftMixedOnce, planMixedGrid, slotAlternatives } = require('./grid')
const { acquireTable, forgetStanding, rememberStanding, retrieveStation } = require('./station')
const { emptyTravel } = require('./travel')
const { countDelta, heldOutsideInventory, inventoryCounts, settleInventory } = require('./world')

// `session`: {tracker, episode, signal}. An abort throws `signal.reason`; a
// table the bot placed (or must still dig up) is then remembered in the
// episode instead of being dug up.
async function craft (bot, { item: itemName, times = 1 }, { tracker, episode = null, signal = null }) {
  signal?.throwIfAborted()
  // Items stranded in a grid by an earlier action would show up as a gain
  await clearCraftingGrids(bot)
  await settleInventory(bot)
  const before = inventoryCounts(bot)
  tracker.log.setPhase('step') // a craft has no travel: its step starts now
  const result = { item: itemName, times, used_table: false, table_retrieved: null, tools_broken: [] }
  const finish = (error, extra = {}) => {
    const inventory = inventoryCounts(bot)
    return {
      ok: !error,
      error: error || null,
      ...result,
      ...extra,
      inventory_delta: countDelta(before, inventory),
      inventory,
      held_outside_inventory: heldOutsideInventory(bot),
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
  let tableWasRemembered = false
  if ((recipe || mixedPlan.recipe).requiresTable) {
    result.used_table = true
    const acquired = await acquireTable(bot, { episode, signal })
    if (acquired.error) return finish(acquired.error, acquired.detail ? { detail: acquired.detail } : {})
    table = acquired.table
    placedTablePos = acquired.placedPos
    tableWasRemembered = acquired.remembered
  }

  // Leaves the way an abort requires: no window left open, the table standing.
  const abandon = () => {
    if (bot.currentWindow) bot.closeWindow(bot.currentWindow)
    if (placedTablePos && episode) rememberStanding(episode, 'crafting_table', placedTablePos)
    throw signal.reason
  }

  // One run at a time: Mineflayer's own multi-run craft waits on the output
  // slot after every grid click and gives up after 20 s when the window it
  // models no longer matches the server's, which a stranded stack causes.
  let craftError = null
  try {
    for (let run = 0; run < times; run++) {
      signal?.throwIfAborted()
      if (recipe) await bot.craft(recipe, 1, table)
      else await craftMixedOnce(bot, table, itemName)
      await clearCraftingGrids(bot)
    }
  } catch (err) {
    if (signal?.aborted) abandon()
    craftError = err.message
  }
  await clearCraftingGrids(bot)
  if (placedTablePos) {
    // a remembered table is not in the inventory yet, so retrieving it adds one
    const countBefore = (before.crafting_table || 0) + (tableWasRemembered ? 1 : 0)
    try {
      const { retrieved, toolsBroken } = await retrieveStation(bot, placedTablePos, 'crafting_table', countBefore, {
        onDrop: (entity) => tracker.ownDrops.add(entity.id),
        signal
      })
      result.table_retrieved = retrieved
      result.tools_broken = toolsBroken
    } catch (err) {
      if (signal?.aborted) abandon()
      throw err
    }
    if (result.table_retrieved && episode) forgetStanding(episode, placedTablePos)
  }
  await settleInventory(bot)
  if (craftError && !recipe) {
    console.log(`warn: mixed-variant craft of ${itemName} failed: ${craftError}`)
    return finish('missing_ingredients', { missing: {}, detail: 'mixed_species' })
  }
  if (craftError) return finish('craft_failed', { detail: craftError })

  const delta = countDelta(before, inventoryCounts(bot))
  if (result.table_retrieved === false && delta.crafting_table === -1) delete delta.crafting_table // the placed table is left standing
  if (tableWasRemembered && delta.crafting_table === 1) delete delta.crafting_table // an earlier action's table, dug up now
  if (!isWholeRuns(delta, itemName, times, bot.recipesAll(itemInfo.id, null, true), bot.registry)) {
    return finish('craft_failed', { detail: 'partial_delta' })
  }
  return finish(null)
}

// Whether `delta` (item name -> change) is exactly `runs` runs of one recipe
// for `itemName`: the ingredients lost are runs x the recipe's, with tag
// variants allowed (1 birch + 1 spruce planks for sticks), the item gained is
// a positive multiple of `runs`, and nothing else changed. The gain is not
// compared with the recipe's result count, so a datapack that changes it is
// still seen as the craft that worked. `recipes` are all recipes for the item.
function isWholeRuns (delta, itemName, runs, recipes, registry) {
  const gained = delta[itemName] || 0
  if (gained <= 0 || gained % runs !== 0) return false
  const lost = {}
  for (const [name, change] of Object.entries(delta)) {
    if (name === itemName) continue
    if (change > 0) return false
    lost[name] = -change
  }
  const lostTotal = Object.values(lost).reduce((sum, count) => sum + count, 0)
  return recipes.some((recipe) => {
    // cells that accept the same variants are one group wanting runs x as many
    const groups = new Map()
    for (const slot of slotAlternatives(recipe, recipes)) {
      const names = slot.ids.map((id) => registry.items[id].name).sort()
      const key = names.join(',')
      groups.set(key, { names, demand: (groups.get(key)?.demand || 0) + runs })
    }
    const wanted = [...groups.values()]
    if (wanted.reduce((sum, group) => sum + group.demand, 0) !== lostTotal) return false
    // every set of groups must find enough of what it accepts among the losses
    for (let subset = 1; subset < 1 << wanted.length; subset++) {
      const chosen = wanted.filter((group, i) => subset & (1 << i))
      const accepted = new Set(chosen.flatMap((group) => group.names))
      const supply = [...accepted].reduce((sum, name) => sum + (lost[name] || 0), 0)
      if (chosen.reduce((sum, group) => sum + group.demand, 0) > supply) return false
    }
    return true
  })
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

module.exports = { craft, isWholeRuns }
