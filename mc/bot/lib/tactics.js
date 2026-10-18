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
//   afterSafety(ctx, released) right after the safety check: the horizontal controls it released
//                              (names, e.g. ['left']) because that step was blocked or unsafe
//   onTick(ctx)                every physics tick while attached, before the mover, active or not
//   attach(ctx) / detach(ctx)  with the controller
//   ownsShield: true           pvp's shield routine is skipped; the tactic raises and lowers it itself
// ctx: {bot, pvp, target, tick, ticksSinceStrike (Infinity before the first strike), cooldownTicks,
//   distance (3D, as pvp measures it), horizontal, dy (target y - bot y), inReach (pvp.wasInRange),
//   shieldRoutine (pvp's shield routine runs: no tactic owns the shield and the bot has one)}.
// Registry functions are called as (bot, {random}), random being Math.random unless createTactics is given one.
// Hooks run in REGISTRY order, whatever the order of the names: movement shapers first, crit last so
// its sprint and jump decisions win.

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
const JUMP_VELOCITY = Math.fround(0.42) // prismarine-physics: a jump's initial vertical velocity
const GRAVITY = 0.08
const AIR_DRAG = Math.fround(1 - 0.02) // vertical velocity = (velocity - GRAVITY) * AIR_DRAG each tick
const CRIT_REACH = 3.5 // horizontal distance within which crit jumps
const CRIT_CLOSING_BLOCKS = 1 // what walking in gains over a jump's rise
const CRIT_FALLBACK_TICKS = 10 // full cooldown and in reach this long without a crit strike: strike on the ground
const CRIT_MARGIN_TICKS = 1 // aim the strike this many ticks after the first descending one
const SHIELD_STRIKE_LEAD = 3 // gate open -> bot.attack: shield down (2 ticks) plus the lookAt
const BARE_STRIKE_LEAD = 1 // the lookAt
const SHIELD_RANGE = 4.5 // the opponent can strike from here
const SHIELD_WARMUP_TICKS = 5 // a raised shield blocks only after this many ticks (vanilla)
const SHIELD_MARGIN_TICKS = 2 // raise this much earlier than the warm-up needs
const SHIELD_HOLD_TICKS = 3 // hold our strike when the opponent's is due within this many ticks
const SHIELD_MAX_HOLD_TICKS = 8 // but never hold this many ticks in a row
const SHIELD_RANGED_MOBS = new Set(['skeleton', 'stray', 'bogged', 'pillager'])
const SHIELD_RANGED_CLOSE = 3 // against ranged mobs the shield stays up beyond this distance
const SHIELD_STRIKE_STALE_TICKS = 10 // a strike sequence still running after this long has failed
const WTAP_RELEASE_TICKS = 3 // ticks after a strike that forward and sprint stay released
const STRAFE_RANGE = 4 // horizontal distance within which strafe circles
const STRAFE_MIN_TICKS = 15 // a side is held for STRAFE_MIN_TICKS to STRAFE_MAX_TICKS steer ticks
const STRAFE_MAX_TICKS = 35
const HAZARDS = new Set(['lava', 'fire', 'soul_fire', 'magma_block', 'water', 'cactus', 'sweet_berry_bush', 'powder_snow'])

// A flat-ground jump, in simulated ticks counted from the one that applies the jump (1): the first
// tick that lowers the bot (the server then has fallDistance > 0) and the tick it lands.
function jumpTimeline () {
  let velocity = JUMP_VELOCITY
  let height = 0
  let descendsAt = null
  for (let tick = 1; ; tick++) {
    if (velocity < 0 && descendsAt === null) descendsAt = tick
    height += velocity
    if (height <= 0) return { descendsAt, landsAt: tick }
    velocity = (velocity - GRAVITY) * AIR_DRAG
  }
}

const JUMP = jumpTimeline()

