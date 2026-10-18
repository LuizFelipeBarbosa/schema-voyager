// The creatures around the bot, for /observe and /status. Informational only: a strange entity is skipped.
const { round } = require('./world')

const NEARBY_RADIUS = 16
// minecraft-data entity types the contract leaves out: items, orbs, boats, ... / arrows, ... / armor stands.
const LEFT_OUT_KINDS = new Set(['other', 'projectile', 'living'])

// minecraft-data's entity type for this entity, or null for the kinds left out and for names the registry
// doesn't know. mineflayer overwrites entity.type (xp orbs get 'orb', unknown ones 'other'), so the name is
// looked up in the registry instead; players are the exception, mineflayer sets type 'player' on them.
function entityKind (registry, entity) {
  if (entity.type === 'player') return 'player'
  const kind = registry.entitiesByName[entity.name]?.type
  return kind && !LEFT_OUT_KINDS.has(kind) ? kind : null
}

// {[type]: {count, nearest, kind}} over bot.entities within `radius` of the bot, not counting the bot itself.
function nearbyEntities (bot, radius = NEARBY_RADIUS) {
  try {
    const nearby = {}
    if (!bot.entity) return nearby
    for (const entity of Object.values(bot.entities)) {
      if (entity === bot.entity || !entity.position) continue
      const distance = entity.position.distanceTo(bot.entity.position)
      const kind = entityKind(bot.registry, entity)
      if (distance > radius || !kind) continue
      const type = kind === 'player' ? 'player' : entity.name
      const seen = nearby[type]
      nearby[type] = { count: (seen?.count ?? 0) + 1, nearest: Math.min(seen?.nearest ?? Infinity, distance), kind }
    }
    for (const seen of Object.values(nearby)) seen.nearest = round(seen.nearest, 1)
    return nearby
  } catch {
    return {}
  }
}

module.exports = { NEARBY_RADIUS, entityKind, nearbyEntities }
