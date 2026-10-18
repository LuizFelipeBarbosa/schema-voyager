// Travel accounting and pathing for v2: what the bot dug, placed, picked up
// and walked on the way into position, before the step's own dig / craft /
// smelt. Everything here is tolerant; it never counts as evidence for a rule.

const { DiggingMovements, cheapestHarvestTool } = require('./movements')
const {
  TICK_MS, addCount, countGains, emptyHand, findInventoryItem, inventoryCounts, round, sleepTicks
} = require('./world')

const SETTLE_TICKS = 5 // quiet time after the last travel dig, before the step

function emptyTravel () {
  return { distance: 0, dug: {}, placed: {}, picked_up: {} }
}

// Pure: the `travel` response object.
function travelSummary ({ walkedBefore, walkedAfter, dug, placed, inventoryBefore, inventoryAfter }) {
  return {
    distance: round(walkedAfter - walkedBefore, 1),
    dug,
    placed,
    picked_up: countGains(inventoryBefore, inventoryAfter)
  }
}

// Starts counting what the pathfinder does. Digs are counted by wrapping
// bot.dig (Mineflayer removes plain 'diggingCompleted' listeners after each
// dig), placements by the 'blockPlaced' event. finish() unwraps and returns
// the counts.
function recordTravel (bot, episode) {
  const walkedBefore = episode.walked
  const inventoryBefore = inventoryCounts(bot)
  const dug = {}
  const placed = {}
  let lastDigAt = null

  const originalDig = bot.dig
  bot.dig = async function (block, ...rest) {
    const name = block.name
    const result = await originalDig.call(this, block, ...rest)
    addCount(dug, name)
    lastDigAt = Date.now()
    return result
  }
  const onPlaced = (oldBlock, newBlock) => addCount(placed, newBlock.name)
  bot.on('blockPlaced', onPlaced)

  return {
    // The 5-tick wait after the last travel dig (none if nothing was dug).
    async settle () {
      if (lastDigAt === null) return
      const remaining = SETTLE_TICKS * TICK_MS - (Date.now() - lastDigAt)
      if (remaining > 0) await sleepTicks(Math.ceil(remaining / TICK_MS))
    },
    finish () {
      bot.dig = originalDig
      bot.removeListener('blockPlaced', onPlaced)
      return travelSummary({
        walkedBefore,
        walkedAfter: episode.walked,
        dug,
        placed,
        inventoryBefore,
        inventoryAfter: inventoryCounts(bot)
      })
    }
  }
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
