// Armor and food: mineflayer-armor-manager and mineflayer-auto-eat, kept from
// clicking the inventory while an action holds the bot's lock. See
// contracts/bot_api.md, "Armor and food".

const IDLE_PERIOD_MS = 2000
const POLL_MS = 10
const STARTS_AT_FULL_HEALTH = 14
// Health only regenerates at food 18 or more, so while hurt the bot eats earlier.
const STARTS_AT_HURT = 17

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function logError (what, error) {
  console.log(`survival: ${what}: ${error && error.message ? error.message : error}`)
}

// Returns { hold, onIdleTick }. `plugins` and `now` are for tests.
function attachSurvival (bot, eventLog, {
  armorManager = require('mineflayer-armor-manager'),
  autoEat = require('mineflayer-auto-eat').plugin,
  now = () => Date.now()
} = {}) {
  // loadPlugin may defer the call until mineflayer's inject_allowed, so each
  // wrapper adjusts its plugin right after the real one has run.
  bot.loadPlugin((b, options) => {
    const before = b.listeners('playerCollect')
    armorManager(b, options)
    for (const listener of b.listeners('playerCollect')) {
      if (!before.includes(listener)) b.removeListener('playerCollect', listener)
    }
  })
  bot.loadPlugin((b, options) => {
    autoEat(b, options)
    Object.assign(b.autoEat.options, { startAt: STARTS_AT_FULL_HEALTH, equipOldItem: false, offhand: false })
  })

  bot.on('health', () => {
    if (!bot.autoEat) return
    bot.autoEat.options.startAt = bot.health < 20 ? STARTS_AT_HURT : STARTS_AT_FULL_HEALTH
  })
  bot.on('autoeat_finished', (item) => {
    eventLog.record('ate', { item: item && item.name, food: bot.food })
  })
  bot.on('autoeat_error', (error) => logError('autoeat_error', error))

  let holds = 0
  let settled = Promise.resolve() // the first hold's wait for the plugins to go quiet
  let equipping = Promise.resolve() // the last equipAll this module started
  let lastIdleRun = -Infinity

  function equipAll () {
    equipping = (async () => {
      try {
        if (bot.armorManager) await bot.armorManager.equipAll()
      } catch (error) {
        logError('equipAll', error)
      }
    })()
    return equipping
  }

  async function eat () {
    if (holds > 0) return // a hold opened after the equipAll that queued this eat
    try {
      if (bot.autoEat) await bot.autoEat.eat()
    } catch (error) {
      // Hungry with nothing to eat is the normal case until M3's hunting: not worth a log line every 2 s.
      if (error?.message !== 'No food found.') logError('eat', error)
    }
  }

  async function waitUntilNotEating () {
    const deadline = now() + bot.autoEat.options.eatingTimeout
    while (bot.autoEat.isEating && now() < deadline) await sleep(POLL_MS)
  }

  async function settle () {
    if (bot.autoEat) bot.autoEat.disable()
    await equipping // an eat it queued with .then(eat) has started by now
    if (bot.autoEat) await waitUntilNotEating()
  }

  return {
    async hold () {
      if (++holds === 1) settled = settle()
      await settled
      let released = false
      return function release () {
        if (released) return
        released = true
        if (--holds > 0) return
        if (bot.autoEat) bot.autoEat.enable()
        // The plugin drops the health and pickup events that came during the hold.
        equipAll().then(eat)
      }
    },

    onIdleTick () {
      if (holds > 0 || now() - lastIdleRun < IDLE_PERIOD_MS) return
      lastIdleRun = now()
      equipAll().then(eat)
    }
  }
}

module.exports = { attachSurvival }
