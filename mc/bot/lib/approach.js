// The v2 candidate loop of POST /mine: walks toward the best candidates, one
// attempt at a time, until one is reached, the call budget runs out or the
// attempts are used up. An attempt ends by progress rules (see
// gotoGoalDetailed), not by a fixed time, so a walk that is still getting
// closer is not cut off. See contracts/bot_api.md, "reaching a /mine target".

const { gotoGoalDetailed } = require('./world')

const CALL_MS = 60000
const APPROACH_WINDOW_MS = 10000 // an attempt ends after this long without getting 1 block closer
const MAX_ATTEMPTS = 4
const MIN_ATTEMPT_MS = 3000 // no new candidate is tried with less call budget than this
const SAME_POCKET_BLOCKS = 4 // candidates this close to a failed one are skipped for the call
const FAIL_FAST = { stallMs: 4000 }

const round1 = (value) => value === null ? null : Math.round(value * 10) / 10

// `goalFor(pos)` makes the goal for a candidate; `withMovements(pos, walk)`
// runs `walk()` under the movements that candidate needs; `mark(pos, end)` is
// told about each failed attempt (not `approaching`, where the bot is just
// nearer). An abort throws `signal.reason` and marks nothing.
// Returns {reached, targetPos (the last candidate tried), attempts}, each
// attempt {target, end, ms, start, closest, resets, think_ms}.
async function approachTarget (bot, candidates, { goalFor, withMovements, mark, signal = null }) {
  const callDeadline = Date.now() + CALL_MS
  const failed = []
  const attempts = []
  let targetPos = null
  let reached = false

  for (const candidate of candidates) {
    const remainingMs = callDeadline - Date.now()
    if (failed.length >= MAX_ATTEMPTS || (failed.length > 0 && remainingMs < MIN_ATTEMPT_MS)) break
    if (failed.some((pos) => pos.distanceTo(candidate) <= SAME_POCKET_BLOCKS)) continue
    targetPos = candidate

    const walk = await withMovements(candidate, () => gotoGoalDetailed(bot, goalFor(candidate), remainingMs, {
      failFast: FAIL_FAST,
      approach: { target: candidate, windowMs: APPROACH_WINDOW_MS },
      signal
    }))
    // Our own timer is the call budget, so that end means still approaching.
    const end = walk.end === 'timeout' ? 'approaching' : walk.end
    attempts.push({
      target: [candidate.x, candidate.y, candidate.z],
      end,
      ms: walk.ms,
      start: round1(walk.start),
      closest: round1(walk.closest),
      resets: walk.resets,
      think_ms: walk.thinkMs
    })

    if (walk.reached) {
      reached = true
      break
    }
    if (end === 'approaching') break
    mark(candidate, end)
    failed.push(candidate)
  }
  return { reached, targetPos, attempts }
}

module.exports = { approachTarget }
