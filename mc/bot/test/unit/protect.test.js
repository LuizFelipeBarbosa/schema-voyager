const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { Vec3 } = require('vec3')
const { Movements } = require('mineflayer-pathfinder')
const { explore } = require('../../lib/explore')
const { DiggingMovements } = require('../../lib/movements')
const { mine } = require('../../lib/mine')
const { StanceGoal, clear, place, validateClear, validatePlace } = require('../../lib/place')
const { breakExclusion, insideAny, placeExclusion, validateProtect } = require('../../lib/protect')
const { chooseTargets, createUnreachableMemory, look } = require('../../lib/targets')
const { validateGoto } = require('../../lib/goto')
const { validateSurvey } = require('../../lib/survey')
const { fakeWorldBot } = require('./world_fixture')

const HOUSE = { origin: [10, 10, 10], size: [3, 3, 3] }

test('validateProtect: absent is fine; at most 32 boxes of integer triples with positive sizes', () => {
  assert.equal(validateProtect(undefined), null)
  assert.equal(validateProtect(null), null)
  assert.equal(validateProtect([]), null)
  assert.equal(validateProtect([HOUSE]), null)
  assert.equal(validateProtect(Array(32).fill(HOUSE)), null)
  const bad = [
    'box', {}, Array(33).fill(HOUSE), [null], [{}], [{ origin: [0, 0], size: [1, 1, 1] }],
    [{ origin: [0, 0, 0.5], size: [1, 1, 1] }], [{ origin: [0, 0, 0], size: [1, 0, 1] }],
    [{ origin: [0, 0, 0], size: [1, -1, 1] }], [{ origin: [0, 0, 0], size: [1, 1, '1'] }], [{ origin: [0, 0, 0] }]
  ]
  for (const protect of bad) {
    assert.match(validateProtect(protect), /protect/, JSON.stringify(protect))
  }
})

test('insideAny: the origin cell is in, origin + size is out, on every axis', () => {
  const boxes = [{ origin: [0, 0, 0], size: [2, 2, 2] }, HOUSE]
  assert.equal(insideAny(boxes, new Vec3(0, 0, 0)), true)
  assert.equal(insideAny(boxes, new Vec3(1, 1, 1)), true)
  assert.equal(insideAny(boxes, { x: 12, y: 12, z: 12 }), true)
  for (const outside of [[2, 0, 0], [0, 2, 0], [0, 0, 2], [-1, 0, 0], [0, -1, 0], [0, 0, -1], [13, 10, 10], [10, 13, 10], [10, 10, 13], [9, 10, 10]]) {
    assert.equal(insideAny(boxes, new Vec3(...outside)), false, outside.join())
  }
  assert.equal(insideAny([], new Vec3(0, 0, 0)), false)
})

const logWorld = (logs) => fakeWorldBot(Object.fromEntries(logs.map((pos) => [pos, 'oak_log'])), { defaultBlock: 'air' })
const search = (bot, protect) => chooseTargets(bot, {
  blockNames: ['oak_log'], maxDistance: 32, buried: true, skipped: new Set(), unreachable: createUnreachableMemory(), onHazard () {}, protect
})
const insideLog = '11,11,11'
const outsideLog = '20,10,20'

test('chooseTargets picks the log outside the box, and none when only the inside one is left', () => {
  const protect = [HOUSE]
  assert.deepEqual(search(logWorld([insideLog, outsideLog]), protect).positions.map(String), ['(20, 10, 20)'])
  assert.deepEqual(search(logWorld([insideLog]), protect).positions, [])
  assert.equal(search(logWorld([insideLog]), undefined).positions.length, 1) // no protect: unchanged
})

test('look drops hits inside a box and reports the next one in the sector', () => {
  const bot = logWorld([insideLog, outsideLog])
  const hits = (protect) => Object.values(look(bot, { blocks: ['oak_log'], radius: 64, protect }).directions)
    .map((sector) => sector.oak_log).filter(Boolean).map(({ x, y, z }) => `${x},${y},${z}`)
  assert.deepEqual(hits([]), [insideLog])
  assert.deepEqual(hits([HOUSE]), [outsideLog])
  assert.deepEqual(hits(undefined), [insideLog])
})

const stoneWorld = () => fakeWorldBot({ '0,10,0': 'air', '0,11,0': 'air' }, { defaultBlock: 'stone' })

