// POST /mine: find the nearest matching block, equip, path, dig, then measure
// what the server dropped (exact) separately from what the bot picked up
// (tolerant). A target the bot could not reach or dig is skipped for the rest
// of the episode, so the next call picks the next nearest one instead of the
// same block again. With dig_through (v2) the path may dig and pillar, the
// target may be buried, and the step's window starts only after the travel has
// settled. See contracts/bot_api.md.

const { goals } = require('mineflayer-pathfinder')
const { watchServerBlockChange } = require('./blockchange')
const { GoalBesideBlock } = require('./movements')
const { chooseTarget } = require('./targets')
const { emptyTravel, recordTravel, reequip, withDiggingMovements } = require('./travel')
const {
  TICK_MS, blockNameAt, countGains, emptyHand, findInventoryItem, findNearestExposedBlock, gotoGoal,
  inventoryCounts, pickUpEntities, settleInventory, sleepTicks, watchDrops
} = require('./world')

const DIG_REACH = 4 // stay inside the 4.5 block survival reach
const DROP_WINDOW_TICKS = 20
const CONFIRM_TIMEOUT_MS = 2000 // for the server's block update after the dig
const DEFAULT_MAX_DISTANCE = 16

function parseRequest (body) {
  const blockNames = Array.isArray(body.blocks) && body.blocks.length > 0 ? body.blocks : [body.block]
  return {
    blockNames,
    tool: body.tool ?? null,
    digThrough: body.dig_through === true,
    maxDistance: body.max_distance ?? DEFAULT_MAX_DISTANCE,
    scaffold: Array.isArray(body.scaffold) ? body.scaffold : [],
    // Any v2 option switches on the v2 target search and block-break confirmation.
    v2: body.blocks != null || body.dig_through != null || body.max_distance != null || body.scaffold != null
  }
}

// `session`: {episode, tracker}; episode.skippedTargets holds the failed
// target positions (Vec3#toString keys).
async function mine (bot, body, { episode, tracker }) {
  const { blockNames, tool, digThrough, maxDistance, scaffold, v2 } = parseRequest(body)
  const startSeq = tracker.log.lastSeq()
  const before = inventoryCounts(bot)
  let pickupBase = before // picked_up: gains since arrival for v2, over the whole call for v1
  const result = {
    block: blockNames[0],
    target: null,
    tool,
    tool_used: null,
    block_removed: false,
    drops: {},
    travel: emptyTravel(),
    inventory_before_step: before
  }
  const finish = (errorCode, detail) => {
    const inventory = inventoryCounts(bot)
    const tookDamage = tracker.log.since(startSeq).some((event) => event.type === 'damage')
    const stoppedByDamage = v2 && tookDamage && (errorCode === 'unreachable' || errorCode === 'dig_failed')
    const error = stoppedByDamage ? 'quarantined' : errorCode
    const response = { ok: !error, error: error || null, ...result, picked_up: countGains(pickupBase, inventory), inventory }
    if (detail) response.detail = detail
    return response
  }

  const { pos: targetPos, hazard } = v2
    ? chooseTarget(bot, {
      blockNames,
      maxDistance,
      buried: digThrough,
      skipped: episode.skippedTargets,
      onHazard: (event) => tracker.log.record('hazard', event)
    })
    : { pos: findNearestExposedBlock(bot, blockNames[0], episode.skippedTargets), hazard: false }
  if (!targetPos) return finish(hazard ? 'hazard' : 'not_found')
  const targetName = blockNameAt(bot, targetPos)
  result.block = targetName
  result.target = { x: targetPos.x, y: targetPos.y, z: targetPos.z }
  const giveUpOnTarget = (error, detail) => {
    episode.skippedTargets.add(targetPos.toString())
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

  // Travel into position; digging and pillaring only with dig_through.
  const recording = recordTravel(bot, episode)
  let reached = false
  try {
    reached = digThrough
      ? await withDiggingMovements(bot, { scaffoldNames: scaffold, protectedBlock: targetPos },
        () => gotoGoal(bot, new GoalBesideBlock(targetPos)))
      : await gotoGoal(bot, new goals.GoalLookAtBlock(targetPos, bot.world, { reach: DIG_REACH }))
    if (reached) await recording.settle()
  } finally {
    result.travel = recording.finish()
  }
  if (!reached) return giveUpOnTarget('unreachable')

  // The pathfinder switches to the best tool for each dig on the way.
  if (digThrough) {
    try {
      await reequip(bot, tool)
    } catch (err) {
      return finish('dig_failed', `could not set the held item: ${err.message}`)
    }
    const standingOnTarget = bot.entity.position.floored().offset(0, -1, 0).equals(targetPos)
    if (standingOnTarget) return giveUpOnTarget('unreachable', 'standing on the target')
  }

  // The step starts here.
  pickupBase = v2 ? inventoryCounts(bot) : before
  result.inventory_before_step = inventoryCounts(bot)
  tracker.log.setPhase('step')
  const current = blockNameAt(bot, targetPos)
  if (current !== targetName) {
    tracker.log.record('target_despawned', { detail: `target changed from ${targetName} to ${current}` })
    return giveUpOnTarget('dig_failed', 'the target changed before the dig')
  }

  result.tool_used = bot.heldItem?.name ?? null
  const watcher = watchDrops(bot, targetPos, {
    inCell: digThrough,
    onDrop: (entity) => tracker.ownDrops.add(entity.id),
    onLost: (entity) => tracker.log.record('target_despawned', { detail: `drop entity ${entity.id} vanished before its stack was read` })
  })
  const stopWatchingInterference = tracker.watchInterference(targetPos)
  const confirmation = watchServerBlockChange(bot, targetPos, targetName)
  try {
    await bot.dig(bot.blockAt(targetPos), true)
  } catch (err) {
    watcher.stop()
    stopWatchingInterference()
    confirmation.stop()
    return giveUpOnTarget('dig_failed', err.message)
  }
  // Mineflayer marks the block as air locally when its dig timer ends; a
  // server rejection re-sends the real block within this window.
  await sleepTicks(DROP_WINDOW_TICKS)
  const drops = watcher.stop()
  stopWatchingInterference()
  const confirmed = v2 ? await confirmation.waitForConfirmation(CONFIRM_TIMEOUT_MS - DROP_WINDOW_TICKS * TICK_MS) : true
  confirmation.stop()
  result.drops = drops.counts
  result.block_removed = confirmed && bot.blockAt(targetPos)?.name !== targetName

  await pickUpEntities(bot, drops.entities)
  await settleInventory(bot)
  if (!result.block_removed) {
    return giveUpOnTarget('dig_failed', confirmed ? 'block still present after dig' : 'no server block update within 2 s')
  }
  return finish(null)
}

module.exports = { mine }
