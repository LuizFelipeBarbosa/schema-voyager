// POST /mine: find the nearest matching block, equip, path, dig, then measure
// what the server dropped (exact) separately from what the bot picked up
// (tolerant). A target the bot could not reach or dig is skipped for the rest
// of the episode, and one it could not reach is also remembered across
// episodes (see createUnreachableMemory), so a later call does not retry the
// same block. A v2 call tries up to a few candidates, best first, within a 60 s
// budget (see approach.js). With dig_through (v2) the path may dig and pillar, the
// target may be buried, and the step's window starts only after the travel has
// settled. See contracts/bot_api.md.

const { goals } = require('mineflayer-pathfinder')
const { watchServerBlockChange } = require('./blockchange')
const { approachTarget } = require('./approach')
const { GoalBesideBlock } = require('./movements')
const { insideAny } = require('./protect')
const { chooseTargets } = require('./targets')
const { emptyTravel, recordTravel, reequip, withDiggingMovements } = require('./travel')
const {
  TICK_MS, blockNameAt, countGains, emptyHand, findInventoryItem, findNearestExposedBlock, gotoGoal,
  inventoryCounts, pickUpEntities, settleInventory, sleepTicks, watchDrops
} = require('./world')

const DIG_REACH = 4 // stay inside the 4.5 block survival reach
const DROP_WINDOW_TICKS = 20
const CONFIRM_TIMEOUT_MS = 2000 // for the server's block update after the dig
const DEFAULT_MAX_DISTANCE = 16
const V1_PATH_MS = 30000 // v1 makes one attempt, with no failFast

function parseRequest (body) {
  const blockNames = Array.isArray(body.blocks) && body.blocks.length > 0 ? body.blocks : [body.block]
  return {
    blockNames,
    tool: body.tool ?? null,
    digThrough: body.dig_through === true,
    maxDistance: body.max_distance ?? DEFAULT_MAX_DISTANCE,
    scaffold: Array.isArray(body.scaffold) ? body.scaffold : [],
    protect: body.protect ?? [],
    // Any v2 option switches on the v2 target search and block-break confirmation.
    v2: body.blocks != null || body.dig_through != null || body.max_distance != null || body.scaffold != null
  }
}

// The v1 search: the nearest exposed block outside every `protect` box. A hit
// inside one is skipped (in a copy of `skipped`) and the search repeated.
function findNearestUnprotectedBlock (bot, blockName, skipped, protect) {
  const skippedHere = new Set(skipped)
  for (;;) {
    const pos = findNearestExposedBlock(bot, blockName, skippedHere)
    if (!pos || !insideAny(protect, pos)) return pos
    skippedHere.add(pos.toString())
  }
}

// `session`: {episode, tracker, unreachable, signal}; an abort throws
// `signal.reason` and marks no target. episode.skippedTargets holds the
// failed target positions (Vec3#toString keys), `unreachable` the memory that
// outlives episodes.
async function mine (bot, body, { episode, tracker, unreachable, signal = null }) {
  signal?.throwIfAborted()
  const { blockNames, tool, digThrough, maxDistance, scaffold, protect, v2 } = parseRequest(body)
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
  // `no_path` is also remembered across episodes; other ends only for this one.
  const skipTarget = (pos, error) => {
    episode.skippedTargets.add(pos.toString())
    if (error === 'unreachable' || error === 'no_path') unreachable.remember(bot, pos)
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

  const { positions, hazard } = v2
    ? chooseTargets(bot, {
      blockNames,
      maxDistance,
      buried: digThrough,
      skipped: episode.skippedTargets,
      unreachable,
      onHazard: (event) => tracker.log.record('hazard', event),
      protect
    })
    : { positions: [findNearestUnprotectedBlock(bot, blockNames[0], episode.skippedTargets, protect)].filter(Boolean), hazard: false }
  if (positions.length === 0) return finish(hazard ? 'hazard' : 'not_found')

  const toolItem = tool ? findInventoryItem(bot, tool) : null
  if (tool && !toolItem) return finish('missing_tool')
  try {
    if (toolItem) await bot.equip(toolItem, 'hand')
    else await emptyHand(bot)
  } catch (err) {
    if (signal?.aborted) throw signal.reason
    return finish('dig_failed', `could not set the held item: ${err.message}`)
  }

  // Travel into position, trying the next candidate when one cannot be
  // reached; digging and pillaring only with dig_through.
  const recording = recordTravel(bot, episode)
  let targetPos = null
  let reached = false
  try {
    if (v2) {
      const approach = await approachTarget(bot, positions, {
        goalFor: (pos) => digThrough ? new GoalBesideBlock(pos) : new goals.GoalLookAtBlock(pos, bot.world, { reach: DIG_REACH }),
        withMovements: (pos, walk) => digThrough
          ? withDiggingMovements(bot, { scaffoldNames: scaffold, protectedBlock: pos, protect }, walk)
          : walk(),
        mark: skipTarget,
        signal
      })
      ;({ reached, targetPos } = approach)
      if (approach.attempts.length > 0) {
        result.reach = { reason: approach.attempts.at(-1).end, attempts: approach.attempts }
      }
    } else {
      targetPos = positions[0]
      reached = await gotoGoal(bot, new goals.GoalLookAtBlock(targetPos, bot.world, { reach: DIG_REACH }), V1_PATH_MS, { signal })
      if (!reached) skipTarget(targetPos, 'unreachable')
    }
    result.block = blockNameAt(bot, targetPos)
    result.target = { x: targetPos.x, y: targetPos.y, z: targetPos.z }
    if (reached) await recording.settle()
  } finally {
    result.travel = recording.finish()
  }
  if (!reached) return finish('unreachable')
  const targetName = result.block
  const giveUpOnTarget = (error, detail) => {
    skipTarget(targetPos, error)
    return finish(error, detail)
  }

  // The pathfinder switches to the best tool for each dig on the way.
  if (digThrough) {
    try {
      await reequip(bot, tool)
    } catch (err) {
      if (signal?.aborted) throw signal.reason
      return finish('dig_failed', `could not set the held item: ${err.message}`)
    }
    const standingOnTarget = bot.entity.position.floored().offset(0, -1, 0).equals(targetPos)
    if (standingOnTarget) return giveUpOnTarget('unreachable', 'standing on the target')
  }

  signal?.throwIfAborted()

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
    if (signal?.aborted) throw signal.reason
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

  await pickUpEntities(bot, drops.entities, { signal })
  await settleInventory(bot)
  if (!result.block_removed) {
    return giveUpOnTarget('dig_failed', confirmed ? 'block still present after dig' : 'no server block update within 2 s')
  }
  return finish(null)
}

module.exports = { mine }
