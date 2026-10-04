// Pure map operations shared by the browser and regression checks.
const MudmakerMapState = (() => {
  const fields = ['width', 'height', 'tiles', 'spawn'];
  function serialize(world) {
    const snapshot = structuredClone(world);
    const area = snapshot.maps?.find(map => map.id === snapshot.default_map_id);
    if (!area) throw Error('Choose an existing default map before saving.');
    for (const field of fields) snapshot[field] = structuredClone(area[field]);
    return snapshot;
  }
  function resizeImpact(world, mapId, width, height) {
    const area = world.maps.find(map => map.id === mapId);
    if (!area) throw Error('Map no longer exists.');
    const issues = [];
    const outside = pos => pos && (pos.x < 0 || pos.y < 0 || pos.x >= width || pos.y >= height);
    const terrainDefaults = {grassland:'Grassland',forest:'Forest',desert:'Desert',tundra:'Tundra',swamp:'Swamp',mountain:'Mountain',water:'Water',road:'Road'};
    const hasAuthoredTileText = tile => {
      const definition = world.biomes?.[tile.biome] || (world.entities || []).find(entity => entity.entity_type === 'biome' && entity.id === tile.biome) || {};
      const name = definition.name || terrainDefaults[tile.biome] || tile.biome;
      const description = definition.description || `A quiet stretch of ${name.toLowerCase()}.`;
      return (tile.name && tile.name !== name) || (tile.description && tile.description !== description);
    };
    if (outside(area.spawn)) issues.push('Player spawn lies outside the new bounds. Move it first.');
    for (const [id, pos] of Object.entries(area.entry_points || {}))
      if (outside(pos)) issues.push(`Arrival point ${id} lies outside the new bounds.`);
    for (const other of world.maps) for (const [index, tile] of other.tiles.entries()) {
      const cropped = other.id === mapId && outside({x:index % area.width, y:Math.floor(index / area.width)});
      if (cropped && (hasAuthoredTileText(tile) || tile.station || tile.rest || tile.resources?.length || Object.keys(tile.exits || {}).length))
        issues.push(`Tile ${index % area.width}, ${Math.floor(index / area.width)} has authored text, services or connections. Remove or relocate them first.`);
      for (const [label, dest] of Object.entries(tile.exits || {})) {
        const target = world.maps.find(map => map.id === (dest.map_id || other.id));
        const arrival = target?.entry_points?.[dest.entry_point] || dest;
        if ((dest.map_id || other.id) === mapId && outside(arrival))
          issues.push(`Connection ${other.name || other.id} → ${label} arrives outside the new bounds.`);
      }
    }
    const visit = (value, path, inheritedMap = world.default_map_id) => {
      if (!value || typeof value !== 'object') return;
      if (Array.isArray(value)) {value.forEach((entry, index) => visit(entry, `${path}[${index}]`, inheritedMap)); return;}
      const ownerMap = value.map_id || inheritedMap;
      if (ownerMap === mapId && outside(value.location || (Number.isInteger(value.x) && Number.isInteger(value.y) ? value : null)))
        issues.push(`${value.name || value.id || path} lies outside the new bounds (${path}).`);
      for (const [key, child] of Object.entries(value)) if (key !== 'location') visit(child, `${path}.${key}`, ownerMap);
    };
    for (const [key, value] of Object.entries(world)) if (!['maps', ...fields].includes(key)) visit(value, key);
    return [...new Set(issues)];
  }
  function resize(world, mapId, width, height) {
    if (![width, height].every(size => Number.isInteger(size) && size >= 4 && size <= 64)) throw Error('Map sides must be 4–64 tiles.');
    const issues = resizeImpact(world, mapId, width, height);
    if (issues.length) throw Error(issues.join('\n'));
    const area = world.maps.find(map => map.id === mapId);
    area.tiles = Array.from({length:width * height}, (_, index) => {
      const x = index % width, y = Math.floor(index / width);
      return x < area.width && y < area.height ? area.tiles[y * area.width + x] : {biome:'grassland', name:'', description:''};
    });
    area.width = width; area.height = height;
    return area;
  }
  return {serialize, resizeImpact, resize};
})();
if (typeof module !== 'undefined') module.exports = MudmakerMapState;
