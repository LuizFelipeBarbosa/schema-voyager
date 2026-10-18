// Open-sky test for POST /explore "up" and /observe: whether nothing solid
// lies above the bot in its column. Leaves and other non-solid blocks (grass,
// flowers, vines) don't count, so standing under a tree is open sky; water
// and lava overhead do. Read from block data, which every server provides
// (sky light is only sent for loaded chunks and is lower under leaves).

const { Vec3 } = require('vec3')

const FALLBACK_TOP_Y = 320
const LIQUIDS = new Set(['water', 'lava'])

function isOverhead (block) {
  return LIQUIDS.has(block.name) || (block.boundingBox !== 'empty' && !block.name.endsWith('_leaves'))
}

function worldTopY (bot) {
  return bot.game?.height != null ? bot.game.minY + bot.game.height : FALLBACK_TOP_Y
}

// True when no overhead block lies from the bot's head cell to the world top.
// An unloaded cell above counts as closed: the answer is unknown.
function hasOpenSky (bot) {
  const feet = bot.entity.position.floored()
  for (let y = feet.y + 1; y < worldTopY(bot); y++) {
    const block = bot.blockAt(new Vec3(feet.x, y, feet.z), false)
    if (!block || isOverhead(block)) return false
  }
  return true
}

// The y to stand on to be above the highest overhead block of the bot's column
// (the bot's own y when there is none).
function skyLevel (bot) {
  const feet = bot.entity.position.floored()
  for (let y = worldTopY(bot) - 1; y > feet.y; y--) {
    const block = bot.blockAt(new Vec3(feet.x, y, feet.z), false)
    if (block && isOverhead(block)) return y + 1
  }
  return feet.y
}

module.exports = { hasOpenSky, skyLevel }
