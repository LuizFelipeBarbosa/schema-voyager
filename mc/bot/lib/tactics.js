// The controller fight() drives during a melee fight (see contracts/bot_api.md, "v3.6: duels").
// The baseline, in every set: one per-instance override of mineflayer-pvp's strike that never
// strikes before a full attack cooldown, and a close-range mover that drives the movement controls
// within MOVER_RANGE of the target, one safety check per step. Named tactics are entries of REGISTRY.
//
// The tactic hook shape. An entry of REGISTRY is a function (bot) => hooks, called once per
// controller, so a tactic keeps its state in that closure. Every hook is optional:
//   canStrike(ctx)             -> false delays the strike to a later tick (the gate)
//   beforeStrike(ctx)          before the strike sequence starts (before the shield goes down)
//   afterStrike(ctx)           right after bot.attack, before the shield goes up again
//   steer(ctx, controls)       every tick the mover is active; may change the planned controls
//                              {forward, back, left, right, jump, sprint} before the safety check
//   attach(ctx) / detach(ctx)  with the controller
//   ownsShield: true           pvp's shield routine is skipped; the tactic raises and lowers it itself
// ctx: {bot, pvp, target, tick, ticksSinceStrike (Infinity before the first strike), cooldownTicks,
//   distance (3D, as pvp measures it), horizontal, dy (target y - bot y), inReach (pvp.wasInRange)}.
// Hooks run in the order of the names.

const { goals } = require('mineflayer-pathfinder')
const { Vec3 } = require('vec3')
const { getAttackSpeed } = require('mineflayer-pvp/lib/Cooldown')

const MOVER_RANGE = 4
const MOVER_RELEASE = 5
const MOVER_MAX_DY = 1
const MOVER_RELEASE_DY = 2 // a jump (1.25 blocks) or a knockback doesn't release the mover
const STAND_DISTANCE = 2.5
const STEP_BLOCKS = 0.8
const SHIELD_DOWN_MS = 100
const SHIELD_UP_DELAY_MS = 150
const STRIKE_RUNNING = 1e9 // pvp.timeToNextAttack while a strike sequence runs, so update() doesn't re-enter
const CONTROLS = ['forward', 'back', 'left', 'right', 'jump', 'sprint']
const HAZARDS = new Set(['lava', 'fire', 'soul_fire', 'magma_block', 'water', 'cactus', 'sweet_berry_bush', 'powder_snow'])

// One entry per tactic, added by its own lane: name -> (bot) => hooks.
const REGISTRY = {}

const TACTICS = Object.freeze(Object.keys(REGISTRY))
const DEFAULT_TACTICS = Object.freeze([])

// A problem for a 400, or null when `names` is a valid tactic list.
function validateTactics (names, registry = REGISTRY) {
  if (!Array.isArray(names)) return 'tactics must be an array of names'
  for (const [index, name] of names.entries()) {
    if (typeof name !== 'string') return 'tactics must be strings'
    if (!(name in registry)) return `unknown tactic: ${name}`
    if (names.indexOf(name) !== index) return `repeated tactic: ${name}`
  }
  return null
}

// True when the bot can stand at `position` (the next position of a step): a full block as the
// floor at the feet's y - 1 or y - 2, and no hazard in the feet cell, the cell below or the floor.
// Blocks that aren't loaded count as unsafe.
function isSafeStep (bot, position) {
  const x = Math.floor(position.x)
  const y = Math.floor(position.y)
  const z = Math.floor(position.z)
  const feet = bot.blockAt(new Vec3(x, y, z))
  const below = bot.blockAt(new Vec3(x, y - 1, z))
  const farBelow = bot.blockAt(new Vec3(x, y - 2, z))
  if (!feet || !below || !farBelow) return false
  const floor = below.boundingBox === 'block' ? below : farBelow
  if (floor.boundingBox !== 'block') return false
  return ![feet, below, floor].some((block) => HAZARDS.has(block.name))
}

