// Authoring conveniences and playtest controls. World data stays in app.js.
const mapUndo = [], mapRedo = [];
let previewState = null, previewPlayer = 'builder', previewBusy = false, previewStart = null, lastBuildReport = null;
let editorModalContext = null;
const commandHistory = [];
let historyCursor = 0;

function mapSnapshot(label='Edit map') {

  return {world:MudmakerMapState.serialize(world),mapId:selectedMapId,tile:selectedTile,label:typeof label==='string'?label:'Edit location'};
}
function mapCheckpoint(label='Edit map') {
  const current=mapSnapshot(label);
  if(!mapUndo.length||JSON.stringify(mapUndo.at(-1).world)!==JSON.stringify(current.world)) {
    mapUndo.push(current);if(mapUndo.length>30)mapUndo.shift();
  }
  mapRedo.length=0;updateHistoryButtons();
}
function clearMapHistory(){mapUndo.length=0;mapRedo.length=0;}
function updateHistoryButtons(){
  for(const [id,stack,prefix] of [['map-undo',mapUndo,'↶ Undo'],['map-redo',mapRedo,'↷ Redo']]) {
    const button=$('#'+id);if(!button)continue;button.disabled=!stack.length;
    button.title=stack.length?`${prefix}: ${stack.at(-1).label}`:prefix;
    button.setAttribute('aria-label',button.title);
  }
}
function restoreMap(direction){
  const source=direction==='undo'?mapUndo:mapRedo,destination=direction==='undo'?mapRedo:mapUndo;
  if(!source.length)return;
  const snapshot=source.pop(),forward=mapSnapshot(snapshot.label);forward.mapId=snapshot.mapId;forward.tile=snapshot.tile;destination.push(forward);
  rememberMapSelection();world=structuredClone(snapshot.world);activateMap(snapshot.mapId,false);
  selectedTile=snapshot.tile>=0&&snapshot.tile<activeMap().tiles.length?snapshot.tile:-1;
  rememberMapSelection();mapPlacementKind=null;save();render();
}
function mapMarkers(index) {
  const x=index%activeMap().width,y=Math.floor(index/activeMap().width);
  const entities=[...world.npcs.map(e=>({...e,marker:'♟'})),...(world.economy?.vendors||[]).map(e=>({...e,marker:'◈'})),
    ...(world.entities||[]).filter(e=>['npc','vendor','creature','custom_entity'].includes(e.entity_type)).map(e=>({...e,marker:e.entity_type==='npc'?'♟':e.entity_type==='vendor'?'◈':e.entity_type==='creature'?(e.icon||'☠'):'✳'}))]
    .filter(e=>(e.map_id||world.default_map_id)===selectedMapId&&(e.location||activeMap().spawn).x===x&&(e.location||activeMap().spawn).y===y);
  const exits=activeMap().tiles[index]?.exits||{};
  const tile=locationContent(index),services=[];
  if(mapLayers.services){
    for(const resource of tile.resources||[])services.push({name:resource.item,marker:'🌿'});
    if(tile.station)services.push({name:`Station: ${tile.station}`,marker:'🔨'});
    if(tile.rest)services.push({name:'Resting place',marker:'☕'});
  }
  return [...(mapLayers.objects?entities:[]),...(mapLayers.connections?Object.keys(exits).map(name=>({name,marker:'🚪'})):[]),...services];
}
function mapMarkerText(index) {
  const spawn=index===activeMap().spawn.y*activeMap().width+activeMap().spawn.x;
  const markers=mapMarkers(index);
  if(spawn)return world.presentation?.map_style==='icons'?'🧭':'⌖';
  if(markers.length)return markers[0].marker+(markers.length>1?markers.length:'');
  const biomeId=activeMap().tiles[index].biome,custom=(world.entities||[]).find(e=>e.entity_type==='biome'&&e.id===biomeId),biome=world.biomes?.[biomeId]||custom||{};
  if(world.presentation?.map_style==='icons')return biome.icon||({grassland:'🌿',forest:'🌲',desert:'🏜️',tundra:'❄️',swamp:'🐸',mountain:'⛰️',water:'🌊',road:'🛤️'}[biomeId]||biome.symbol||'·');
  return biome.symbol||({grassland:'.',forest:'T',desert:'~',tundra:'*',swamp:'%',mountain:'^',water:'=',road:'#'}[biomeId]||'.');
}
function filterEntities() {
  const query=($('#entity-search')?.value||'').trim().toLowerCase();
  const kind=$('#entity-filter')?.value||'';
  let visible=0;
  $$('.entity-card').forEach(card=>{
    const matches=card.dataset.search.includes(query)&&(!kind||card.dataset.kind===kind);
    card.classList.toggle('hidden',!matches);
    if(matches)visible++;
  });
  if($('#entity-match-count'))$('#entity-match-count').textContent=`${visible} shown`;
  if($('#entity-no-matches'))$('#entity-no-matches').classList.toggle('hidden',visible>0||!$$('.entity-card').length);
}
function duplicateEntity(key,index) {
  const entries=entityContainer(key), source=entries[index], copy=structuredClone(source);
  const base=(source.id||slugName(source.name)||'entity')+'-copy';
  let id=base,n=2;
  while(entries.some(e=>e.id===id))id=base+'-'+n++;
  copy.id=id;copy.name=(source.name||'Untitled')+' (copy)';
  editEntity(key,null,copy);
}
function previewItemIcon(name) {
  const item=[...(world.entities||[]).filter(entry=>entry.entity_type==='item'),...(world.items||[])].find(entry=>entry.name?.toLowerCase()===name.toLowerCase());
  if(item?.icon)return item.icon;
  const key=name.toLowerCase();
  return key.includes('torch')?'🔦':key.includes('berry')?'🍓':key.includes('herb')?'🌿':key.includes('ration')?'🍲':key.includes('fang')?'🦷':key.includes('note')?'📜':'🎒';
}
function previewDashboard() {
  if(!previewState)return '<p class="small-copy">Run a command to see your character.</p>';
  const state=previewState;
  const items=Object.entries(state.inventory||{}).filter(([,count])=>count>0);
  const equipment=state.equipment||{};
  const gear=Object.values(equipment).map(previewItemIcon).join(' ');
  return `<div class="playtest-stats"><div><small>HEALTH</small><b>${state.hp}/100</b><progress max="100" value="${state.hp}" aria-label="Health"></progress></div><div><small>${esc(world.economy?.currency||'Coins')}</small><b>${esc(world.presentation?.currency_icon||'🪙')} ${state.currency}</b></div><div><small>POSITION</small><b>${state.x}, ${state.y}</b></div><div><small>DISCOVERED</small><b>${state.discovered.length} locations</b></div><div><small>EXPERIENCE</small><b>${state.experience||0} XP</b></div></div><div class="prompt-row"><small>SSH PROMPT PREVIEW</small><br><code>${esc(previewPlayer)}${gear?' '+esc(gear):''} &gt;</code></div><h3>Inventory</h3><div class="inventory-chips">${items.length?items.map(([name,count])=>`<span class="badge">${esc(previewItemIcon(name))} ${esc(name)} ×${count}${Object.entries(equipment).some(([,equipped])=>equipped===name)?' · equipped':''}</span>`).join(''):'<span class="muted">Empty inventory</span>'}</div><h3>Quest journal</h3><p class="small-copy">${state.active_quests.length} active · ${state.completed_quests.length} complete</p>${state.tracked_quest?`<div class="prompt-row">Tracking: ${esc(state.tracked_quest)}</div>`:''}`;
}
function wireStudio() {
  const startMap=world.maps.find(map=>map.id===selectedMapId)||world.maps[0];
  previewStart ||= {map_id:startMap.id,...(selectedTile>=0?{x:selectedTile%startMap.width,y:Math.floor(selectedTile/startMap.width)}:(startMap.spawn||{x:0,y:0}))};
  const startGrid=$('#preview-start-grid'),startMapSelect=$('#preview-start-map');
  if(startGrid&&startMapSelect){
    const picker=MudmakerLocationPicker.wire({world,mapSelect:startMapSelect,grid:startGrid,readout:$('#preview-start-readout'),initial:previewStart,onChange:value=>{previewStart={map_id:value.map_id,x:value.x,y:value.y};const reset=$('#preview-reset');if(reset)reset.disabled=!value.passable}});
    $('#preview-reset')?.addEventListener('click',async()=>{const button=$('#preview-reset');button.disabled=true;try{await flushSave();const player=$('#preview-player')?.value.trim()||'builder';const result=await api(`/api/worlds/${world.id}/preview/reset`,{method:'POST',body:{player,start:picker.value}});previewPlayer=player;previewState=result.state;previewLines=[result.output];$('#preview-log').textContent=previewLines.join('\n');$('#preview-dashboard').innerHTML=previewDashboard();toast('Preview restarted at selected location')}catch(error){toast(error.message)}finally{if(button.isConnected)button.disabled=false}});
  }
  $('#map-select')?.addEventListener('change',event=>{activateMap(event.target.value);render()});
  $('#map-undo')?.addEventListener('click',()=>restoreMap('undo'));
  $('#map-redo')?.addEventListener('click',()=>restoreMap('redo'));
  updateHistoryButtons();
  $('#tile-name')?.addEventListener('focus',mapCheckpoint);
  $('#tile-desc')?.addEventListener('focus',mapCheckpoint);
  $('#tile-buildable')?.addEventListener('change',event=>{if(selectedTile>=0){mapCheckpoint();editLocation(selectedTile,{buildable:event.target.value===''?undefined:event.target.value==='true'});save();render()}});
  $('#entity-search')?.addEventListener('input',filterEntities);
  $('#entity-filter')?.addEventListener('change',filterEntities);
  $$('[data-duplicate]').forEach(button=>button.onclick=()=>duplicateEntity(viewName,+button.dataset.duplicate));
  $$('[data-preview-command]').forEach(button=>button.onclick=()=>runPreview(button.dataset.previewCommand));
  $('#preview-player')?.addEventListener('change',()=>{
    previewPlayer=$('#preview-player').value.trim()||'builder';
    previewLines=[];previewState=null;runPreview('look');
  });
  $('#preview-clear')?.addEventListener('click',()=>{previewLines=[];$('#preview-log').textContent='';});
  $('#preview-command')?.addEventListener('keydown',event=>{
    if(!['ArrowUp','ArrowDown'].includes(event.key)||!commandHistory.length)return;
    event.preventDefault();
    historyCursor=Math.max(0,Math.min(commandHistory.length,historyCursor+(event.key==='ArrowUp'?-1:1)));
    event.target.value=commandHistory[historyCursor]||'';
  });
}
document.addEventListener('keydown',event=>{
  if(event.key==='Escape')hideModal();
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){
    event.preventDefault();flushSave().then(()=>toast('World saved')).catch(error=>toast(error.message));
  }
  const typing=event.target.closest('input,textarea,select,[contenteditable]');
  if(!typing&&viewName==='map'&&(event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){
    event.preventDefault();restoreMap(event.shiftKey?'redo':'undo');
  }
});

