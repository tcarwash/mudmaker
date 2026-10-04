// Shared map/tile picker for spatial authoring controls.
const MudmakerLocationPicker = (() => {
  const symbols = {grassland:'·',forest:'♣',desert:'~',tundra:'*',swamp:'%',mountain:'^',water:'≈',road:'#'};
  function biomeFor(world, id) {
    return world.biomes?.[id] || (world.entities || []).find(entity => entity.entity_type === 'biome' && entity.id === id) || {};
  }
  function passable(world, tile) {
    const definition = biomeFor(world, tile.biome);
    return definition.passable !== false && !(tile.biome === 'water' && definition.passable === undefined);
  }
  function draw(grid, world, map, value) {
    if (!map) return;
    grid.style.gridTemplateColumns = `repeat(${map.width},minmax(15px,1fr))`;
    grid.innerHTML = map.tiles.map((tile, index) => {
      const x = index % map.width, y = Math.floor(index / map.width);
      const biome = biomeFor(world, tile.biome);
      const selected = map.id === value.map_id && x === value.x && y === value.y;
      const title = `${map.name || map.id} · ${x}, ${y}${passable(world, tile) ? '' : ' · blocked terrain'}`;
      return `<button type="button" class="entity-map-cell ${selected ? 'selected' : ''}" role="gridcell" aria-selected="${selected}" aria-label="${title}${selected ? ', selected' : ''}" title="${title}" data-location-picker-cell="${index}" data-location-map-cell="${index}" style="background:${biome.color || '#b3cb81'}">${biome.symbol || symbols[tile.biome] || '·'}</button>`;
    }).join('');
    const tile = map.tiles[value.y * map.width + value.x];
    const state = passable(world, tile) ? 'passable' : 'blocked terrain';
    grid.setAttribute('aria-label', `${map.name || map.id} map; ${state}`);
  }
  function wire({world, mapSelect, grid, readout, initial, onChange}) {
    let value = {...initial};
    const getMap = () => world.maps.find(map => map.id === mapSelect.value);
    const publish = (notify=true) => {
      const map = getMap();
      if (!map) return;
      value.map_id = map.id;
      value.x = Math.max(0, Math.min(map.width - 1, Number(value.x) || 0));
      value.y = Math.max(0, Math.min(map.height - 1, Number(value.y) || 0));
      draw(grid, world, map, value);
      const xField = document.getElementById(`${grid.id.replace(/-grid$/, '')}-x`);
      const yField = document.getElementById(`${grid.id.replace(/-grid$/, '')}-y`);
      if (xField) {xField.max = map.width - 1; xField.value = value.x;}
      if (yField) {yField.max = map.height - 1; yField.value = value.y;}
      const tile = map.tiles[value.y * map.width + value.x];
      const terrain = tile.biome || 'terrain';
      const state = passable(world, tile) ? 'passable' : 'blocked terrain';
      readout.textContent = `${value.x}, ${value.y} · ${map.name || map.id} · ${terrain} · ${state}`;
      if (notify) onChange({...value, passable: passable(world, tile)});
    };
    mapSelect.value = value.map_id;
    mapSelect.addEventListener('change', () => {
      const map = getMap();
      value = {map_id:map.id, ...(map.spawn || {x:0,y:0})};
      publish();
    });
    grid.addEventListener('click', event => {
      const cell = event.target.closest('[data-location-picker-cell]');
      if (!cell) return;
      const map = getMap(), index = Number(cell.dataset.locationPickerCell);
      value = {map_id:map.id, x:index % map.width, y:Math.floor(index / map.width)};
      publish();
      grid.querySelector(`[data-location-picker-cell="${index}"]`)?.focus();
    });
    grid.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return;
      const cell = event.target.closest('[data-location-picker-cell]');
      if (!cell) return;
      event.preventDefault();
      const map = getMap(), index = Number(cell.dataset.locationPickerCell);
      const x = index % map.width, y = Math.floor(index / map.width);
      const nextX = Math.max(0, Math.min(map.width - 1, x + (event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0)));
      const nextY = Math.max(0, Math.min(map.height - 1, y + (event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0)));
      grid.querySelector(`[data-location-picker-cell="${nextY * map.width + nextX}"]`)?.focus();
    });
    publish(false);
    return {get value() { return {...value}; }, set(next, notify=true) {value = {...next}; mapSelect.value = value.map_id; publish(notify);}};
  }
  function markup({world, mapId, location, prefix, label='Choose a tile', maps=world.maps}) {
    const selectedMap = world.maps.find(map => map.id === mapId) || world.maps[0];
    const choices = maps.map(map => `<option value="${esc(map.id)}" ${map.id === selectedMap.id ? 'selected' : ''}>${esc(map.name || map.id)} · ${map.width}×${map.height}</option>`).join('');
    return `<div class="entity-location-picker"><label for="${prefix}-map">📍 ${label.toUpperCase()}</label><select class="select" id="${prefix}-map" aria-label="Choose map">${choices}</select><div class="entity-location-map-grid" id="${prefix}-grid" role="grid"></div><small class="muted entity-location-readout" id="${prefix}-readout"></small><details class="entity-coordinate-advanced"><summary>Enter coordinates directly (advanced)</summary><div class="entity-coordinate-fields"><label>X <input class="input" id="${prefix}-x" type="number" min="0" value="${location.x}"></label><label>Y <input class="input" id="${prefix}-y" type="number" min="0" value="${location.y}"></label></div></details></div>`;
  }
  return {wire, markup};
})();
