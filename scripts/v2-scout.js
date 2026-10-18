// Scouts the spawn area of the world running on this instance's server.
// Joins as VoyagerBot (stop the instance's bot first), waits for chunks, and
// prints a JSON summary of logs, exposed stone and exposed coal within RADIUS.
// Usage: NODE_PATH=$SV2_HOME/mc/bot/node_modules node scripts/v2-scout.js
const mineflayer = require('mineflayer')

const RADIUS = 40
const bot = mineflayer.createBot({
  host: '127.0.0.1',
  port: Number(process.env.SV2_MC_PORT || 25566),
  username: 'VoyagerBot',
  version: '1.21.1',
})

bot.once('spawn', async () => {
  await bot.waitForChunksToLoad()
  await bot.waitForTicks(40)
  const origin = bot.entity.position.floored()
  const isExposed = (p) => [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]]
    .some(([dx, dy, dz]) => bot.blockAt(p.offset(dx, dy, dz))?.name === 'air')
  const find = (matching, filter = () => true) => bot.findBlocks({ matching, maxDistance: RADIUS, count: 2000 })
    .filter(filter)
    .map((p) => ({ name: bot.blockAt(p).name, x: p.x, y: p.y, z: p.z, dist: Math.round(p.distanceTo(origin)) }))
    .sort((a, b) => a.dist - b.dist)

  const logs = find((b) => b.name.endsWith('_log'))
  const stone = find((b) => b.name === 'stone', isExposed)
  const coal = find((b) => b.name === 'coal_ore', isExposed)
  const nearSurfaceCoal = coal.filter((c) => c.y >= origin.y - 10) // not deep in a cave
  const species = {}
  for (const l of logs) species[l.name] = (species[l.name] || 0) + 1
  console.log(JSON.stringify({
    spawn: origin,
    biome: bot.blockAt(origin)?.biome?.name,
    logs: logs.length, species, nearestLogs: logs.slice(0, 3),
    exposedStone: stone.length, nearestStone: stone.slice(0, 3),
    exposedCoal: coal.length, nearestCoal: coal.slice(0, 3),
    nearSurfaceCoal: nearSurfaceCoal.length, nearestSurfaceCoal: nearSurfaceCoal.slice(0, 3),
  }))
  bot.end()
  process.exit(0)
})
bot.on('error', (e) => { console.error(String(e)); process.exit(1) })
bot.on('kicked', (r) => { console.error('kicked', r); process.exit(1) })
setTimeout(() => { console.error('timeout'); process.exit(1) }, 90000)
