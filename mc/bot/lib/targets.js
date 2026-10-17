// Target search for v2 /mine and the 8-sector /look scan. Both use
// bot.findBlocks, which checks each chunk section's palette first, so a search
// stays fast at a radius of 96.

const { Vec3 } = require('vec3')
const { DIRECTIONS, sectorOf } = require('./geometry')
const { hasAirNeighbour, round } = require('./world')

const MAX_CANDIDATES = 1024
const MAX_HAZARD_EVENTS = 3 // per search; the rest of the candidates are skipped quietly
const LOOK_MAX_RADIUS = 96
const LOOK_DEFAULT_RADIUS = 64
const LOOK_CANDIDATES_PER_ID = 4096
const NEIGHBOUR_OFFSETS = [
  new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 1, 0),
  new Vec3(0, -1, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)
]

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

// The nearest block named in `blockNames` within `maxDistance` that is not in
// `skipped` (Vec3#toString keys) and not beside lava or flowing water (each
// of those is reported through `onHazard({kind, position})`). With `buried`
// allowed, an exposed block (one with an air neighbour) is preferred and the
// nearest buried one is the fallback; otherwise only exposed blocks count.
// Returns {pos, hazard}: `pos` is null when nothing qualifies, `hazard` is
// true when at least one candidate was refused for lava or water.
function chooseTarget (bot, { blockNames, maxDistance, buried, skipped, onHazard }) {
  const ids = blockIds(bot, blockNames)
  if (ids.length === 0) return { pos: null, hazard: false }
  const candidates = bot.findBlocks({ matching: ids, maxDistance, count: MAX_CANDIDATES })
  let nearestBuried = null
  let hazard = false
  let hazardEvents = 0
  for (const pos of candidates) {
    if (skipped.has(pos.toString())) continue
    const kind = hazardNear(bot, pos)
    if (kind) {
      hazard = true
      if (hazardEvents++ < MAX_HAZARD_EVENTS) onHazard({ kind, position: { x: pos.x, y: pos.y, z: pos.z } })
      continue
    }
    if (hasAirNeighbour(bot, pos)) return { pos, hazard }
    if (buried && !nearestBuried) nearestBuried = pos
  }
  return { pos: nearestBuried, hazard }
}

// For each of the 8 sectors around the bot, the nearest block of each id in
// `blockNames` within `radius` (default 64, at most 96).
function look (bot, { blocks: blockNames, radius = LOOK_DEFAULT_RADIUS }) {
  const searchRadius = Math.min(LOOK_MAX_RADIUS, Math.max(1, radius))
  const origin = bot.entity.position
  const directions = Object.fromEntries(DIRECTIONS.map((name) => [name, {}]))
  for (const name of blockNames) {
    const ids = blockIds(bot, [name])
    if (ids.length === 0) continue
    // findBlocks sorts by distance, so the first hit in a sector is its nearest.
    const found = bot.findBlocks({ matching: ids, maxDistance: searchRadius, count: LOOK_CANDIDATES_PER_ID })
    for (const pos of found) {
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
  return { ok: true, position: { x: round(origin.x), y: round(origin.y), z: round(origin.z) }, radius: searchRadius, directions }
}

module.exports = { chooseTarget, hazardNear, look }
