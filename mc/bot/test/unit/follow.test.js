const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { Vec3 } = require('vec3')
const registry = require('prismarine-registry')('1.21.1')
const { createEventLog } = require('../../lib/events')
const { createGuard } = require('../../lib/guard')
const { createFollow, validateFollow } = require('../../lib/follow')

const sword = { name: 'iron_sword', slot: 36 }

function player (id, username, x) {
  return { id, type: 'player', username, name: 'player', position: new Vec3(x, 64, 0) }
}

function fixture ({ mode = 'defend', owners = 'Otopo' } = {}) {
  process.env.BOT_OWNERS = owners
  const bot = new EventEmitter()
  bot.health = 20
  bot.food = 20
  bot.entity = { id: 1, position: new Vec3(0, 64, 0) }
  bot.registry = registry
  bot.currentWindow = null
  bot.inventory = { items: () => [sword] }
  bot.equip = async () => {}
  bot.entities = {}
  bot.players = {}
  bot.setGoals = []
  bot.movementsSet = []
  bot.pathfinder = {
    goal: null,
    setGoal (goal) { this.goal = goal; bot.setGoals.push(goal) },
    setMovements: (movements) => bot.movementsSet.push(movements),
    isMoving: () => bot.pathMoving
  }
  bot.pathMoving = false
  const pvpCalls = []
  bot.pvp = Object.assign(new EventEmitter(), {
    attack (entity) { pvpCalls.push({ entity, movements: this.movements }) },
    stop () {}
  })
  let clock = 1000
  let free = true
  let ms = 0
  const log = createEventLog()
  const guard = createGuard(bot, { eventLog: log, mode, now: () => clock })
  const movements = { name: 'walking' }
  const follow = createFollow(bot, { guard, eventLog: log, isFree: () => free, movements: () => movements, now: () => ms, goneMs: 10000 })
  const addPlayer = (entity) => {
    bot.players[entity.username] = { username: entity.username, entity }
    bot.entities[entity.id] = entity
    return entity
  }
  const addMob = (id, name, x) => {
    bot.entities[id] = { id, name, type: 'hostile', position: new Vec3(x, 64, 0) }
    return bot.entities[id]
  }
  return {
    bot, guard, follow, log, movements, pvpCalls, addPlayer, addMob,
    setFree: (value) => { free = value },
    advanceMs: (delta) => { ms += delta },
    advanceSeconds: (delta) => { clock += delta },
    events: () => log.since(0).map((event) => event.type)
  }
}

test('validateFollow: player a non-empty string or null, distance 1 to 8', () => {
  assert.equal(validateFollow({ player: 'Tester' }), null)
  assert.equal(validateFollow({ player: null, distance: 8 }), null)
  assert.equal(validateFollow({ player: 'Tester', distance: 1 }), null)
  for (const body of [{}, { player: '' }, { player: 3 }, { player: 'T', distance: 0.5 }, { player: 'T', distance: 9 }, { player: 'T', distance: '3' }]) {
    assert.match(validateFollow(body), /must be/, JSON.stringify(body))
  }
})

test('follow: finds a player case-insensitively, answers not_found otherwise, and records follow_start', () => {
  const f = fixture()
  assert.deepEqual(f.follow.start('tester'), { ok: false, error: 'not_found' })
  f.bot.players.Tester = { username: 'Tester', entity: null }
  assert.deepEqual(f.follow.start('tester'), { ok: false, error: 'not_found' }) // no entity in view
  f.addPlayer(player(5, 'Tester', 6))
  assert.deepEqual(f.follow.start('tESTER', 4), { ok: true, player: 'Tester', distance: 4 })
  const started = f.log.since(0).find((event) => event.type === 'follow_start')
  assert.deepEqual([started.player, started.distance], ['Tester', 4])
})

