// prismarine-viewer 1.33.0 assumes worlds start at y=0, as before 1.18. Our
// 1.21 superflat arena stands at y=-60, so the browser viewer showed empty
// sky. These patches to the bundled client fix it:
//   1. the camera only snapped to the bot when the bot was above y=0;
//   2. the renderer only meshed chunk sections from y=0 to 255;
//   3. the mesher hid every face whose neighbour was below y=0;
//   4. the worker looked sections up by y/16, so negative y found nothing
//      (1.18+ columns index sections from minY, so offset by it).
// Idempotent; runs as the package's postinstall.

const fs = require('fs')
const path = require('path')

const PUBLIC = path.join(__dirname, '..', 'node_modules', 'prismarine-viewer', 'public')
const PATCHES = [
  { file: 'index.js', from: 'if(t.y>0&&s&&(', to: 'if(s&&(', count: 1 },
  { file: 'index.js', from: 'for(let i=0;i<256;i+=16)', to: 'for(let i=-64;i<320;i+=16)', count: 2 },
  { file: 'worker.js', from: 'position.y<0)continue', to: 'position.y<-64)continue', count: 2 },
  { file: 'worker.js', from: 'i&&i.sections[Math.floor(a/16)]', to: 'i&&i.sections[Math.floor((a-(i.minY||0))/16)]', count: 1 },
  { file: 'worker.js', from: 'l&&l.sections[Math.floor(n/16)]', to: 'l&&l.sections[Math.floor((n-(l.minY||0))/16)]', count: 1 }
]

for (const { file, from, to, count } of PATCHES) {
  const bundle = path.join(PUBLIC, file)
  const source = fs.readFileSync(bundle, 'utf8')
  const found = source.split(from).length - 1
  if (found === count) {
    fs.writeFileSync(bundle, source.split(from).join(to))
    console.log(`patch-viewer: ${file}: ${from} -> ${to}`)
  } else if (found === 0 && source.includes(to)) {
    console.log(`patch-viewer: ${file}: already patched (${to})`)
  } else {
    console.warn(`patch-viewer: ${file}: expected ${count} x "${from}", found ${found}; prismarine-viewer changed?`)
  }
}