function commonEntityFields(entity) {
  const hasIcon=['item','creature','biome'].includes(entity.entity_type);
  const iconLabel=entity.entity_type==='creature'?'CREATURE ICON':entity.entity_type==='biome'?'MAP ICON':'INVENTORY / PROMPT ICON';
  const iconDefault=entity.entity_type==='creature'?'🐾':entity.entity_type==='biome'?'🌿':'🎒';
  const slots=['head','body','hand','offhand','feet','neck','ring'];
  const stats=['max_hp','damage','experience','respawn_seconds'];
  const statLabels=['MAX HEALTH','DAMAGE','EXPERIENCE REWARD','RESPAWN SECONDS'];
  const initialMap=world.maps.find(area=>area.id===(entity.map_id||world.default_map_id))||world.maps[0];
  const location=entity.location||initialMap?.spawn||{x:0,y:0};
  const mapChoices=world.maps.map(area=>`<option value="${esc(area.id)}" ${area.id===initialMap?.id?'selected':''}>${esc(area.name||area.id)} · ${area.width}×${area.height}</option>`).join('');
  return `<div class="entity-edit basic-entity-fields"><div class="form-field"><label for="entity-name">NAME</label><input class="input" id="entity-name" data-entity-field="name" value="${esc(entity.name||'')}"></div><div class="form-field"><label for="entity-id">STABLE ID</label><input class="input" id="entity-id" data-entity-field="id" value="${esc(entity.id||'')}" placeholder="Generated from name if blank"></div><div class="form-field full"><label for="entity-description">DESCRIPTION</label><textarea class="textarea" id="entity-description" data-entity-field="description" placeholder="Leave blank for AI assistance">${esc(entity.description||'')}</textarea></div>${entity.location?`<div class="form-field full entity-location-picker"><label for="entity-location-map">📍 LOCATION ON MAP</label><select class="select" id="entity-location-map" aria-label="Choose map">${mapChoices}</select><div class="entity-location-map-grid" id="entity-location-map-grid" role="grid" aria-label="Select a map tile" style="grid-template-columns:repeat(${initialMap.width},minmax(15px,1fr))">${initialMap.tiles.map((tile,i)=>{const x=i%initialMap.width,y=Math.floor(i/initialMap.width),biome=world.biomes?.[tile.biome]||{},symbol=biome.symbol||({grassland:'·',forest:'♣',desert:'~',tundra:'*',swamp:'%',mountain:'^',water:'≈',road:'#'}[tile.biome]||'·'),active=x===location.x&&y===location.y;return `<button type="button" class="entity-map-cell ${active?'selected':''}" role="gridcell" aria-selected="${active}" aria-label="${esc(initialMap.name||initialMap.id)} tile ${x}, ${y}${active?', selected':''}" title="${esc(initialMap.name||initialMap.id)} · ${x}, ${y}" data-location-map-cell="${i}" style="background:${biomeColor(tile.biome)}">${symbol}</button>`}).join('')}</div><small class="muted" id="entity-location-readout">Selected tile: ${location.x}, ${location.y}</small><details class="entity-coordinate-advanced"><summary>Enter coordinates directly (advanced)</summary><div class="entity-coordinate-fields"><label for="entity-x">X <input class="input" id="entity-x" type="number" min="0" max="${initialMap.width-1}" data-location-field="x" value="${location.x??''}"></label><label for="entity-y">Y <input class="input" id="entity-y" type="number" min="0" max="${initialMap.height-1}" data-location-field="y" value="${location.y??''}"></label></div></details></div><input type="hidden" data-entity-field="map_id" value="${esc(initialMap.id)}">`:''}${entity.entity_type==='creature'?`<section class="panel full ${entity.spawn_area?'':'hidden'}" id="creature-spawn-area"><h3>🐾 Spawn area</h3><p class="small-copy">The creature starts at its selected tile. After each respawn, it appears at a random passable tile in this rectangle.</p><label><input type="checkbox" data-spawn-field="enabled" ${entity.spawn_area?'checked':''}> Spawn within an area</label><div class="entity-edit"><label class="form-field">AREA X<input class="input" type="number" min="0" data-spawn-field="x" value="${entity.spawn_area?.x??location.x??0}"></label><label class="form-field">AREA Y<input class="input" type="number" min="0" data-spawn-field="y" value="${entity.spawn_area?.y??location.y??0}"></label><label class="form-field">WIDTH<input class="input" type="number" min="1" data-spawn-field="width" value="${entity.spawn_area?.width??1}"></label><label class="form-field">HEIGHT<input class="input" type="number" min="1" data-spawn-field="height" value="${entity.spawn_area?.height??1}"></label></div></section>`:''}${entity.entity_type==='npc'?`<div class="form-field"><label>ROLE</label><input class="input" data-entity-field="role" value="${esc(entity.role||'')}" placeholder="Guide, merchant, guard…"></div><div class="form-field full"><label>DIALOGUE</label><textarea class="textarea" data-entity-field="dialog" placeholder="What does this character say?">${esc(entity.dialog||'')}</textarea></div>`:''}<div class="form-field full ${hasIcon?'':'hidden'}" id="entity-icon-options"><label id="entity-icon-label" for="entity-icon">${iconLabel}</label><input class="input" id="entity-icon" data-entity-field="icon" maxlength="12" value="${esc(entity.icon||iconDefault)}" placeholder="🎒"><small class="muted">Emoji shown in inventory, the equipped prompt, creature descriptions, or icon map mode.</small></div><div class="form-field ${entity.entity_type==='biome'?'':'hidden'}" id="biome-symbol-option"><label>ASCII MAP SYMBOL</label><input class="input" data-entity-field="symbol" maxlength="1" value="${esc(entity.symbol||'+')}"></div><div class="form-field ${entity.entity_type==='biome'?'':'hidden'}" id="biome-color-option"><label>TERRAIN COLOR</label><input class="input" data-entity-field="color" type="color" value="${esc(entity.color||'#87a982')}"></div><div class="form-field full ${entity.entity_type==='biome'?'':'hidden'}" id="biome-buildable-option"><label><input type="checkbox" class="toggle" data-entity-field="buildable" ${entity.buildable===false?'':'checked'}> Players may build entrances on this terrain by default</label><small class="muted">Individual map tiles can override this terrain rule.</small></div><div class="form-field ${entity.entity_type==='item'?'':'hidden'}" id="item-slot-option"><label for="entity-equip-slot">EQUIPMENT SLOT</label><select class="select" id="entity-equip-slot" data-entity-field="equip_slot"><option value="" ${!entity.equip_slot?'selected':''}>Not equippable</option>${slots.map(slot=>`<option value="${slot}" ${entity.equip_slot===slot?'selected':''}>${slot}</option>`).join('')}</select></div>${stats.map((key,i)=>`<div class="form-field ${entity.entity_type==='creature'?'':'hidden'} creature-number"><label for="creature-${key}">${statLabels[i]}</label><input class="input" id="creature-${key}" type="number" min="0" data-entity-field="${key}" value="${entity[key]??''}"></div>`).join('')}</div>`;
}
function wireEntityLocationPicker(){
 const grid=$('#entity-location-map-grid'),mapSelect=$('#entity-location-map');if(!grid||!mapSelect)return;
 let entity;try{entity=JSON.parse($('#entity-json').value)}catch{return}
 const initialMap=world.maps.find(map=>map.id===(entity.map_id||selectedMapId))||world.maps[0];
 const picker=MudmakerLocationPicker.wire({world,mapSelect,grid,readout:$('#entity-location-readout'),initial:{map_id:initialMap.id,...(entity.location||initialMap.spawn)},onChange:value=>{try{const draft=JSON.parse($('#entity-json').value);draft.map_id=value.map_id;draft.location={x:value.x,y:value.y};$('#entity-json').value=JSON.stringify(draft,null,2);syncEntityFields()}catch{}}});
 for(const field of ['x','y'])$(`#entity-${field}`).addEventListener('input',()=>picker.set({...picker.value,[field]:Number($(`#entity-${field}`).value)}));
}
function renderMarketContractRow(contract={},vendor={}){
 let stock=vendor.stock||[];try{const edited=JSON.parse(document.querySelector('#market-stock')?.value||'null');if(Array.isArray(edited))stock=edited}catch{};
 const items=[...new Set(stock.map(offer=>offer.item).filter(Boolean))];
 const destinations=(world.economy?.vendors||[]).filter(market=>market.id!==vendor.id);
 const deadline=contract.deadline_seconds||0;
 return `<article class="panel delivery-contract-row" data-delivery-contract><div class="panel-heading"><b>🚚 Delivery contract</b><button type="button" class="entity-action" data-contract-remove aria-label="Remove delivery contract">×</button></div><div class="entity-edit"><div class="form-field"><label>CONTRACT ID</label><input class="input" data-contract-id value="${esc(contract.id||'')}" placeholder="herb-run"></div><div class="form-field"><label>DISPLAY NAME</label><input class="input" data-contract-name value="${esc(contract.name||'')}" placeholder="Herb run"></div><div class="form-field"><label>CARGO ITEM</label><select class="select" data-contract-item>${items.map(item=>`<option value="${esc(item)}" ${item===contract.item?'selected':''}>${esc(item)}</option>`).join('')||'<option value="">Add a market stock offer first</option>'}</select></div><div class="form-field"><label>QUANTITY</label><input class="input" data-contract-count type="number" min="1" max="9999" value="${contract.count??1}"></div><div class="form-field"><label>DESTINATION MARKET</label><select class="select" data-contract-destination>${destinations.map(market=>`<option value="${esc(market.id)}" ${market.id===contract.destination_market_id?'selected':''}>${esc(market.name)} · ${esc(market.id)}</option>`).join('')||'<option value="">Create another market first</option>'}</select></div><div class="form-field"><label>CURRENCY REWARD</label><input class="input" data-contract-reward type="number" min="0" value="${contract.reward_currency??0}"></div><div class="form-field"><label>DEADLINE (SECONDS, 0 = NONE)</label><input class="input" data-contract-deadline type="number" min="0" max="2592000" value="${deadline}"></div><div class="form-field"><label>PREREQUISITE CONTRACT IDS</label><input class="input" data-contract-prerequisites value="${esc((contract.prerequisites||[]).join(', '))}" placeholder="harbor-intro, herb-run"></div></div><small class="small-copy">Cargo is drawn from this market's finite stock when accepted. The player carries it to the destination market; expired runs do not pay a reward.</small></article>`;
}
function readMarketContracts(){return $$('[data-delivery-contract]').map(row=>{const contract={id:row.querySelector('[data-contract-id]').value.trim(),name:row.querySelector('[data-contract-name]').value.trim(),item:row.querySelector('[data-contract-item]').value,count:Number(row.querySelector('[data-contract-count]').value),destination_market_id:row.querySelector('[data-contract-destination]').value,reward_currency:Number(row.querySelector('[data-contract-reward]').value),prerequisites:row.querySelector('[data-contract-prerequisites]').value.split(',').map(id=>id.trim()).filter(Boolean)};const deadline=Number(row.querySelector('[data-contract-deadline]').value);if(deadline>0)contract.deadline_seconds=deadline;return contract})}
function syncEntityFields() {
  try {
    const entity=JSON.parse($('#entity-json').value);
    $$('[data-entity-field]').forEach(input=>{if(input.type==='checkbox')input.checked=entity[input.dataset.entityField]===true;else input.value=entity[input.dataset.entityField]??''});
    $$('[data-location-field]').forEach(input=>input.value=entity.location?.[input.dataset.locationField]??'');$$('[data-spawn-field]').forEach(input=>{const area=entity.spawn_area;input.checked=input.dataset.spawnField==='enabled'?!!area:input.checked;if(input.dataset.spawnField!=='enabled')input.value=area?.[input.dataset.spawnField]??(input.dataset.spawnField==='x'?entity.location?.x:input.dataset.spawnField==='y'?entity.location?.y:1)});
  } catch {} // Keep the incomplete JSON visible while the author is typing.
}
function wireEntityFields() {
  if(!$('#entity-json'))return;
  $('#entity-json').addEventListener('input',syncEntityFields);
  $$('[data-spawn-field]').forEach(input=>input.addEventListener('input',()=>{try{const entity=JSON.parse($('#entity-json').value),enabled=$('[data-spawn-field="enabled"]').checked;entity.spawn_area=enabled?{x:Number($('[data-spawn-field="x"]').value),y:Number($('[data-spawn-field="y"]').value),width:Number($('[data-spawn-field="width"]').value),height:Number($('[data-spawn-field="height"]').value)}:null;$('#entity-json').value=JSON.stringify(entity,null,2);$('#creature-spawn-area').classList.toggle('hidden',!enabled)}catch{toast('Finish the JSON edit before changing spawn area fields.')}}));$$('[data-entity-field],[data-location-field]').forEach(input=>input.addEventListener('input',()=>{
    try {
      const entity=JSON.parse($('#entity-json').value);
      if(input.dataset.entityField)entity[input.dataset.entityField]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;
      else{entity.location??={};entity.location[input.dataset.locationField]=input.value===''?null:Number(input.value);}
      $('#entity-json').value=JSON.stringify(entity,null,2);
    }catch{toast('Finish the JSON edit before changing these fields.');}
  }));
}
