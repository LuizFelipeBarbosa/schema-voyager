// Pure geometry helpers: compass directions, sectors, and cell tests.

const DIRECTIONS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
const SECTOR_DEGREES = 360 / DIRECTIONS.length

// Compass direction of an offset; N is centred on -z and E is +x.
function sectorOf (dx, dz) {
  const degrees = (Math.atan2(dx, 0 - dz) * 180) / Math.PI
  const shifted = (degrees + SECTOR_DEGREES / 2 + 360) % 360
  return DIRECTIONS[Math.floor(shifted / SECTOR_DEGREES)]
}

// Unit {x, z} vector for a direction name, or null for an unknown name.
function directionVector (name) {
  const index = DIRECTIONS.indexOf(name)
  if (index < 0) return null
  const radians = (index * SECTOR_DEGREES * Math.PI) / 180
  const clean = (value) => Math.round(value * 1e6) / 1e6 + 0 // +0 turns -0 into 0
  return { x: clean(Math.sin(radians)), z: clean(-Math.cos(radians)) }
}

// True when `point` lies inside the 1x1x1 box of the block at `cell`, grown by
// `margin` on every side.
function isInCell (point, cell, margin = 0.25) {
  return point.x >= cell.x - margin && point.x <= cell.x + 1 + margin &&
    point.y >= cell.y - margin && point.y <= cell.y + 1 + margin &&
    point.z >= cell.z - margin && point.z <= cell.z + 1 + margin
}

// True when two block positions are at most `radius` apart on every axis.
function withinBox (a, b, radius) {
  return Math.abs(a.x - b.x) <= radius && Math.abs(a.y - b.y) <= radius && Math.abs(a.z - b.z) <= radius
}

function horizontalDistance (a, b) {
  return Math.hypot(a.x - b.x, a.z - b.z)
}

module.exports = { DIRECTIONS, directionVector, horizontalDistance, isInCell, sectorOf, withinBox }
