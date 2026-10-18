// Crafting by filling the grid ourselves: used by /probe, and by /craft when
// the only way to pay a tag ingredient is by mixing variants (planks of two
// kinds in one pickaxe), which no single Mineflayer recipe covers.
//
// minecraft-data lists one recipe per tag member (a wooden pickaxe per planks
// type). The variants a grid cell accepts are read off those sibling recipes:
// same output, same shape, different ingredient id in that cell.

const { resyncWindow } = require('./world')

const OPEN_WINDOW_TIMEOUT_MS = 5000
const CLICK_TIMEOUT_MS = 3000

// --- Pure helpers ------------------------------------------------------------

// The cells a recipe fills: [{x, y, id}]. A shapeless recipe is laid out in
// reading order.
function recipeSlots (recipe) {
  if (recipe.inShape) {
    return recipe.inShape.flatMap((row, y) =>
      row.map((cell, x) => ({ x, y, id: cell.id })).filter((slot) => slot.id !== -1))
  }
  const width = recipe.requiresTable ? 3 : 2
  return recipe.ingredients.map((ingredient, i) => ({ x: i % width, y: Math.floor(i / width), id: ingredient.id }))
}

// The cells of `recipe` with every id each cell accepts: [{x, y, ids}], read
// off the sibling recipes (same output) that lay out the same cells.
function slotAlternatives (recipe, siblings) {
  const slots = recipeSlots(recipe)
  const sameLayout = (other) => {
    const otherSlots = recipeSlots(other)
    return other.result.count === recipe.result.count && other.requiresTable === recipe.requiresTable &&
      otherSlots.length === slots.length &&
      otherSlots.every((otherSlot, i) => otherSlot.x === slots[i].x && otherSlot.y === slots[i].y)
  }
  const variants = [recipe, ...siblings.filter(sameLayout)].map(recipeSlots)
  return slots.map((slot, i) => ({ x: slot.x, y: slot.y, ids: [...new Set(variants.map((cells) => cells[i].id))] }))
}

// Picks one id per cell from what each cell accepts, spending from `counts`
// (id -> how many are held). The most constrained cells choose first, each
// taking the id it has most of. Returns the chosen ids, parallel to `slots`,
// or null when the inventory cannot pay.
function assignGrid (slots, counts) {
  const remaining = new Map(Object.entries(counts).map(([id, count]) => [Number(id), count]))
  const chosen = new Array(slots.length).fill(null)
  const mostConstrainedFirst = slots.map((slot, i) => i).sort((a, b) => slots[a].ids.length - slots[b].ids.length)
  for (const i of mostConstrainedFirst) {
    let best = null
    for (const id of slots[i].ids) {
      const held = remaining.get(id) || 0
      if (held > 0 && (best === null || held > remaining.get(best))) best = id
    }
    if (best === null) return null
    chosen[i] = best
    remaining.set(best, remaining.get(best) - 1)
  }
  return chosen
}

// Window slot of grid cell (x, y); slot 0 is the output.
function gridSlotIndex (x, y, width) {
  return 1 + x + width * y
}

// A width x width grid of ids (null = empty) from cells and their chosen ids.
function gridOf (slots, chosen, width) {
  const grid = Array.from({ length: width }, () => new Array(width).fill(null))
  slots.forEach((slot, i) => { grid[slot.y][slot.x] = chosen[i] })
  return grid
}

// --- Mixed-variant planning --------------------------------------------------

// A grid that makes `itemId` by mixing variants from the inventory, if the
// inventory can pay for `times` runs: {recipe, grid} with the first run's grid
// as item names, or null.
function planMixedGrid (bot, itemId, times = 1) {
  const recipes = bot.recipesAll(itemId, null, true)
  const counts = {}
  for (const item of bot.inventory.items()) counts[item.type] = (counts[item.type] || 0) + item.count
  for (const recipe of recipes) {
    const slots = slotAlternatives(recipe, recipes)
    const allRuns = Array.from({ length: times }, () => slots).flat()
    const chosen = assignGrid(allRuns, counts)
    if (!chosen) continue
    const width = recipe.requiresTable ? 3 : 2
    const grid = gridOf(slots, chosen.slice(0, slots.length), width)
      .map((row) => row.map((id) => (id === null ? null : bot.registry.items[id].name)))
    return { recipe, grid }
  }
  return null
}

// --- Filling a window --------------------------------------------------------

