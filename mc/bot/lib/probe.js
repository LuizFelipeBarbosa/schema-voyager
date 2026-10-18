// POST /probe: ask the server what a crafting grid makes, without keeping
// anything. Fills the grid (3x3 grids use a crafting table, placed and
// retrieved as for /craft), reads the output slot after a resync, takes
// everything back, and checks the inventory is unchanged.
// See contracts/bot_api.md.

const { clearCraftingGrids, probeGrid } = require('./grid')
const { acquireTable, retrieveStation } = require('./station')
const { addCount, countDelta, heldOutsideInventory, inventoryCounts, settleInventory } = require('./world')

async function probe (bot, { grid }, { tracker }) {
  await clearCraftingGrids(bot)
  await settleInventory(bot)
  const before = inventoryCounts(bot)
  tracker.log.setPhase('step')
  let toolsBroken = []
  const finish = (error, extra = {}) => {
    const inventory = inventoryCounts(bot)
    return {
      ok: !error,
      error: error || null,
      output: null,
      tools_broken: toolsBroken,
      ...extra,
      inventory_delta: countDelta(before, inventory),
      held_outside_inventory: heldOutsideInventory(bot)
    }
  }

  const needed = {}
  for (const name of grid.flat().filter(Boolean)) {
    if (!bot.registry.itemsByName[name]) return finish('missing_ingredients', { missing: {}, detail: `unknown item ${name}` })
    addCount(needed, name)
  }
  const missing = {}
  for (const [name, count] of Object.entries(needed)) {
    if ((before[name] || 0) < count) missing[name] = count - (before[name] || 0)
  }
  if (Object.keys(missing).length > 0) return finish('missing_ingredients', { missing })

  let table = null
  let placedTablePos = null
  if (grid.length === 3) {
    const acquired = await acquireTable(bot)
    if (acquired.error) return finish(acquired.error, acquired.detail ? { detail: acquired.detail } : {})
    table = acquired.table
    placedTablePos = acquired.placedPos
  }

  let output = null
  let probeError = null
  try {
    ({ output } = await probeGrid(bot, table, grid))
  } catch (err) {
    probeError = err.message
  }
  await clearCraftingGrids(bot)
  if (placedTablePos) {
    ({ toolsBroken } = await retrieveStation(bot, placedTablePos, 'crafting_table', before.crafting_table || 0, {
      onDrop: (entity) => tracker.ownDrops.add(entity.id)
    }))
  }
  await settleInventory(bot)

  if (probeError) return finish('craft_failed', { detail: probeError })
  const result = finish(null, { output })
  if (Object.keys(result.inventory_delta).length > 0) {
    return { ...result, ok: false, error: 'craft_failed', detail: 'the inventory changed during the probe' }
  }
  return result
}

module.exports = { probe }
