// Target search for v2 /mine and the 8-sector /look scan. Both use
// bot.findBlocks, which checks each chunk section's palette first, so a search
// stays fast at a radius of 96.

const { Vec3 } = require('vec3')
const { DIRECTIONS, sectorOf } = require('./geometry')
const { insideAny } = require('./protect')
const { AIR_BLOCKS, blockNameAt, hasAirNeighbour, round } = require('./world')

const MAX_CANDIDATES = 1024
const MAX_HAZARD_EVENTS = 3 // per search; the rest of the candidates are skipped quietly
const UNREACHABLE_TTL_MS = 10 * 60 * 1000
const NO_STANDING_SPOT_PENALTY = 16
const BURIED_PENALTY = 12 // digging in costs about this much walking, however near the block is
const LOOK_MAX_RADIUS = 96
const LOOK_DEFAULT_RADIUS = 64
const LOOK_CANDIDATES_PER_ID = 4096
const NEIGHBOUR_OFFSETS = [
  new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 1, 0),
  new Vec3(0, -1, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)
]
const HORIZONTAL_OFFSETS = NEIGHBOUR_OFFSETS.filter((offset) => offset.y === 0)

function blockIds (bot, names) {
  return names.map((name) => bot.registry.blocksByName[name]?.id).filter((id) => id !== undefined)
}

// 'lava' or 'water' if the block at `pos` has lava, or flowing water, on one
// of its six sides; else null.
function hazardNear (bot, pos) {
  for (const offset of NEIGHBOUR_OFFSETS) {
    const neighbour = bot.blockAt(pos.plus(offset), false)
    if (neighbour?.name === 'lava') return 'lava'
    if (neighbour?.name === 'water' && Number(neighbour.getProperties().level) !== 0) return 'water'
  }
  return null
}

// Remembers targets the bot could not path to, across episodes, for `ttlMs`.
// An entry is forgotten when the block at its position is no longer the one
// that was remembered (mined, replaced, or the chunk is gone).
function createUnreachableMemory ({ ttlMs = UNREACHABLE_TTL_MS, now = Date.now } = {}) {
  const entries = new Map() // Vec3#toString -> {blockName, expiresAt}
  return {
    remember (bot, pos) {
      entries.set(pos.toString(), { blockName: blockNameAt(bot, pos), expiresAt: now() + ttlMs })
    },
    has (bot, pos) {
      const key = pos.toString()
      const entry = entries.get(key)
      if (!entry) return false
      if (now() >= entry.expiresAt || blockNameAt(bot, pos) !== entry.blockName) {
        entries.delete(key)
        return false
      }
      return true
    }
  }
}

// True when the bot could stand beside `pos`: a horizontal neighbour that is
// air with air above it and solid ground below.
function hasStandingSpot (bot, pos) {
  return HORIZONTAL_OFFSETS.some((offset) => {
    const feet = pos.plus(offset)
    const ground = blockNameAt(bot, feet.offset(0, -1, 0))
    return AIR_BLOCKS.has(blockNameAt(bot, feet)) && AIR_BLOCKS.has(blockNameAt(bot, feet.offset(0, 1, 0))) &&
      !AIR_BLOCKS.has(ground) && ground !== 'water' && ground !== 'lava'
  })
}

// Lower is better: how costly `pos` is to reach from `origin`. Height
// differences count double (digging or pillaring across them is slow). A
// buried block is penalised, and so is an exposed one with no spot to stand
// beside it, since reaching either needs digging.
function reachCost (bot, pos, origin, exposed) {
  const centre = pos.offset(0.5, 0.5, 0.5)
  const digging = exposed ? (hasStandingSpot(bot, pos) ? 0 : NO_STANDING_SPOT_PENALTY) : BURIED_PENALTY
  return origin.distanceTo(centre) + 2 * Math.abs(centre.y - origin.y) + digging
}

