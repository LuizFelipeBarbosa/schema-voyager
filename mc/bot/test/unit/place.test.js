const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const { Movements, goals } = require('mineflayer-pathfinder')
const {
  StanceGoal, checkClear, checkPlace, clear, fastestTool, keepOutExclusion, place, scaffoldNamesFor, supportFaces,
  validateClear, validatePlace
} = require('../../lib/place')
const { fakeWorldBot, registry } = require('./world_fixture')

const itemType = (name) => registry.itemsByName[name].id
function stack (name, count = 1) {
  const item = registry.itemsByName[name]
  return { name, type: item.id, count, maxDurability: item.maxDurability || null, durabilityUsed: 0 }
}

// The first solid block along a ray, with the index of the face entered (0 down, 1 up, 2 north, 3 south, 4 west, 5 east).
function marchRay (bot, from, direction, range) {
  const faceOf = [[0, -1, 0], [0, 1, 0], [0, 0, -1], [0, 0, 1], [-1, 0, 0], [1, 0, 0]]
  let previous = from.floored()
  for (let t = 0.02; t <= range; t += 0.02) {
    const cell = from.plus(direction.scaled(t)).floored()
    if (cell.equals(previous)) continue
    const block = bot.blockAt(cell)
    if (block.boundingBox === 'block') {
      const entered = previous.minus(cell)
      return Object.assign(block, { face: faceOf.findIndex(([x, y, z]) => x === entered.x && y === entered.y && z === entered.z) })
    }
    previous = cell
  }
  return null
}

// A fake bot standing at (0.5, 10, 0.5) on a stone floor at y = 9, holding `inventory`
// (name -> count). `blocks` is the live world; placeBlock/dig/equip/goto are stubs.
function buildBot (inventory = {}, extra = {}) {
  const blocks = { ...extra }
  for (let x = -4; x <= 4; x++) for (let z = -4; z <= 4; z++) blocks[`${x},9,${z}`] ??= 'stone'
  const bot = fakeWorldBot(blocks)
  const slots = new Array(46).fill(null)
  Object.entries(inventory).forEach(([name, count], i) => { slots[36 + i] = stack(name, count) })
  bot.blocks = blocks
  bot.inventory = { slots, items: () => slots.filter(Boolean), on () {}, removeListener () {} }
  bot.heldItem = null
  bot.entity.eyeHeight = 1.62
  Object.assign(bot, { clearControlStates () {}, setControlState () {}, lookAt: async () => {}, on () {}, removeListener () {}, dig: async () => {}, equip: async () => {} })
  bot._client = new EventEmitter()
  bot.world.getBlock = (pos) => bot.blockAt(pos)
  bot.world.raycast = (from, direction, range) => marchRay(bot, from, direction, range)
  bot.pathfinder = { movements: 'previous', setMovements (m) { this.movements = m }, goto: async () => {}, setGoal () {}, stop () {}, bestHarvestTool: () => null }
  bot.placeCalls = []
  bot.placeBlock = async (ref, face) => {
    bot.placeCalls.push([ref.position.toString(), face.toString()])
    const at = ref.position.plus(face)
    blocks[`${at.x},${at.y},${at.z}`] = bot.placeAs
  }
  return bot
}

const newSession = (extra = {}) => ({ episode: { walked: 0 }, tracker: { log: { setPhase (phase) { this.phase = phase } } }, ...extra })
const request = (extra = {}) => ({ block: 'oak_planks', x: 2, y: 10, z: 0, ...extra })

test('validatePlace and validateClear check the block, the integer cell, scaffold and keep_out', () => {
  const keepOut = { origin: [8, 64, -5], size: [7, 4, 5] }
  assert.equal(validatePlace(request()), null)
  assert.equal(validatePlace(request({ scaffold: ['dirt'], keep_out: keepOut })), null)
  assert.equal(validatePlace(request({ scaffold: null, keep_out: null })), null)
  assert.equal(validateClear({ x: 1, y: 2, z: 3, scaffold: [], keep_out: keepOut }), null)
  const bad = [
    request({ block: '' }), request({ block: 3 }), { x: 1, y: 2, z: 3 }, request({ x: 1.5 }), request({ y: '2' }),
    request({ z: undefined }), request({ scaffold: 'dirt' }), request({ scaffold: [1] }),
    request({ keep_out: { origin: [0, 0], size: [1, 1, 1] } }), request({ keep_out: { origin: [0, 0, 0], size: [1, 0, 1] } }),
    request({ keep_out: { origin: [0, 0, 0.5], size: [1, 1, 1] } }), request({ keep_out: {} })
  ]
  for (const body of bad) assert.equal(typeof validatePlace(body), 'string', JSON.stringify(body))
  assert.equal(typeof validateClear({ x: 1, y: 2 }), 'string')
  assert.equal(typeof validateClear({ x: 1, y: 2, z: 3, scaffold: [3] }), 'string')
})

