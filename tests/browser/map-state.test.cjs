const assert = require('node:assert/strict');
const {serialize, resizeImpact, resize} = require('../../web/map-state.js');
const area = (id, width=8) => ({id, name:id, width, height:8, spawn:{x:0,y:0}, entry_points:{start:{x:0,y:0}}, tiles:Array.from({length:width*8},()=>({biome:'grassland'}))});
const world={default_map_id:'main',maps:[area('main',16),area('home')],npcs:[{id:'far-npc',map_id:'main',location:{x:15,y:7}}]};
// UI aliases describe the viewed home, but persisted aliases describe the default map.
Object.assign(world,world.maps[1]);world.id='realm';
const saved=serialize(world);
assert.equal(saved.width,16);assert.equal(saved.tiles.length,128);assert.equal(world.width,8);
assert.deepEqual(resizeImpact(world,'home',4,4),[]);
resize(world,'home',4,4);assert.equal(world.maps[1].tiles.length,16);assert.equal(world.maps[0].width,16);
world.maps[0].tiles[0].exits={door:{map_id:'home',x:3,y:3}};
world.maps[1].entry_points.remote={x:3,y:3};
world.npcs.push({id:'resident',map_id:'home',location:{x:3,y:3},decision_policy:{routes:[{id:'route',map_id:'main',location:{x:15,y:7}}]}});
assert.deepEqual(resizeImpact(world,'home',4,4),[]);
world.maps[0].tiles[0].exits.door.x=5;
assert.match(resizeImpact(world,'home',4,4).join('\n'),/Connection main/);
assert.throws(()=>resize(world,'home',4,4),/Connection/);
assert.equal(world.maps[1].width,4);
// Both authored route and quest targets participate in impact analysis.
world.npcs[1].decision_policy.routes[0]={id:'outside-route',map_id:'home',location:{x:5,y:0}};
world.quests=[{id:'visit-home',objectives:[{type:'visit',target:{map_id:'home',x:6,y:0}}]}];
assert.match(resizeImpact(world,'home',4,4).join('\n'),/outside-route/);
assert.match(resizeImpact(world,'home',4,4).join('\n'),/quests/);
world.maps[1].tiles[15].name='Keep this authored room';
assert.match(resizeImpact(world,'home',4,4).join('\n'),/Connection/);
const textWorld={default_map_id:'room',maps:[area('room')]};textWorld.maps[0].tiles[63].description='An authored passage';
assert.match(resizeImpact(textWorld,'room',4,4).join('\n'),/authored text/);
const generatedTextWorld={default_map_id:'room',maps:[area('room')]};
for(const tile of generatedTextWorld.maps[0].tiles){tile.name='Grassland';tile.description='A quiet stretch of grassland.';}
assert.deepEqual(resizeImpact(generatedTextWorld,'room',4,4),[]);
generatedTextWorld.maps[0].tiles[63].description='A secret written by the author.';
assert.match(resizeImpact(generatedTextWorld,'room',4,4).join('\n'),/authored text/);
console.log('Map serialization and resize regressions passed');
