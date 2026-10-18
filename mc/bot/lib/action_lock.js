// The action lock: one action at a time, the guard's cut-in, and the rule that
// `busy` is held until the handler has settled. Dependencies are injected so
// none of this needs a server. See contracts/bot_api.md, "The guard".

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function withTimeout (promise, ms) {
  let timer = null
  const timeout = new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(Object.assign(new Error(`action timed out after ${ms} ms`), { code: 'timeout' })), ms)
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

// interruptBot: stops pathing and digging and closes a window (death, timeout).
// stopForGuard: the same without closing the window (the handler closes its own).
function createActionLock ({
  eventLog, interruptBot, stopForGuard, guard, survival, reconnect,
  settleMs = 10000, backstopMs = 45000, idleGapMs = 1000
}) {
  let current = null // the action that holds the lock, until its handler has settled and run() has returned
  let fighting = false // an idle fight is running
  let lastFightEnd = -Infinity

  // null when free; else the 409 detail: undefined (an action runs), 'orphan' or 'guard'.
  function busyDetail () {
    if (current) return current.aborted && !current.settled ? 'orphan' : undefined // else run() is still going
    return fighting ? 'guard' : null
  }

  function abort (action) {
    if (action.aborted) return
    action.aborted = true
    action.controller.abort(Object.assign(new Error('aborted'), { code: 'aborted' }))
    action.backstop = setTimeout(() => {
      if (action.settled) return
      eventLog.record('orphaned_handler', { path: action.path, reconnected: true })
      reconnect()
      releaseLock(action)
    }, backstopMs)
  }

  function releaseLock (action) {
    clearTimeout(action.backstop)
    if (action.releaseHold) action.releaseHold()
    if (current === action) current = null
  }

  // Answers a guard threat: stop the handler, wait for it, fight or flee.
  async function cutIn (action, found, death) {
    abort(action)
    stopForGuard()
    // The handler's own hold ends when it settles; the fight keeps the plugins quiet until it is over.
    const releaseFight = await survival.hold()
    try {
      let timer = null
      const timedOut = new Promise((resolve) => { timer = setTimeout(resolve, settleMs, 'timeout') })
      const result = await Promise.race([action.done.then(() => 'settled'), timedOut, death.promise]).finally(() => clearTimeout(timer))
      if (result === 'timeout') eventLog.record('orphaned_handler', { path: action.path })
      const response = await Promise.race([guard.respond(found, { action: action.path }), death.promise])
      return { kind: 'interrupted', threat: found.threat.mob, outcome: response.outcome }
    } finally {
      releaseFight()
    }
  }

  async function run ({ path, cutIn: cutsIn, timeoutMs, tracker, session, handler }) {
    const action = { path, cutIn: cutsIn, controller: new AbortController(), aborted: false, settled: false, started: false, interrupting: false }
    current = action
    const death = tracker.rejectOnDeath()
    const threat = tracker.rejectOnThreat()
    action.threat = threat
    death.promise.catch(() => {}) // a death while the hold is pending is answered once the race starts
    try {
      action.releaseHold = await survival.hold()
      const running = Promise.resolve().then(() => handler({ ...session, signal: action.controller.signal }))
      action.done = running.then(() => {}, () => {}).then(() => {
        action.settled = true
        if (action.responded) releaseLock(action)
      })
      action.started = true
      try {
        const payload = await withTimeout(Promise.race([running, death.promise, threat.promise]), timeoutMs)
        return { kind: 'ok', payload }
      } catch (err) {
        if (err.code !== 'interrupted') throw err
        return await cutIn(action, err.found, death)
      }
    } catch (err) {
      // A death (also during the cut-in) or a timeout; anything else is the handler's own error.
      if (err.code !== 'quarantined' && err.code !== 'timeout') throw err
      abort(action)
      interruptBot()
      return { kind: err.code }
    } finally {
      death.dispose()
      threat.dispose()
      action.responded = true
      // The lock goes with the later of the handler's end and this return (the cut-in fight runs after the handler).
      if (!action.started || action.settled) releaseLock(action) // not started: the hold failed
    }
  }

  async function fightIdle (found) {
    fighting = true // before any await: busyDetail() answers 'guard' from now on
    try {
      const release = await survival.hold()
      try {
        await guard.respond(found, { action: null })
      } finally {
        release()
      }
    } catch (err) {
      console.log(`guard: idle fight failed: ${err.message}`)
    } finally {
      fighting = false
      lastFightEnd = Date.now()
    }
  }

  // Called by the guard tick with guard.check()'s result (null: nothing to do).
  function onTick (found) {
    if (current) {
      if (!found) return
      if (current.started && current.cutIn && !current.aborted && !current.interrupting) {
        current.interrupting = true
        current.threat.reject(found)
        return
      }
      // An orphan, whose answer is out, doesn't stop the guard from fighting.
      if (!(current.aborted && current.responded)) return
    }
    if (fighting) return
    if (!found) {
      if (!current) survival.onIdleTick()
      return
    }
    if (Date.now() - lastFightEnd < idleGapMs) return
    return fightIdle(found)
  }

  return { busyDetail, run, onTick }
}

module.exports = { createActionLock }