test('supportFaces tries below, north, south, east, west, above', () => {
  const bot = fakeWorldBot({
    '0,0,0': 'stone', '0,-1,0': 'stone', '0,1,0': 'stone', '0,0,-1': 'stone', '0,0,1': 'stone', '1,0,0': 'stone', '-1,0,0': 'stone'
  })
  const cell = new Vec3(0, 0, 0)
  bot.blockAt = ((blockAt) => (pos) => (pos.equals(cell) ? blockAt(new Vec3(9, 9, 9)) : blockAt(pos)))(bot.blockAt)
  const names = (blockName) => supportFaces(bot, cell, blockName).map((dir) => dir.toString())
  assert.deepEqual(names('oak_planks'), ['(0, -1, 0)', '(0, 0, -1)', '(0, 0, 1)', '(1, 0, 0)', '(-1, 0, 0)', '(0, 1, 0)'])
  assert.deepEqual(names('torch'), ['(0, -1, 0)', '(0, 0, -1)', '(0, 0, 1)', '(1, 0, 0)', '(-1, 0, 0)'])
  assert.deepEqual(names('oak_door'), ['(0, -1, 0)'])
})

test('supportFaces skips doors, trapdoors, gates, beds, signs, buttons, levers, windows and partial blocks', () => {
  const notSupport = [
    'oak_door', 'oak_trapdoor', 'oak_fence_gate', 'red_bed', 'oak_sign', 'oak_wall_sign', 'stone_button', 'lever', 'chest',
    'trapped_chest', 'barrel', 'ender_chest', 'white_shulker_box', 'shulker_box', 'crafting_table', 'furnace', 'blast_furnace',
    'smoker', 'hopper', 'dispenser', 'dropper', 'anvil', 'chipped_anvil', 'enchanting_table', 'brewing_stand', 'loom',
    'cartography_table', 'smithing_table', 'grindstone', 'stonecutter', 'lectern', 'beacon', 'note_block',
    'torch', 'short_grass'
  ]
  for (const name of notSupport) {
    const bot = fakeWorldBot({ '0,-1,0': name })
    assert.deepEqual(supportFaces(bot, new Vec3(0, 0, 0), 'oak_planks'), [], name)
  }
  const bot = fakeWorldBot({ '0,-1,0': 'dirt', '0,0,1': 'chest', '1,0,0': 'oak_planks' })
  assert.deepEqual(supportFaces(bot, new Vec3(0, 0, 0), 'stone').map(String), ['(0, -1, 0)', '(1, 0, 0)'])
})

test('scaffoldNamesFor keeps the held scaffold names and never the placed block', () => {
  const bot = buildBot({ dirt: 10, cobbled_deepslate: 3, oak_planks: 5 })
  assert.deepEqual(scaffoldNamesFor(bot, ['dirt', 'cobbled_deepslate', 'cobblestone'], 'cobbled_deepslate'), ['dirt'])
  assert.deepEqual(scaffoldNamesFor(bot, ['dirt', 'cobbled_deepslate', 'cobblestone']), ['dirt', 'cobbled_deepslate'])
  assert.deepEqual(scaffoldNamesFor(bot, []), [])
})

test('keepOutExclusion rules scaffold out inside the box and allows it outside', () => {
  const exclusion = keepOutExclusion({ origin: [8, 64, -5], size: [7, 4, 5] })
  const at = (x, y, z) => exclusion({ position: new Vec3(x, y, z) })
  assert.ok(at(8, 64, -5) > 100)
  assert.ok(at(14, 67, -1) > 100)
  assert.equal(at(15, 64, -5), 0)
  assert.equal(at(7, 64, -5), 0)
  assert.equal(at(8, 68, -5), 0)
  assert.equal(at(8, 63, -5), 0)
  assert.equal(at(8, 64, 0), 0)
})

