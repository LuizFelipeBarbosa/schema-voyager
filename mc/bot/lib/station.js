// Placing a crafting table or a furnace from the inventory next to the bot
// and digging it back up afterwards.

const { Vec3 } = require('vec3')
const { goals } = require('mineflayer-pathfinder')
const {
  AIR_BLOCKS, findInventoryItem, gotoGoal, inventoryCounts, pickUpEntities, settleInventory, sleepTicks, watchDrops
} = require('./world')
const { withWalkingMovements } = require('./goto')
const { cheapestHarvestTool } = require('./movements')
const { insideAny } = require('./protect')
const { recordTravel } = require('./travel')

const SEARCH_RADIUS = 4
const DROP_WINDOW_TICKS = 20
const MAX_CELLS = 6 // distinct cells tried before giving up on placing
const RETRY_TICKS = 5
const STANDING_RADIUS = 24 // a remembered station this close is walked back to
const WALK_BACK_TIMEOUT_MS = 15000
const HOME_WALK_TIMEOUT_MS = 45000

// A station this handler placed and won't dig back up because of an abort:
// remember it (name and position) in the episode for later actions.
function rememberStanding (episode, name, position) {
  const standing = episode.standingStations || (episode.standingStations = [])
  if (!standing.some((entry) => entry.position.equals(position))) standing.push({ name, position: position.clone() })
}

// Forget a remembered station once it has been dug back up.
function forgetStanding (episode, position) {
  if (!episode.standingStations) return
  episode.standingStations = episode.standingStations.filter((entry) => !entry.position.equals(position))
}

// The 400 message for a bad `station` ([x, y, z] integers) on /craft and /smelt, or null.
function validateStation (station) {
  const valid = station == null || (Array.isArray(station) && station.length === 3 && station.every(Number.isInteger))
  return valid ? null : '"station" must be three integers'
}

// A placed `blockName` within SEARCH_RADIUS of the bot or, with `episode`, a
// remembered one of that name within STANDING_RADIUS whose block is still
// there (remembered ones whose block is gone are forgotten). Null if none.
function findPlacedStation (bot, blockName, { episode = null } = {}) {
  const nearby = bot.findBlock({ matching: bot.registry.blocksByName[blockName].id, maxDistance: SEARCH_RADIUS })
  if (nearby || !episode?.standingStations) return nearby
  let best = null
  let bestDistance = STANDING_RADIUS
  for (const entry of [...episode.standingStations]) {
    if (entry.name !== blockName) continue
    const block = bot.blockAt(entry.position)
    if (!block) continue // not loaded: neither usable nor known to be gone
    if (block.name !== blockName) {
      forgetStanding(episode, entry.position)
      continue
    }
    const distance = bot.entity.position.distanceTo(entry.position.offset(0.5, 0.5, 0.5))
    if (distance <= bestDistance) {
      best = block
      bestDistance = distance
    }
  }
  return best
}

// A placed `name` station the bot can use: one within reach, or a remembered
// one it walks back to. Returns {station, remembered} (`remembered`: it was
// left standing by an earlier action, so digging it up adds one to the
// inventory), or null if there is none or the walk back failed.
async function reachStation (bot, name, { episode = null, signal = null } = {}) {
  const found = findPlacedStation(bot, name, { episode })
  if (!found) return null
  const remembered = Boolean(episode?.standingStations?.some((entry) => entry.position.equals(found.position)))
  const center = found.position.offset(0.5, 0.5, 0.5)
  const walkedBack = bot.entity.position.distanceTo(center) <= SEARCH_RADIUS ||
    await gotoGoal(bot, new goals.GoalNear(center.x, center.y, center.z, 2), WALK_BACK_TIMEOUT_MS, { signal })
  return walkedBack ? { station: found, remembered } : null
}

