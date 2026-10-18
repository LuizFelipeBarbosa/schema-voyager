// POST /place and POST /clear: put one block in a cell, or dig one cell. Both
// walk within reach first (never digging, never opening doors, scaffolding
// only with the request's blocks), check the cell, then act and read it back.
// See contracts/bot_api.md (v3.4).

const { Movements, goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const { watchServerBlockChange } = require('./blockchange')
const { sprintAllowed } = require('./movements')
const { insideAny, placeExclusion, validateProtect } = require('./protect')
const { hazardNear } = require('./targets')
const { emptyTravel, recordTravel } = require('./travel')
const { AIR_BLOCKS, emptyHand, findInventoryItem, gotoGoal, inventoryCounts, sleepTicks } = require('./world')

const REACH = 4.5
const SHAPELESS_REACH = 4 // GoalWithinReach: the eyes' distance to the cell's centre
const EYE_HEIGHT = 1.62
const PLAYER_HALF_WIDTH = 0.3
const HITBOX_EPSILON = 1e-3
const WALK_MS = 20000
const RECENTRE_TICKS = 20
const RECENTRE_DISTANCE = 0.1
const PLACE_TRIES = 3
const RETRY_TICKS = 5
const REFUSAL_POLL_TICKS = 2
const DOOR_UPPER_WAIT_TICKS = 20 // after a door's lower half: how long its upper half's block update is awaited
const REFUSAL_WAIT_TICKS = 20 // after a refused placement, how long a late block update is awaited
const CONFIRM_TIMEOUT_MS = 2000
const SETTLE_TICKS = 10 // after a dig: falling sand, flowing water and drops
const MAX_DIG_MS = 15000
const MIN_DURABILITY_LEFT = 2 // a tool with less is kept: one more block would break it
// exclusionAreasPlace weight: pathfinder drops a move once its cost passes 100
// (`if (cost > 100) return` after each placement), so this alone rules it out.
const KEEP_OUT_COST = 1000

const REPLACEABLE = new Set(['short_grass', 'tall_grass', 'fern', 'large_fern', 'dead_bush', 'snow'])
const LIQUIDS = new Set(['water', 'lava', 'bubble_column'])
// Faces that never count as support: they do something when used on, or have no full box.
const NOT_SUPPORT_PATTERN = /(_door|_trapdoor|_fence_gate|_bed|_sign|_button|shulker_box|anvil)$/
const NOT_SUPPORT = new Set([
  'lever', 'chest', 'trapped_chest', 'barrel', 'ender_chest', 'crafting_table', 'furnace', 'blast_furnace', 'smoker',
  'hopper', 'dispenser', 'dropper', 'enchanting_table', 'brewing_stand', 'loom', 'cartography_table',
  'smithing_table', 'grindstone', 'stonecutter', 'lectern', 'beacon', 'note_block'
])
// From the cell to its support: below (the support's top face), north, south, east, west, above.
const SUPPORT_DIRS = [new Vec3(0, -1, 0), new Vec3(0, 0, -1), new Vec3(0, 0, 1), new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 1, 0)]
const UP = new Vec3(0, 1, 0)

// --- Validation ------------------------------------------------------------

const isIntegerTriple = (value) => Array.isArray(value) && value.length === 3 && value.every(Number.isInteger)

function validateTarget (body) {
  if (!['x', 'y', 'z'].every((axis) => Number.isInteger(body[axis]))) return '"x", "y" and "z" must be integers'
  if (body.scaffold != null && !(Array.isArray(body.scaffold) && body.scaffold.every((name) => typeof name === 'string'))) {
    return '"scaffold" must be a list of strings'
  }
  const box = body.keep_out
  if (box != null && !(isIntegerTriple(box.origin) && isIntegerTriple(box.size) && box.size.every((side) => side > 0))) {
    return '"keep_out" must have an "origin" of three integers and a "size" of three positive integers'
  }
  return validateProtect(body.protect)
}

function validatePlace (body) {
  return typeof body.block === 'string' && body.block.length > 0 ? validateTarget(body) : '"block" must be a non-empty string'
}

const validateClear = validateTarget

// --- Pure rules ------------------------------------------------------------

