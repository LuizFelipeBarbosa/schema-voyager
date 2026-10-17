// Server-confirmed block changes. Mineflayer marks a block as broken locally
// the moment its dig timer ends, so a block update seen through the world
// events says nothing about the server. These listeners read the packets.

const { Vec3 } = require('vec3')
const { sleepTicks } = require('./world')

// Pure: the {pos, stateId} changes in a 1.21 multi_block_change packet. Each
// record packs the state id above 12 bits of in-section offsets.
function decodeMultiBlockChange (packet) {
  const { x, y, z } = packet.chunkCoordinates
  const section = new Vec3(x, y, z).scaled(16)
  return packet.records.map((record) => ({
    pos: section.offset((record >> 8) & 0x0f, record & 0x0f, (record >> 4) & 0x0f),
    stateId: record >> 12
  }))
}

// Watches the server's block packets for the cell at `pos`, which held
// `originalName`. confirmed() turns true once the server reports another block
// there. stop() removes the listeners.
function watchServerBlockChange (bot, pos, originalName) {
  let confirmed = false
  const onChange = ({ pos: changedPos, stateId }) => {
    if (!changedPos.equals(pos)) return
    if (bot.registry.blocksByStateId[stateId]?.name !== originalName) confirmed = true
  }
  const onBlockChange = (packet) => onChange({ pos: new Vec3(packet.location.x, packet.location.y, packet.location.z), stateId: packet.type })
  const onMultiBlockChange = (packet) => decodeMultiBlockChange(packet).forEach(onChange)
  bot._client.on('block_change', onBlockChange)
  bot._client.on('multi_block_change', onMultiBlockChange)

  return {
    confirmed: () => confirmed,
    // Resolves true as soon as the change is seen, false after `timeoutMs`.
    async waitForConfirmation (timeoutMs) {
      const deadline = Date.now() + timeoutMs
      while (!confirmed && Date.now() < deadline) await sleepTicks(1)
      return confirmed
    },
    stop () {
      bot._client.removeListener('block_change', onBlockChange)
      bot._client.removeListener('multi_block_change', onMultiBlockChange)
    }
  }
}

module.exports = { decodeMultiBlockChange, watchServerBlockChange }
