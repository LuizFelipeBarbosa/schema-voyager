// The browser viewer (prismarine-viewer) and its camera mode, plus the CORS
// rule for the two endpoints the trace panel calls (contract v2.5).

const VIEWER_MODES = ['third', 'first']
const RESTART_DELAY_MS = 200 // the old HTTP server must have released the port
const LOCAL_ORIGIN = /^http:\/\/(localhost|127\.0\.0\.1):\d+$/

// `launch(bot, {port, firstPerson, viewDistance})` starts a viewer on `bot`
// (prismarine-viewer's mineflayer()); `bot.viewer.close()` stops it.
function createViewerControl ({ port, mode = 'third', launch, restartDelayMs = RESTART_DELAY_MS }) {
  let current = null
  let currentMode = mode
  let restarting = Promise.resolve()

  const start = (bot) => launch(bot, { port, firstPerson: currentMode === 'first', viewDistance: 4 })

  return {
    get mode () { return currentMode },

    // Starts the viewer on a freshly spawned bot, in the current mode.
    attach (bot) {
      current = bot
      start(bot)
    },

    // The bot disconnected: its viewer is closed by the caller.
    detach (bot) {
      if (current === bot) current = null
    },

    // Restarts the viewer on the same port in `next`. Calls queue up in order.
    setMode (next) {
      currentMode = next
      const restart = restarting.then(async () => {
        const bot = current
        if (!bot || !bot.viewer) return
        bot.viewer.close()
        await new Promise((resolve) => setTimeout(resolve, restartDelayMs))
        if (current === bot) start(bot)
      })
      restarting = restart.catch(() => {}) // a failed restart must not block the next one
      return restart
    }
  }
}

// Express middleware: answers CORS for localhost origins on /status and /viewer only.
function localCors (req, res, next) {
  const isViewerApi = req.path === '/status' || req.path === '/viewer'
  const origin = req.headers.origin
  if (isViewerApi && origin && LOCAL_ORIGIN.test(origin)) {
    res.set({
      'Access-Control-Allow-Origin': origin,
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      Vary: 'Origin'
    })
    if (req.method === 'OPTIONS') return res.sendStatus(204)
  }
  next()
}

module.exports = { VIEWER_MODES, createViewerControl, localCors }
