// POST /smelt: put the ingredient and fuel in a furnace (placing the bot's own
// if none is near, like the crafting table), wait for the output, take
// everything back, and dig the furnace back up. See contracts/bot_api.md.

const { forgetStanding, placeHeldOrUseHome, reachStation, rememberStanding, retrieveStation } = require('./station')
const { withTimeout } = require('./grid')
const {
  countDelta, inventoryCounts, resyncWindow, settleInventory, sleepTicks
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

// `session`: {tracker, episode, signal}. An abort throws `signal.reason`; a
// furnace the bot placed (or must still dig up) is then remembered in the
// episode instead of being dug up.
async function smelt (bot, { ingredient, item, times = 1, fuel, station = null, protect = [] }, { tracker, episode = null, signal = null }) {
  tracker.log.setPhase('step')
  // Find the furnace first and empty it, so that an earlier aborted smelt's
  // leftovers count as held (and the step is quarantined), not as this step's gain.
  const reached = await reachStation(bot, 'furnace', { episode, signal })
  if (reached) await drainAndRecord(bot, tracker, reached.station, signal)
  const furnaceWasRemembered = Boolean(reached?.remembered)

  let before = inventoryCounts(bot)
  const result = {
    ingredient,
    item,
    times,
    fuel,
    fuel_used: 0,
    smelted: 0,
    output_taken: 0,
    used_furnace: false,
    used_home_station: false,
    furnace_retrieved: null,
    tools_broken: [],
    travel: emptyTravel()
  }
  const finish = (error, extra = {}) => {
    const inventory = inventoryCounts(bot)
    const delta = countDelta(before, inventory)
    if (furnaceWasRemembered && delta.furnace === 1) delete delta.furnace // an earlier action's furnace, dug up now
    return {
      ok: !error,
      error: error || null,
      ...result,
      ...extra,
      inventory_delta: delta,
      inventory,
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

  let furnace = reached?.station
  let placedPos = furnaceWasRemembered ? furnace.position : null // a furnace to dig up afterwards
  if (!furnace) {
    const acquired = await placeHeldOrUseHome(bot, 'furnace', { station, protect, episode, signal })
    if (acquired.error) {
      const { error, ...extra } = acquired
      return finish(error, extra)
    }
    furnace = acquired.station
    placedPos = acquired.placedPos
    if (acquired.usedHome) {
      result.used_home_station = true
      result.travel = acquired.travel
      await drainAndRecord(bot, tracker, furnace, signal)
      before = inventoryCounts(bot) // what the home furnace held counts as held, as for a remembered one
    }
  }
  result.used_furnace = true

  // Leaves the way an abort requires: the furnace standing, and remembered.
  const abandon = () => {
    if (placedPos && episode) rememberStanding(episode, 'furnace', placedPos)
    throw signal.reason
  }

  let smeltError = null
  try {
    Object.assign(result, await runFurnace(bot, furnace, { ingredient, times, fuel, signal }))
  } catch (err) {
    if (signal?.aborted) abandon()
    smeltError = err.message
  }
  if (placedPos) {
    // a remembered furnace is not in the inventory yet, so retrieving it adds one
    const countBefore = (before.furnace || 0) + (furnaceWasRemembered ? 1 : 0)
    try {
      const { retrieved, toolsBroken } = await retrieveStation(bot, placedPos, 'furnace', countBefore, {
        withBestTool: true,
        onDrop: (entity) => tracker.ownDrops.add(entity.id),
        signal
      })
      result.furnace_retrieved = retrieved
      result.tools_broken = toolsBroken
    } catch (err) {
      if (signal?.aborted) abandon()
      throw err
    }
    if (result.furnace_retrieved && episode) forgetStanding(episode, placedPos)
  }
  await resyncWindow(bot, bot.inventory)
  await settleInventory(bot)
  result.fuel_used = Math.max(0, (before[fuel] || 0) - (inventoryCounts(bot)[fuel] || 0))
  if (smeltError) return finish('craft_failed', { detail: smeltError })
  const verdict = smeltVerdict({ smelted: result.smelted, outputTaken: result.output_taken, times })
  return verdict.ok ? finish(null) : finish('craft_failed', { detail: verdict.detail })
}

// Drains the furnace; what it held is interference, not this step's gain.
async function drainAndRecord (bot, tracker, furnaceBlock, signal) {
  if (!await drainFurnace(bot, furnaceBlock, signal)) return
  const { x, y, z } = furnaceBlock.position
  tracker.log.record('interference', { block: 'furnace', position: { x, y, z } })
}

// Opens the furnace and takes everything in it back. True if there was anything.
async function drainFurnace (bot, furnaceBlock, signal) {
  try {
    const window = await withTimeout(bot.openFurnace(furnaceBlock), OPEN_FURNACE_TIMEOUT_MS, 'the furnace did not open')
    try {
      const { outputTaken, fuelTaken, unsmelted } = await takeEverything(window)
      return outputTaken > 0 || fuelTaken || unsmelted > 0
    } finally {
      await bot.closeWindow(window)
    }
  } catch (err) {
    if (signal?.aborted) throw signal.reason
    console.log(`warn: could not drain the furnace: ${err.message}`)
    return false
  }
}

// Takes the output, the fuel and the unsmelted input out of an open furnace.
async function takeEverything (window) {
  const outputTaken = window.outputItem()?.count || 0
  if (outputTaken > 0) await window.takeOutput()
  const fuelTaken = Boolean(window.fuelItem())
  if (fuelTaken) await window.takeFuel()
  const unsmelted = window.inputItem()?.count || 0
  if (unsmelted > 0) await window.takeInput()
  return { outputTaken, fuelTaken, unsmelted }
}

// Loads the furnace, waits until the input is all smelted and an output is
// there (or the furnace goes cold), takes output, leftover fuel and leftover
// input back, and closes the window. Returns {smelted: inputs consumed,
// output_taken}. An abort takes the same things back and then throws
// `signal.reason`; if the window is already closed (a timeout or a death
// closed it), it throws at once without clicking it.
async function runFurnace (bot, furnaceBlock, { ingredient, times, fuel, signal }) {
  const items = bot.registry.itemsByName
  const window = await withTimeout(bot.openFurnace(furnaceBlock), OPEN_FURNACE_TIMEOUT_MS, 'the furnace did not open')
  let windowClosed = false
  try {
    await window.putInput(items[ingredient].id, null, times)
    await window.putFuel(items[fuel].id, null, fuelNeeded(fuel, times))
    const deadline = Date.now() + SMELT_MS_PER_ITEM * times
    let coldPolls = 0
    while (!signal?.aborted && !(window.outputItem() && !window.inputItem()) && Date.now() < deadline && coldPolls < COLD_POLLS_TO_GIVE_UP) {
      await sleepTicks(POLL_TICKS)
      coldPolls = furnaceIsCold(window) ? coldPolls + 1 : 0
    }
    if (signal?.aborted && bot.currentWindow !== window) {
      windowClosed = true
      throw signal.reason
    }
    const { outputTaken, unsmelted } = await takeEverything(window)
    if (signal?.aborted) throw signal.reason
    return { smelted: times - unsmelted, output_taken: outputTaken }
  } finally {
    if (!windowClosed) await bot.closeWindow(window)
  }
}

// Out of fuel and not burning, with input left: nothing more will come out.
function furnaceIsCold (window) {
  return !window.fuelItem() && window.fuel === 0 && Boolean(window.inputItem())
}

module.exports = { fuelNeeded, itemsPerFuel, smelt, smeltShortfall, smeltVerdict }