test('StanceGoal refuses standing in the target cells, on the floor cell, or inside the avoid box', () => {
  const inner = { heuristic: () => 7, isEnd: () => true }
  const target = new Vec3(2, 10, 0)
  const door = new StanceGoal(inner, { cells: [target, target.offset(0, 1, 0)], avoidBox: { origin: [0, 10, 0], size: [3, 3, 3] } })
  assert.equal(door.heuristic(new Vec3(0, 0, 0)), 7)
  assert.equal(door.isEnd(target), false)
  assert.equal(door.isEnd(target.offset(0, -1, 0)), false) // its head would be in the cell
  assert.equal(door.isEnd(target.offset(0, 1, 0)), false)
  assert.equal(door.isEnd(new Vec3(1, 10, 0)), false) // in the box
  assert.equal(door.isEnd(new Vec3(3, 10, 0)), true)
  const plain = new StanceGoal(inner, { cells: [target] })
  assert.equal(plain.isEnd(new Vec3(1, 10, 0)), true)
  const fromAbove = new StanceGoal(inner, { cells: [target], floor: target })
  assert.equal(fromAbove.isEnd(target.offset(0, 1, 0)), false) // standing on the target
  assert.equal(fromAbove.isEnd(target.offset(1, 1, 0)), true)
  assert.equal(new StanceGoal({ ...inner, isEnd: () => false }, { cells: [target] }).isEnd(new Vec3(5, 10, 5)), false)
})

test('checkPlace order: already there, missing item, occupied, no support', () => {
  const pos = new Vec3(2, 10, 0)
  assert.deepEqual(checkPlace(buildBot({ oak_planks: 1 }, { '2,10,0': 'oak_planks' }), pos, 'oak_planks'), { alreadyThere: 'oak_planks' })
  assert.deepEqual(checkPlace(buildBot({}, { '2,10,0': 'oak_planks' }), pos, 'oak_planks'), { alreadyThere: 'oak_planks' })
  assert.deepEqual(checkPlace(buildBot({}), pos, 'oak_planks'), { error: 'missing_item' })
  assert.deepEqual(checkPlace(buildBot({ oak_planks: 1 }, { '2,10,0': 'dirt' }), pos, 'oak_planks'), { error: 'occupied', found: 'dirt' })
  assert.deepEqual(checkPlace(buildBot({ oak_planks: 1 }), new Vec3(2, 30, 0), 'oak_planks'), { error: 'no_support' })
  assert.deepEqual(checkPlace(buildBot({ oak_planks: 1 }), pos, 'oak_planks').faces.map(String), ['(0, -1, 0)'])
})

test('checkPlace treats the replaceable plants as free, a wall_torch as a torch, and a door needs the cell above', () => {
  const pos = new Vec3(2, 10, 0)
  for (const plant of ['short_grass', 'tall_grass', 'fern', 'large_fern', 'dead_bush', 'snow']) {
    assert.ok(checkPlace(buildBot({ oak_planks: 1 }, { '2,10,0': plant }), pos, 'oak_planks').faces, plant)
  }
  assert.deepEqual(checkPlace(buildBot({}, { '2,10,0': 'wall_torch' }), pos, 'torch'), { alreadyThere: 'wall_torch' })
  assert.deepEqual(checkPlace(buildBot({}, { '2,10,0': 'oak_planks' }), pos, 'torch'), { error: 'missing_item' })
  assert.deepEqual(checkPlace(buildBot({ oak_door: 1 }, { '2,11,0': 'stone' }), pos, 'oak_door'), { error: 'occupied', found: 'stone' })
  assert.deepEqual(checkPlace(buildBot({ oak_door: 1 }), pos, 'oak_door').faces.map(String), ['(0, -1, 0)'])
  assert.deepEqual(checkPlace(buildBot({ oak_door: 1 }, { '2,9,0': 'air' }), pos, 'oak_door'), { error: 'no_support' })
})

test('checkPlace skips the cell checks while its chunk is not loaded', () => {
  const bot = buildBot({ oak_planks: 1 })
  bot.blockAt = () => null
  assert.deepEqual(checkPlace(bot, new Vec3(2, 10, 0), 'oak_planks'), {})
  assert.deepEqual(checkPlace(buildBot({}), new Vec3(2, 10, 0), 'oak_planks').error, 'missing_item')
})

test('/place already there: ok, placed false, no walk, nothing spent', async () => {
  const bot = buildBot({ oak_planks: 5 }, { '2,10,0': 'oak_planks' })
  let walks = 0
  bot.pathfinder.goto = async () => { walks++ }
  const result = await place(bot, request(), newSession())
  assert.equal(walks, 0)
  assert.equal(bot.placeCalls.length, 0)
  assert.deepEqual({ ok: result.ok, placed: result.placed, found: result.found, inventory: result.inventory }, { ok: true, placed: false, found: 'oak_planks', inventory: { oak_planks: 5 } })
  assert.deepEqual(result.travel, { distance: 0, dug: {}, placed: {}, picked_up: {}, placed_cells: [] })
})

