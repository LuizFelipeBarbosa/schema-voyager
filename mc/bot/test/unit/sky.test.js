const test = require('node:test')
const assert = require('node:assert/strict')
const { hasOpenSky, skyLevel } = require('../../lib/sky')
const { fakeWorldBot } = require('./world_fixture')

// The fixture bot stands at (0.5, 10, 0.5).

test('open sky: nothing above the head', () => {
  const bot = fakeWorldBot({ '0,9,0': 'stone' })
  assert.equal(hasOpenSky(bot), true)
  assert.equal(skyLevel(bot), 10)
})

test('stone anywhere overhead closes the sky, and skyLevel is above the highest one', () => {
  const bot = fakeWorldBot({ '0,12,0': 'stone', '0,30,0': 'dirt' })
  assert.equal(hasOpenSky(bot), false)
  assert.equal(skyLevel(bot), 31)
})

test('leaves, grass and flowers overhead leave the sky open; water and lava do not', () => {
  assert.equal(hasOpenSky(fakeWorldBot({ '0,14,0': 'oak_leaves', '0,11,0': 'short_grass', '0,12,0': 'poppy' })), true)
  assert.equal(hasOpenSky(fakeWorldBot({ '0,40,0': 'water' })), false)
  assert.equal(hasOpenSky(fakeWorldBot({ '0,40,0': 'lava' })), false)
})

test('an unloaded cell above counts as closed', () => {
  const bot = fakeWorldBot()
  const blockAt = bot.blockAt
  bot.blockAt = (pos) => (pos.y === 50 ? null : blockAt(pos))
  assert.equal(hasOpenSky(bot), false)
})

test('the scan stops at the world top', () => {
  const bot = fakeWorldBot({ '0,400,0': 'stone' })
  assert.equal(hasOpenSky(bot), true) // above minY + height = 320
})