test('DiggingMovements with boxes refuses to break inside and prices scaffold there out', () => {
  const movements = new DiggingMovements(stoneWorld(), { scaffoldNames: ['dirt'], protect: [{ origin: [5, 10, 0], size: [2, 2, 2] }] })
  const at = (x, y, z) => movements.getBlock(new Vec3(x, y, z), 0, 0, 0)
  assert.equal(movements.safeToBreak(at(5, 10, 0)), false)
  assert.equal(movements.safeToBreak(at(6, 11, 1)), false)
  assert.equal(movements.safeToBreak(at(7, 10, 0)), true)
  assert.equal(movements.exclusionPlace(at(6, 11, 1)) >= 100, true)
  assert.equal(movements.exclusionPlace(at(7, 10, 0)), 0)
  assert.equal(new DiggingMovements(stoneWorld()).safeToBreak(at(5, 10, 0)), true) // no protect: unchanged
})

test('the exclusion helpers weigh a block by its position', () => {
  const boxes = [HOUSE]
  const inside = { position: new Vec3(10, 10, 10) }
  const outside = { position: new Vec3(13, 10, 10) }
  assert.equal(breakExclusion(boxes)(inside) >= 100, true)
  assert.equal(breakExclusion(boxes)(outside), 0)
  assert.equal(placeExclusion(boxes)(inside) > 100, true)
  assert.equal(placeExclusion(boxes)(outside), 0)
})

// --- /place and /clear -------------------------------------------------------

function buildBot (extra = {}, held = {}) {
  const blocks = { ...extra }
  for (let x = -4; x <= 4; x++) for (let z = -4; z <= 4; z++) blocks[`${x},9,${z}`] ??= 'stone'
  const bot = fakeWorldBot(blocks)
  Object.assign(bot, { on () {}, removeListener () {}, dig: async () => {}, equip: async () => {}, heldItem: null })
  const slots = new Array(46).fill(null)
  Object.entries(held).forEach(([name, count], i) => { slots[36 + i] = { name, type: bot.registry.itemsByName[name].id, count, maxDurability: null, durabilityUsed: 0 } })
  bot.inventory = { slots, items: () => slots.filter(Boolean), on () {}, removeListener () {} }
  Object.assign(bot.world, { getBlock: (pos) => bot.blockAt(pos), raycast: () => null })
  bot.pathfinder = { movements: 'previous', setMovements (m) { this.movements = m }, goto: async () => {}, setGoal () {}, stop () {}, bestHarvestTool: () => null }
  return bot
}
const newSession = () => ({ episode: { walked: 0 }, tracker: { log: { setPhase () {} } } })

test('/place and /clear walk with scaffold excluded inside every protect box', async () => {
  const protect = [{ origin: [1, 10, -1], size: [3, 3, 3] }, { origin: [20, 10, 0], size: [1, 1, 1] }]
  for (const run of [
    { target: 'air', walk: (bot) => place(bot, { block: 'oak_planks', x: 2, y: 10, z: 0, scaffold: ['dirt'], protect }, newSession()) },
    { target: 'stone', walk: (bot) => clear(bot, { x: 2, y: 10, z: 0, scaffold: ['dirt'], protect }, newSession()) }
  ]) {
    const bot = buildBot({ '2,10,0': run.target }, { oak_planks: 1, dirt: 4 })
    let seen = null
    bot.pathfinder.goto = async () => { seen = bot.pathfinder.movements }
    await run.walk(bot)
    assert.ok(seen instanceof Movements)
    const cost = (x, y, z) => seen.exclusionPlace({ position: new Vec3(x, y, z) })
    assert.equal(cost(2, 11, 0) > 100, true)
    assert.equal(cost(20, 10, 0) > 100, true)
    assert.equal(cost(5, 11, 0), 0)
  }
})

test('/place and /clear validators check protect', () => {
  const target = { block: 'oak_planks', x: 1, y: 2, z: 3 }
  assert.equal(validatePlace({ ...target, protect: [HOUSE] }), null)
  assert.equal(validateClear({ ...target, protect: [HOUSE] }), null)
  for (const protect of ['x', [{ origin: [0, 0, 0], size: [0, 1, 1] }], Array(33).fill(HOUSE)]) {
    assert.match(validatePlace({ ...target, protect }), /protect/)
    assert.match(validateClear({ ...target, protect }), /protect/)
  }
})

test('/goto and /survey validators check protect', () => {
  const bad = [{ origin: [0, 0], size: [1, 1, 1] }]
  assert.match(validateGoto({ x: 1, y: 2, z: 3, protect: bad }), /protect/)
  assert.match(validateSurvey({ size: [3, 3], protect: bad }), /protect/)
})