test('/place errors before the walk: missing_item, occupied with found, no_support', async () => {
  let bot = buildBot({})
  assert.equal((await place(bot, request(), newSession())).error, 'missing_item')
  bot = buildBot({ oak_planks: 1 }, { '2,10,0': 'dirt' })
  const occupied = await place(bot, request(), newSession())
  assert.deepEqual([occupied.ok, occupied.error, occupied.found], [false, 'occupied', 'dirt'])
  bot = buildBot({ oak_planks: 1 })
  const floating = await place(bot, request({ y: 30 }), newSession())
  assert.equal(floating.error, 'no_support')
  assert.ok(floating.travel)
})

test('/place walks, places against the block below and answers with the shapes', async () => {
  const bot = buildBot({ oak_planks: 5 })
  bot.placeAs = 'oak_planks'
  const session = newSession()
  const goals_ = []
  bot.pathfinder.goto = async (goal) => {
    goals_.push(goal)
    bot.entity.position = new Vec3(0.5, 10, 0.5)
  }
  const result = await place(bot, request({ scaffold: ['dirt'] }), session)
  assert.deepEqual(bot.placeCalls, [['(2, 9, 0)', '(0, 1, 0)']])
  assert.deepEqual(
    { ok: result.ok, error: result.error, placed: result.placed, block: result.block, found: result.found },
    { ok: true, error: null, placed: true, block: 'oak_planks', found: 'oak_planks' }
  )
  assert.deepEqual(result.inventory_before_step, { oak_planks: 5 })
  assert.equal(result.travel.distance, 0)
  assert.equal(session.tracker.log.phase, 'step')
  assert.ok(goals_[0] instanceof StanceGoal)
  assert.ok(goals_[0].inner instanceof goals.GoalPlaceBlock)
  assert.equal(goals_[0].inner.options.range, 4.5)
  assert.equal(bot.pathfinder.movements, 'previous')
})

test('/place walks with build movements: no digging, no doors, scaffold minus the block, keep_out excluded', async () => {
  const bot = buildBot({ cobbled_deepslate: 5, dirt: 9, oak_planks: 1 })
  bot.placeAs = 'cobbled_deepslate'
  let seen = null
  bot.pathfinder.goto = async () => { seen = bot.pathfinder.movements }
  const keepOut = { origin: [1, 10, -1], size: [3, 3, 3] }
  await place(bot, request({ block: 'cobbled_deepslate', scaffold: ['dirt', 'cobbled_deepslate', 'cobblestone'], keep_out: keepOut }), newSession())
  assert.ok(seen instanceof Movements)
  assert.equal(seen.canDig, false)
  assert.equal(seen.canOpenDoors, false)
  assert.deepEqual(seen.scafoldingBlocks, [itemType('dirt')])
  assert.equal(seen.allow1by1towers, true)
  assert.equal(seen.exclusionPlace({ position: new Vec3(2, 11, 0) }) > 100, true)
  assert.equal(seen.exclusionPlace({ position: new Vec3(5, 11, 0) }), 0)
  assert.equal(bot.pathfinder.movements, 'previous')
})

test('/place build movements sprint only on peaceful', async () => {
  const sprintOf = async (game) => {
    const bot = buildBot({ cobbled_deepslate: 5 })
    bot.placeAs = 'cobbled_deepslate'
    if (game) bot.game = game
    let seen = null
    bot.pathfinder.goto = async () => { seen = bot.pathfinder.movements }
    await place(bot, request({ block: 'cobbled_deepslate' }), newSession())
    return seen.allowSprinting
  }
  assert.equal(await sprintOf({ difficulty: 'peaceful' }), true)
  assert.equal(await sprintOf({ difficulty: 'easy' }), false)
  assert.equal(await sprintOf(null), false)
})

test('/place with no scaffold held: no towers, nothing to place on the way', async () => {
  const bot = buildBot({ oak_planks: 1 })
  bot.placeAs = 'oak_planks'
  let seen = null
  bot.pathfinder.goto = async () => { seen = bot.pathfinder.movements }
  await place(bot, request({ scaffold: ['dirt', 'oak_planks'] }), newSession())
  assert.deepEqual(seen.scafoldingBlocks, [])
  assert.equal(seen.allow1by1towers, false)
  assert.equal(seen.exclusionAreasPlace.length, 0)
})