// The blocks named in `blockNames` within `maxDistance` that are worth trying,
// best first: not in `skipped` (Vec3#toString keys), not in the `unreachable`
// memory, not inside a `protect` box, and not beside lava or flowing water (each of those is reported
// through `onHazard({kind, position})`). Buried blocks (no air neighbour) only
// count with `buried` allowed. The order is by reachCost, so a block near the
// bot's own height, exposed and with a place to stand, wins. Returns {positions, hazard}:
// `hazard` is true when at least one candidate was refused for lava or water.
function chooseTargets (bot, { blockNames, maxDistance, buried, skipped, unreachable, onHazard, protect = [] }) {
  const ids = blockIds(bot, blockNames)
  if (ids.length === 0) return { positions: [], hazard: false }
  const origin = bot.entity.position
  const candidates = bot.findBlocks({ matching: ids, maxDistance, count: MAX_CANDIDATES })
  const ranked = []
  let hazard = false
  let hazardEvents = 0
  for (const pos of candidates) {
    if (skipped.has(pos.toString()) || unreachable.has(bot, pos) || insideAny(protect, pos)) continue
    const kind = hazardNear(bot, pos)
    if (kind) {
      hazard = true
      if (hazardEvents++ < MAX_HAZARD_EVENTS) onHazard({ kind, position: { x: pos.x, y: pos.y, z: pos.z } })
      continue
    }
    const exposed = hasAirNeighbour(bot, pos)
    if (!exposed && !buried) continue
    ranked.push({ pos, cost: reachCost(bot, pos, origin, exposed) })
  }
  ranked.sort((a, b) => a.cost - b.cost)
  return { positions: ranked.map((entry) => entry.pos), hazard }
}

// Health is entity metadata index 9; a mob at 0 is dying and is no target. Entities that
// have been removed (entityGone) have isValid false.
const HEALTH_METADATA_INDEX = 9

function isAlive (entity) {
  return entity.isValid !== false && !(entity.metadata?.[HEALTH_METADATA_INDEX] <= 0)
}

// For each of the 8 sectors around the bot, the nearest block of each id in
// `blockNames` within `radius` (default 64, at most 96), leaving out blocks inside a `protect` box,
// and the nearest living entity of each name in `mobNames` (from bot.entities, floored coordinates).
function look (bot, { blocks: blockNames = [], mobs: mobNames = [], radius = LOOK_DEFAULT_RADIUS, protect = [] }) {
  const searchRadius = Math.min(LOOK_MAX_RADIUS, Math.max(1, radius))
  const origin = bot.entity.position
  const directions = Object.fromEntries(DIRECTIONS.map((name) => [name, {}]))
  for (const name of blockNames) {
    const ids = blockIds(bot, [name])
    if (ids.length === 0) continue
    // findBlocks sorts by distance, so the first hit in a sector is its nearest.
    const found = bot.findBlocks({ matching: ids, maxDistance: searchRadius, count: LOOK_CANDIDATES_PER_ID })
    for (const pos of found) {
      if (insideAny(protect, pos)) continue
      const centre = pos.offset(0.5, 0.5, 0.5)
      const sector = directions[sectorOf(centre.x - origin.x, centre.z - origin.z)]
      if (sector[name]) continue
      sector[name] = {
        x: pos.x,
        y: pos.y,
        z: pos.z,
        distance: round(origin.distanceTo(centre), 1),
        exposed: hasAirNeighbour(bot, pos)
      }
    }
  }
  const entities = Object.values(bot.entities)
    .filter((entity) => entity !== bot.entity && entity.position && isAlive(entity))
    .map((entity) => ({ entity, distance: origin.distanceTo(entity.position) }))
    .sort((a, b) => a.distance - b.distance)
  for (const name of mobNames) {
    for (const { entity, distance } of entities) {
      if (entity.name !== name || distance > searchRadius) continue
      const sector = directions[sectorOf(entity.position.x - origin.x, entity.position.z - origin.z)]
      if (sector[name]) continue
      const { x, y, z } = entity.position.floored()
      sector[name] = { x, y, z, distance: round(distance, 1) }
    }
  }
  return { ok: true, position: { x: round(origin.x), y: round(origin.y), z: round(origin.z) }, radius: searchRadius, directions }
}

module.exports = { chooseTargets, createUnreachableMemory, hazardNear, isAlive, look }