// One entry per tactic, added by its own lane: name -> (bot) => hooks.
const REGISTRY = {
  // Sprint reset: release forward and sprint for a moment after each strike and press them again, so
  // the server sees a fresh start-sprinting and every strike is a sprint hit (extra knockback).
  // Between strikes it keeps walking into the target, which the baseline mover would not do within 2.5 blocks.
  wtap (bot) {
    return {
      steer (ctx, controls) {
        if (ctx.ticksSinceStrike <= WTAP_RELEASE_TICKS) {
          controls.forward = false
          controls.sprint = false
        } else if (bot.entity.onGround) {
          controls.forward = true
          controls.sprint = true
        }
      }
    }
  },

  // Circle the target: hold left or right and switch sides now and then (a random 15 to 35 ticks), or at
  // once when the safety check released the side (blocked or unsafe: the bot wouldn't move).
  strafe (bot, { random }) {
    let side = null // 'left' or 'right', set on attach
    let ticksHeld = 0
    let holdTicks = 0

    const drawHoldTicks = () => STRAFE_MIN_TICKS + Math.floor(random() * (STRAFE_MAX_TICKS - STRAFE_MIN_TICKS + 1))
    const switchSide = () => {
      side = side === 'left' ? 'right' : 'left'
      ticksHeld = 0
      holdTicks = drawHoldTicks()
    }

    return {
      attach () {
        side = random() < 0.5 ? 'left' : 'right'
        ticksHeld = 0
        holdTicks = drawHoldTicks()
      },

      steer (ctx, controls) {
        if (ticksHeld >= holdTicks) switchSide()
        ticksHeld++
        if (ctx.horizontal <= STRAFE_RANGE) controls[side] = true
      },

      afterSafety (ctx, released) {
        if (released.includes(side)) switchSide()
      }
    }
  },

  // Shield timing, replacing pvp's routine: raise the shield when the opponent's next strike is due (its last
  // swing plus its weapon's cooldown) within the warm-up, lower it only for our own strike, and hold our
  // strike for a moment when the block is about to count, so that we strike right after its swing.
  // Ticks are the controller's: a swing is stamped with the last tick onTick saw.
  shield (bot) {
    let target = null
    let currentTick = 0
    let lastSwingTick = null // null before the opponent's first swing: its strike is due now
    let raisedAt = null // the tick the shield went up, null while it is down
    let strikeStartedAt = null // the tick our strike sequence began, until afterStrike
    let heldTicks = 0 // consecutive ticks the gate has been held

    const onSwing = (entity) => {
      if (entity.id === target?.id) lastSwingTick = currentTick
    }
    const ticksUntilTheirStrike = (ctx) => {
      if (lastSwingTick === null) return 0
      return lastSwingTick + Math.ceil(20 / getAttackSpeed(ctx.target.heldItem?.name)) - ctx.tick
    }
    const shieldWanted = (ctx) => {
      // a sequence that failed before bot.attack never reaches afterStrike, so it counts only SHIELD_STRIKE_STALE_TICKS
      const striking = strikeStartedAt !== null && ctx.tick - strikeStartedAt < SHIELD_STRIKE_STALE_TICKS
      if (striking || !ctx.pvp.hasShield()) return false
      if (SHIELD_RANGED_MOBS.has(ctx.target.name) && ctx.distance > SHIELD_RANGED_CLOSE) return true
      return ctx.distance <= SHIELD_RANGE &&
        ticksUntilTheirStrike(ctx) <= SHIELD_WARMUP_TICKS + SHIELD_MARGIN_TICKS
    }
    const lower = () => {
      if (raisedAt === null) return
      bot.deactivateItem()
      raisedAt = null
    }

    return {
      ownsShield: true,

      attach (ctx) {
        target = ctx.target
        currentTick = ctx.tick
        lastSwingTick = null
        raisedAt = null
        strikeStartedAt = null
        heldTicks = 0
        bot.on('entitySwingArm', onSwing)
      },

      onTick (ctx) {
        currentTick = ctx.tick
        const wanted = shieldWanted(ctx)
        if (wanted && raisedAt === null) {
          bot.activateItem(true)
          raisedAt = ctx.tick
        } else if (!wanted) {
          lower()
        }
      },

      canStrike (ctx) {
        const warm = raisedAt !== null && ctx.tick - raisedAt >= SHIELD_WARMUP_TICKS
        if (!warm || ticksUntilTheirStrike(ctx) > SHIELD_HOLD_TICKS) {
          heldTicks = 0
          return true
        }
        if (heldTicks >= SHIELD_MAX_HOLD_TICKS) return true
        heldTicks++
        return false
      },

      beforeStrike (ctx) {
        lower()
        strikeStartedAt = ctx.tick
      },

      afterStrike () {
        strikeStartedAt = null
        heldTicks = 0
      },

      detach () {
        lower()
        bot.removeListener('entitySwingArm', onSwing)
      }
    }
  },

  // Jump so that the strike lands while falling, with sprint released: a critical hit (1.5x damage).
  // The server needs the descent in the position packets and the stop-sprinting before the jump.
  // Ticks here are counted from the physicsTick that requested the jump (0); the jump is simulated in the next.
  crit (bot) {
    let jumpTick = null // the tick that requested the jump, until it strikes or ends
    let missedJump = false // a jump ended without a strike: strike on the ground
    let waitingSince = null // the first tick of the current run of open cooldown gates
    let lastGateTick = null

    const strikeLead = (ctx) => ctx.shieldRoutine ? SHIELD_STRIKE_LEAD : BARE_STRIKE_LEAD
    const hasCeiling = () => {
      const above = bot.blockAt(bot.entity.position.floored().offset(0, 2, 0))
      return Boolean(above) && above.boundingBox !== 'empty'
    }

    return {
      attach () {
        jumpTick = null
        missedJump = false
        waitingSince = null
        lastGateTick = null
      },

      steer (ctx, controls) {
        if (jumpTick !== null) {
          if (!bot.entity.onGround) {
            controls.sprint = false
            return
          }
          jumpTick = null // landed, or the jump was dropped, without striking
          missedJump = true
        }
        if (missedJump || !bot.entity.onGround) return
        const closing = controls.forward ? CRIT_CLOSING_BLOCKS : 0
        if (ctx.horizontal - closing > CRIT_REACH) return
        // jump early enough that the cooldown is full when the gate opens for a strike landing just after the fall starts
        const jumpLead = JUMP.descendsAt + CRIT_MARGIN_TICKS - strikeLead(ctx)
        if (ctx.ticksSinceStrike < ctx.cooldownTicks - jumpLead || hasCeiling()) return
        controls.sprint = false
        if (bot.controlState?.sprint) return // the server sees stop-sprinting a tick before the jump
        controls.jump = true
        jumpTick = ctx.tick
      },

      canStrike (ctx) {
        if (lastGateTick === null || ctx.tick - lastGateTick > 1) waitingSince = ctx.tick
        lastGateTick = ctx.tick

        const { onGround, velocity } = bot.entity
        if (!onGround && velocity.y < 0) return true
        const risingIntoFall = jumpTick !== null && !onGround &&
          ctx.tick - jumpTick + strikeLead(ctx) >= JUMP.descendsAt + CRIT_MARGIN_TICKS
        if (risingIntoFall) return true
        return missedJump || ctx.tick - waitingSince > CRIT_FALLBACK_TICKS
      },

      afterStrike () {
        jumpTick = null
        missedJump = false
        lastGateTick = null
      }
    }
  }
}

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

