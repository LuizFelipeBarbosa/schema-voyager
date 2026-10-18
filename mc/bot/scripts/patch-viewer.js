// prismarine-viewer 1.33.0 assumes worlds start at y=0, as before 1.18. Our
// 1.21 superflat arena stands at y=-60, so the browser viewer showed empty
// sky. These patches to the bundled client fix it:
//   1. the camera only snapped to the bot when the bot was above y=0;
//   2. the renderer only meshed chunk sections from y=0 to 255;
//   3. the mesher hid every face whose neighbour was below y=0;
//   4. the worker looked sections up by y/16, so negative y found nothing
//      (1.18+ columns index sections from minY, so offset by it).
//   5. third person set the orbit camera once, 20 blocks above and behind +z
//      of the bot's first position, and never moved it again, so the bot
//      walked out of view. Now every position update carries the camera along
//      (orbit and zoom by hand are kept), eases it round to behind the walking
//      direction, and the page can freeze it with
//      postMessage({type: 'sv-pin', pinned: true}) (the trace panel's pin).
// Idempotent; runs as the package's postinstall.

const fs = require('fs')
const path = require('path')

const PUBLIC = path.join(__dirname, '..', 'node_modules', 'prismarine-viewer', 'public')

// Replaces `if(s&&(h.target.set(...),...,s=!1),i){` of the socket's `position`
// handler (s: first update, h: orbit controls, u: viewer, t: position, r: yaw,
// i: addMesh). The statement list runs, then the comma operator yields `i`.
const FOLLOW_FROM = 'if(s&&(h.target.set(t.x,t.y,t.z),u.camera.position.set(t.x,t.y+20,t.z+20),h.update(),s=!1),i){'
const FOLLOW_TO = 'if((()=>{' +
  'window.__svListen||(window.__svListen=1,window.addEventListener("message",m=>{m.data&&"sv-pin"===m.data.type&&(window.__svPin=!!m.data.pinned)}));' +
  'if(window.__svPin)return void(window.__svReplace=1);' +
  'if(s||window.__svReplace){h.target.set(t.x,t.y+1,t.z),u.camera.position.set(t.x+8*Math.sin(r),t.y+6,t.z+8*Math.cos(r)),s=!1,window.__svReplace=0}' +
  'else{const l=window.__svLast,dx=t.x-l.x,dy=t.y-l.y,dz=t.z-l.z;' +
  'h.target.x+=dx,h.target.y+=dy,h.target.z+=dz,u.camera.position.x+=dx,u.camera.position.y+=dy,u.camera.position.z+=dz;' +
  'if(dx*dx+dz*dz>.0025){const m=Math.hypot(dx,dz),cx=u.camera.position.x-h.target.x,cz=u.camera.position.z-h.target.z,cl=Math.hypot(cx,cz)||1,' +
  'nx=cx+(-dx/m*cl-cx)*.1,nz=cz+(-dz/m*cl-cz)*.1,nl=Math.hypot(nx,nz)||1;' +
  'u.camera.position.x=h.target.x+nx/nl*cl,u.camera.position.z=h.target.z+nz/nl*cl}}' +
  'window.__svLast={x:t.x,y:t.y,z:t.z},h.update()' +
  '})(),i){'

const PATCHES = [
  { file: 'index.js', from: 'if(t.y>0&&s&&(', to: 'if(s&&(', count: 1, supersededBy: FOLLOW_TO },
  { file: 'index.js', from: FOLLOW_FROM, to: FOLLOW_TO, count: 1 },
  { file: 'index.js', from: 'for(let i=0;i<256;i+=16)', to: 'for(let i=-64;i<320;i+=16)', count: 2 },
  { file: 'worker.js', from: 'position.y<0)continue', to: 'position.y<-64)continue', count: 2 },
  { file: 'worker.js', from: 'i&&i.sections[Math.floor(a/16)]', to: 'i&&i.sections[Math.floor((a-(i.minY||0))/16)]', count: 1 },
  { file: 'worker.js', from: 'l&&l.sections[Math.floor(n/16)]', to: 'l&&l.sections[Math.floor((n-(l.minY||0))/16)]', count: 1 }
]

for (const { file, from, to, count, supersededBy } of PATCHES) {
  const bundle = path.join(PUBLIC, file)
  const source = fs.readFileSync(bundle, 'utf8')
  const found = source.split(from).length - 1
  if (found === count) {
    fs.writeFileSync(bundle, source.split(from).join(to))
    console.log(`patch-viewer: ${file}: ${from} -> ${to}`)
  } else if (found === 0 && (source.includes(to) || (supersededBy && source.includes(supersededBy)))) {
    console.log(`patch-viewer: ${file}: already patched (${to})`)
  } else {
    console.warn(`patch-viewer: ${file}: expected ${count} x "${from}", found ${found}; prismarine-viewer changed?`)
  }
}
