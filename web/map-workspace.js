function placementPalette(){
 const choices=[['npc','🧑','NPC'],['vendor','🛒','Market'],['creature','🐾','Creature'],['custom_entity','✳','Interactable'],['room','🏠','Room'],['description','📝','Description']];
 return `<section class="placement-palette" aria-label="Place content"><strong>Place on map</strong><div class="placement-buttons">${choices.map(([kind,icon,label])=>`<button type="button" class="tool-button ${mapPlacementKind===kind?'selected':''}" data-place-kind="${kind}" aria-pressed="${mapPlacementKind===kind}">${icon} ${label}</button>`).join('')}</div><small>Choose an object, then select its tile. Escape cancels placement.</small></section>`;
}
function selectedLocationContent(index){
 const host=document.querySelector('#tile-content');if(!host)return;
 const x=index%activeMap().width,y=Math.floor(index/activeMap().width),groups=[['npcs',world.npcs||[]],['economy',world.economy?.vendors||[]],['entities',world.entities||[]]];
 const placed=groups.flatMap(([key,entries])=>entries.map((entity,i)=>({key,index:i,entity}))).filter(({entity})=>entity.location&&(entity.map_id||world.default_map_id)===selectedMapId&&entity.location.x===x&&entity.location.y===y);
 host.innerHTML=`<div class="panel-heading"><h3>Content here</h3><span class="badge">${placed.length} objects</span></div>${placed.length?placed.map(({key,index:entryIndex,entity})=>`<article class="tile-content-card"><strong>${esc(entity.icon||({npcs:'🧑',economy:'🛒'}[key]||'✳'))} ${esc(entity.name||entity.id)}</strong><small>${esc(entity.entity_type||sections[key]?.singular||'object')} · ${esc(entity.id)}</small><div class="placement-buttons"><button type="button" class="tool-button" data-tile-edit="${key}:${entryIndex}">Edit / move</button><button type="button" class="tool-button" data-tile-copy="${key}:${entryIndex}">Duplicate</button></div></article>`).join(''):'<p class="small-copy">No placed objects. Choose an object from the palette, then this tile.</p>'}`;
}
function wireMapWorkspace(){
 document.querySelectorAll('[data-place-kind]').forEach(button=>button.addEventListener('click',()=>beginMapPlacement(button.dataset.placeKind)));
 document.querySelector('#tile-content')?.addEventListener('click',event=>{
   const button=event.target.closest('[data-tile-edit],[data-tile-copy]');if(!button)return;
   const value=button.dataset.tileEdit||button.dataset.tileCopy,[key,index]=value.split(':');
   if(button.dataset.tileCopy)duplicateEntity(key,Number(index));else editEntity(key,Number(index));
 });
 if(selectedTile>=0)selectedLocationContent(selectedTile);
}
function editTileServices(){
 if(selectedTile<0){toast('Select a tile first');return;}
 const index=selectedTile,draft=structuredClone(locationContent(index));
 const resourceRow=resource=>`<div class="entity-edit" data-tile-resource data-original="${esc(JSON.stringify(resource))}"><label class="form-field">ITEM<input class="input" data-resource-item value="${esc(resource.item||'')}" list="tile-resource-items"></label><label class="form-field">AVAILABLE UNITS<input class="input" type="number" min="1" step="1" data-resource-quantity value="${resource.quantity??resource.count??1}"></label><button type="button" class="button button-secondary" data-resource-remove>Remove resource</button></div>`;
 const items=[...new Set([...(world.entities||[]).filter(e=>e.entity_type==='item').map(e=>e.name),...(world.economy?.vendors||[]).flatMap(v=>(v.stock||[]).map(o=>o.item)),...(world.crafting||[]).flatMap(r=>[...(r.inputs||[]).map(p=>p.item),r.output?.item])].filter(Boolean))];
 const stations=[...new Set((world.crafting||[]).map(r=>r.station).filter(Boolean))];
 modal(`<div class="modal-head"><div><h2>🌿 Resources & services</h2><p>Configure gatherable stock and crafting at this tile. Changes apply together when saved.</p></div><button class="modal-close">×</button></div><label class="form-field">CRAFTING STATION<input class="input" id="tile-service-station" list="tile-stations" value="${esc(draft.station||'')}" placeholder="None"><small>Use the station ID required by a recipe, such as campfire.</small></label><datalist id="tile-stations">${stations.map(id=>`<option value="${esc(id)}"></option>`).join('')}</datalist><label><input type="checkbox" id="tile-service-rest" ${draft.rest?'checked':''}> Players may rest here</label><h3>Gatherable resources</h3><datalist id="tile-resource-items">${items.map(name=>`<option value="${esc(name)}"></option>`).join('')}</datalist><div id="tile-resource-list">${(draft.resources||[]).map(resourceRow).join('')}</div><button class="button button-secondary" type="button" id="tile-resource-add">＋ Add resource</button><div id="tile-service-error" role="alert"></div><div class="modal-actions"><button class="button button-secondary modal-cancel">Cancel</button><button class="button button-primary" id="tile-services-save">Save services</button></div>`);
 $('#tile-resource-add').onclick=()=>{$('#tile-resource-list').insertAdjacentHTML('beforeend',resourceRow({item:'',quantity:1}));$('#tile-resource-list').lastElementChild.querySelector('[data-resource-item]').focus()};
 $('#tile-resource-list').onclick=event=>{if(event.target.closest('[data-resource-remove]'))event.target.closest('[data-tile-resource]').remove()};
 $('#tile-services-save').onclick=()=>{try{const seen=new Set(),resources=$$('[data-tile-resource]').map(row=>{const item=row.querySelector('[data-resource-item]').value.trim(),quantity=Number(row.querySelector('[data-resource-quantity]').value);if(!item||seen.has(item.toLowerCase()))throw Error('Each resource needs a unique item name.');if(!Number.isSafeInteger(quantity)||quantity<1)throw Error('Resource stock must be a positive whole number.');seen.add(item.toLowerCase());return {...JSON.parse(row.dataset.original),item,quantity}});const station=$('#tile-service-station').value.trim();if(station&&!/^[a-z][a-z0-9_-]{0,31}$/.test(station))throw Error('Station IDs use lowercase letters, numbers, dash or underscore.');mapCheckpoint('Edit tile services');editLocation(index,{resources,station,rest:$('#tile-service-rest').checked});save();hideModal();render()}catch(error){$('#tile-service-error').textContent=error.message}};
}
let mapSearchQuery='';
function mapSearchResults(source,mapId,query){
 const area=source.maps.find(map=>map.id===mapId),needle=query.trim().toLocaleLowerCase();if(!area||!needle)return [];
 const matches=value=>[value.name,value.id,value.description,value.role,value.dialog,value.station,...(value.resources||[]).map(part=>part.item),...Object.keys(value.exits||{})].filter(Boolean).some(text=>String(text).toLocaleLowerCase().includes(needle));
 const results=[];
 for(const [key,entries] of [['npcs',source.npcs||[]],['economy',source.economy?.vendors||[]],['entities',source.entities||[]]]){
   entries.forEach((entity,index)=>{const pos=entity.location;if(pos&&(entity.map_id||source.default_map_id)===mapId&&matches(entity))results.push({key,index,tile:pos.y*area.width+pos.x,name:entity.name||entity.id,kind:entity.entity_type||key,id:entity.id})});
 }
 area.tiles.forEach((tile,index)=>{if(matches(tile))results.push({tile:index,name:tile.name||`Tile ${index%area.width}, ${Math.floor(index/area.width)}`,kind:'location',id:tile.id||''})});
 return results;
}
function mapSearchPanel(){return `<section class="map-search"><label for="map-search-input">🔎 Find on this map</label><input class="input" type="search" id="map-search-input" value="${esc(mapSearchQuery)}" placeholder="Name, ID, prose, resource or station…"><div id="map-search-results" aria-live="polite"></div></section>`}
function wireMapSearch(){
 const input=$('#map-search-input'),host=$('#map-search-results');if(!input||!host)return;
 const draw=()=>{mapSearchQuery=input.value;const results=mapSearchResults(world,selectedMapId,mapSearchQuery);host.innerHTML=mapSearchQuery.trim()?`<p class="small-copy">${results.length} matches${results.length>30?' · showing first 30':''}</p>${results.slice(0,30).map(result=>`<button class="map-search-result" type="button" data-search-tile="${result.tile}"><strong>${esc(result.name)}</strong><span>${esc(result.kind)}${result.id?' · '+esc(result.id):''} · ${result.tile%activeMap().width}, ${Math.floor(result.tile/activeMap().width)}</span></button>`).join('')||'<p>No matching places or objects on this map.</p>'}`:''};
 input.addEventListener('input',draw);
 host.addEventListener('click',event=>{const button=event.target.closest('[data-search-tile]');if(!button)return;mapPlacementKind=null;paint=false;const tile=Number(button.dataset.searchTile);mapWorkspaceTab='map';render();moveMapCursor(tile);$(`[data-cell="${tile}"]`)?.scrollIntoView({block:'nearest',inline:'nearest'})});
 draw();
}
if(typeof module!=='undefined')module.exports={mapSearchResults};

const mapLayers={objects:true,connections:true,services:false,buildability:false};
function mapLayerControls(){return `<fieldset class="map-layer-controls"><legend>Map layers</legend>${[['objects','Objects'],['connections','Connections'],['services','Resources & services'],['buildability','Home buildability']].map(([key,label])=>`<label><input type="checkbox" data-map-layer="${key}" ${mapLayers[key]?'checked':''}> ${label}</label>`).join('')}</fieldset>`}
function buildabilityOverlay(tile){if(!mapLayers.buildability)return '';const biome=world.biomes?.[tile.biome]||{},allowed=tile.buildable??biome.buildable??false;return `buildability-${allowed?'allowed':'blocked'}`}
function wireMapLayers(){document.querySelectorAll('[data-map-layer]').forEach(input=>input.addEventListener('change',()=>{mapLayers[input.dataset.mapLayer]=input.checked;render()}))}