const isFreeToPlace = (name) => AIR_BLOCKS.has(name) || REPLACEABLE.has(name)
const isTorch = (name) => name.endsWith('torch')
const isDoor = (name) => name.endsWith('_door')
// The name a torch has once it stands on a side: torch -> wall_torch.
const wallTorchName = (name) => name.replace(/torch$/, 'wall_torch')

function canSupport (block) {
  return block?.boundingBox === 'block' && !NOT_SUPPORT.has(block.name) && !NOT_SUPPORT_PATTERN.test(block.name)
}

// The directions from the cell at `pos` to the blocks that can hold `blockName`,
// in the order they are tried. A torch takes only the one below or a side one,
// and a door only the one below.
function supportFaces (bot, pos, blockName) {
  const allowed = isDoor(blockName) ? SUPPORT_DIRS.slice(0, 1) : isTorch(blockName) ? SUPPORT_DIRS.slice(0, 5) : SUPPORT_DIRS
  return allowed.filter((dir) => canSupport(bot.blockAt(pos.plus(dir))))
}

// Names to scaffold with: the requested ones the bot holds, never `block` itself.
function scaffoldNamesFor (bot, scaffold, block = null) {
  const held = inventoryCounts(bot)
  return scaffold.filter((name) => name !== block && held[name] > 0)
}

const insideBox = (box, cell) => insideAny([box], cell)

// True if a bot with its feet in `cell` stands on top of the box: inside its
// x and z footprint, at or above its top. It could not get down without digging the roof.
function standsOnTopOf (box, cell) {
  const [ox, oy, oz] = box.origin
  const [width, height, depth] = box.size
  return cell.y >= oy + height && cell.x >= ox && cell.x < ox + width && cell.z >= oz && cell.z < oz + depth
}

// exclusionAreasPlace entry: scaffold inside the box costs KEEP_OUT_COST.
const keepOutExclusion = (box) => (block) => (insideBox(box, block.position) ? KEEP_OUT_COST : 0)

// What /place can say about the cell before and after the walk: `alreadyThere`
// (the cell's name), `error` (with `found`), or `faces`. Empty if the cell's
// chunk isn't loaded (only the inventory is checked then).
function checkPlace (bot, pos, blockName) {
  const cell = bot.blockAt(pos)
  if (cell && (cell.name === blockName || (isTorch(blockName) && cell.name === wallTorchName(blockName)))) {
    return { alreadyThere: cell.name }
  }
  if (!findInventoryItem(bot, blockName)) return { error: 'missing_item' }
  if (!cell) return {}
  if (!isFreeToPlace(cell.name)) return { error: 'occupied', found: cell.name }
  const above = isDoor(blockName) ? bot.blockAt(pos.plus(UP)) : null
  if (above && !isFreeToPlace(above.name)) return { error: 'occupied', found: above.name }
  const faces = supportFaces(bot, pos, blockName)
  return faces.length === 0 ? { error: 'no_support' } : { faces }
}

// What /clear can say about the cell: `alreadyClear`, or `error` ('unbreakable'
// or 'hazard', with `found`).
function checkClear (bot, pos) {
  const block = bot.blockAt(pos)
  if (!block) return {}
  if (AIR_BLOCKS.has(block.name)) return { alreadyClear: true }
  const unbreakable = block.hardness == null || block.hardness < 0 || block.diggable === false
  if (unbreakable || LIQUIDS.has(block.name)) return { error: 'unbreakable', found: block.name }
  if (hazardNear(bot, pos)) return { error: 'hazard', found: block.name }
  return {}
}

// The tool (null: the bare hand) that digs `block` fastest, with the time in ms.
function fastestTool (bot, block) {
  const digMs = (item) => block.digTime(item ? item.type : null, false, false, false, [], bot.entity.effects)
  const tools = bot.inventory.items().filter((item) => item.maxDurability && item.maxDurability - (item.durabilityUsed || 0) >= MIN_DURABILITY_LEFT)
  return [null, ...tools].map((tool) => ({ tool, ms: digMs(tool) })).reduce((best, next) => (next.ms < best.ms ? next : best))
}

// --- Walking ---------------------------------------------------------------