test('/place restores the movements and counts the travel when the walk throws', async () => {
  const bot = buildBot({ oak_planks: 1 })
  bot.entity.position = new Vec3(30.5, 10, 0.5)
  bot.pathfinder.goto = async () => { throw new Error('no path') }
  const result = await place(bot, request(), newSession())
  assert.equal(result.error, 'unreachable')
  assert.equal(bot.pathfinder.movements, 'previous')
  assert.ok(result.travel)
})

test('/place is unreachable when the walk ends outside the goal', async () => {
  const bot = buildBot({ oak_planks: 1 })
  bot.entity.position = new Vec3(2.5, 10, 0.5) // standing in the target cell: the goal is not met
  const result = await place(bot, request(), newSession())
  assert.deepEqual([result.ok, result.error], [false, 'unreachable'])
  assert.equal(bot.placeCalls.length, 0)
})

test('/place retries 3 times on refusals, trying the next support face, then place_failed with found', async () => {
  const bot = buildBot({ oak_planks: 1 }, { '1,10,0': 'stone', '2,10,1': 'stone' })
  bot.placeBlock = async (ref, face) => { bot.placeCalls.push([ref.position.toString(), face.toString()]) } // refused: the cell stays free
  const result = await place(bot, request({ x: 2, z: 0 }), newSession())
  assert.deepEqual([result.ok, result.error, result.found, result.placed], [false, 'place_failed', 'air', undefined])
  assert.deepEqual(bot.placeCalls.map(([ref]) => ref), ['(2, 9, 0)', '(2, 10, 1)', '(1, 10, 0)'])
})

test('/place succeeds on a later try', async () => {
  const bot = buildBot({ oak_planks: 1 })
  let calls = 0
  bot.placeBlock = async () => {
    if (++calls === 2) bot.blocks['2,10,0'] = 'oak_planks'
  }
  const result = await place(bot, request(), newSession())
  assert.deepEqual([result.ok, result.placed, result.found, calls], [true, true, 'oak_planks', 2])
})

test('/place reads a torch on a side back as wall_torch', async () => {
  const bot = buildBot({ torch: 4 }, { '2,9,0': 'air', '3,10,0': 'stone' })
  bot.placeAs = 'wall_torch'
  const result = await place(bot, { block: 'torch', x: 2, y: 10, z: 0 }, newSession())
  assert.deepEqual([result.ok, result.placed, result.found], [true, true, 'wall_torch'])
  assert.deepEqual(bot.placeCalls, [['(3, 10, 0)', '(-1, 0, 0)']])
})

test('/place a door against the floor, walking clear of both cells and outside keep_out', async () => {
  const bot = buildBot({ oak_door: 1 })
  bot.placeAs = 'oak_door'
  let goal = null
  bot.pathfinder.goto = async (g) => { goal = g }
  const keepOut = { origin: [1, 10, -1], size: [3, 3, 3] }
  const result = await place(bot, request({ block: 'oak_door', keep_out: keepOut }), newSession())
  assert.deepEqual([result.ok, result.found], [true, 'oak_door'])
  assert.equal(goal.isEnd(new Vec3(2, 9, 0)), false) // head in the lower cell
  assert.equal(goal.isEnd(new Vec3(2, 11, 0)), false) // feet in the upper cell
  assert.equal(goal.isEnd(new Vec3(1, 10, 1)), false) // inside keep_out
})

test('/place of a door waits for the upper half to arrive before answering', async () => {
  const bot = buildBot({ oak_door: 1 })
  bot.placeAs = 'oak_door'
  const placeBlock = bot.placeBlock
  bot.placeBlock = async (ref, face) => {
    await placeBlock(ref, face)
    setTimeout(() => { bot.blocks['2,11,0'] = 'oak_door' }, 100)
  }
  const result = await place(bot, request({ block: 'oak_door' }), newSession())
  assert.deepEqual([result.ok, result.found], [true, 'oak_door'])
  assert.equal(bot.blocks['2,11,0'], 'oak_door')
})

test('/place with the bot overlapping the cell is unreachable for a solid block, but not for a torch', async () => {
  const bot = buildBot({ oak_planks: 1 })
  bot.entity.position = new Vec3(1.9, 10, 0.5) // in the next cell, but its hitbox reaches into the target
  const result = await place(bot, request(), newSession())
  assert.deepEqual([result.error, result.detail], ['unreachable', 'standing in the target cell'])
  assert.equal(bot.placeCalls.length, 0)

  const torchBot = buildBot({ torch: 1 })
  torchBot.entity.position = new Vec3(1.9, 10, 0.5)
  torchBot.placeAs = 'torch'
  assert.equal((await place(torchBot, { block: 'torch', x: 2, y: 10, z: 0 }, newSession())).ok, true)
})

