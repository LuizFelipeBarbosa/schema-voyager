const test = require('node:test')
const assert = require('node:assert/strict')
const { createViewerControl, localCors } = require('../../lib/viewer')

function fakeBot () {
  return { viewer: { closed: 0, close () { this.closed++ } } }
}

function control (launched, mode) {
  return createViewerControl({ port: 3207, mode, restartDelayMs: 1, launch: (bot, options) => launched.push(options) })
}

test('attach starts the viewer in the start mode', () => {
  const launched = []
  control(launched, 'first').attach(fakeBot())
  assert.deepEqual(launched, [{ port: 3207, firstPerson: true, viewDistance: 4 }])
})

test('setMode closes the viewer and restarts it on the same port in the new mode', async () => {
  const launched = []
  const viewer = control(launched)
  const bot = fakeBot()
  viewer.attach(bot)
  await viewer.setMode('first')
  assert.equal(bot.viewer.closed, 1)
  assert.equal(viewer.mode, 'first')
  assert.deepEqual(launched.map((options) => [options.port, options.firstPerson]), [[3207, false], [3207, true]])
})

test('queued mode changes end in the last mode', async () => {
  const launched = []
  const viewer = control(launched)
  viewer.attach(fakeBot())
  await Promise.all([viewer.setMode('third'), viewer.setMode('first')])
  assert.equal(launched.at(-1).firstPerson, true)
})

test('setMode without a bot only remembers the mode for the next attach', async () => {
  const launched = []
  const viewer = control(launched)
  await viewer.setMode('first')
  assert.equal(launched.length, 0)
  viewer.attach(fakeBot())
  assert.equal(launched[0].firstPerson, true)
})

test('a failed restart does not block the next one', async () => {
  let fail = true
  const viewer = createViewerControl({
    port: 1, restartDelayMs: 1, launch: () => { if (fail && viewer.mode === 'first') throw new Error('port busy') }
  })
  viewer.attach(fakeBot())
  await assert.rejects(viewer.setMode('first'), /port busy/)
  fail = false
  await viewer.setMode('third')
  assert.equal(viewer.mode, 'third')
})

function cors (path, origin, method = 'GET') {
  const headers = {}
  let status = null
  let nexted = false
  const res = { set: (values) => Object.assign(headers, values), sendStatus: (code) => { status = code } }
  localCors({ path, method, headers: origin ? { origin } : {} }, res, () => { nexted = true })
  return { headers, status, nexted }
}

test('CORS is only answered for local origins on /status and /viewer', () => {
  assert.equal(cors('/status', 'http://localhost:8766').headers['Access-Control-Allow-Origin'], 'http://localhost:8766')
  assert.equal(cors('/viewer', 'http://127.0.0.1:8794', 'POST').headers['Access-Control-Allow-Origin'], 'http://127.0.0.1:8794')
  assert.deepEqual(cors('/status', 'http://evil.example').headers, {})
  assert.deepEqual(cors('/status', 'http://localhost.evil.example:80').headers, {})
  assert.deepEqual(cors('/mine', 'http://localhost:8766').headers, {})
  assert.deepEqual(cors('/status').headers, {})
})

test('an OPTIONS preflight is answered with 204 and not passed on', () => {
  const result = cors('/viewer', 'http://localhost:8766', 'OPTIONS')
  assert.equal(result.status, 204)
  assert.equal(result.nexted, false)
  assert.equal(cors('/mine', 'http://localhost:8766', 'OPTIONS').nexted, true)
})
