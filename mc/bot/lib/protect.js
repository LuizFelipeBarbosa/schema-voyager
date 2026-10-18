// Protected boxes: what the bot built must survive the next goal. A request's
// `protect` is a list of {origin: [x, y, z], size: [w, h, d]} boxes; the bot
// keeps none of it between requests. See contracts/bot_api.md (v3.4b).

const MAX_PROTECT_BOXES = 32
// Exclusion weight for a block inside a box. The pathfinder refuses a dig at
// 100 or more and drops a move once its cost passes 100, so this alone rules both out.
const PROTECTED_COST = 1000

const isIntegerTriple = (value) => Array.isArray(value) && value.length === 3 && value.every(Number.isInteger)

// Shared by every route that takes `protect`. Returns the 400 message, or null.
function validateProtect (protect) {
  if (protect == null) return null
  const valid = Array.isArray(protect) && protect.length <= MAX_PROTECT_BOXES && protect.every((box) =>
    box && isIntegerTriple(box.origin) && isIntegerTriple(box.size) && box.size.every((side) => side > 0))
  return valid ? null : `"protect" must be a list of at most ${MAX_PROTECT_BOXES} boxes {origin: three integers, size: three positive integers}`
}

// True if the cell `pos` ({x, y, z}) is in any box: origin <= cell < origin + size on each axis.
function insideAny (boxes, pos) {
  return boxes.some((box) => [pos.x, pos.y, pos.z].every((value, axis) => {
    const offset = value - box.origin[axis]
    return offset >= 0 && offset < box.size[axis]
  }))
}

// exclusionAreasBreak entry: a block inside a box is never broken.
const breakExclusion = (boxes) => (block) => (insideAny(boxes, block.position) ? PROTECTED_COST : 0)

// exclusionAreasPlace entry: scaffold is never placed inside a box.
const placeExclusion = (boxes) => (block) => (insideAny(boxes, block.position) ? PROTECTED_COST : 0)

module.exports = { breakExclusion, insideAny, placeExclusion, validateProtect }
