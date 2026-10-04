// Terrain changes preserve authored content unless the author explicitly clears it.
(function(root){
 function defaults(source,id,base={}){
  const definition={...(base[id]||{}),...(source.biomes?.[id]||{})};
  for(const entity of source.entities||[])if(entity.entity_type==='biome'&&entity.id===id)Object.assign(definition,entity);
  const name=definition.name||id;
  return {name,description:definition.description||`A quiet stretch of ${name.toLowerCase()}.`};
 }
 function placed(source,mapId,index){
  const area=source.maps.find(m=>m.id===mapId),x=index%area.width,y=Math.floor(index/area.width);
  return entity=>entity.location&&(entity.map_id||source.default_map_id)===mapId&&entity.location.x===x&&entity.location.y===y;
 }
 function plan(source,mapId,index,target,base={}){
  const area=source.maps.find(m=>m.id===mapId),at=placed(source,mapId,index);
  const rooms=(source.entities||[]).filter(e=>['room','description'].includes(e.entity_type)&&at(e));
  const tile=Object.assign({},area.tiles[index],...rooms),before=defaults(source,tile.biome,base),after=defaults(source,target,base);
  const legacy=`A quiet stretch of ${before.name.toLowerCase()}.`;
  const groups=[];
  if(tile.name&&tile.name!==before.name)groups.push(['name','Custom title']);
  if(tile.description&&![before.description,legacy].includes(tile.description))groups.push(['description','Custom description']);
  if(tile.resources?.length)groups.push(['resources','Resources']);
  if(tile.station||tile.rest)groups.push(['services','Crafting station & resting place']);
  if(Object.keys(tile.exits||{}).length)groups.push(['exits','Outgoing connections']);
  if(Object.hasOwn(tile,'buildable')&&tile.buildable!=null)groups.push(['buildable','Tile buildability override']);
  if(rooms.length)groups.push(['rooms',`Rooms & descriptions (${rooms.length})`]);
  const containers=[source.npcs,source.economy?.vendors,source.creatures,source.interactables,source.entities].filter(Boolean);
  const objects=containers.flat().filter(e=>!['room','description','biome'].includes(e.entity_type)&&at(e));
  if(objects.length)groups.push(['objects',`Placed characters, markets & objects (${objects.length})`]);
  return {tile,after,groups,rooms,at};
 }
 function apply(source,mapId,index,target,keep,base={}){
  const change=plan(source,mapId,index,target,base),area=source.maps.find(m=>m.id===mapId);
  const has=key=>keep.includes(key);
  if(!has('rooms'))source.entities=(source.entities||[]).filter(e=>!change.rooms.includes(e));
  if(!has('objects')){
   for(const list of [source.npcs,source.economy?.vendors,source.creatures,source.interactables,source.entities].filter(Boolean)){
    for(let i=list.length-1;i>=0;i--)if(!['room','description','biome'].includes(list[i].entity_type)&&change.at(list[i]))list.splice(i,1);
   }
  }
  const targets=[area.tiles[index],...(source.entities||[]).filter(e=>['room','description'].includes(e.entity_type)&&change.at(e))];
  for(const tile of targets){
   tile.biome=target;
   tile.name=has('name')?change.tile.name:change.after.name;
   tile.description=has('description')?change.tile.description:change.after.description;
   for(const [group,fields] of [['resources',['resources']],['services',['station','rest']],['exits',['exits']],['buildable',['buildable']]]){
    for(const field of fields){if(has(group)&&Object.hasOwn(change.tile,field))tile[field]=structuredClone(change.tile[field]);else delete tile[field];}
   }
  }
 }
 const api={defaults,plan,apply};root.MudmakerTerrainEdit=api;
 if(typeof module!=='undefined')module.exports=api;
})(globalThis);