test('follow: null picks the nearest owner that has an entity', () => {
  const f = fixture({ owners: 'Otopo, Far' })
  f.addPlayer(player(5, 'Stranger', 1))
  assert.deepEqual(f.follow.start(null), { ok: false, error: 'not_found' })
  f.addPlayer(player(6, 'Far', 20))
  f.addPlayer(player(7, 'Otopo', 9))
  assert.equal(f.follow.start(null).player, 'Otopo')
})

test('follow: the mode becomes defend, a second follow replaces the first, and stop restores the first mode', () => {
  const f = fixture({ mode: 'passive' })
  f.addPlayer(player(5, 'Tester', 6))
  f.addPlayer(player(6, 'Other', 6))
  f.follow.start('Tester')
  assert.equal(f.guard.mode, 'defend')
  f.follow.start('Other')
  assert.equal(f.follow.status().player, 'Other')
  assert.equal(f.follow.stop(), 'Other')
  assert.equal(f.guard.mode, 'passive')
  assert.equal(f.follow.stop(), null)
  const ends = f.log.since(0).filter((event) => event.type === 'follow_end')
  assert.deepEqual(ends.map((event) => event.reason), ['stopped'])
})

test('follow: the goal is set when free, not set again, not set while busy, and set again after a fight', () => {
  const f = fixture()
  const tester = f.addPlayer(player(5, 'Tester', 6))
  f.follow.start('Tester', 4)
  f.setFree(false)
  f.follow.tick()
  assert.equal(f.bot.setGoals.length, 0)
  f.setFree(true)
  f.follow.tick()
  assert.equal(f.bot.setGoals.length, 1)
  assert.equal(f.bot.pathfinder.goal.entity, tester)
  assert.equal(f.bot.pathfinder.goal.rangeSq, 16)
  assert.deepEqual(f.bot.movementsSet, [f.movements])
  f.bot.pathMoving = true
  f.follow.tick()
  assert.equal(f.bot.setGoals.length, 1)
  f.bot.pathMoving = false
  f.bot.pathfinder.setGoal(null) // a fight ends and clears the goal
  f.follow.tick()
  assert.equal(f.bot.setGoals.length, 3)
  assert.equal(f.bot.pathfinder.goal.entity, tester)
})

test('follow: a free bot resting more than distance + 1.5 from the player gets a fresh goal, unless moving or within', () => {
  const f = fixture()
  const tester = f.addPlayer(player(5, 'Tester', 5.5))
  f.follow.start('Tester', 3)
  f.follow.tick()
  assert.equal(f.bot.setGoals.length, 1)
  f.bot.pathMoving = true
  f.follow.tick()
  assert.equal(f.bot.setGoals.length, 1)
  f.bot.pathMoving = false
  tester.position = new Vec3(4, 64, 0) // within distance + 1.5
  f.follow.tick()
  assert.equal(f.bot.setGoals.length, 1)
  tester.position = new Vec3(5.5, 64, 0)
  f.setFree(false)
  f.follow.tick()
  assert.equal(f.bot.setGoals.length, 1)
  f.setFree(true)
  f.follow.tick()
  assert.equal(f.bot.setGoals.length, 2)
  assert.notEqual(f.bot.setGoals[1], f.bot.setGoals[0])
  assert.equal(f.bot.pathfinder.goal.entity, tester)
})

test('follow: no entity for 10 s ends it as gone; a returning player resets the clock', () => {
  const f = fixture()
  f.addPlayer(player(5, 'Tester', 6))
  f.follow.start('Tester')
  f.bot.players.Tester.entity = null
  f.follow.tick()
  f.advanceMs(9000)
  f.follow.tick()
  assert.notEqual(f.follow.status(), null)
  f.bot.players.Tester.entity = f.bot.entities[5]
  f.follow.tick()
  f.bot.players.Tester.entity = null
  f.follow.tick()
  f.advanceMs(9000)
  f.follow.tick()
  assert.notEqual(f.follow.status(), null)
  f.advanceMs(1000)
  f.follow.tick()
  assert.equal(f.follow.status(), null)
  const end = f.log.since(0).find((event) => event.type === 'follow_end')
  assert.deepEqual([end.player, end.reason], ['Tester', 'gone'])
  assert.equal(f.guard.mode, 'defend')
})

