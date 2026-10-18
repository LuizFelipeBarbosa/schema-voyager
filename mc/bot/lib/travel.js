// Travel accounting and pathing for v2: what the bot dug, placed, picked up
// and walked on the way into position, before the step's own dig / craft /
// smelt. Everything here is tolerant; it never counts as evidence for a rule.

const { DiggingMovements, cheapestHarvestTool } = require('./movements')
const {
  TICK_MS, addCount, countGains, emptyHand, findInventoryItem, inventoryCounts, round, sleepTicks
} = require('./world')

const SETTLE_TICKS = 5 // quiet time after the last travel dig, before the step
const PLACEMENT_WAIT_MS = 6000 // the mineflayer patch times a placement out at 5 s
const GROUND_WAIT_TICKS = 20
const REPLACEABLE_NAMES = ['air', 'cave_air', 'void_air', 'water']

function emptyTravel () {
  return { distance: 0, dug: {}, placed: {}, picked_up: {}, placed_cells: [] }
}

// Pure: the `travel` response object.
function travelSummary ({ walkedBefore, walkedAfter, dug, placed, placedCells = [], inventoryBefore, inventoryAfter }) {
  return {
    distance: round(walkedAfter - walkedBefore, 1),
    dug,
    placed,
    picked_up: countGains(inventoryBefore, inventoryAfter),
    placed_cells: placedCells
  }
}

// Starts counting what the pathfinder does. Digs and placements are counted by
// wrapping bot.dig and bot.placeBlock (Mineflayer removes plain
// 'diggingCompleted' listeners after each dig; 'blockPlaced' only fires once the
// server's update arrives, which can be after the walk has resolved). finish()
// unwraps and returns the counts, reading each attempted cell back from the world.
function recordTravel (bot, episode) {
  const walkedBefore = episode.walked
  const inventoryBefore = inventoryCounts(bot)
  const dug = {}
  let lastDigAt = null
  const placementAttempts = [] // {dest, oldName, itemName}, in order
  const placementsInFlight = new Set()

  const originalDig = bot.dig
  bot.dig = async function (block, ...rest) {
    const name = block.name
    const result = await originalDig.call(this, block, ...rest)
    addCount(dug, name)
    lastDigAt = Date.now()
    return result
  }
  const originalPlaceBlock = bot.placeBlock
  bot.placeBlock = function (referenceBlock, faceVector, ...rest) {
    const dest = referenceBlock.position.plus(faceVector).floored()
    placementAttempts.push({ dest, oldName: bot.blockAt(dest)?.name, itemName: bot.heldItem?.name })
    const placement = originalPlaceBlock.call(this, referenceBlock, faceVector, ...rest)
    const done = () => placementsInFlight.delete(placement)
    placementsInFlight.add(placement)
    placement.then(done, done)
    return placement
  }

  return {
    // Waits for placements in flight and the bot to land (none if nothing was
    // placed), then the 5-tick wait after the last travel dig (none if nothing was dug).
    async settle () {
      if (placementAttempts.length > 0) await settlePlacements(bot, placementsInFlight)
      if (lastDigAt === null) return
      const remaining = SETTLE_TICKS * TICK_MS - (Date.now() - lastDigAt)
      if (remaining > 0) await sleepTicks(Math.ceil(remaining / TICK_MS))
    },
    finish () {
      bot.dig = originalDig
      bot.placeBlock = originalPlaceBlock
      const placedCells = readBackPlacedCells(bot, placementAttempts)
      const placed = {}
      for (const [, , , name] of placedCells) addCount(placed, name)
      return travelSummary({
        walkedBefore,
        walkedAfter: episode.walked,
        dug,
        placed,
        placedCells,
        inventoryBefore,
        inventoryAfter: inventoryCounts(bot)
      })
    }
  }
}

// Waits for every placement in flight to settle (resolved or rejected), at most
// PLACEMENT_WAIT_MS in total, then for the bot to be on the ground, then 2 ticks.
async function settlePlacements (bot, placementsInFlight) {
  let timer
  const capped = new Promise((resolve) => { timer = setTimeout(resolve, PLACEMENT_WAIT_MS) })
  const allSettled = Promise.allSettled([...placementsInFlight])
  await Promise.race([allSettled, capped])
  clearTimeout(timer)
  for (let tick = 0; tick < GROUND_WAIT_TICKS && bot.entity?.onGround === false; tick++) await sleepTicks(1)
  await sleepTicks(2)
}

// The [x, y, z, name] of each attempted cell that was empty (or water) before and
// now holds the block the bot held, in attempt order, one entry per cell.
function readBackPlacedCells (bot, placementAttempts) {
  const cells = []
  const seen = new Set()
  for (const { dest, oldName, itemName } of placementAttempts) {
    const key = `${dest.x},${dest.y},${dest.z}`
    if (seen.has(key)) continue
    seen.add(key)
    if (!REPLACEABLE_NAMES.includes(oldName) || bot.blockAt(dest)?.name !== itemName) continue
    cells.push([dest.x, dest.y, dest.z, itemName])
  }
  return cells
}

// Holds `toolName` (or nothing, for null) in the main hand. The pathfinder
// switches to the best tool for each dig and to scaffold blocks to place.
async function reequip (bot, toolName) {
  const tool = toolName ? findInventoryItem(bot, toolName) : null
  if (tool) await bot.equip(tool, 'hand')
  else await emptyHand(bot)
}

// Runs `fn` with a fresh digging Movements object and the cheapest-tool dig
// choice, and restores the movements that were set before (the v1
// world-preserving ones) and the pathfinder's own tool choice, even on failure.
async function withDiggingMovements (bot, options, fn) {
  const previous = bot.pathfinder.movements
  const previousTool = bot.pathfinder.bestHarvestTool
  bot.pathfinder.setMovements(new DiggingMovements(bot, options))
  bot.pathfinder.bestHarvestTool = cheapestHarvestTool(bot)
  try {
    return await fn()
  } finally {
    bot.pathfinder.setMovements(previous)
    bot.pathfinder.bestHarvestTool = previousTool
  }
}

module.exports = { emptyTravel, recordTravel, reequip, travelSummary, withDiggingMovements }