// A bot at (x, 10, 0.5) whose forward control walks it 0.05 per tick toward the point it last looked at.
function offCentreBot (x, { walks = true } = {}) {
  const bot = buildBot({ oak_planks: 1 })
  bot.entity.position = new Vec3(x, 10, 0.5) // feet in the next cell, hitbox reaching into the target
  bot.placeAs = 'oak_planks'
  bot.controls = { forward: false, sneak: false }
  bot.looks = []
  let walker = null
  bot.lookAt = async (point, force) => { bot.looks.push([point, force]) }
  bot.setControlState = (name, on) => {
    bot.controls[name] = on
    if (name !== 'forward') return
    clearInterval(walker)
    if (!on || !walks) return
    walker = setInterval(() => {
      const toward = Math.sign(bot.looks.at(-1)[0].x - bot.entity.position.x)
      bot.entity.position = bot.entity.position.offset(0.05 * toward, 0, 0)
    }, 50)
  }
  return bot
}

test('/place re-centres an off-centre bot by sneaking toward its feet cell centre, then places', async () => {
  const bot = offCentreBot(1.9)
  const result = await place(bot, request(), newSession())
  assert.deepEqual([result.ok, result.placed, result.found], [true, true, 'oak_planks'])
  assert.ok(Math.abs(bot.entity.position.x - 1.5) < 0.1)
  const [point, force] = bot.looks[0]
  assert.deepEqual([point.x, point.z, force], [1.5, 0.5, true])
  assert.ok(Math.abs(point.y - 11.62) < 1e-9) // level with the eyes
  assert.deepEqual(bot.controls, { forward: false, sneak: false })
})

test('/place is unreachable when the re-centre does not move the bot, and releases the controls', async () => {
  const bot = offCentreBot(1.9, { walks: false })
  const result = await place(bot, request(), newSession())
  assert.deepEqual([result.error, result.detail], ['unreachable', 'standing in the target cell'])
  assert.equal(bot.looks.length, 20)
  assert.deepEqual(bot.controls, { forward: false, sneak: false })
  assert.equal(bot.placeCalls.length, 0)
})

test('/place throws the abort reason when aborted while re-centring, with the controls released', async () => {
  const bot = offCentreBot(1.9, { walks: false })
  const controller = new AbortController()
  const lookAt = bot.lookAt
  bot.lookAt = async (...args) => {
    await lookAt(...args)
    if (bot.looks.length === 3) controller.abort(Object.assign(new Error('guard'), { code: 'aborted' }))
  }
  await assert.rejects(place(bot, request(), newSession({ signal: controller.signal })), { code: 'aborted' })
  assert.deepEqual(bot.controls, { forward: false, sneak: false })
  assert.equal(bot.placeCalls.length, 0)
})

test('/place throws the abort reason when aborted', async () => {
  const bot = buildBot({ oak_planks: 1 })
  const controller = new AbortController()
  controller.abort(Object.assign(new Error('guard'), { code: 'aborted' }))
  await assert.rejects(place(bot, request(), newSession({ signal: controller.signal })), { code: 'aborted' })
  assert.equal(bot.placeCalls.length, 0)

  const walking = new AbortController()
  const other = buildBot({ oak_planks: 1 })
  other.pathfinder.goto = async () => { walking.abort(Object.assign(new Error('guard'), { code: 'aborted' })) }
  await assert.rejects(place(other, request(), newSession({ signal: walking.signal })), { code: 'aborted' })
  assert.equal(other.pathfinder.movements, 'previous')
})

// --- /clear ----------------------------------------------------------------

test('checkClear: air is done, bedrock/barrier/liquids are unbreakable, lava next to it is a hazard', () => {
  const pos = new Vec3(2, 10, 0)
  for (const air of ['air', 'cave_air', 'void_air']) assert.deepEqual(checkClear(buildBot({}, { '2,10,0': air }), pos), { alreadyClear: true })
  for (const name of ['bedrock', 'barrier', 'water', 'lava', 'bubble_column']) {
    assert.deepEqual(checkClear(buildBot({}, { '2,10,0': name }), pos), { error: 'unbreakable', found: name }, name)
  }
  assert.deepEqual(checkClear(buildBot({}, { '2,10,0': 'dirt', '3,10,0': 'lava' }), pos), { error: 'hazard', found: 'dirt' })
  assert.deepEqual(checkClear(buildBot({}, { '2,10,0': 'dirt' }), pos), {})
  assert.deepEqual(checkClear(buildBot({}, { '2,10,0': 'short_grass' }), pos), {}) // grass is dug
  const unloaded = buildBot({})
  unloaded.blockAt = () => null
  assert.deepEqual(checkClear(unloaded, pos), {})
})

