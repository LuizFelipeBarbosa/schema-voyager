// The duel loop: fights one named player through repeated guard fight() calls until one of the two
// dies, the time is up or the duel is stopped, counts what happened and records one `duel_end`.
// See contracts/bot_api.md, "v3.6: duels".
const guard = require('./guard')
const tactics = require('./tactics')
const { round } = require('./world')

const DUEL_TIMEOUT_S = 60
const NO_OPPONENT_MS = 10000
const KILL_GRACE_MS = 500 // after the opponent's death, a death of the bot's own still counts as a double KO
const OPPONENT_POLL_MS = 250
const REENGAGE_PAUSE_MS = 100
const SHIELD_BLOCK_STATUS = 29
const PLAYER_HEALTH_METADATA = 9 // 'health' in registry.entitiesByName.player.metadataKeys (1.21.1)

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// -> a problem string for a 400, or null
function validateDuel (body) {
  if (typeof body.player !== 'string' || body.player === '') return '"player" must be a non-empty string'
  const tacticsProblem = tactics.validateTactics(body.tactics ?? [])
  if (tacticsProblem) return tacticsProblem
  const { timeout_s: timeoutS, duel_id: duelId } = body
  if (timeoutS !== undefined && !(Number.isFinite(timeoutS) && timeoutS >= 5 && timeoutS <= 300)) {
    return '"timeout_s" must be a number from 5 to 300'
  }
  if (duelId !== undefined && (typeof duelId !== 'string' || duelId.length > 64)) {
    return '"duel_id" must be a string of at most 64 characters'
  }
  return null
}

// A player's health as its entity metadata shows it, or null when not seen.
function metadataHealth (entity) {
  const health = entity?.metadata?.[PLAYER_HEALTH_METADATA]
  return typeof health === 'number' ? health : null
}

// Fights body.player until the duel ends. Never rejects; records `duel_end` exactly once and resolves
// its fields. `signal` is the action lock's, `stopSignal` is /duel/stop's; either one ends it `stopped`.
async function runDuel (bot, body, {
  eventLog, movements = null, signal = null, stopSignal = null,
  fight = guard.fight, createTactics = tactics.createTactics, noOpponentMs = NO_OPPONENT_MS,
  killGraceMs = KILL_GRACE_MS
} = {}) {
  const opponent = body.player
  const duelTactics = createTactics(bot, body.tactics ?? [])
  const startedAt = Date.now()
  const deadline = startedAt + (body.timeout_s ?? DUEL_TIMEOUT_S) * 1000
  const opponentId = () => bot.players[opponent]?.entity?.id
  const isStopped = () => Boolean(stopSignal?.aborted || signal?.aborted)

  const count = { swings: 0, hits: 0, crits: 0, blocked: 0, theirBlocked: 0, damageTaken: 0, damageDealt: null }
  let engagements = 0
  let botHealth = bot.health
  let opponentHealth = metadataHealth(bot.players[opponent]?.entity)
  if (opponentHealth !== null) count.damageDealt = 0
  let result = null // set by the first ending, which also records duel_end
  let opponentDied = false // the opponent's death is pending: `killed` is recorded after killGraceMs
  let graceTimer = null

  // The first call fixes the result and records `duel_end` at once: a death ends the action on the
  // same emit, and the answer's events must already hold it.
  function end (reason) {
    if (result) return
    result = {
      duel_id: body.duel_id ?? null,
      opponent,
      tactics: duelTactics.names,
      winner: reason === 'killed' ? bot.username : reason === 'died' ? opponent : null,
      reason,
      duration: round((Date.now() - startedAt) / 1000, 1),
      swings: count.swings,
      hits: count.hits,
      crits: count.crits,
      damage_dealt: count.damageDealt === null ? null : round(count.damageDealt, 1),
      damage_taken: round(count.damageTaken, 1),
      blocked: count.blocked,
      their_blocked: count.theirBlocked,
      health_end: round(bot.health, 1),
      engagements
    }
    eventLog.record('duel_end', result)
  }

  const onDeath = () => end(opponentDied ? 'both_died' : 'died')
  // A respawn reuses the entity id, so a killed opponent is never fought again. Both bots often die
  // in the same tick, so `killed` waits killGraceMs for the bot's own death.
  const onEntityDead = (entity) => {
    if (result || opponentDied || entity.id !== opponentId()) return
    opponentDied = true
    graceTimer = setTimeout(() => end('killed'), killGraceMs)
  }
  const onAttackedTarget = () => { count.swings++ }
  const onEntityHurt = (entity) => { if (entity?.id === opponentId()) count.hits++ }
  const onCritical = (entity) => { if (entity.id === opponentId()) count.crits++ }
  const onHealth = () => {
    if (bot.health < botHealth) count.damageTaken += botHealth - bot.health
    botHealth = bot.health
  }
  const onEntityUpdate = (entity) => {
    if (entity.id !== opponentId()) return
    const health = metadataHealth(entity)
    if (health === null) return
    if (opponentHealth !== null && health < opponentHealth) count.damageDealt += opponentHealth - health
    count.damageDealt ??= 0
    opponentHealth = health
  }
  const onEntityStatus = (packet) => {
    if (packet.entityStatus !== SHIELD_BLOCK_STATUS) return
    if (packet.entityId === bot.entity?.id) count.blocked++
    else if (packet.entityId === opponentId()) count.theirBlocked++
  }
  const botEvents = [
    ['death', onDeath], ['entityDead', onEntityDead], ['attackedTarget', onAttackedTarget],
    ['entityHurt', onEntityHurt], ['entityCriticalEffect', onCritical], ['health', onHealth],
    ['entityUpdate', onEntityUpdate]
  ]
  for (const [name, listener] of botEvents) bot.on(name, listener)
  bot._client.on('entity_status', onEntityStatus)

  try {
    let missingSince = null
    while (!result) {
      if (opponentDied) await sleep(REENGAGE_PAUSE_MS)
      else if (isStopped()) end('stopped')
      else if (Date.now() >= deadline) end('timeout')
      else if (!bot.players[opponent]?.entity) {
        missingSince ??= Date.now()
        if (Date.now() - missingSince >= noOpponentMs) end('no_opponent')
        else await sleep(OPPONENT_POLL_MS)
      } else {
        missingSince = null
        engagements++
        // `disengage` (later lane) changes fleeBelow here and adds the eating.
        await fight(bot, bot.players[opponent].entity, {
          timeoutMs: deadline - Date.now(), deadline, movements, eventLog, flee: false, sprint: true,
          tactics: duelTactics, fleeBelow: 0, shouldStop: () => Boolean(result) || opponentDied || isStopped()
        })
        if (!result) await sleep(REENGAGE_PAUSE_MS)
      }
    }
  } catch (err) {
    end('stopped') // fight() never rejects; this keeps the promise from rejecting if it ever does
  } finally {
    clearTimeout(graceTimer)
    for (const [name, listener] of botEvents) bot.removeListener(name, listener)
    bot._client.removeListener('entity_status', onEntityStatus)
  }
  return result
}

module.exports = { DUEL_TIMEOUT_S, validateDuel, runDuel }
