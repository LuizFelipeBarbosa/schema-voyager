// Live v2 test of target choice and fail-fast pathing: from wherever the bot
// stands in a natural world, with only a wooden pickaxe, call /mine on coal
// (the options the life sends) until 3 coal are in the inventory, exploring
// after an `unreachable` or `not_found` as the life does. Prints, per attempt,
// the seconds, the distance walked, ok/error and the target, then the seconds
// to the first coal. Fails if the first coal takes more than 90 s.
//
// Uses the one bot already running at SV2_BOT_PORT and RCON at SV2_RCON_PORT
// (password RCON_PASSWORD, bot name BOT_USERNAME); starts nothing. Refuses to
// run against the live demo's ports (3000, 25575). Environment variables are
// loaded from $SV2_HOME/.env without overriding the ones already set.
//
// Usage: node mc/bot/test/v2_coal_live.js   (exit 0 = first coal within 90 s)

const { RconClient, health, loadEnv, nearestInLook, post, requirePort, sleep } = require('./v2_helpers')

const COAL_NEEDED = 3
const FIRST_COAL_LIMIT_S = 90
const GIVE_SETTLE_MS = 1500
const MAX_ATTEMPTS = 30
const TIME_LIMIT_MS = 10 * 60 * 1000
const COAL_BLOCKS = ['coal_ore', 'deepslate_coal_ore']
const MINE_MAX_DISTANCE = 32 // as Python's natural mode
const EXPLORE_DISTANCE = 32
const SCAFFOLD = ['dirt', 'cobbled_deepslate', 'netherrack', 'andesite', 'diorite', 'granite', 'tuff']
const DIRECTIONS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']

const seconds = (ms) => (ms / 1000).toFixed(1)

// Explores toward the nearest coal /look reports, else around the compass.
async function exploreAfterFailure (attempt) {
  const { body: look } = await post('/look', { blocks: COAL_BLOCKS, radius: 64 })
  const direction = nearestInLook(look.directions)?.direction ?? DIRECTIONS[attempt % DIRECTIONS.length]
  const { body, ms } = await post('/explore', { direction, distance: EXPLORE_DISTANCE, dig_through: true, scaffold: SCAFFOLD })
  console.log(`   explore ${direction}: ${seconds(ms)} s, walked ${body.travel?.distance ?? 0}, ${body.ok ? 'ok' : body.error}`)
}

async function main () {
  loadEnv()
  requirePort('SV2_BOT_PORT', [3000])
  requirePort('SV2_RCON_PORT', [25575])
  const username = process.env.BOT_USERNAME || 'VoyagerBot'
  if (!(await health()).connected) throw new Error('the bot is not connected to its server')

  const rcon = new RconClient()
  await rcon.connect()
  try {
    await rcon.exec(`clear ${username}`)
    await rcon.exec(`give ${username} minecraft:wooden_pickaxe 1`)
  } finally {
    rcon.close()
  }
  await sleep(GIVE_SETTLE_MS)
  await post('/reset_episode')

  const startedAt = Date.now()
  let firstCoalMs = null
  let coal = 0
  for (let attempt = 1; attempt <= MAX_ATTEMPTS && coal < COAL_NEEDED && Date.now() - startedAt < TIME_LIMIT_MS; attempt++) {
    const { body, ms } = await post('/mine', {
      block: COAL_BLOCKS[0],
      blocks: COAL_BLOCKS,
      tool: 'wooden_pickaxe',
      dig_through: true,
      max_distance: MINE_MAX_DISTANCE,
      scaffold: SCAFFOLD
    })
    const target = body.target ? `${body.target.x},${body.target.y},${body.target.z}` : 'none'
    console.log(`#${attempt} ${seconds(ms)} s, walked ${body.travel?.distance ?? 0}, ${body.ok ? 'ok' : body.error}, target ${target}`)
    coal = body.inventory?.coal ?? coal
    if (body.ok && coal > 0 && firstCoalMs === null) firstCoalMs = Date.now() - startedAt
    if (!body.ok) await exploreAfterFailure(attempt)
  }

  if (firstCoalMs === null) throw new Error('no coal was mined')
  console.log(`first coal after ${seconds(firstCoalMs)} s; ${coal} coal in the end`)
  if (firstCoalMs > FIRST_COAL_LIMIT_S * 1000) throw new Error(`first coal took more than ${FIRST_COAL_LIMIT_S} s`)
}

main()
  .then(() => process.exit(0))
  .catch((err) => {
    console.log(`FAIL ${err.message}`)
    process.exit(1)
  })