test('follow: the followed entity dying ends it as died, and the goal is cleared when free', () => {
  const f = fixture()
  const tester = f.addPlayer(player(5, 'Tester', 6))
  f.follow.start('Tester')
  f.follow.tick()
  f.bot.emit('entityDead', { id: 99 })
  assert.notEqual(f.follow.status(), null)
  f.bot.emit('entityDead', tester)
  assert.equal(f.follow.status(), null)
  assert.equal(f.bot.pathfinder.goal, null)
  assert.equal(f.log.since(0).find((event) => event.type === 'follow_end').reason, 'died')
})

test('follow: ending while busy leaves the pathfinder goal alone', () => {
  const f = fixture()
  f.addPlayer(player(5, 'Tester', 6))
  f.follow.start('Tester')
  f.follow.tick()
  const goal = f.bot.pathfinder.goal
  f.setFree(false)
  f.follow.stop()
  assert.equal(f.bot.pathfinder.goal, goal)
})

test('follow: status shows the player and the distance rounded to 0.1, null without an entity, null when not following', () => {
  const f = fixture()
  assert.equal(f.follow.status(), null)
  f.addPlayer(player(5, 'Tester', 4.24))
  f.follow.start('Tester')
  assert.deepEqual(f.follow.status(), { player: 'Tester', distance: 4.2 })
  f.bot.players.Tester.entity = null
  assert.deepEqual(f.follow.status(), { player: 'Tester', distance: null })
})

// ---- the guard defends the followed player ----

test('guard: a zombie that hurt the followed player is a player_attacked threat, for 3 s', () => {
  const f = fixture()
  const tester = f.addPlayer(player(5, 'Tester', 30))
  const zombie = f.addMob(8, 'zombie', 12)
  f.follow.start('Tester')
  assert.equal(f.guard.check(), null)
  f.bot.emit('entityHurt', tester, zombie)
  const { threat, decision } = f.guard.check()
  assert.deepEqual([threat.mob, threat.reason, decision], ['zombie', 'player_attacked', 'fight'])
  assert.equal(threat.distance, 12) // from the bot
  f.advanceSeconds(3.5)
  assert.equal(f.guard.check(), null)
})

test('guard: nothing is protected after the follow ends', () => {
  const f = fixture()
  const tester = f.addPlayer(player(5, 'Tester', 30))
  const zombie = f.addMob(8, 'zombie', 12)
  f.follow.start('Tester')
  f.bot.emit('entityHurt', tester, zombie)
  f.follow.stop()
  assert.equal(f.guard.check(), null)
  f.bot.emit('entityHurt', tester, zombie)
  assert.equal(f.guard.check(), null)
})

test('guard: a zombie 4 blocks from the player and more than 5 from the bot is player_near', () => {
  const f = fixture()
  f.addPlayer(player(5, 'Tester', 10))
  f.addMob(8, 'zombie', 14)
  f.follow.start('Tester')
  assert.equal(f.guard.check().threat.reason, 'player_near')
  f.bot.entities[8].position = new Vec3(16, 64, 0) // 6 from the player
  assert.equal(f.guard.check(), null)
})

test('guard: a zombie within 8 of the player and closing on it is player_near', () => {
  const f = fixture()
  f.addPlayer(player(5, 'Tester', 6))
  f.addMob(8, 'zombie', 14)
  f.follow.start('Tester')
  assert.equal(f.guard.check(), null)
  f.advanceSeconds(1)
  f.bot.entities[8].position = new Vec3(13, 64, 0)
  assert.equal(f.guard.check().threat.reason, 'player_near')
})

