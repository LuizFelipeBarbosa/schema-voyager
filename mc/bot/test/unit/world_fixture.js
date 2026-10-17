// A tiny fake bot over a Map of blocks, built from real prismarine blocks, for
// the movements and target tests. Not a test file.
const { Vec3 } = require('vec3')
const registry = require('prismarine-registry')('1.21.1')
const Block = require('prismarine-block')(registry)

function fakeWorldBot (blocks = {}, { defaultBlock = 'air' } = {}) {
  const at = (x, y, z) => blocks[`${x},${y},${z}`] || defaultBlock
  const blockAt = (pos) => {
    const block = Block.fromStateId(registry.blocksByName[at(pos.x, pos.y, pos.z)].defaultState, 0)
    block.position = new Vec3(pos.x, pos.y, pos.z)
    return block
  }
  return {
    registry,
    blockAt,
    entity: { position: new Vec3(0.5, 10, 0.5), effects: {} },
    entities: {},
    inventory: { items: () => [] },
    pathfinder: { bestHarvestTool: () => null },
    world: {
      getBlockStateId: (pos) => registry.blocksByName[at(pos.x, pos.y, pos.z)].defaultState
    },
    game: { minY: -64, height: 384 },
    findBlocks ({ matching, maxDistance, count }) {
      const ids = new Set([].concat(matching))
      const origin = this.entity.position.floored()
      return Object.entries(blocks)
        .map(([key, name]) => ({ pos: new Vec3(...key.split(',').map(Number)), name }))
        .filter(({ pos, name }) => ids.has(registry.blocksByName[name].id) && pos.distanceTo(origin) <= maxDistance)
        .sort((a, b) => a.pos.distanceTo(origin) - b.pos.distanceTo(origin))
        .slice(0, count)
        .map(({ pos }) => pos)
    }
  }
}

module.exports = { fakeWorldBot, registry }