function withTimeout (promise, ms, message) {
  let timer = null
  const timeout = new Promise((resolve, reject) => { timer = setTimeout(() => reject(new Error(message)), ms) })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

// Runs fn(window, width) with the 2x2 player grid, or with `table` opened
// as a 3x3 window. Afterwards the grid is emptied back into the inventory,
// whether fn worked or not, and a table window is closed.
async function withGridWindow (bot, table, fn) {
  const window = table
    ? await withTimeout(bot.openBlock(table), OPEN_WINDOW_TIMEOUT_MS, 'crafting table did not open')
    : bot.inventory
  const width = table ? 3 : 2
  try {
    return await fn(window, width)
  } finally {
    await returnGridToInventory(bot, window, width * width).catch((err) =>
      console.log(`warn: could not empty the crafting grid: ${err.message}`))
    if (table) await bot.closeWindow(window)
  }
}

// Puts one item in each cell named in `grid` (rows of item names or null).
async function fillGrid (bot, window, grid) {
  const width = grid.length
  for (let y = 0; y < width; y++) {
    for (let x = 0; x < width; x++) {
      if (!grid[y][x]) continue
      const id = bot.registry.itemsByName[grid[y][x]].id
      if (window.selectedItem?.type !== id) {
        const source = window.findInventoryItem(id, null)
        if (!source) throw new Error(`no ${grid[y][x]} in the inventory`)
        await bot.clickWindow(source.slot, 0, 0)
      }
      await bot.clickWindow(gridSlotIndex(x, y, width), 1, 0)
    }
  }
  if (window.selectedItem) await bot.putSelectedItemRange(window.inventoryStart, window.inventoryEnd, window, null)
}

// Resyncs `window` (so the stacks seen are the server's), then moves what is
// in its first `cells` grid slots and on the cursor back into the inventory.
// `window` must be the window the bot has open (bot.currentWindow or the
// player window).
async function returnGridToInventory (bot, window, cells) {
  await resyncWindow(bot, window)
  for (let slot = 1; slot <= cells; slot++) {
    if (window.slots[slot]) await withTimeout(bot.putAway(slot), CLICK_TIMEOUT_MS, `slot ${slot} did not empty`)
  }
  if (window.selectedItem) await bot.putSelectedItemRange(window.inventoryStart, window.inventoryEnd, window, null)
  await resyncWindow(bot, window)
}

// Empties every crafting grid the bot may have left items in: the table
// window if one is open (then closed), and the 2x2 grid of the player window,
// which is never closed and so keeps what Mineflayer's craft stranded there.
async function clearCraftingGrids (bot) {
  try {
    const open = bot.currentWindow
    if (open) {
      if (open.type.startsWith('minecraft:crafting')) await returnGridToInventory(bot, open, 9)
      await bot.closeWindow(open)
    }
    await returnGridToInventory(bot, bot.inventory, 4)
  } catch (err) {
    console.log(`warn: could not clear the crafting grid: ${err.message}`)
  }
}

function outputOf (window) {
  const item = window.slots[0]
  return item ? { item: item.name, count: item.count } : null
}

// Fills the grid and reads the server's output slot after a resync; the grid
// is emptied again by withGridWindow. Returns {output}.
async function probeGrid (bot, table, grid) {
  return withGridWindow(bot, table, async (window) => {
    await fillGrid(bot, window, grid)
    await resyncWindow(bot, window)
    return { output: outputOf(window) }
  })
}

// One run of a mixed-variant recipe: fills the grid and takes the output; what
// is left in the grid goes back to the inventory. Throws if the server does not make the item.
async function craftMixedOnce (bot, table, itemName) {
  return withGridWindow(bot, table, async (window, width) => {
    const plan = planMixedGrid(bot, bot.registry.itemsByName[itemName].id)
    if (!plan) throw new Error('the inventory cannot pay any mix of variants')
    if (plan.grid.length !== width) throw new Error(`the recipe needs a ${plan.grid.length}x${plan.grid.length} grid`)
    await fillGrid(bot, window, plan.grid)
    await resyncWindow(bot, window)
    const output = outputOf(window)
    if (output?.item !== itemName) throw new Error(`the server made ${output ? output.item : 'nothing'} from the mixed grid`)
    await bot.putAway(0)
  })
}

module.exports = {
  assignGrid, clearCraftingGrids, craftMixedOnce, gridOf, gridSlotIndex, planMixedGrid, probeGrid,
  recipeSlots, slotAlternatives, withTimeout
}
