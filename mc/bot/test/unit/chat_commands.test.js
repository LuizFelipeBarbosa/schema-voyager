const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { attachChatCommands, parseChatCommand, parseOwners } = require('../../lib/chat_commands')

// Runs `say(bot)` on a bot with BOT_OWNERS set to `owners` (undefined: unset)
// and returns what was recorded.
function recordedFrom (owners, say) {
  const previous = process.env.BOT_OWNERS
  if (owners === undefined) delete process.env.BOT_OWNERS
  else process.env.BOT_OWNERS = owners
  try {
    const bot = new EventEmitter()
    bot.username = 'VoyagerBot'
    const recorded = []
    attachChatCommands(bot, { record: (type, fields) => recorded.push({ type, ...fields }) })
    say(bot)
    return recorded
  } finally {
    if (previous === undefined) delete process.env.BOT_OWNERS
    else process.env.BOT_OWNERS = previous
  }
}

const sayAs = (owners, username, message) => recordedFrom(owners, (bot) => bot.emit('chat', username, message))

test('an owner command is recorded as a chat_command event', () => {
  assert.deepEqual(sayAs('Otopo', 'Otopo', '!goal 10 torches'),
    [{ type: 'chat_command', player: 'Otopo', command: 'goal', args: '10 torches' }])
})

test('owners match case-insensitively and ignore spaces around names', () => {
  const owners = ' Otopo , Tester '
  assert.equal(sayAs(owners, 'otopo', '!stop').length, 1)
  assert.equal(sayAs(owners, 'TESTER', '!stop')[0].player, 'TESTER')
})

test('a non-owner is ignored', () => {
  assert.deepEqual(sayAs('Otopo', 'Mallory', '!goal 10 torches'), [])
})

test('the bot itself is ignored even when listed as an owner', () => {
  assert.deepEqual(sayAs('Otopo,VoyagerBot', 'voyagerbot', '!goal x'), [])
})

test('a message without ! is ignored', () => {
  assert.deepEqual(sayAs('Otopo', 'Otopo', 'goal 10 torches'), [])
})

test('malformed commands are ignored', () => {
  for (const message of ['!', '!123', '!go-al now', '! goal']) {
    assert.deepEqual(sayAs('Otopo', 'Otopo', message), [], message)
  }
})

test('the command is lowercased and args default to an empty string', () => {
  assert.deepEqual(sayAs('Otopo', 'Otopo', '!STOP'),
    [{ type: 'chat_command', player: 'Otopo', command: 'stop', args: '' }])
})

test('args are trimmed and cut to 200 characters', () => {
  assert.equal(sayAs('Otopo', 'Otopo', '!goal    spaced out   ')[0].args, 'spaced out')
  assert.equal(sayAs('Otopo', 'Otopo', `!goal ${'x'.repeat(300)}`)[0].args, 'x'.repeat(200))
})

test('nothing is recorded when BOT_OWNERS is unset or empty', () => {
  assert.deepEqual(sayAs(undefined, 'Otopo', '!goal x'), [])
  assert.deepEqual(sayAs('', 'Otopo', '!goal x'), [])
  assert.deepEqual(sayAs(' , ', '', '!goal x'), [])
})

test('parseOwners lowercases, trims and drops empty names', () => {
  assert.deepEqual(parseOwners(' Otopo ,, Tester '), ['otopo', 'tester'])
  assert.deepEqual(parseOwners(undefined), [])
})

test('parseChatCommand splits a message into command and args', () => {
  assert.deepEqual(parseChatCommand('!Goal 10 torches'), { command: 'goal', args: '10 torches' })
  assert.equal(parseChatCommand('hello'), null)
})