// Wraps a pathfinder goal so the walk never ends with the bot's feet or head
// in `cells`, on top of `floor` (the cell the bot would stand on), inside `avoidBox`,
// or on top of `roofBox`.
class StanceGoal extends goals.Goal {
  constructor (inner, { cells, floor = null, avoidBox = null, roofBox = null }) {
    super()
    Object.assign(this, { inner, cells, floor, avoidBox, roofBox })
  }

  heuristic (node) {
    return this.inner.heuristic(node)
  }

  // True if a bot with its feet in the cell `node` stands where this goal forbids.
  blocksStance (node) {
    const body = [node, node.plus(UP)]
    return body.some((cell) => this.cells.some((target) => target.equals(cell))) ||
      Boolean(this.floor?.equals(node.offset(0, -1, 0))) ||
      Boolean(this.avoidBox && body.some((cell) => insideBox(this.avoidBox, cell))) ||
      Boolean(this.roofBox && standsOnTopOf(this.roofBox, node))
  }

  isEnd (node) {
    return !this.blocksStance(node) && this.inner.isEnd(node)
  }
}

// Reach-only goal for a block with no collision shape (torch, grass, flower),
// which GoalLookAtBlock's raycast can never hit: any stance whose eyes are within `reach` of the cell's centre.
class GoalWithinReach extends goals.Goal {
  constructor (pos, reach) {
    super()
    Object.assign(this, { centre: pos.offset(0.5, 0.5, 0.5), reach })
  }

  eyeDistance (node) {
    return this.centre.distanceTo(node.offset(0.5, EYE_HEIGHT, 0.5))
  }

  heuristic (node) {
    return Math.max(0, this.eyeDistance(node) - this.reach)
  }

  isEnd (node) {
    return this.eyeDistance(node) <= this.reach
  }
}

// Runs `fn` with world-preserving movements that may scaffold with `scaffoldNames`
// (and, with `keepOut` or `protect`, never inside them); restores the previous movements.
async function withBuildMovements (bot, { scaffoldNames, keepOut, protect }, fn) {
  const previous = bot.pathfinder.movements
  const movements = new Movements(bot)
  movements.canDig = false
  movements.canOpenDoors = false
  movements.allowSprinting = sprintAllowed(bot)
  movements.scafoldingBlocks = scaffoldNames.map((name) => bot.registry.itemsByName[name]?.id).filter((id) => id !== undefined)
  movements.allow1by1towers = movements.scafoldingBlocks.length > 0
  if (keepOut) movements.exclusionAreasPlace.push(keepOutExclusion(keepOut))
  if (protect.length > 0) movements.exclusionAreasPlace.push(placeExclusion(protect))
  bot.pathfinder.setMovements(movements)
  try {
    return await fn()
  } finally {
    bot.pathfinder.setMovements(previous)
  }
}

// Walks to `goal` and counts what the pathfinder did on the way. Returns {reached, travel}.
async function walk (bot, episode, goal, { scaffoldNames, keepOut, protect, signal }) {
  const recording = recordTravel(bot, episode)
  try {
    const reached = await withBuildMovements(bot, { scaffoldNames, keepOut, protect }, () => gotoGoal(bot, goal, WALK_MS, { signal }))
    await recording.settle()
    return { reached, travel: recording.finish() }
  } catch (err) {
    recording.finish()
    throw err
  }
}

// Walks to the middle of the cell the bot's feet are in, to get its hitbox off the neighbouring cells.
// Sneaking is slow and never walks off an edge. If it runs out of ticks the caller re-checks.
async function recentre (bot, { signal }) {
  const centre = bot.entity.position.floored().offset(0.5, 0, 0.5)
  try {
    for (let tick = 0; tick < RECENTRE_TICKS; tick++) {
      const { x, y, z } = bot.entity.position
      if (Math.hypot(centre.x - x, centre.z - z) < RECENTRE_DISTANCE) return
      signal?.throwIfAborted()
      await bot.lookAt(new Vec3(centre.x, y + bot.entity.eyeHeight, centre.z), true)
      bot.setControlState('sneak', true)
      bot.setControlState('forward', true)
      await sleepTicks(1)
    }
  } finally {
    bot.setControlState('forward', false)
    bot.setControlState('sneak', false)
  }
}