test('fastestTool picks the pickaxe for stone, the hand for dirt, and skips a tool about to break', () => {
  const bot = buildBot({ stone_pickaxe: 1, wooden_pickaxe: 1, dirt: 3 })
  const stone = bot.blockAt(new Vec3(0, 9, 0))
  const { tool, ms } = fastestTool(bot, stone)
  assert.equal(tool.name, 'stone_pickaxe')
  assert.ok(ms < 1000)
  const dirt = fakeWorldBot({ '0,0,0': 'dirt' }).blockAt(new Vec3(0, 0, 0))
  assert.equal(fastestTool(bot, dirt).tool, null)
  bot.inventory.slots[36].durabilityUsed = bot.inventory.slots[36].maxDurability - 1
  assert.equal(fastestTool(bot, stone).tool.name, 'wooden_pickaxe')
  const obsidian = fakeWorldBot({ '0,0,0': 'obsidian' }).blockAt(new Vec3(0, 0, 0))
  assert.ok(fastestTool(buildBot({}), obsidian).ms > 15000)
})

function clearBot (inventory, blocks) {
  const bot = buildBot(inventory, blocks)
  bot.dig = async (block) => {
    bot.digs = (bot.digs || 0) + 1
    const { x, y, z } = block.position
    bot.blocks[`${x},${y},${z}`] = 'air'
    bot._client.emit('block_change', { location: { x, y, z }, type: registry.blocksByName.air.defaultState })
  }
  return bot
}
const clearRequest = (extra = {}) => ({ x: 2, y: 10, z: 0, ...extra })

test('/clear on air: ok, cleared false, found air, no walk', async () => {
  const bot = clearBot({}, {})
  let walks = 0
  bot.pathfinder.goto = async () => { walks++ }
  const result = await clear(bot, clearRequest(), newSession())
  assert.deepEqual([result.ok, result.cleared, result.found, walks], [true, false, 'air', 0])
  assert.deepEqual(result.travel, { distance: 0, dug: {}, placed: {}, picked_up: {}, placed_cells: [] })
})

test('/clear answers unbreakable and hazard without walking', async () => {
  let walks = 0
  for (const [blocks, error, found] of [[{ '2,10,0': 'bedrock' }, 'unbreakable', 'bedrock'], [{ '2,10,0': 'water' }, 'unbreakable', 'water'], [{ '2,10,0': 'dirt', '2,11,0': 'lava' }, 'hazard', 'dirt']]) {
    const bot = clearBot({}, blocks)
    bot.pathfinder.goto = async () => { walks++ }
    const result = await clear(bot, clearRequest(), newSession())
    assert.deepEqual([result.ok, result.error, result.found], [false, error, found])
  }
  assert.equal(walks, 0)
})

test('/clear digs with the fastest tool, confirms, and reports removed, found and the inventories', async () => {
  const bot = clearBot({ stone_pickaxe: 1, dirt: 2 }, { '2,10,0': 'stone' })
  const equipped = []
  bot.equip = async (item) => { equipped.push(item.name) }
  const session = newSession()
  let goal = null
  bot.pathfinder.goto = async (g) => { goal = g }
  const result = await clear(bot, clearRequest({ scaffold: ['dirt'] }), session)
  assert.deepEqual([result.ok, result.cleared, result.removed, result.found], [true, true, 'stone', 'air'])
  assert.deepEqual(equipped, ['stone_pickaxe'])
  assert.deepEqual(result.inventory_before_step, { stone_pickaxe: 1, dirt: 2 })
  assert.equal(session.tracker.log.phase, 'step')
  assert.ok(goal.inner instanceof goals.GoalLookAtBlock)
  assert.equal(goal.inner.reach, 4.5)
  assert.equal(goal.isEnd(new Vec3(2, 11, 0)), false) // standing on it
  assert.equal(goal.isEnd(new Vec3(2, 10, 0)), false) // inside it
  assert.equal(bot.pathfinder.movements, 'previous')
})