test('the stance never ends on top of the keep_out box, and beside it is fine', () => {
  const inner = { heuristic: () => 0, isEnd: () => true }
  const keepOut = { origin: [0, 10, 0], size: [3, 3, 3] }
  const goal = new StanceGoal(inner, { cells: [new Vec3(9, 10, 9)], roofBox: keepOut })
  assert.equal(goal.isEnd(new Vec3(1, 13, 1)), false) // on the roof
  assert.equal(goal.isEnd(new Vec3(2, 15, 2)), false) // higher up still
  assert.equal(goal.isEnd(new Vec3(3, 13, 1)), true) // beside it
  assert.equal(goal.isEnd(new Vec3(1, 13, 3)), true)
  assert.equal(goal.isEnd(new Vec3(1, 12, 1)), true) // inside is the avoid box's business, not the roof's
  assert.equal(new StanceGoal(inner, { cells: [] }).isEnd(new Vec3(1, 13, 1)), true) // no keep_out
})

test('/place does not report a stance on top of keep_out as reached', async () => {
  const bot = buildBot({ '2,12,1': 'stone' }, { oak_planks: 1 })
  bot.entity.position = new Vec3(1.5, 13, 1.5)
  const result = await place(bot, { block: 'oak_planks', x: 2, y: 13, z: 1, keep_out: { origin: [0, 10, 0], size: [3, 3, 3] } }, newSession())
  assert.equal(result.error, 'unreachable')
})

// --- /mine and /explore ------------------------------------------------------

test('v1 /mine skips a block inside a box and finds the next', async () => {
  const mineSession = () => ({
    episode: { skippedTargets: new Set(), walked: 0 },
    tracker: { log: { lastSeq: () => 0, since: () => [], setPhase () {}, record () {} } },
    unreachable: createUnreachableMemory()
  })
  const protect = [{ origin: [5, 10, 5], size: [1, 1, 1] }]
  const bot = logWorld(['5,10,5', '8,10,8'])
  bot.inventory = { slots: [], items: () => [] }
  bot.on = bot.removeListener = () => {}
  bot.pathfinder = { setGoal () {}, goto: async () => {} }
  const insideOnly = logWorld(['5,10,5'])
  Object.assign(insideOnly, { inventory: bot.inventory, on: bot.on, removeListener: bot.removeListener, pathfinder: bot.pathfinder })

  const hidden = await mine(insideOnly, { block: 'oak_log', protect }, mineSession())
  assert.equal(hidden.error, 'not_found')
  const picked = await mine(bot, { block: 'oak_log', protect }, mineSession())
  assert.deepEqual(picked.target, { x: 8, y: 10, z: 8 })
})

test('/explore up with dig_through climbs with movements that refuse protected blocks', async () => {
  const blocks = {}
  for (let x = -5; x <= 5; x++) for (let z = -5; z <= 5; z++) blocks[`${x},9,${z}`] = 'stone'
  blocks['0,12,0'] = 'stone' // a roof over the bot
  blocks['0,13,0'] = 'stone'
  const bot = fakeWorldBot(blocks)
  Object.assign(bot, { on () {}, removeListener () {}, dig: async () => {}, heldItem: null, clearControlStates () {} })
  bot.entity.onGround = true
  bot.inventory = { slots: [], items: () => [] }
  let seen = null
  bot.pathfinder = { setMovements (m) { this.movements = m }, setGoal () {}, goto: async () => { seen = bot.pathfinder.movements }, bestHarvestTool: () => null }
  const session = { episode: { walked: 0 }, tracker: { log: { record () {} } }, border: {} }
  await explore(bot, session, { direction: 'up', dig_through: true, protect: [{ origin: [0, 12, 0], size: [1, 1, 1] }] })
  assert.ok(seen instanceof DiggingMovements)
  assert.equal(seen.safeToBreak(seen.getBlock(new Vec3(0, 12, 0), 0, 0, 0)), false)
  assert.equal(seen.safeToBreak(seen.getBlock(new Vec3(0, 13, 0), 0, 0, 0)), true)
})

test('server.js validates protect on /mine, /explore, /look, /place and /clear', () => {
  const server = fs.readFileSync(path.join(__dirname, '../../server.js'), 'utf8')
  const validators = server.slice(server.indexOf('const validators = {'), server.indexOf('const actionLock'))
  const section = (name, next) => validators.slice(validators.indexOf(`  ${name}:`), validators.indexOf(`  ${next}:`))
  for (const [name, next] of [['mine', 'craft'], ['explore', 'look'], ['look', 'smelt']]) {
    assert.match(section(name, next), /validateProtect\(body\.protect\)/, name)
  }
  assert.match(validators, /place: validatePlace/) // place.js validates protect (tested above)
  assert.match(validators, /clear: validateClear/)
})
