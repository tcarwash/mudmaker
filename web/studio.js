// Authoring conveniences and playtest controls. World data stays in app.js.
const mapUndo = [], mapRedo = [];
let previewState = null, previewPlayer = 'builder', previewBusy = false;
const commandHistory = [];
let historyCursor = 0;

function mapSnapshot() {
  return structuredClone({width:world.width, height:world.height, tiles:world.tiles,
    spawn:world.spawn, rooms:(world.entities||[]).filter(e=>['room','description'].includes(e.entity_type))});
}
function mapCheckpoint() {
  const current = mapSnapshot();
  if (!mapUndo.length || JSON.stringify(mapUndo.at(-1)) !== JSON.stringify(current)) {
    mapUndo.push(current);
    if (mapUndo.length > 30) mapUndo.shift();
  }
  mapRedo.length = 0;
  updateHistoryButtons();
}
function clearMapHistory() { mapUndo.length=0; mapRedo.length=0; }
function updateHistoryButtons() {
  if ($('#map-undo')) $('#map-undo').disabled=!mapUndo.length;
  if ($('#map-redo')) $('#map-redo').disabled=!mapRedo.length;
}
function restoreMap(direction) {
  const source=direction==='undo'?mapUndo:mapRedo, destination=direction==='undo'?mapRedo:mapUndo;
  if(!source.length)return;
  destination.push(mapSnapshot());
  const snapshot=source.pop();
  Object.assign(world,{width:snapshot.width,height:snapshot.height,tiles:snapshot.tiles,spawn:snapshot.spawn});
  const rooms=new Map(snapshot.rooms.map(e=>[e.id,e]));
  world.entities=(world.entities||[]).map(e=>rooms.has(e.id)&&['room','description'].includes(e.entity_type)?rooms.get(e.id):e);
  if(selectedTile>=world.tiles.length)selectedTile=-1;
  save();render();
}
function mapMarkers(index) {
  const x=index%world.width,y=Math.floor(index/world.width);
  return [...world.npcs.map(e=>({...e,marker:'♟'})),...(world.economy?.vendors||[]).map(e=>({...e,marker:'◈'})),
    ...(world.entities||[]).filter(e=>['npc','vendor','creature','custom_entity'].includes(e.entity_type)).map(e=>({...e,marker:e.entity_type==='npc'?'♟':e.entity_type==='vendor'?'◈':e.entity_type==='creature'?(e.icon||'☠'):'✳'}))]
    .filter(e=>(e.location||world.spawn).x===x&&(e.location||world.spawn).y===y);
}
function mapMarkerText(index) {
  const spawn=index===world.spawn.y*world.width+world.spawn.x;
  const markers=mapMarkers(index);
  if(spawn)return world.presentation?.map_style==='icons'?'🧭':'⌖';
  if(markers.length)return markers[0].marker+(markers.length>1?markers.length:'');
  const biomeId=world.tiles[index].biome,custom=(world.entities||[]).find(e=>e.entity_type==='biome'&&e.id===biomeId),biome=world.biomes?.[biomeId]||custom||{};
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
  return `<div class="playtest-stats"><div><small>HEALTH</small><b>${state.hp}/100</b><progress max="100" value="${state.hp}" aria-label="Health"></progress></div><div><small>${esc(world.economy?.currency||'Coins')}</small><b>${esc(world.presentation?.currency_icon||'🪙')} ${state.currency}</b></div><div><small>POSITION</small><b>${state.x}, ${state.y}</b></div><div><small>DISCOVERED</small><b>${state.discovered.length} / ${world.tiles.length}</b></div><div><small>EXPERIENCE</small><b>${state.experience||0} XP</b></div></div><div class="prompt-row"><small>SSH PROMPT PREVIEW</small><br><code>${esc(previewPlayer)}${gear?' '+esc(gear):''} &gt;</code></div><h3>Inventory</h3><div class="inventory-chips">${items.length?items.map(([name,count])=>`<span class="badge">${esc(previewItemIcon(name))} ${esc(name)} ×${count}${Object.entries(equipment).some(([,equipped])=>equipped===name)?' · equipped':''}</span>`).join(''):'<span class="muted">Empty inventory</span>'}</div><h3>Quest journal</h3><p class="small-copy">${state.active_quests.length} active · ${state.completed_quests.length} complete</p>${state.tracked_quest?`<div class="prompt-row">Tracking: ${esc(state.tracked_quest)}</div>`:''}`;
}
function wireStudio() {
  $('#map-undo')?.addEventListener('click',()=>restoreMap('undo'));
  $('#map-redo')?.addEventListener('click',()=>restoreMap('redo'));
  updateHistoryButtons();
  $('#tile-name')?.addEventListener('focus',mapCheckpoint);
  $('#tile-desc')?.addEventListener('focus',mapCheckpoint);
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
  return `<div class="entity-edit basic-entity-fields"><div class="form-field"><label for="entity-name">NAME</label><input class="input" id="entity-name" data-entity-field="name" value="${esc(entity.name||'')}"></div><div class="form-field"><label for="entity-id">STABLE ID</label><input class="input" id="entity-id" data-entity-field="id" value="${esc(entity.id||'')}" placeholder="Generated from name if blank"></div><div class="form-field full"><label for="entity-description">DESCRIPTION</label><textarea class="textarea" id="entity-description" data-entity-field="description" placeholder="Leave blank for AI assistance">${esc(entity.description||'')}</textarea></div>${entity.location?`<div class="form-field"><label for="entity-x">MAP X</label><input class="input" id="entity-x" type="number" min="0" max="${world.width-1}" data-location-field="x" value="${entity.location.x??''}"></div><div class="form-field"><label for="entity-y">MAP Y</label><input class="input" id="entity-y" type="number" min="0" max="${world.height-1}" data-location-field="y" value="${entity.location.y??''}"></div>`:''}<div class="form-field full ${hasIcon?'':'hidden'}" id="entity-icon-options"><label id="entity-icon-label" for="entity-icon">${iconLabel}</label><input class="input" id="entity-icon" data-entity-field="icon" maxlength="12" value="${esc(entity.icon||iconDefault)}" placeholder="🎒"><small class="muted">Emoji shown in inventory, the equipped prompt, creature descriptions, or icon map mode.</small></div><div class="form-field ${entity.entity_type==='biome'?'':'hidden'}" id="biome-symbol-option"><label>ASCII MAP SYMBOL</label><input class="input" data-entity-field="symbol" maxlength="1" value="${esc(entity.symbol||'+')}"></div><div class="form-field ${entity.entity_type==='biome'?'':'hidden'}" id="biome-color-option"><label>TERRAIN COLOR</label><input class="input" data-entity-field="color" type="color" value="${esc(entity.color||'#87a982')}"></div><div class="form-field ${entity.entity_type==='item'?'':'hidden'}" id="item-slot-option"><label for="entity-equip-slot">EQUIPMENT SLOT</label><select class="select" id="entity-equip-slot" data-entity-field="equip_slot"><option value="" ${!entity.equip_slot?'selected':''}>Not equippable</option>${slots.map(slot=>`<option value="${slot}" ${entity.equip_slot===slot?'selected':''}>${slot}</option>`).join('')}</select></div>${stats.map((key,i)=>`<div class="form-field ${entity.entity_type==='creature'?'':'hidden'} creature-number"><label for="creature-${key}">${statLabels[i]}</label><input class="input" id="creature-${key}" type="number" min="0" data-entity-field="${key}" value="${entity[key]??''}"></div>`).join('')}</div>`;
}
function syncEntityFields() {
  try {
    const entity=JSON.parse($('#entity-json').value);
    $$('[data-entity-field]').forEach(input=>input.value=entity[input.dataset.entityField]??'');
    $$('[data-location-field]').forEach(input=>input.value=entity.location?.[input.dataset.locationField]??'');
  } catch {} // Keep the incomplete JSON visible while the author is typing.
}
function wireEntityFields() {
  if(!$('#entity-json'))return;
  $('#entity-json').addEventListener('input',syncEntityFields);
  $$('[data-entity-field],[data-location-field]').forEach(input=>input.addEventListener('input',()=>{
    try {
      const entity=JSON.parse($('#entity-json').value);
      if(input.dataset.entityField)entity[input.dataset.entityField]=input.type==='number'?Number(input.value):input.value;
      else{entity.location??={};entity.location[input.dataset.locationField]=input.value===''?null:Number(input.value);}
      $('#entity-json').value=JSON.stringify(entity,null,2);
    }catch{toast('Finish the JSON edit before changing these fields.');}
  }));
}
