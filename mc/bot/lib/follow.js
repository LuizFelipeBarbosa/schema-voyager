// Following a player: the pathfinder goal GoalFollow, kept up across fights and actions, with the guard
// defending the player. The bot is injected so none of this needs a server. See contracts/bot_api.md, "POST /follow".

const { goals } = require('mineflayer-pathfinder')
const { round } = require('./world')
const { parseOwners } = require('./chat_commands')

const DEFAULT_DISTANCE = 3
const MIN_DISTANCE = 1
const MAX_DISTANCE = 8
const GONE_MS = 10000

function validateFollow (body) {
  const { player, distance } = body
  if (player !== null && !(typeof player === 'string' && player.length > 0)) return '"player" must be a non-empty string or null'
  if (distance != null && !(typeof distance === 'number' && distance >= MIN_DISTANCE && distance <= MAX_DISTANCE)) {
    return `"distance" must be a number from ${MIN_DISTANCE} to ${MAX_DISTANCE}`
  }
  return null
}

// guard: {mode, setMode, setProtected}. isFree(): true when no action, guard fight or flight, or orphan holds the bot.
// movements(): the walking movements, or null before the spawn.
function createFollow (bot, { guard, eventLog, isFree, movements = () => null, now = () => Date.now(), goneMs = GONE_MS }) {
  let following = null // {player, distance, previousMode, goal, goalEntity, entityId, noEntitySince}

  const entityOf = (username) => bot.players[username]?.entity

  // The exact username of the player to follow, or null: `name` case-insensitively, or the nearest owner in view.
  function findPlayer (name) {
    const withEntity = Object.values(bot.players).filter((player) => player.entity)
    if (name !== null) return withEntity.find((player) => player.username.toLowerCase() === name.toLowerCase())?.username ?? null
    const owners = parseOwners(process.env.BOT_OWNERS)
    const botPos = bot.entity.position
    const distanceTo = (player) => botPos.distanceTo(player.entity.position)
    const nearest = withEntity
      .filter((player) => owners.includes(player.username.toLowerCase()))
      .sort((a, b) => distanceTo(a) - distanceTo(b))[0]
    return nearest?.username ?? null
  }

  function end (reason) {
    const { player, previousMode, goal } = following
    following = null
    guard.setProtected(null)
    guard.setMode(previousMode)
    if (isFree() && bot.pathfinder.goal === goal) bot.pathfinder.setGoal(null)
    eventLog.record('follow_end', { player, reason })
  }

  const onEntityDead = (entity) => {
    if (following && entity.id === following.entityId) end('died')
  }
  bot.on('entityDead', onEntityDead)

  return {
    // -> {ok: true, player, distance} | {ok: false, error: 'not_found'}
    start (name, distance = DEFAULT_DISTANCE) {
      const player = findPlayer(name)
      if (player === null) return { ok: false, error: 'not_found' }
      following = {
        player,
        distance,
        previousMode: following ? following.previousMode : guard.mode, // a second /follow keeps the first one's
        goal: null,
        goalEntity: null,
        entityId: entityOf(player).id,
        noEntitySince: null
      }
      guard.setMode('defend')
      guard.setProtected(player)
      eventLog.record('follow_start', { player, distance })
      return { ok: true, player, distance }
    },

    // -> the player it was following, or null.
    stop () {
      if (!following) return null
      const { player } = following
      end('stopped')
      return player
    },

    // Called on each guard tick: ends the following when the player is gone, and sets the goal when the bot is free.
    tick () {
      if (!following) return
      const entity = entityOf(following.player)
      if (!entity) {
        following.noEntitySince ??= now()
        if (now() - following.noEntitySince >= goneMs) end('gone')
        return
      }
      following.noEntitySince = null
      following.entityId = entity.id
      if (!isFree() || (bot.pathfinder.goal === following.goal && following.goalEntity === entity)) return
      following.goal = new goals.GoalFollow(entity, following.distance)
      following.goalEntity = entity
      const walking = movements()
      if (walking) bot.pathfinder.setMovements(walking)
      bot.pathfinder.setGoal(following.goal, true)
    },

    // -> {player, distance} while following, else null.
    status () {
      if (!following) return null
      const entity = entityOf(following.player)
      return { player: following.player, distance: entity ? round(bot.entity.position.distanceTo(entity.position), 1) : null }
    },

    dispose () {
      bot.removeListener('entityDead', onEntityDead)
    }
  }
}

module.exports = { createFollow, validateFollow }