test('guard: a player who hurt the followed player, owner or not, is no threat', () => {
  const f = fixture({ owners: 'Otopo' })
  const tester = f.addPlayer(player(5, 'Tester', 2))
  const owner = f.addPlayer(player(6, 'Otopo', 3))
  const stranger = f.addPlayer(player(7, 'Stranger', 3))
  f.follow.start('Tester')
  f.bot.emit('entityHurt', tester, owner)
  f.bot.emit('entityHurt', tester, stranger)
  assert.equal(f.guard.check(), null)
  f.bot.emit('entityHurt', f.bot.entity, stranger) // v3.2: a player who hits the bot still is
  assert.equal(f.guard.check().threat.reason, 'attacked')
})

test('guard: order is a creeper, the bot\'s attackers, the player\'s attackers, then the nearest', () => {
  const f = fixture()
  const tester = f.addPlayer(player(5, 'Tester', 40))
  const near = f.addMob(8, 'zombie', 2)
  const hitBot = f.addMob(9, 'spider', 12)
  const hitPlayer = f.addMob(10, 'husk', 13)
  f.follow.start('Tester')
  assert.equal(f.guard.check().threat.id, 8)
  f.bot.emit('entityHurt', tester, hitPlayer)
  assert.equal(f.guard.check().threat.id, 10)
  f.bot.emit('entityHurt', f.bot.entity, hitBot)
  assert.equal(f.guard.check().threat.id, 9)
  const creeper = f.addMob(11, 'creeper', 7)
  f.guard.check()
  f.advanceSeconds(1)
  creeper.position = new Vec3(6, 64, 0)
  assert.equal(f.guard.check().threat.id, 11)
  assert.equal(near.id, 8)
})

test('guard: the quarry is never a player threat', () => {
  const f = fixture()
  const tester = f.addPlayer(player(5, 'Tester', 30))
  const zombie = f.addMob(8, 'zombie', 12)
  f.follow.start('Tester')
  f.guard.setQuarry(8)
  f.bot.emit('entityHurt', tester, zombie)
  assert.equal(f.guard.check(), null)
})

// ---- v3.3j ----

test('guard: the guard\'s fight paths with a copy of the movements that sprints', async () => {
  const seen = []
  for (const name of ['skeleton', 'stray', 'bogged', 'pillager', 'witch', 'zombie']) {
    const f = fixture()
    const target = f.addMob(8, name, 3)
    const movements = { name: 'walking', allowSprinting: false }
    const guard = createGuard(f.bot, { eventLog: f.log, mode: 'defend', movements: () => movements, respondCapMs: 400, outOfReachMs: 5000 })
    const picked = guard.check()
    assert.equal(picked.threat.mob, name)
    const responding = guard.respond(picked)
    setTimeout(() => f.bot.emit('entityDead', target), 30)
    await responding
    assert.equal(f.pvpCalls[0].movements.allowSprinting, true)
    assert.equal(movements.allowSprinting, false)
    seen.push(name)
  }
  assert.equal(seen.length, 6)
})

test('guard: a target out of reach ends the fight after 8 s for a skeleton and 5 s for a zombie', async (t) => {
  t.mock.timers.enable({ apis: ['Date', 'setInterval', 'setTimeout'] })
  for (const [name, expectedMs] of [['skeleton', 8000], ['zombie', 5000]]) {
    const f = fixture()
    f.addMob(8, name, 3)
    const guard = createGuard(f.bot, { eventLog: f.log, mode: 'defend', respondCapMs: 60000 })
    const picked = guard.check()
    f.bot.entities[8].position = new Vec3(6, 64, 0) // past REACH_BLOCKS, within sight
    const responding = guard.respond(picked)
    await new Promise((resolve) => setImmediate(resolve))
    const start = Date.now()
    let elapsed = null
    for (let step = 0; step < 1000 && guard.status().fighting; step++) {
      t.mock.timers.tick(50)
      await new Promise((resolve) => setImmediate(resolve))
      if (elapsed === null && f.events().includes('fight_end')) elapsed = Date.now() - start
    }
    await responding
    assert.ok(Math.abs(elapsed - expectedMs) <= 200, `${name}: ${elapsed}`)
  }
})