// True if the bot's real hitbox (pathfinder snaps it to a cell's centre after a walk) reaches into `cell`.
function hitboxOverlaps (bot, cell) {
  const { x, z } = bot.entity.position
  const halfWidth = (typeof bot.entity.width === 'number' ? bot.entity.width / 2 : PLAYER_HALF_WIDTH) + HITBOX_EPSILON
  return cell.x < x + halfWidth && cell.x + 1 > x - halfWidth &&
    cell.z < z + halfWidth && cell.z + 1 > z - halfWidth
}

// --- /place ----------------------------------------------------------------

// Waits for the server's answer to a placement mineflayer gave up on: the
// cell turning into something other than free.
async function waitUntilNotFree (bot, pos) {
  for (let waited = 0; waited < REFUSAL_WAIT_TICKS; waited += REFUSAL_POLL_TICKS) {
    if (!isFreeToPlace(bot.blockAt(pos)?.name)) return
    await sleepTicks(REFUSAL_POLL_TICKS)
  }
}

// The server sets a door's upper half right after the lower one, but its block
// update reaches the bot a few ticks later: waits for the cell above to read as `doorName`.
async function waitForUpperHalf (bot, pos, doorName) {
  for (let waited = 0; waited < DOOR_UPPER_WAIT_TICKS; waited += REFUSAL_POLL_TICKS) {
    if (bot.blockAt(pos.plus(UP))?.name === doorName) return
    await sleepTicks(REFUSAL_POLL_TICKS)
  }
}

// Tries to place `blockName` at `pos`, against the next support face on each
// try. Returns {placed, found}: `found` is the cell read back.
async function placeAgainstSupport (bot, blockName, pos, faces, signal) {
  let found = null
  for (let attempt = 0; attempt < PLACE_TRIES; attempt++) {
    signal?.throwIfAborted()
    const toSupport = faces[attempt % faces.length]
    try {
      await bot.equip(findInventoryItem(bot, blockName), 'hand')
      await bot.placeBlock(bot.blockAt(pos.plus(toSupport)), toSupport.scaled(-1))
    } catch (err) {
      if (signal?.aborted) throw signal.reason
      await waitUntilNotFree(bot, pos)
    }
    found = bot.blockAt(pos)?.name ?? null
    if (found !== null && !isFreeToPlace(found)) return { placed: true, found }
    signal?.throwIfAborted()
    await sleepTicks(RETRY_TICKS)
  }
  return { placed: false, found }
}

// `session`: {episode, tracker, signal}. An abort throws `signal.reason`.
async function place (bot, body, { episode, tracker, signal = null }) {
  signal?.throwIfAborted()
  const { block: blockName, keep_out: keepOut = null, scaffold = [], protect = [] } = body
  const pos = new Vec3(body.x, body.y, body.z)
  const result = { block: blockName, travel: emptyTravel(), inventory_before_step: inventoryCounts(bot) }
  const finish = (error, extra = {}) => ({ ok: !error, error: error || null, ...result, ...extra, inventory: inventoryCounts(bot) })
  const respondTo = (check) => {
    if (check.alreadyThere) return finish(null, { placed: false, found: check.alreadyThere })
    if (check.error) return finish(check.error, check.found ? { found: check.found } : {})
    return null
  }

  const before = checkPlace(bot, pos, blockName)
  const early = respondTo(before)
  if (early) return early

  const cells = isDoor(blockName) ? [pos, pos.plus(UP)] : [pos]
  const reach = new goals.GoalPlaceBlock(pos, bot.world, { range: REACH, ...(before.faces && { faces: before.faces }) })
  const goal = new StanceGoal(reach, { cells, avoidBox: isDoor(blockName) ? keepOut : null, roofBox: keepOut })
  const walked = await walk(bot, episode, goal, { scaffoldNames: scaffoldNamesFor(bot, scaffold, blockName), keepOut, protect, signal })
  result.travel = walked.travel
  // gotoGoal also accepts a goal met one cell above the feet, so the real stance is checked too.
  if (!walked.reached || goal.blocksStance(bot.entity.position.floored())) return finish('unreachable')

  signal?.throwIfAborted()
  result.inventory_before_step = inventoryCounts(bot)
  tracker.log.setPhase('step')
  const arrival = checkPlace(bot, pos, blockName)
  const late = respondTo(arrival)
  if (late) return late
  if (!arrival.faces) return finish('unreachable', { detail: 'the target cell is not loaded' })
  // A solid block can't go where the bot stands; the walk avoids it, this is the backstop.
  await sleepTicks(1) // the stopped position reaches the server
  const overlapsCells = () => {
    const feetY = Math.floor(bot.entity.position.y)
    return !isTorch(blockName) && cells.some((cell) => (cell.y === feetY || cell.y === feetY + 1) && hitboxOverlaps(bot, cell))
  }
  if (overlapsCells()) {
    // The walk counts whole cells, so it can end off-centre with the hitbox reaching into the target.
    await recentre(bot, { signal })
    if (overlapsCells()) return finish('unreachable', { detail: 'standing in the target cell' })
  }

  const { placed, found } = await placeAgainstSupport(bot, blockName, pos, arrival.faces, signal)
  if (!placed) return finish('place_failed', { found })
  if (isDoor(blockName)) await waitForUpperHalf(bot, pos, found)
  return finish(null, { placed: true, found })
}

