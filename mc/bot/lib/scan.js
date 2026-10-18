// POST /scan: the block names in a box, with the blueprint's axes. Never walks.
// See contracts/bot_api.md (v3.4).

const { Vec3 } = require('vec3')

const MAX_SIDE = 32
const MAX_CELLS = 8192

const isIntegerTriple = (value) => Array.isArray(value) && value.length === 3 && value.every(Number.isInteger)

function validateScan (body) {
  if (!isIntegerTriple(body.origin)) return '"origin" must be three integers'
  const { size } = body
  if (!isIntegerTriple(size) || size.some((side) => side < 1 || side > MAX_SIDE)) return `"size" must be three integers from 1 to ${MAX_SIDE}`
  return size[0] * size[1] * size[2] > MAX_CELLS ? `"size" must have at most ${MAX_CELLS} cells` : null
}

// layers[y][z][x] is the block name at origin + (x, y, z); null in a chunk that is not loaded.
function scan (bot, { origin, size }) {
  const [ox, oy, oz] = origin
  const [width, height, depth] = size
  const layers = Array.from({ length: height }, (_, y) =>
    Array.from({ length: depth }, (_, z) =>
      Array.from({ length: width }, (_, x) => bot.blockAt(new Vec3(ox + x, oy + y, oz + z))?.name ?? null)))
  return { ok: true, origin, size, layers }
}

module.exports = { scan, validateScan }
