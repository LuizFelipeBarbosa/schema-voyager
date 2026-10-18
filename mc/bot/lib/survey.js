// POST /survey: where to put a width x depth footprint near the bot, ranked by
// the blocks to dig and fill. Never walks. See contracts/bot_api.md (v3.4b) and
// docs/v3-interfaces.md (v3.4b "/survey"); the simulator ranks the same way.

const { Vec3 } = require('vec3')
const { validateProtect } = require('./protect')

const MAX_FOOTPRINT_SIDE = 16
const MAX_RADIUS = 32
const DEFAULT_RADIUS = 24
const WINDOW_BLOCKS = 12 // ground is looked for this far above and below the bot's feet
const MAX_STEP = 3 // how far a column's ground may be above the layer under the floor
const MAX_SITES = 10

const isIntegerInRange = (value, min, max) => Number.isInteger(value) && value >= min && value <= max
const isLiquid = (name) => name === 'water' || name === 'lava'
const isTree = (name) => /(_log|_wood|_leaves|mushroom_block)$/.test(name) || name === 'mushroom_stem'

function validateSurvey (body) {
  const { size, radius } = body
  if (!Array.isArray(size) || size.length !== 2 || !size.every((side) => isIntegerInRange(side, 1, MAX_FOOTPRINT_SIDE))) {
    return `"size" must be two integers from 1 to ${MAX_FOOTPRINT_SIDE}`
  }
  if (radius != null && !isIntegerInRange(radius, 1, MAX_RADIUS)) return `"radius" must be an integer from 1 to ${MAX_RADIUS}`
  return validateProtect(body.protect)
}

// {ground, reason} of the column (x, z): `ground` is the y of its first solid
// block going down the window, or null with the reason the column is ruled
// out (`unknown`, `liquid` or `tree`). `blockAt(x, y, z)` gives a block or null.
function columnAround (blockAt, feetY, x, z) {
  const top = feetY + WINDOW_BLOCKS
  // Anything but air at the top means the bot is underground: rock there is a cave floor, not ground.
  if (blockAt(x, top, z)?.name !== 'air') return { ground: null, reason: 'unknown' }
  for (let y = top - 1; y >= feetY - WINDOW_BLOCKS; y--) {
    const block = blockAt(x, y, z)
    if (!block) return { ground: null, reason: 'unknown' }
    if (isLiquid(block.name)) return { ground: null, reason: 'liquid' }
    if (block.boundingBox !== 'block') continue
    return isTree(block.name) ? { ground: null, reason: 'tree' } : { ground: y, reason: null }
  }
  return { ground: null, reason: 'unknown' }
}

// Pure ranking over a column reader. `columnAt(x, z)` -> {ground, reason}
// (reason null when the column is usable); `feet` is {x, z}, the bot's
// position. Footprints lie inside the square of `radius` columns around it.
function rankSites ({ columnAt, feet, size: [width, depth], radius, protect = [] }) {
  const ruledOut = { liquid: 0, tree: 0, unknown: 0, steep: 0, protected: 0 }
  const sites = []
  let considered = 0
  const centreX = Math.floor(feet.x)
  const centreZ = Math.floor(feet.z)

  for (let z0 = centreZ - radius; z0 <= centreZ + radius - depth + 1; z0++) {
    for (let x0 = centreX - radius; x0 <= centreX + radius - width + 1; x0++) {
      considered++
      const grounds = []
      let reason = null
      for (let z = z0; z < z0 + depth && !reason; z++) {
        for (let x = x0; x < x0 + width && !reason; x++) {
          const column = columnAt(x, z)
          reason = column.reason
          grounds.push(column.ground)
        }
      }
      if (!reason && ringHasLiquid(columnAt, x0, z0, width, depth)) reason = 'liquid'
      if (!reason && overlapsProtected(protect, x0, z0, width, depth)) reason = 'protected'

      const floorY = reason ? null : lowerMedian(grounds) + 1
      if (!reason && grounds.some((ground) => ground > floorY - 1 + MAX_STEP || ground < floorY - 2)) reason = 'steep'
      if (reason) {
        ruledOut[reason]++
        continue
      }

      sites.push({
        origin: [x0, floorY, z0],
        cut: grounds.reduce((sum, ground) => sum + Math.max(0, ground - floorY + 1), 0),
        fill: grounds.reduce((sum, ground) => sum + Math.max(0, floorY - 1 - ground), 0),
        distance: Math.round(Math.hypot(x0 + width / 2 - feet.x, z0 + depth / 2 - feet.z) * 10) / 10
      })
    }
  }

  sites.sort((a, b) => (a.cut + a.fill) - (b.cut + b.fill) || a.distance - b.distance ||
    a.origin[0] - b.origin[0] || a.origin[2] - b.origin[2])
  return { sites: sites.slice(0, MAX_SITES), considered, ruled_out: ruledOut }
}

function lowerMedian (values) {
  return [...values].sort((a, b) => a - b)[Math.floor((values.length - 1) / 2)]
}

function ringHasLiquid (columnAt, x0, z0, width, depth) {
  for (let z = z0 - 1; z <= z0 + depth; z++) {
    for (let x = x0 - 1; x <= x0 + width; x++) {
      const onRing = x < x0 || x >= x0 + width || z < z0 || z >= z0 + depth
      if (onRing && columnAt(x, z).reason === 'liquid') return true
    }
  }
  return false
}

// Whether the footprint grown by one block on each side overlaps a box in x and z.
function overlapsProtected (protect, x0, z0, width, depth) {
  return protect.some(({ origin: [ox, , oz], size: [boxWidth, , boxDepth] }) =>
    x0 - 1 <= ox + boxWidth - 1 && x0 + width >= ox && z0 - 1 <= oz + boxDepth - 1 && z0 + depth >= oz)
}

function survey (bot, { size, radius = DEFAULT_RADIUS, protect = [] }) {
  const { x, y, z } = bot.entity.position
  const feetY = Math.floor(y)
  const blockAt = (bx, by, bz) => bot.blockAt(new Vec3(bx, by, bz))
  const columns = new Map()
  const columnAt = (cx, cz) => {
    const key = `${cx},${cz}`
    if (!columns.has(key)) columns.set(key, columnAround(blockAt, feetY, cx, cz))
    return columns.get(key)
  }
  return { ok: true, ...rankSites({ columnAt, feet: { x, z }, size, radius, protect }) }
}

module.exports = { rankSites, survey, validateSurvey }