// --- /clear ----------------------------------------------------------------

async function clear (bot, body, { episode, tracker, signal = null }) {
  signal?.throwIfAborted()
  const { keep_out: keepOut = null, scaffold = [], protect = [] } = body
  const pos = new Vec3(body.x, body.y, body.z)
  const result = { travel: emptyTravel(), inventory_before_step: inventoryCounts(bot) }
  const finish = (error, extra = {}) => ({ ok: !error, error: error || null, ...result, ...extra, inventory: inventoryCounts(bot) })
  const respondTo = (check) => {
    if (check.alreadyClear) return finish(null, { cleared: false, found: 'air' })
    if (check.error) return finish(check.error, { found: check.found })
    return null
  }

  const early = respondTo(checkClear(bot, pos))
  if (early) return early

  // A door goes as a whole, so the walk keeps clear of both halves.
  const target = bot.blockAt(pos)
  const cells = target && isDoor(target.name) ? [pos, pos.offset(0, target.getProperties().half === 'upper' ? -1 : 1, 0)] : [pos]
  const hasShape = !target || target.shapes.length > 0
  const reach = hasShape ? new goals.GoalLookAtBlock(pos, bot.world, { reach: REACH }) : new GoalWithinReach(pos, SHAPELESS_REACH)
  const goal = new StanceGoal(reach, { cells, floor: pos, avoidBox: cells.length > 1 ? keepOut : null, roofBox: keepOut })
  const walked = await walk(bot, episode, goal, { scaffoldNames: scaffoldNamesFor(bot, scaffold), keepOut, protect, signal })
  result.travel = walked.travel
  if (!walked.reached || goal.blocksStance(bot.entity.position.floored())) return finish('unreachable')

  signal?.throwIfAborted()
  result.inventory_before_step = inventoryCounts(bot)
  tracker.log.setPhase('step')
  const late = respondTo(checkClear(bot, pos))
  if (late) return late
  const block = bot.blockAt(pos)
  if (!block) return finish('unreachable', { detail: 'the target cell is not loaded' })

  const { tool, ms } = fastestTool(bot, block)
  if (ms > MAX_DIG_MS) return finish('missing_tool', { found: block.name })
  const confirmation = watchServerBlockChange(bot, pos, block.name)
  try {
    if (tool) await bot.equip(tool, 'hand')
    else await emptyHand(bot)
    await bot.dig(block, true)
  } catch (err) {
    confirmation.stop()
    if (signal?.aborted) throw signal.reason
    return finish('dig_failed', { detail: err.message, found: bot.blockAt(pos)?.name ?? null })
  }
  const confirmed = await confirmation.waitForConfirmation(CONFIRM_TIMEOUT_MS)
  confirmation.stop()
  await sleepTicks(SETTLE_TICKS)
  const found = bot.blockAt(pos)?.name ?? null
  if (!confirmed) return finish('dig_failed', { detail: 'no server block update within 2 s', found })
  return finish(null, { cleared: true, removed: block.name, found })
}

module.exports = {
  StanceGoal, checkClear, checkPlace, clear, fastestTool, isFreeToPlace, keepOutExclusion, place, scaffoldNamesFor,
  supportFaces, validateClear, validatePlace
}
