// The bot's event log: a ring buffer of what happened to the bot (damage,
// death, interference, ...), and the listeners that fill it. Events carry the
// phase of the action in progress: "travel" before the step's own dig / craft
// / smelt, "step" from then on. See contracts/bot_api.md.

const { withinBox } = require('./geometry')
const { AIR_BLOCKS, readDroppedStack, round } = require('./world')

const RING_SIZE = 200
const INTERFERENCE_RADIUS = 2

function createEventLog (capacity = RING_SIZE, clock = () => Date.now() / 1000) {
  const events = []
  let lastSeq = 0
  let phase = 'travel'
  return {
    setPhase (next) { phase = next },
    phase () { return phase },
    record (type, fields = {}) {
      const event = { seq: ++lastSeq, t: round(clock(), 3), type, phase, ...fields }
      events.push(event)
      if (events.length > capacity) events.shift()
      return event
    },
    // Events with seq > afterSeq, oldest first.
    since (afterSeq) { return events.filter((event) => event.seq > afterSeq) },
    lastSeq () { return lastSeq }
  }
}

function cellOf (pos) {
  return { x: pos.x, y: pos.y, z: pos.z }
}

// Hooks `bot` into `log` and returns the helpers actions use to report what
// they see. `ownDrops` holds the item entities that belong to the action in
// progress; a pickup of any other item entity during its step is stray.
function attachEventTracker (bot, log) {
  const ownDrops = new Set()
  let lastHealth = bot.health
  let dead = false

  bot.on('health', () => {
    if (lastHealth != null && bot.health < lastHealth) {
      log.record('damage', { amount: round(lastHealth - bot.health, 1), health: round(bot.health, 1) })
    }
    lastHealth = bot.health
  })
  bot.on('death', () => {
    dead = true
    log.record('death', { position: positionOf(bot.entity.position) })
  })
  bot.on('spawn', () => {
    lastHealth = bot.health
    if (!dead) return
    dead = false
    log.record('respawn', { position: positionOf(bot.entity.position) })
  })
  bot.on('playerCollect', (collector, collected) => {
    if (log.phase() !== 'step' || collector !== bot.entity) return
    if (collected?.name !== 'item' || ownDrops.has(collected.id)) return
    const stack = readDroppedStack(collected)
    log.record('stray_pickup', { items: stack ? { [stack.name]: stack.count } : {} })
  })

  return {
    log,
    ownDrops,

    beginAction () {
      ownDrops.clear()
      log.setPhase('travel')
    },

    // Records `interference` for block changes and falling blocks near
    // `targetPos` until the returned stop() is called.
    watchInterference (targetPos) {
      const onBlockUpdate = (oldBlock, newBlock) => {
        if (!oldBlock || !newBlock || oldBlock.type === newBlock.type) return
        if (newBlock.position.equals(targetPos) || !withinBox(newBlock.position, targetPos, INTERFERENCE_RADIUS)) return
        // What changed: the block that vanished (leaf decay), or the one that appeared.
        const block = AIR_BLOCKS.has(newBlock.name) ? oldBlock.name : newBlock.name
        log.record('interference', { block, position: cellOf(newBlock.position) })
      }
      const onSpawn = (entity) => {
        if (entity.name !== 'falling_block' || !withinBox(entity.position.floored(), targetPos, INTERFERENCE_RADIUS)) return
        log.record('interference', { block: 'falling_block', position: cellOf(entity.position.floored()) })
      }
      bot.on('blockUpdate', onBlockUpdate)
      bot.on('entitySpawn', onSpawn)
      return () => {
        bot.removeListener('blockUpdate', onBlockUpdate)
        bot.removeListener('entitySpawn', onSpawn)
      }
    },

    // A promise that rejects (code "quarantined") if the bot dies; call
    // dispose() once the action is over.
    rejectOnDeath () {
      let onDeath = null
      const promise = new Promise((resolve, reject) => {
        onDeath = () => reject(Object.assign(new Error('the bot died'), { code: 'quarantined' }))
        bot.once('death', onDeath)
      })
      return { promise, dispose: () => bot.removeListener('death', onDeath) }
    },

    // A promise that rejects (code "interrupted", with `found` attached) when
    // the guard calls reject(found), once at most; dispose() when the action is over.
    rejectOnThreat () {
      let rejectPromise = null
      let disposed = false
      const promise = new Promise((resolve, reject) => { rejectPromise = reject })
      return {
        promise,
        reject: (found) => {
          if (!disposed) rejectPromise(Object.assign(new Error('the guard interrupted the action'), { code: 'interrupted', found }))
        },
        dispose: () => { disposed = true }
      }
    }
  }
}

function positionOf (pos) {
  return { x: round(pos.x), y: round(pos.y), z: round(pos.z) }
}

module.exports = { RING_SIZE, attachEventTracker, createEventLog }
