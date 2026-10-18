// Entry point for `node --test mc/bot/test/unit/`: this Node resolves a
// directory argument as a module (it loads this file) instead of expanding it
// to the test files, so the files are required from here.
const fs = require('node:fs')
const path = require('node:path')

for (const file of fs.readdirSync(__dirname).sort()) {
  if (file.endsWith('.test.js')) require(path.join(__dirname, file))
}