function createTactics (bot, names = [], { pvp = bot.pvp, registry = REGISTRY, random = Math.random } = {}) {
  const problem = validateTactics(names, registry)
  if (problem) throw new Error(problem)
  const registryOrder = Object.keys(registry)
  const hooks = [...names]
    .sort((a, b) => registryOrder.indexOf(a) - registryOrder.indexOf(b))
    .map((name) => registry[name](bot, { random }))
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
      inReach: pvp.wasInRange,
      shieldRoutine: !ownsShield && pvp.hasShield()
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
  // Returns the names of the released horizontal controls.
  function makeSafe (controls) {
    const { position, yaw } = bot.entity
    const released = []
    for (const control of ['forward', 'back', 'left', 'right']) {
      if (!controls[control]) continue
      const direction = stepDirection(control, yaw)
      const next = position.offset(direction.x * STEP_BLOCKS, 0, direction.z * STEP_BLOCKS)
      if (isSafeStep(bot, next)) continue
      controls[control] = false
      controls.jump = false
      if (control === 'forward') controls.sprint = false
      released.push(control)
    }
    return released
  }

  function onPhysicsTick () {
    tick++
    const ctx = context()
    runHooks('onTick', ctx)
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
    runHooks('afterSafety', ctx, makeSafe(controls))
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
  jumpTimeline,
  validateTactics,
  createTactics,
  isSafeStep
}
