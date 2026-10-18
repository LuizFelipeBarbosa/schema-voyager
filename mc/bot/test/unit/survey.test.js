const test = require('node:test')
const assert = require('node:assert/strict')
const { rankSites, survey, validateSurvey } = require('../../lib/survey')
const { fakeWorldBot } = require('./world_fixture')

const feet = { x: 0.5, z: 0.5 }
const ground = (y) => ({ ground: y, reason: null })
const ruledOut = (reason) => ({ ground: null, reason })

// A flat field of ground y 9 over x, z in [-10, 10) (unknown outside), with `overrides` by "x,z".
function field (overrides = {}) {
  return (x, z) => {
    if (x < -10 || x >= 10 || z < -10 || z >= 10) return ruledOut('unknown')
    return overrides[`${x},${z}`] ?? ground(9)
  }
}

const rank = (columnAt, options = {}) => rankSites({ columnAt, feet, size: [3, 3], radius: 24, ...options })
const siteAt = (result, x0, z0) => result.sites.find((site) => site.origin[0] === x0 && site.origin[2] === z0)

test('a flat field ranks the sites nearest the bot first, with cut 0 and fill 0', () => {
  const result = rank(field())
  assert.equal(result.sites.length, 10)
  assert.deepEqual(result.sites[0], { origin: [-1, 10, -1], cut: 0, fill: 0, distance: 0 })
  for (const site of result.sites) assert.equal(site.cut + site.fill, 0)
  const distances = result.sites.map((site) => site.distance)
  assert.deepEqual(distances, [...distances].sort((a, b) => a - b))
  assert.ok(result.considered > 0)
  assert.deepEqual(Object.keys(result.ruled_out).sort(), ['liquid', 'protected', 'steep', 'tree', 'unknown'])
})

test('ties are broken by distance, then x0, then z0', () => {
  const result = rank(field(), { size: [2, 2] })
  // The four footprints around the bot are equally far: x0, then z0 decide.
  const keys = result.sites.map((site) => [site.distance, site.origin[0], site.origin[2]])
  const sorted = [...keys].sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2])
  assert.deepEqual(keys, sorted)
  assert.deepEqual(result.sites[0].origin, [-1, 10, -1])
})

test('a one-block bump gives cut 1 and a one-block hole gives fill 1', () => {
  const bump = rank(field({ '0,0': ground(10) }), { size: [3, 3], radius: 1 })
  assert.deepEqual(bump.sites.map((site) => site.origin), [[-1, 10, -1]])
  assert.equal(bump.sites[0].cut, 1)
  assert.equal(bump.sites[0].fill, 0)

  const hole = rank(field({ '0,0': ground(8) }), { size: [3, 3], radius: 1 })
  assert.equal(hole.sites[0].fill, 1)
  assert.equal(hole.sites[0].cut, 0)
})

test('cheaper sites rank before nearer ones', () => {
  // The nearest footprint has a bump; a flat one further away wins.
  const result = rank(field({ '0,0': ground(10) }), { size: [3, 3] })
  assert.equal(result.sites[0].cut + result.sites[0].fill, 0)
  assert.ok(result.sites[0].distance > 1)
})

test('water inside the footprint or in its ring rules a site out as liquid', () => {
  const water = { '0,0': ruledOut('liquid') }
  const inside = rank(field(water), { size: [3, 3], radius: 1 })
  assert.deepEqual(inside.sites, [])
  assert.equal(inside.ruled_out.liquid, inside.considered)

  // (2, 0) is in the ring of the footprint at x0 = -1 but outside it.
  const ring = rank(field({ '2,0': ruledOut('liquid') }), { size: [3, 3], radius: 1 })
  assert.equal(ring.sites.length, 0)
  assert.equal(ring.ruled_out.liquid, 1)
})

test('a log column rules the site out as tree, but a tree in the ring does not', () => {
  const inside = rank(field({ '0,0': ruledOut('tree') }), { size: [3, 3], radius: 1 })
  assert.equal(inside.ruled_out.tree, 1)
  assert.equal(inside.sites.length, 0)
  const ring = rank(field({ '2,0': ruledOut('tree') }), { size: [3, 3], radius: 1 })
  assert.equal(ring.sites.length, 1)
})

