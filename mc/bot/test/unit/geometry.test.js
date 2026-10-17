const test = require('node:test')
const assert = require('node:assert/strict')
const { Vec3 } = require('vec3')
const { DIRECTIONS, directionVector, isInCell, sectorOf, withinBox } = require('../../lib/geometry')

test('sectorOf puts N on -z and E on +x', () => {
  assert.equal(sectorOf(0, -10), 'N')
  assert.equal(sectorOf(10, 0), 'E')
  assert.equal(sectorOf(0, 10), 'S')
  assert.equal(sectorOf(-10, 0), 'W')
  assert.equal(sectorOf(10, -10), 'NE')
  assert.equal(sectorOf(10, 10), 'SE')
  assert.equal(sectorOf(-10, 10), 'SW')
  assert.equal(sectorOf(-10, -10), 'NW')
})

test('sectorOf sectors are 45 degrees wide and centred on their direction', () => {
  // N spans -22.5 to +22.5 degrees around -z.
  assert.equal(sectorOf(Math.tan(Math.PI / 8) * 0.99, -1), 'N')
  assert.equal(sectorOf(Math.tan(Math.PI / 8) * 1.01, -1), 'NE')
  assert.equal(sectorOf(-Math.tan(Math.PI / 8) * 0.99, -1), 'N')
  assert.equal(sectorOf(-Math.tan(Math.PI / 8) * 1.01, -1), 'NW')
  assert.equal(sectorOf(0, 0), 'N')
})

test('directionVector is the unit vector of each sector centre', () => {
  assert.deepEqual(directionVector('N'), { x: 0, z: -1 })
  assert.deepEqual(directionVector('E'), { x: 1, z: 0 })
  assert.deepEqual(directionVector('S'), { x: 0, z: 1 })
  assert.deepEqual(directionVector('W'), { x: -1, z: 0 })
  assert.equal(directionVector('up'), null)
  for (const name of DIRECTIONS) {
    const { x, z } = directionVector(name)
    assert.ok(Math.abs(Math.hypot(x, z) - 1) < 1e-5)
    assert.equal(sectorOf(x, z), name)
  }
})

test('isInCell accepts the cell grown by 0.25 and nothing further', () => {
  const cell = new Vec3(10, 64, -3)
  assert.ok(isInCell(new Vec3(10.5, 64.5, -2.5), cell))
  assert.ok(isInCell(new Vec3(9.8, 64.5, -2.5), cell))
  assert.ok(isInCell(new Vec3(11.2, 65.2, -1.8), cell))
  assert.ok(!isInCell(new Vec3(9.7, 64.5, -2.5), cell))
  assert.ok(!isInCell(new Vec3(11.3, 64.5, -2.5), cell))
  assert.ok(!isInCell(new Vec3(10.5, 65.3, -2.5), cell))
  assert.ok(!isInCell(new Vec3(10.5, 64.5, -3.3), cell))
  // a drop of the block next door is not this cell's
  assert.ok(!isInCell(new Vec3(11.5, 64.5, -2.5), cell))
})

test('withinBox compares every axis', () => {
  const a = new Vec3(0, 0, 0)
  assert.ok(withinBox(new Vec3(2, -2, 1), a, 2))
  assert.ok(!withinBox(new Vec3(3, 0, 0), a, 2))
  assert.ok(!withinBox(new Vec3(0, 0, -3), a, 2))
})
