// POST /smelt: put the ingredient and fuel in a furnace (placing the bot's own
// if none is near, like the crafting table), wait for the output, take
// everything back, and dig the furnace back up. See contracts/bot_api.md.

const { findPlacedStation, placeStation, retrieveStation } = require('./station')
const { withTimeout } = require('./grid')
const {
  countDelta, findInventoryItem, inventoryCounts, resyncWindow, settleInventory, sleepTicks
} = require('./world')
const { emptyTravel } = require('./travel')

const ITEMS_PER_FUEL = { coal: 8, charcoal: 8 } // any other fuel counts as 1 item per fuel item
const SMELT_MS_PER_ITEM = 12000 // vanilla needs 10 s; the rest is slack
const POLL_TICKS = 5
const OPEN_FURNACE_TIMEOUT_MS = 5000

function itemsPerFuel (fuelName) {
  return ITEMS_PER_FUEL[fuelName] ?? 1
}

// How many fuel items it takes to smelt `times` items.
function fuelNeeded (fuelName, times) {
  return Math.ceil(times / itemsPerFuel(fuelName))
}

// What the inventory lacks for the smelt, as {name: shortfall}.
function smeltShortfall (counts, { ingredient, fuel, times }) {
  const needs = {}
  needs[ingredient] = times
  needs[fuel] = (needs[fuel] || 0) + fuelNeeded(fuel, times)
  const missing = {}
  for (const [name, needed] of Object.entries(needs)) {
    const shortfall = needed - (counts[name] || 0)
    if (shortfall > 0) missing[name] = shortfall
  }
  return missing
}

async function smelt (bot, { ingredient, item, times = 1, fuel }, { tracker }) {
  const before = inventoryCounts(bot)
  tracker.log.setPhase('step')
  const result = { ingredient, item, times, fuel, fuel_used: 0, used_furnace: false, furnace_retrieved: null }
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

  const items = bot.registry.itemsByName
  for (const name of [ingredient, fuel]) {
    if (!items[name]) return finish('missing_ingredients', { missing: {}, detail: `unknown item ${name}` })
  }
  const missing = smeltShortfall(before, { ingredient, fuel, times })
  if (Object.keys(missing).length > 0) return finish('missing_ingredients', { missing })

  let furnace = findPlacedStation(bot, 'furnace')
  let placedPos = null
  if (!furnace) {
    if (!findInventoryItem(bot, 'furnace')) return finish('missing_furnace')
    try {
      furnace = await placeStation(bot, 'furnace')
      placedPos = furnace.position
    } catch (err) {
      return finish('craft_failed', { detail: `could not place a furnace: ${err.message}` })
    }
  }
  result.used_furnace = true

  let smeltError = null
  try {
    await runFurnace(bot, furnace, { ingredient, item, times, fuel })
  } catch (err) {
    smeltError = err.message
  }
  if (placedPos) {
    result.furnace_retrieved = await retrieveStation(bot, placedPos, 'furnace', before.furnace || 0, {
      withBestTool: true,
      onDrop: (entity) => tracker.ownDrops.add(entity.id)
    })
  }
  await resyncWindow(bot, bot.inventory)
  await settleInventory(bot)
  result.fuel_used = Math.max(0, (before[fuel] || 0) - (inventoryCounts(bot)[fuel] || 0))
  return smeltError ? finish('craft_failed', { detail: smeltError }) : finish(null)
}

// Loads the furnace, waits for `times` items, takes output, leftover fuel and
// leftover input back, and closes the window.
async function runFurnace (bot, furnaceBlock, { ingredient, times, fuel }) {
  const items = bot.registry.itemsByName
  const window = await withTimeout(bot.openFurnace(furnaceBlock), OPEN_FURNACE_TIMEOUT_MS, 'the furnace did not open')
  try {
    await window.putInput(items[ingredient].id, null, times)
    await window.putFuel(items[fuel].id, null, fuelNeeded(fuel, times))
    const deadline = Date.now() + SMELT_MS_PER_ITEM * times
    while ((window.outputItem()?.count || 0) < times && Date.now() < deadline) {
      await sleepTicks(POLL_TICKS)
    }
    if (window.outputItem()) await window.takeOutput()
    if (window.fuelItem()) await window.takeFuel()
    if (window.inputItem()) await window.takeInput()
  } finally {
    await bot.closeWindow(window)
  }
}

module.exports = { fuelNeeded, itemsPerFuel, smelt, smeltShortfall }
