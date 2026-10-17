// POST /mine: find the nearest exposed block, equip, path, dig, then measure
// what the server dropped (exact) separately from what the bot picked up
// (tolerant). See contracts/bot_api.md.

const { goals } = require('mineflayer-pathfinder')
const {
  countGains, emptyHand, findInventoryItem, findNearestExposedBlock, gotoGoal,
  inventoryCounts, pickUpEntities, settleInventory, sleepTicks, watchDrops
} = require('./world')

const DIG_REACH = 4 // stay inside the 4.5 block survival reach
const DROP_WINDOW_TICKS = 20

async function mine (bot, { block: blockName, tool = null }) {
  const before = inventoryCounts(bot)
  const result = {
    block: blockName,
    target: null,
    tool,
    block_removed: false,
    drops: {},
    picked_up: {}
  }
  const finish = (error, detail) => {
    const inventory = inventoryCounts(bot)
    const body = { ok: !error, error: error || null, ...result, picked_up: countGains(before, inventory), inventory }
    if (detail) body.detail = detail
    return body
  }

  const targetPos = findNearestExposedBlock(bot, blockName)
  if (!targetPos) return finish('not_found')
  result.target = { x: targetPos.x, y: targetPos.y, z: targetPos.z }

  const toolItem = tool ? findInventoryItem(bot, tool) : null
  if (tool && !toolItem) return finish('missing_tool')
  try {
    if (toolItem) await bot.equip(toolItem, 'hand')
    else await emptyHand(bot)
  } catch (err) {
    return finish('dig_failed', `could not set the held item: ${err.message}`)
  }

  const goal = new goals.GoalLookAtBlock(targetPos, bot.world, { reach: DIG_REACH })
  if (!await gotoGoal(bot, goal)) return finish('unreachable')

  const watcher = watchDrops(bot, targetPos)
  try {
    await bot.dig(bot.blockAt(targetPos), true)
  } catch (err) {
    watcher.stop()
    return finish('dig_failed', err.message)
  }
  // Mineflayer marks the block as air locally when its dig timer ends; a
  // server rejection re-sends the real block within this window.
  await sleepTicks(DROP_WINDOW_TICKS)
  const drops = watcher.stop()
  result.drops = drops.counts
  result.block_removed = bot.blockAt(targetPos)?.name !== blockName

  await pickUpEntities(bot, drops.entities)
  await settleInventory(bot)
  return finish(result.block_removed ? null : 'dig_failed', result.block_removed ? null : 'block still present after dig')
}

module.exports = { mine }
