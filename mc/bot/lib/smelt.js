// POST /smelt: put the ingredient and fuel in a furnace (placing the bot's own
// if none is near, like the crafting table), wait for the output, take
// everything back, and dig the furnace back up. See contracts/bot_api.md.

const { findPlacedStation, placeStation, retrieveStation } = require('./station')
const { withTimeout } = require('./grid')
const {
  countDelta, findInventoryItem, inventoryCounts, resyncWindow, settleInventory, sleepTicks
} = require('./world')
const { emptyTravel } = require('./travel')

// Items smelted per fuel item (vanilla burn time / 200 ticks). Anything else is not a fuel.
const FUEL_SUFFIXES = ['_log', '_wood', '_stem', '_planks']
const ITEMS_PER_FUEL = { coal: 8, charcoal: 8, stick: 0.5 }
const ITEMS_PER_WOODEN_FUEL = 1.5
const SMELT_MS_PER_ITEM = 12000 // vanilla needs 10 s; the rest is slack
const POLL_TICKS = 5
const COLD_POLLS_TO_GIVE_UP = 10 // 2.5 s, so a stale window update is not mistaken for a cold furnace
const OPEN_FURNACE_TIMEOUT_MS = 5000

// Items one fuel item smelts, or null if it is not a fuel.
function itemsPerFuel (fuelName) {
  if (ITEMS_PER_FUEL[fuelName]) return ITEMS_PER_FUEL[fuelName]
  return FUEL_SUFFIXES.some((suffix) => fuelName.endsWith(suffix)) ? ITEMS_PER_WOODEN_FUEL : null
}

// How many fuel items it takes to smelt `times` items.
function fuelNeeded (fuelName, times) {
  return Math.ceil(times / itemsPerFuel(fuelName))
}

// Whether a run succeeded, given the inputs the furnace consumed and the
// outputs taken out; if not, why. The output count is not judged: a datapack
// may change a recipe's yield.
function smeltVerdict ({ smelted, outputTaken, times }) {
  if (smelted === times && outputTaken > 0) return { ok: true, detail: null }
  return { ok: false, detail: `smelted ${smelted} of ${times}` }
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
  const result = { ingredient, item, times, fuel, fuel_used: 0, smelted: 0, output_taken: 0, used_furnace: false, furnace_retrieved: null }
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
  if (itemsPerFuel(fuel) === null) return finish('missing_ingredients', { missing: {}, detail: 'not_fuel' })
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
    Object.assign(result, await runFurnace(bot, furnace, { ingredient, times, fuel }))
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
  if (smeltError) return finish('craft_failed', { detail: smeltError })
  const verdict = smeltVerdict({ smelted: result.smelted, outputTaken: result.output_taken, times })
  return verdict.ok ? finish(null) : finish('craft_failed', { detail: verdict.detail })
}

// Loads the furnace, waits until the input is all smelted and an output is
// there (or the furnace goes cold), takes output, leftover fuel and leftover
// input back, and closes the window. Returns {smelted: inputs consumed,
// output_taken}.
async function runFurnace (bot, furnaceBlock, { ingredient, times, fuel }) {
  const items = bot.registry.itemsByName
  const window = await withTimeout(bot.openFurnace(furnaceBlock), OPEN_FURNACE_TIMEOUT_MS, 'the furnace did not open')
  try {
    await window.putInput(items[ingredient].id, null, times)
    await window.putFuel(items[fuel].id, null, fuelNeeded(fuel, times))
    const deadline = Date.now() + SMELT_MS_PER_ITEM * times
    let coldPolls = 0
    while (!(window.outputItem() && !window.inputItem()) && Date.now() < deadline && coldPolls < COLD_POLLS_TO_GIVE_UP) {
      await sleepTicks(POLL_TICKS)
      coldPolls = furnaceIsCold(window) ? coldPolls + 1 : 0
    }
    const outputTaken = window.outputItem()?.count || 0
    if (outputTaken > 0) await window.takeOutput()
    if (window.fuelItem()) await window.takeFuel()
    const unsmelted = window.inputItem()?.count || 0
    if (unsmelted > 0) await window.takeInput()
    return { smelted: times - unsmelted, output_taken: outputTaken }
  } finally {
    await bot.closeWindow(window)
  }
}

// Out of fuel and not burning, with input left: nothing more will come out.
function furnaceIsCold (window) {
  return !window.fuelItem() && window.fuel === 0 && Boolean(window.inputItem())
}

module.exports = { fuelNeeded, itemsPerFuel, smelt, smeltShortfall, smeltVerdict }