test('a 4-high step and a 2-deep hole rule the site out as steep; 3 up and 1 down do not', () => {
  const options = { size: [2, 2], radius: 1 }
  // Three flat columns fix the floor at 10; the fourth is the odd one out.
  const odd = (y) => rank(field({ '0,0': ground(y) }), options)
  // All four footprints of radius 1 contain the odd column.
  assert.equal(odd(13).ruled_out.steep, 4)
  assert.equal(odd(13).sites.length, 0)
  assert.equal(odd(7).ruled_out.steep, 4)
  assert.equal(odd(12).sites[0].cut, 3)
  assert.equal(odd(8).sites[0].fill, 1)
})

test('the floor is the lower median of the grounds plus one', () => {
  const result = rank(field({ '0,0': ground(10), '1,0': ground(10) }), { size: [2, 2], radius: 1 })
  // The footprint at (0, 0) has grounds 10, 10, 9, 9: the lower median is 9.
  assert.deepEqual(siteAt(result, 0, 0), { origin: [0, 10, 0], cut: 2, fill: 0, distance: 0.7 })
})

test('a column that is not usable is counted under its own reason, first reason only', () => {
  const result = rank(field({ '0,0': ruledOut('unknown'), '1,0': ruledOut('tree') }), { size: [3, 3], radius: 1 })
  assert.equal(result.considered, 1)
  assert.equal(result.ruled_out.unknown, 1)
  assert.equal(result.ruled_out.tree, 0)
})

test('a protected box rules out overlapping sites, ring included, but not one two blocks away', () => {
  const protect = [{ origin: [6, 0, 0], size: [2, 10, 1] }] // x 6..7, z 0
  const result = rankSites({ columnAt: field(), feet: { x: 4.5, z: 0.5 }, size: [1, 1], radius: 1, protect })
  // x0 = 5 has the box in its ring; x0 = 4 has a free cell between; x0 = 3 is further.
  assert.deepEqual(result.sites.map((site) => site.origin[0]).sort(), [3, 3, 3, 4, 4, 4])
  assert.equal(result.ruled_out.protected, 3)
})

test('at most 10 sites are answered', () => {
  assert.equal(rank(field(), { size: [1, 1] }).sites.length, 10)
})

test('survey reads columns around the bot from blockAt: ground, covered window top and water', () => {
  const blocks = {}
  for (let x = -3; x <= 3; x++) for (let z = -3; z <= 3; z++) blocks[`${x},9,${z}`] = 'grass_block'
  const bot = fakeWorldBot(blocks)
  bot.entity.position.y = 10
  const open = survey(bot, { size: [2, 2], radius: 3 })
  assert.equal(open.ok, true)
  assert.equal(open.sites[0].origin[1], 10)
  assert.equal(open.sites[0].cut + open.sites[0].fill, 0)

  const covered = fakeWorldBot({ ...blocks, '0,22,0': 'stone' })
  covered.entity.position.y = 10
  assert.ok(survey(covered, { size: [2, 2], radius: 3 }).ruled_out.unknown > 0)

  const wet = fakeWorldBot({ ...blocks, '0,10,0': 'water' })
  wet.entity.position.y = 10
  assert.ok(survey(wet, { size: [2, 2], radius: 3 }).ruled_out.liquid > 0)

  const log = fakeWorldBot({ ...blocks, '0,10,0': 'oak_log' })
  log.entity.position.y = 10
  assert.ok(survey(log, { size: [2, 2], radius: 3 }).ruled_out.tree > 0)
})

test('validateSurvey accepts a good body and rejects the rest', () => {
  assert.equal(validateSurvey({ size: [7, 5] }), null)
  assert.equal(validateSurvey({ size: [16, 1], radius: 32, protect: [{ origin: [1, 2, 3], size: [1, 1, 1] }] }), null)
  const bad = [
    {},
    { size: [7] },
    { size: [7, 5, 2] },
    { size: [0, 5] },
    { size: [17, 5] },
    { size: [7.5, 5] },
    { size: [7, 5], radius: 0 },
    { size: [7, 5], radius: 33 },
    { size: [7, 5], radius: 2.5 },
    { size: [7, 5], protect: 'x' },
    { size: [7, 5], protect: [{ origin: [1, 2], size: [1, 1, 1] }] },
    { size: [7, 5], protect: [{ origin: [1, 2, 3], size: [1, 0, 1] }] },
    { size: [7, 5], protect: [null] },
    { size: [7, 5], protect: Array.from({ length: 33 }, () => ({ origin: [0, 0, 0], size: [1, 1, 1] })) }
  ]
  for (const body of bad) assert.equal(typeof validateSurvey(body), 'string', JSON.stringify(body))
})
