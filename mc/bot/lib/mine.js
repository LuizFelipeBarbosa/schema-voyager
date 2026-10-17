// POST /mine: find the nearest exposed block, equip, path, dig, then measure
// what the server dropped (exact) separately from what the bot picked up
// (tolerant). A target the bot could not reach or dig is skipped for the rest
// of the episode, so the next call picks the next nearest one instead of the
// same block again. See contracts/bot_api.md.

const { goals } = require('mineflayer-pathfinder')
const {
  countGains, emptyHand, findInventoryItem, findNearestExposedBlock, gotoGoal,
  inventoryCounts, pickUpEntities, settleInventory, sleepTicks, watchDrops
} = require('./world')

const DIG_REACH = 4 // stay inside the 4.5 block survival reach
const DROP_WINDOW_TICKS = 20

// `skipped`: the episode's set of failed target positions (Vec3#toString keys).
async function mine (bot, { block: blockName, tool = null }, skipped) {
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

  const targetPos = findNearestExposedBlock(bot, blockName, skipped)
  if (!targetPos) return finish('not_found')
  result.target = { x: targetPos.x, y: targetPos.y, z: targetPos.z }
  const giveUpOnTarget = (error, detail) => {
    skipped.add(targetPos.toString())
    return finish(error, detail)
  }

  const toolItem = tool ? findInventoryItem(bot, tool) : null
  if (tool && !toolItem) return finish('missing_tool')
  try {
    if (toolItem) await bot.equip(toolItem, 'hand')
    else await emptyHand(bot)
  } catch (err) {
    return finish('dig_failed', `could not set the held item: ${err.message}`)
  }

  const goal = new goals.GoalLookAtBlock(targetPos, bot.world, { reach: DIG_REACH })
  if (!await gotoGoal(bot, goal)) return giveUpOnTarget('unreachable')

  const watcher = watchDrops(bot, targetPos)
  try {
    await bot.dig(bot.blockAt(targetPos), true)
  } catch (err) {
    watcher.stop()
    return giveUpOnTarget('dig_failed', err.message)
  }
  // Mineflayer marks the block as air locally when its dig timer ends; a
  // server rejection re-sends the real block within this window.
  await sleepTicks(DROP_WINDOW_TICKS)
  const drops = watcher.stop()
  result.drops = drops.counts
  result.block_removed = bot.blockAt(targetPos)?.name !== blockName

  await pickUpEntities(bot, drops.entities)
  await settleInventory(bot)
  if (!result.block_removed) return giveUpOnTarget('dig_failed', 'block still present after dig')
  return finish(null)
}

module.exports = { mine }
