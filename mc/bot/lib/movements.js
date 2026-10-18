// Pathfinder settings for v2 requests that may dig and pillar (dig_through),
// and the goal that keeps the bot beside its target. v1 requests keep the
// world-preserving Movements built in server.js.

const { Movements, goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')

const MAX_DROP_DOWN = 3
const LIQUID_COST = 5 // wading is allowed but unattractive
const BLOCKED_COST = 100 // exclusionAreasBreak weight at which a block counts as unbreakable
const NEIGHBOUR_OFFSETS = [
  new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 1, 0),
  new Vec3(0, -1, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)
]
// Blocks that fall when unsupported (the pathfinder only knows sand).
const FALLING_BLOCKS = /^(sand|red_sand|gravel|suspicious_sand|suspicious_gravel|.*_concrete_powder|anvil|chipped_anvil|damaged_anvil)$/

// Tool tiers, cheapest first; an unknown material counts as the dearest.
const TOOL_TIERS = ['wooden', 'golden', 'stone', 'iron', 'diamond', 'netherite']
// A tool is worth using on a block the bare hand can also harvest only when it is
// at least this many times faster.
const WORTH_USING_SPEEDUP = 3
// A tool with this much durability left or less is kept: one more block would break it.
const MIN_DURABILITY_LEFT = 2

const toolTier = (item) => {
  const tier = TOOL_TIERS.indexOf(item.name.split('_')[0])
  return tier === -1 ? TOOL_TIERS.length : tier
}

// A replacement for the pathfinder's `bestHarvestTool` (which picks the fastest
// tool, wearing out the best pickaxe on dirt): for each block, the lowest-tier
// tool that still harvests it, or the bare hand when the block drops without a
// tool or no harvesting tool is at least WORTH_USING_SPEEDUP times faster.
// Never a tool that the dig would break. The pathfinder digs with whatever is
// held when this returns null, so the hand is an item that digs like one (a
// block), else nothing equipped.
function cheapestHarvestTool (bot) {
  const digTime = (block, item) => block.digTime(item ? item.type : null, false, false, false, [], bot.entity.effects)

  return (block) => {
    const items = bot.inventory.items()
    const durableTools = items.filter((item) => item.maxDurability && item.maxDurability - (item.durabilityUsed || 0) >= MIN_DURABILITY_LEFT)
    const handHarvests = Boolean(block.canHarvest(null))
    const handTime = digTime(block, null)
    const usable = durableTools
      .filter((tool) => block.canHarvest(tool.type))
      .filter((tool) => !handHarvests || handTime >= WORTH_USING_SPEEDUP * digTime(block, tool))
      .sort((a, b) => toolTier(a) - toolTier(b) || digTime(block, a) - digTime(block, b))
    if (usable.length > 0) return usable[0]

    const held = bot.heldItem
    if (!held || !held.maxDurability) return null // already bare-handed in effect
    const filler = items.find((item) => !item.maxDurability)
    if (filler) return filler
    bot.unequip('hand').catch(() => {})
    return null
  }
}

// Movements for a request with dig_through. On top of the pathfinder's own
// safety rules (dontCreateFlow, dontMineUnderFallingBlock, lava in
// blocksToAvoid) it:
//  - never breaks a block with lava or water on any of its six sides;
//  - never digs the block straight below the bot (no downward dig moves);
//  - never breaks `protectedBlock` (the step's own target) on the way;
//  - drops at most MAX_DROP_DOWN blocks;
//  - places only `scaffoldNames`: with none, it neither pillars nor bridges.
class DiggingMovements extends Movements {
  constructor (bot, { scaffoldNames = [], protectedBlock = null } = {}) {
    super(bot)
    this.canDig = true
    this.dontCreateFlow = true
    this.dontMineUnderFallingBlock = true
    this.maxDropDown = MAX_DROP_DOWN
    this.liquidCost = LIQUID_COST

    this.scafoldingBlocks = scaffoldNames
      .map((name) => bot.registry.itemsByName[name])
      .filter(Boolean)
      .map((item) => item.id)
    this.allow1by1towers = this.scafoldingBlocks.length > 0

    for (const block of bot.registry.blocksArray) {
      if (FALLING_BLOCKS.test(block.name)) this.gravityBlocks.add(block.id)
    }
    if (protectedBlock) {
      this.exclusionAreasBreak.push((block) => (block.position.equals(protectedBlock) ? BLOCKED_COST : 0))
    }
  }

  touchesLiquid (pos) {
    return NEIGHBOUR_OFFSETS.some((offset) => this.getBlock(pos, offset.x, offset.y, offset.z).liquid)
  }

  safeToBreak (block) {
    return !this.touchesLiquid(block.position) && super.safeToBreak(block)
  }

  // The stock move breaks the block below the bot to step down into it;
  // only the plain drop (nothing to break) is kept.
  getMoveDown (node, neighbors) {
    const before = neighbors.length
    super.getMoveDown(node, neighbors)
    if (neighbors.length > before && neighbors[before].toBreak.length > 0) neighbors.length = before
  }
}

// Ends beside `target` (face-adjacent to the bot's feet or head cell), never
// on top of it: a target under the bot's feet is reached from the side, so
// the dig never removes the bot's own floor.
class GoalBesideBlock extends goals.Goal {
  constructor (target) {
    super()
    this.target = target.clone()
  }

  heuristic (node) {
    const dx = node.x - this.target.x
    const dy = node.y - this.target.y
    const dz = node.z - this.target.z
    return Math.max(0, Math.hypot(dx, dz) + Math.abs(dy) - 1)
  }

  isEnd (node) {
    const dx = this.target.x - node.x
    const dz = this.target.z - node.z
    const touches = (dy) => Math.abs(dx) + Math.abs(dz) + Math.abs(dy) === 1
    const dyFromFeet = this.target.y - node.y
    const underFeet = dyFromFeet === -1 && dx === 0 && dz === 0
    return (touches(dyFromFeet) && !underFeet) || touches(dyFromFeet - 1)
  }
}

module.exports = { DiggingMovements, GoalBesideBlock, cheapestHarvestTool }
