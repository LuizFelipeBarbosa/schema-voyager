// Live v2 test of POST /smelt fuel accounting on a vanilla world: a stick is
// half a smelt, so one stick is refused and two work; planks are 1.5 smelts;
// coal is 8. Gives the bot items over RCON, calls /smelt, and checks the
// answer exactly.
//
// Uses the one bot already running at SV2_BOT_PORT and RCON at SV2_RCON_PORT
// (password RCON_PASSWORD, bot name BOT_USERNAME); starts nothing.
//
// Usage: node mc/bot/test/v2_smelt_live.js   (exit 0 = every case passed)

const { RconClient, health, loadEnv, post, requirePort } = require('./v2_helpers')

const short = (value) => JSON.stringify(value)
let failures = 0

function report (name, passed, detail) {
  if (!passed) failures++
  console.log(`${passed ? 'PASS' : 'FAIL'} ${name}${passed ? '' : ` | ${detail}`}`)
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
    const give = (item, count) => rcon.exec(`give ${username} ${item} ${count}`)
    const smelt = async (times, fuel) => (await post('/smelt', { ingredient: 'raw_iron', item: 'iron_ingot', times, fuel })).body

    await rcon.exec(`clear ${username}`)
    await give('raw_iron', 1)
    await give('stick', 1)
    await give('furnace', 1)

    const oneStick = await smelt(1, 'stick')
    report('1 stick for 1 smelt is refused, short by 1',
      oneStick.ok === false && oneStick.error === 'missing_ingredients' && oneStick.missing?.stick === 1, short(oneStick))

    await give('stick', 1)
    const twoSticks = await smelt(1, 'stick')
    report('2 sticks smelt 1 ingot',
      twoSticks.ok === true && twoSticks.smelted === 1 && twoSticks.inventory_delta?.iron_ingot === 1, short(twoSticks))

    await rcon.exec(`clear ${username}`)
    await give('raw_iron', 2)
    await give('oak_planks', 1)
    await give('furnace', 1)
    const onePlank = await smelt(2, 'oak_planks')
    report('1 plank for 2 smelts is refused, short by 1',
      onePlank.ok === false && onePlank.error === 'missing_ingredients' && onePlank.missing?.oak_planks === 1, short(onePlank))

    await give('oak_planks', 1)
    const twoPlanks = await smelt(2, 'oak_planks')
    report('2 planks smelt 2 ingots',
      twoPlanks.ok === true && twoPlanks.smelted === 2 && twoPlanks.inventory_delta?.iron_ingot === 2, short(twoPlanks))

    await rcon.exec(`clear ${username}`)
    await give('raw_iron', 3)
    await give('coal', 1)
    await give('furnace', 1)
    const coal = await smelt(3, 'coal')
    report('1 coal smelts 3 ingots',
      coal.ok === true && coal.smelted === 3 && coal.inventory_delta?.iron_ingot === 3 && coal.inventory_delta?.coal === -1, short(coal))
  } finally {
    rcon.close()
  }
}

main()
  .catch((err) => {
    failures++
    console.log(`FAIL aborted: ${err.message}`)
  })
  .finally(() => process.exit(failures ? 1 : 0))
