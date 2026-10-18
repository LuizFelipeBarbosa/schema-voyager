// Chat commands from the bot's owners: "!goal 10 torches" typed by a player
// listed in BOT_OWNERS becomes a chat_command event for the Python life to
// read through /status. The bot never answers in chat. See contracts/bot_api.md.

const MAX_ARGS_LENGTH = 200

function parseOwners (value) {
  return (value || '').split(',').map((name) => name.trim().toLowerCase()).filter(Boolean)
}

// "!Goal 10 torches" -> { command: 'goal', args: '10 torches' }; null when the
// message is not a command.
function parseChatCommand (message) {
  if (!message.startsWith('!')) return null
  const [word] = message.trim().split(/\s+/)
  if (!/^![A-Za-z]+$/.test(word)) return null
  const args = message.trim().slice(word.length).trim().slice(0, MAX_ARGS_LENGTH)
  return { command: word.slice(1).toLowerCase(), args }
}

function attachChatCommands (bot, eventLog) {
  const owners = parseOwners(process.env.BOT_OWNERS)
  bot.on('chat', (username, message) => {
    const sender = username.toLowerCase()
    if (!owners.includes(sender) || sender === bot.username.toLowerCase()) return
    const parsed = parseChatCommand(message)
    if (parsed) eventLog.record('chat_command', { player: username, ...parsed })
  })
}

module.exports = { attachChatCommands, parseChatCommand, parseOwners }