// Where each horizontal control moves the bot, as a unit vector; mineflayer's yaw 0 faces -z.
function stepDirection (control, yaw) {
  const forward = { x: -Math.sin(yaw), z: -Math.cos(yaw) }
  const right = { x: Math.cos(yaw), z: -Math.sin(yaw) }
  if (control === 'forward') return forward
  if (control === 'back') return { x: -forward.x, z: -forward.z }
  if (control === 'right') return right
  return { x: -right.x, z: -right.z }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function createTactics (bot, names = [], { pvp = bot.pvp, registry = REGISTRY } = {}) {
  const problem = validateTactics(names, registry)
  if (problem) throw new Error(problem)
  const hooks = names.map((name) => registry[name](bot))
  const ownsShield = hooks.some((hook) => hook.ownsShield)

  let target = null // set while attached
  let tick = 0
  let lastStrikeTick = -Infinity
  let moverActive = false
  const applied = Object.fromEntries(CONTROLS.map((control) => [control, false])) // what the mover pressed

  function context () {
    const offset = target.position.minus(bot.entity.position)
    const heldName = bot.heldItem?.name
    return {
      bot,
      pvp,
      target,
      tick,
      ticksSinceStrike: tick - lastStrikeTick,
      cooldownTicks: Math.ceil(20 / getAttackSpeed(heldName)),
      distance: target.position.distanceTo(bot.entity.position),
      horizontal: Math.hypot(offset.x, offset.z),
      dy: offset.y,
      inReach: pvp.wasInRange
    }
  }

  function runHooks (name, ...args) {
    for (const hook of hooks) hook[name]?.(...args)
  }

  // pvp's attemptAttack, with a gate. A closed gate sets the timer to 0, so update() runs checkRange
  // and calls this again next tick (checkRange resets the timer on every range re-entry anyway).
  async function attemptAttack () {
    if (!target || !pvp.target) return
    const struck = target
    const ctx = context()
    const gateOpen = ctx.inReach &&
      ctx.ticksSinceStrike >= ctx.cooldownTicks &&
      hooks.every((hook) => hook.canStrike?.(ctx) !== false)
    if (!gateOpen) {
      pvp.timeToNextAttack = 0
      return
    }

    pvp.timeToNextAttack = STRIKE_RUNNING
    const aborted = () => target !== struck || pvp.target !== struck
    const useShield = !ownsShield && pvp.hasShield()
    try {
      runHooks('beforeStrike', ctx)
      if (useShield) {
        bot.deactivateItem()
        await sleep(SHIELD_DOWN_MS)
        if (aborted()) return
      }
      await bot.lookAt(struck.position.offset(0, struck.height, 0), true)
      if (aborted()) return
      bot.attack(struck)
      lastStrikeTick = tick
      bot.emit('attackedTarget')
      runHooks('afterStrike', context())
      if (useShield) {
        await sleep(SHIELD_UP_DELAY_MS)
        if (aborted()) return
        if (pvp.hasShield()) bot.activateItem(true)
      }
    } catch {
      // a failed look or hook ends this strike like an aborted one
    }
    if (!aborted()) pvp.timeToNextAttack = 0
  }

  function setControl (control, value) {
    if (applied[control] === value) return
    applied[control] = value
    bot.setControlState(control, value)
  }

  function releaseControls () {
    for (const control of CONTROLS) setControl(control, false)
  }

  // Releases every pressed horizontal control that would step somewhere unsafe, and the jump with it.
  function makeSafe (controls) {
    const { position, yaw } = bot.entity
    for (const control of ['forward', 'back', 'left', 'right']) {
      if (!controls[control]) continue
      const direction = stepDirection(control, yaw)
      const next = position.offset(direction.x * STEP_BLOCKS, 0, direction.z * STEP_BLOCKS)
      if (isSafeStep(bot, next)) continue
      controls[control] = false
      controls.jump = false
      if (control === 'forward') controls.sprint = false
    }
  }

  function onPhysicsTick () {
    tick++
    const ctx = context()
    const near = ctx.horizontal <= MOVER_RANGE && Math.abs(ctx.dy) <= MOVER_MAX_DY
    const far = ctx.horizontal > MOVER_RELEASE || Math.abs(ctx.dy) > MOVER_RELEASE_DY
    if (!moverActive && near) {
      // pathfinder's stop at each path end clears all controls and the horizontal velocity,
      // which would cancel jumps, strafes and knockback, so it must not hold the goal here
      moverActive = true
      bot.pathfinder.setGoal(null)
    } else if (moverActive && far) {
      moverActive = false
      releaseControls()
      bot.pathfinder.setGoal(new goals.GoalFollow(target, pvp.followRange), true)
    }
    if (!moverActive) return

    bot.lookAt(target.position.offset(0, target.height, 0), true).catch(() => {})
    const walkIn = ctx.horizontal > STAND_DISTANCE
    const controls = { forward: walkIn, back: false, left: false, right: false, jump: false, sprint: walkIn }
    runHooks('steer', ctx, controls)
    makeSafe(controls)
    for (const control of CONTROLS) setControl(control, controls[control])
  }

  return {
    names: [...names],

    attach (newTarget) {
      if (target) this.detach()
      target = newTarget
      tick = 0
      lastStrikeTick = -Infinity
      pvp.attemptAttack = attemptAttack
      bot.on('physicsTick', onPhysicsTick)
      runHooks('attach', context())
    },

    detach () {
      if (!target) return
      const ctx = context()
      bot.removeListener('physicsTick', onPhysicsTick)
      delete pvp.attemptAttack
      runHooks('detach', ctx)
      releaseControls()
      moverActive = false
      target = null // an in-flight strike sequence sees this and stops
      pvp.timeToNextAttack = 0
    }
  }
}

module.exports = {
  TACTICS,
  DEFAULT_TACTICS,
  MOVER_RANGE,
  MOVER_RELEASE,
  validateTactics,
  createTactics,
  isSafeStep
}