// A crafting table to use: a placed one within reach (walking back to a
// remembered one that is farther), else one placed from the inventory or the
// home one (see placeHeldOrUseHome). Returns {table, placedPos, remembered,
// usedHome, travel}: placedPos is set when the bot placed the table or must dig
// up a remembered one (`remembered`); or {error, detail, station_gone,
// station_unreachable, travel} (the keys that apply).
async function acquireTable (bot, { station = null, protect = [], episode = null, signal = null } = {}) {
  const reached = await reachStation(bot, 'crafting_table', { episode, signal })
  if (reached) {
    const { station: table, remembered } = reached
    return { table, placedPos: remembered ? table.position : null, remembered }
  }
  const { station: table, ...rest } = await placeHeldOrUseHome(bot, 'crafting_table', { station, protect, episode, signal })
  return rest.error ? rest : { table, remembered: false, ...rest }
}

// When no `name` station is within reach: place one from the inventory on a
// spot outside the `protect` boxes, else walk to the home `station` given ([x, y,
// z]) and use it where it stands. Returns {station, placedPos} for a placed one
// (placedPos: dig it up after), {station, placedPos: null, usedHome: true,
// travel} for the home one, or {error, detail, station_gone, station_unreachable,
// travel} (the keys that apply). Home stations stand outside the walls, so the
// walk never digs, places or opens a door.
async function placeHeldOrUseHome (bot, name, { station = null, protect = [], episode = null, signal = null } = {}) {
  // with no home station to fall back on, placing is still tried, so a failure is reported as it always was
  const canPlace = findSpots(bot, protect).length > 0 || !station
  if (canPlace && findInventoryItem(bot, name)) {
    try {
      const placed = await placeStation(bot, name, { protect, signal })
      return { station: placed, placedPos: placed.position }
    } catch (err) {
      if (signal?.aborted) throw signal.reason
      return { error: 'craft_failed', detail: `could not place a ${name.replace('_', ' ')}: ${err.message}` }
    }
  }

  if (!station) return { error: `missing_${name}` }
  const [x, y, z] = station
  const block = bot.blockAt(new Vec3(x, y, z))
  if (block?.name !== name) return { error: `missing_${name}`, ...(block ? { station_gone: true } : {}) }

  const recording = recordTravel(bot, episode ?? { walked: 0 })
  let arrived
  try {
    arrived = await withWalkingMovements(bot, () => gotoGoal(bot, new goals.GoalNear(x, y, z, 2), HOME_WALK_TIMEOUT_MS, { signal }))
  } catch (err) {
    recording.finish()
    throw err
  }
  await recording.settle()
  const travel = recording.finish()
  if (!arrived) return { error: 'unreachable', station_unreachable: true, travel }
  return { station: block, placedPos: null, usedHome: true, travel }
}

// The server can refuse a cell that looks free here (say, an entity is in the
// way), and asking again for the same cell gets the same answer. So each
// refusal moves on to the next closest cell, a few ticks later.
async function placeStation (bot, itemName, { protect = [], signal = null } = {}) {
  const spots = findSpots(bot, protect).slice(0, MAX_CELLS)
  if (spots.length === 0) throw new Error('no free spot next to the bot')
  let lastProblem = null
  for (const spot of spots) {
    signal?.throwIfAborted()
    lastProblem = await tryPlace(bot, itemName, spot)
    if (!lastProblem) return bot.blockAt(spot.pos)
    signal?.throwIfAborted()
    console.log(`warn: ${itemName} not placed at ${spot.pos}: ${lastProblem}`)
    await sleepTicks(RETRY_TICKS)
  }
  throw new Error(`refused at ${spots.length} cells, last: ${lastProblem}`)
}

// Null when the station stands at `spot.pos`, else what went wrong.
async function tryPlace (bot, itemName, spot) {
  try {
    await bot.equip(findInventoryItem(bot, itemName), 'hand')
    await bot.placeBlock(bot.blockAt(spot.pos.minus(spot.faceToCell)), spot.faceToCell)
  } catch (err) {
    return err.message
  }
  const placed = bot.blockAt(spot.pos)
  return placed?.name === itemName ? null : `found ${placed?.name} at ${spot.pos} after placing`
}

