const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const {
  RconClient, botUrl, decodeRconPackets, encodeRconPacket, loadEnv, parseEnv, requirePort
} = require('../v2_helpers')

test('an RCON packet is length, id, type, body and two NULs, little endian', () => {
  const packet = encodeRconPacket(7, 2, 'say hi')
  assert.equal(packet.readInt32LE(0), 4 + 4 + 6 + 2) // everything after the length
  assert.equal(packet.readInt32LE(4), 7)
  assert.equal(packet.readInt32LE(8), 2)
  assert.equal(packet.toString('utf8', 12, 18), 'say hi')
  assert.deepEqual([...packet.subarray(18)], [0, 0])
  assert.equal(packet.length, 4 + packet.readInt32LE(0))
})

test('an empty body still has the two NULs', () => {
  const packet = encodeRconPacket(1, 0, '')
  assert.equal(packet.length, 14)
  assert.equal(packet.readInt32LE(0), 10)
})

test('decodeRconPackets reads whole packets and keeps the partial tail', () => {
  const first = encodeRconPacket(1, 2, '')
  const second = encodeRconPacket(2, 0, 'Cleared the inventory')
  const stream = Buffer.concat([first, second, second.subarray(0, 9)])
  const { packets, rest } = decodeRconPackets(stream)
  assert.deepEqual(packets, [
    { id: 1, type: 2, body: '' },
    { id: 2, type: 0, body: 'Cleared the inventory' }
  ])
  assert.equal(rest.length, 9)
  const done = decodeRconPackets(Buffer.concat([rest, second.subarray(9)]))
  assert.equal(done.packets.length, 1)
  assert.equal(done.rest.length, 0)
})

test('the RCON client and the HTTP helpers refuse the live demo\'s ports', () => {
  assert.throws(() => new RconClient({ port: 25575, password: 'x' }), /live demo/)
  const saved = process.env.SV2_BOT_PORT
  process.env.SV2_BOT_PORT = '3000'
  assert.throws(() => botUrl('/health'), /live demo/)
  process.env.SV2_BOT_PORT = '3200'
  assert.equal(botUrl('/health'), 'http://127.0.0.1:3200/health')
  assert.throws(() => requirePort('SV2_NOT_SET_ANYWHERE', []), /must be set/)
  if (saved === undefined) delete process.env.SV2_BOT_PORT
  else process.env.SV2_BOT_PORT = saved
})

test('parseEnv reads KEY=VALUE lines', () => {
  assert.deepEqual(parseEnv('# comment\nA=1\n\nexport B="two words"\nC=\'x\'\nD=3 # trailing\nnot a line\n'),
    { A: '1', B: 'two words', C: 'x', D: '3' })
})

test('loadEnv does not override variables that are already set', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sv2-env-'))
  fs.writeFileSync(path.join(dir, '.env'), 'SV2_TEST_KEPT=from-file\nSV2_TEST_NEW=from-file\n')
  process.env.SV2_TEST_KEPT = 'already-set'
  delete process.env.SV2_TEST_NEW
  try {
    assert.equal(loadEnv(dir), 1)
    assert.equal(process.env.SV2_TEST_KEPT, 'already-set')
    assert.equal(process.env.SV2_TEST_NEW, 'from-file')
    assert.equal(loadEnv(path.join(dir, 'missing')), 0)
  } finally {
    delete process.env.SV2_TEST_KEPT
    delete process.env.SV2_TEST_NEW
    fs.rmSync(dir, { recursive: true })
  }
})

const { deltaMatches, nearestInLook, quarantineReason } = require('../v2_helpers')

test('deltaMatches: fixed entries exactly, tag entries by their sum', () => {
  const isPlanks = (name) => name.endsWith('_planks')
  const spec = { fixed: { wooden_pickaxe: 1, stick: -2 }, tag: { test: isPlanks, total: -3 } }
  assert.equal(deltaMatches({ oak_planks: -3, stick: -2, wooden_pickaxe: 1 }, spec), true)
  assert.equal(deltaMatches({ oak_planks: -2, birch_planks: -1, stick: -2, wooden_pickaxe: 1 }, spec), true) // mixed
  assert.equal(deltaMatches({ oak_planks: -2, stick: -2, wooden_pickaxe: 1 }, spec), false) // short
  assert.equal(deltaMatches({ oak_planks: -3, stick: -2, wooden_pickaxe: 1, dirt: 1 }, spec), false) // extra
  assert.equal(deltaMatches({ oak_planks: -3, stick: -1, wooden_pickaxe: 1 }, spec), false)
  assert.equal(deltaMatches({ oak_planks: -4, birch_planks: 1, stick: -2, wooden_pickaxe: 1 }, spec), false) // wrong side
  assert.equal(deltaMatches({ oak_log: -3, oak_planks: 12 }, { fixed: { oak_log: -3, oak_planks: 12 } }), true)
  assert.equal(deltaMatches({ oak_log: -3, birch_planks: 12 }, { fixed: { oak_log: -3, oak_planks: 12 } }), false) // wrong species
  assert.equal(deltaMatches({}, {}), true)
  // a smelt's fuel is left out of the check
  const smelt = { fixed: { raw_iron: -3, iron_ingot: 3 }, ignore: ['coal'] }
  assert.equal(deltaMatches({ raw_iron: -3, iron_ingot: 3, coal: -1 }, smelt), true)
  assert.equal(deltaMatches({ raw_iron: -3, iron_ingot: 2, coal: -1 }, smelt), false)
})

test('quarantineReason names the event that makes a step say nothing', () => {
  assert.equal(quarantineReason({ ok: true, events: [] }), null)
  assert.equal(quarantineReason({ ok: false, error: 'quarantined', events: [] }), 'quarantined')
  assert.equal(quarantineReason({ events: [{ type: 'hazard' }, { type: 'interference' }] }), 'interference')
  assert.equal(quarantineReason({ events: [{ type: 'stray_pickup' }] }), null)
  assert.equal(quarantineReason({ events: [{ type: 'stray_pickup' }] }, { stationStep: true }), 'stray_pickup')
})

test('nearestInLook picks the closest hit over all directions', () => {
  assert.equal(nearestInLook({ N: {}, E: {} }), null)
  assert.deepEqual(nearestInLook({
    N: { oak_log: { distance: 37.6 } },
    SW: { birch_log: { distance: 12.1 }, oak_log: { distance: 50 } }
  }), { direction: 'SW', name: 'birch_log', distance: 12.1 })
})
