// Writes src/schema_voyager/data/items_1_21_1.json from minecraft-data (v3.1e).
// Run from the repo root: node scripts/export-items.js
const fs = require('fs')
const path = require('path')

const minecraftDataDir = path.join(__dirname, '..', 'mc', 'bot', 'node_modules', 'minecraft-data')
const mcData = require(minecraftDataDir)('1.21.1')
const packageVersion = require(path.join(minecraftDataDir, 'package.json')).version

const items = mcData.itemsArray.map((item) => item.name).sort()
const foods = {}
for (const food of mcData.foodsArray.slice().sort((a, b) => (a.name < b.name ? -1 : 1))) {
  foods[food.name] = food.foodPoints
}

const outPath = path.join(__dirname, '..', 'src', 'schema_voyager', 'data', 'items_1_21_1.json')
const data = { version: '1.21.1', minecraft_data: packageVersion, items, foods }
fs.writeFileSync(outPath, JSON.stringify(data, null, 2) + '\n')
console.log(`wrote ${items.length} items and ${Object.keys(foods).length} foods to ${outPath}`)