test('/clear reports what is in the cell afterwards (sand that fell in)', async () => {
  const bot = clearBot({}, { '2,10,0': 'dirt', '2,11,0': 'sand' })
  const dig = bot.dig
  bot.dig = async (block) => { await dig(block); bot.blocks['2,10,0'] = 'sand' }
  const result = await clear(bot, clearRequest(), newSession())
  assert.deepEqual([result.ok, result.removed, result.found], [true, 'dirt', 'sand'])
})

test('/clear answers missing_tool without digging when the dig would take over 15 s', async () => {
  const bot = clearBot({}, { '2,10,0': 'obsidian' })
  const result = await clear(bot, clearRequest(), newSession())
  assert.deepEqual([result.ok, result.error, result.found, bot.digs], [false, 'missing_tool', 'obsidian', undefined])
})

test('/clear answers dig_failed when the server never confirms', { timeout: 10000 }, async () => {
  const bot = clearBot({}, { '2,10,0': 'dirt' })
  bot.dig = async () => {}
  const result = await clear(bot, clearRequest(), newSession())
  assert.deepEqual([result.ok, result.error], [false, 'dig_failed'])
})

test('/clear answers dig_failed when the dig throws, and rethrows an abort', async () => {
  const bot = clearBot({}, { '2,10,0': 'dirt' })
  bot.dig = async () => { throw new Error('too far') }
  const result = await clear(bot, clearRequest(), newSession())
  assert.deepEqual([result.error, result.detail], ['dig_failed', 'too far'])

  const controller = new AbortController()
  const aborted = clearBot({}, { '2,10,0': 'dirt' })
  aborted.dig = async () => { controller.abort(Object.assign(new Error('guard'), { code: 'aborted' })); throw new Error('stopped') }
  await assert.rejects(clear(aborted, clearRequest(), newSession({ signal: controller.signal })), { code: 'aborted' })
})

test('/clear on a door keeps clear of both halves and, with keep_out, ends outside the box', async () => {
  const bot = clearBot({}, { '2,10,0': 'oak_door', '2,11,0': 'oak_door' })
  let goal = null
  bot.pathfinder.goto = async (g) => { goal = g }
  await clear(bot, clearRequest({ keep_out: { origin: [0, 10, -1], size: [2, 3, 3] } }), newSession())
  assert.equal(goal.isEnd(new Vec3(2, 11, 0)), false)
  assert.equal(goal.isEnd(new Vec3(2, 9, 0)), false)
  assert.equal(goal.isEnd(new Vec3(1, 10, 0)), false)
  assert.equal(goal.blocksStance(new Vec3(3, 10, 0)), false)
})

test('/clear is unreachable when the walk fails', async () => {
  const bot = clearBot({}, { '2,10,0': 'dirt' })
  bot.entity.position = new Vec3(2.5, 11, 0.5) // on top of the target
  const result = await clear(bot, clearRequest(), newSession())
  assert.deepEqual([result.ok, result.error, bot.digs], [false, 'unreachable', undefined])
})

test('/place does not re-centre a bot whose real hitbox stays out of the target cell', async () => {
  const bot = offCentreBot(1.81) // box 1.51..2.11, clear of cell 0
  const result = await place(bot, request({ x: 0 }), newSession())
  assert.deepEqual([result.ok, result.placed], [true, true])
  assert.equal(bot.looks.length, 0)
  assert.ok(!Object.values(bot.controls).includes(true))
})

test('/clear of a block with no collision shape walks to a reach-only goal, a solid block keeps GoalLookAtBlock', async () => {
  for (const name of ['torch', 'short_grass', 'poppy']) {
    const bot = clearBot({}, { '2,10,0': name })
    let goal = null
    bot.pathfinder.goto = async (g) => { goal = g }
    const result = await clear(bot, clearRequest(), newSession())
    assert.deepEqual([result.ok, result.cleared], [true, true], name)
    assert.ok(!(goal.inner instanceof goals.GoalLookAtBlock), name)
    assert.equal(goal.isEnd(new Vec3(2, 10, 3)), true, name) // in reach
    assert.equal(goal.isEnd(new Vec3(2, 10, 6)), false, name) // too far
    assert.equal(goal.isEnd(new Vec3(2, 10, 0)), false, name) // inside it
    assert.equal(goal.isEnd(new Vec3(2, 11, 0)), false, name) // on top of it
  }
  const bot = clearBot({}, { '2,10,0': 'dirt' })
  let goal = null
  bot.pathfinder.goto = async (g) => { goal = g }
  await clear(bot, clearRequest(), newSession())
  assert.ok(goal.inner instanceof goals.GoalLookAtBlock)
})
