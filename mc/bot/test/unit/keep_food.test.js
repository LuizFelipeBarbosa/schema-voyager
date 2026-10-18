const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { attachSurvival, validateKeptFood } = require('../../lib/survival')
const { createEventLog } = require('../../lib/events')

const DEFAULT_BANNED = ['pufferfish', 'rotten_flesh']

function setup () {
  const bot = new EventEmitter()
  bot.health = 20
  bot.food = 20
  bot.loadPlugin = (plugin) => plugin(bot, {})
  const pluginArray = [...DEFAULT_BANNED]
  const autoEat = (b) => {
    b.autoEat = {
      options: { startAt: 16, eatingTimeout: 50, bannedFood: pluginArray },
      enable () {}, disable () {}, isEating: false, eat: async () => {}
    }
  }
  const survival = attachSurvival(bot, createEventLog(), { armorManager: () => {}, autoEat })
  return { bot, survival, pluginArray }
}

test('the kept list is the default plus the items, and [] restores the default', () => {
  const { bot, survival } = setup()
  assert.deepEqual(survival.setKeptFood(['beef']), ['beef'])
  assert.deepEqual(bot.autoEat.options.bannedFood, [...DEFAULT_BANNED, 'beef'])
  survival.setKeptFood([])
  assert.deepEqual(bot.autoEat.options.bannedFood, DEFAULT_BANNED)
})

test("the plugin's default array is never mutated", () => {
  const { bot, survival, pluginArray } = setup()
  survival.setKeptFood(['beef', 'porkchop'])
  survival.setKeptFood(['mutton'])
  assert.deepEqual(pluginArray, DEFAULT_BANNED)
  assert.notEqual(bot.autoEat.options.bannedFood, pluginArray)
})

test('a starving bot (food 0, health 8 or less) may eat the kept items, and they are kept again after', () => {
  const { bot, survival } = setup()
  survival.setKeptFood(['beef'])
  bot.food = 0
  bot.health = 9
  bot.emit('health')
  assert.deepEqual(bot.autoEat.options.bannedFood, [...DEFAULT_BANNED, 'beef'])
  bot.health = 8
  bot.emit('health')
  assert.deepEqual(bot.autoEat.options.bannedFood, DEFAULT_BANNED)
  bot.food = 3
  bot.emit('health')
  assert.deepEqual(bot.autoEat.options.bannedFood, [...DEFAULT_BANNED, 'beef'])
})

test('items set while starving wait for the valve to close', () => {
  const { bot, survival } = setup()
  bot.food = 0
  bot.health = 4
  survival.setKeptFood(['beef'])
  assert.deepEqual(bot.autoEat.options.bannedFood, DEFAULT_BANNED)
  bot.food = 6
  bot.emit('health')
  assert.deepEqual(bot.autoEat.options.bannedFood, [...DEFAULT_BANNED, 'beef'])
})

test('validateKeptFood takes an array of at most 32 item names', () => {
  assert.equal(validateKeptFood([]), null)
  assert.equal(validateKeptFood(['beef']), null)
  assert.equal(validateKeptFood(Array(32).fill('beef')), null)
  for (const bad of [undefined, 'beef', { 0: 'beef' }, [1], [''], [null], Array(33).fill('beef')]) {
    assert.match(validateKeptFood(bad), /items/, JSON.stringify(bad))
  }
})