// Cells where a station can go: air within 2 blocks of the bot's feet (one
// below to one above), next to a full solid block to place against, not
// overlapping the bot's hitbox. Returns [{pos, faceToCell}], where faceToCell
// is the face of that block the station goes on. Cells on the ground come
// first, then the closest; the others are for a bot standing on a pillar or a
// ledge, with nothing solid at its own level. A cell inside a `protect` box is never one.
function findSpots (bot, protect = []) {
  const feet = bot.entity.position.floored()
  const botPos = bot.entity.position
  // the face to place on, preferring the top of the block below the cell
  const faces = [new Vec3(0, 1, 0), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(0, -1, 0)]
  const candidates = []
  for (let dy = -1; dy <= 1; dy++) {
    for (let dx = -2; dx <= 2; dx++) {
      for (let dz = -2; dz <= 2; dz++) {
        const pos = feet.offset(dx, dy, dz)
        if (!AIR_BLOCKS.has(bot.blockAt(pos)?.name)) continue
        if (dy >= 0 && overlapsBot(bot, pos)) continue
        if (insideAny(protect, pos)) continue
        const faceToCell = faces.find((face) => bot.blockAt(pos.minus(face))?.boundingBox === 'block')
        if (faceToCell) candidates.push({ pos, faceToCell, onGround: faceToCell.y === 1 && dy === 0 })
      }
    }
  }
  const distance = (spot) => botPos.distanceTo(spot.pos.offset(0.5, 0.5, 0.5))
  return candidates.sort((a, b) => (b.onGround - a.onGround) || (distance(a) - distance(b)))
}

function overlapsBot (bot, cell) {
  const { x, z } = bot.entity.position
  const halfWidth = 0.3 + 0.05 // player hitbox plus a small margin
  return cell.x < x + halfWidth && cell.x + 1 > x - halfWidth &&
    cell.z < z + halfWidth && cell.z + 1 > z - halfWidth
}

// Digs the station the bot placed and picks it back up. Resolves to
// {retrieved, toolsBroken}: `retrieved` is true when the bot holds as many
// `itemName` as before the call; `toolsBroken` names the tool the dig wore out.
// The tool is the cheapest that harvests the block. With `withBestTool` and no
// such tool, the best one in the inventory is held (a furnace only drops when
// broken with a pickaxe), even if it breaks. `onDrop` sees each item entity the
// dig drops. An abort throws `signal.reason`, leaving the station as it is.
async function retrieveStation (bot, pos, itemName, countBefore, { withBestTool = false, onDrop = null, signal = null } = {}) {
  signal?.throwIfAborted()
  const watcher = watchDrops(bot, pos, { onDrop })
  let held = null // the tool the dig starts with, and how many of it there are
  try {
    const block = bot.blockAt(pos)
    let tool = cheapestHarvestTool(bot)(block)
    if (withBestTool && !(tool && block.canHarvest(tool.type))) tool = bot.pathfinder.bestHarvestTool(block)
    if (tool) await bot.equip(tool, 'hand')
    if (bot.heldItem?.maxDurability) held = { name: bot.heldItem.name, count: inventoryCounts(bot)[bot.heldItem.name] }
    await bot.dig(block, true)
    await sleepTicks(DROP_WINDOW_TICKS)
  } catch (err) {
    if (signal?.aborted) {
      watcher.stop()
      throw signal.reason
    }
    console.log(`warn: could not dig the placed ${itemName}: ${err.message}`)
  }
  const { entities } = watcher.stop()
  await pickUpEntities(bot, entities, { signal })
  await settleInventory(bot)
  const counts = inventoryCounts(bot)
  return {
    retrieved: (counts[itemName] || 0) >= countBefore,
    toolsBroken: held && (counts[held.name] || 0) < held.count ? [held.name] : []
  }
}

module.exports = {
  acquireTable, findPlacedStation, findSpots, forgetStanding, overlapsBot, placeHeldOrUseHome, placeStation, reachStation,
  rememberStanding, retrieveStation, validateStation
}
