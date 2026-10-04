let conflictReviewActive=false;
function conflictFieldLabel(path,source){
 const match=path.match(/^maps\[([^\]]+)\](?:\.tiles\[(\d+)\])?(?:\.(.*))?$/);
 if(!match)return path.replaceAll('_',' ');
 const area=source.maps.find(m=>m.id===match[1]);
 return `${area?.name||match[1]}${match[2]!==undefined&&area?` · tile ${Number(match[2])%area.width}, ${Math.floor(Number(match[2])/area.width)}`:''}${match[3]?` · ${match[3].replaceAll('_',' ')}`:''}`;
}
async function reviewSaveConflict(){
 clearTimeout(saveTimer);const local=MudmakerMapState.serialize(world),base=worldBases.get(world.id);
 try{
  const response=await fetch(`/api/worlds/${world.id}`);if(!response.ok)throw Error('Could not load the server version.');
  const server=await response.json(),revision=response.headers.get('etag');if(!revision)throw Error('Server revision is missing.');
  const result=base?MudmakerWorldMerge.merge(base,local,server):{conflicts:[{path:'world',local,server}],world:local};
  const show=value=>value===undefined?'Removed':JSON.stringify(value,null,2);
  modal(`<div class="modal-head"><div><h2>Review save conflict</h2><p>Independent edits are combined. Choose a version for each overlapping edit. Nothing is saved until you apply.</p></div><button class="modal-close">×</button></div><div class="conflict-review">${result.conflicts.length?result.conflicts.map((entry,index)=>`<section class="conflict-entry"><h3>${esc(conflictFieldLabel(entry.path,local))}</h3><details><summary>Advanced field reference</summary><code>${esc(entry.path)}</code></details><div class="conflict-values"><div><strong>Your draft</strong><pre>${esc(show(entry.local))}</pre></div><div><strong>Server version</strong><pre>${esc(show(entry.server))}</pre></div></div><label>Keep <select class="select" data-conflict-choice="${index}"><option value="">Choose a version</option><option value="local">Your draft</option><option value="server">Server version</option></select></label></section>`).join(''):'<p>No overlapping edits. Your changes and the latest server changes can be combined.</p>'}</div><div id="conflict-error" role="alert"></div><div class="modal-actions"><button class="button button-secondary modal-cancel">Cancel review</button><button class="button button-primary" id="conflict-apply">Apply resolution and save</button></div>`);
  conflictReviewActive=true;lockDraftChoice(true);
  $$('[data-conflict-choice]').forEach(select=>select.onchange=()=>{$('#conflict-error').textContent=''});
  $('#conflict-apply').onclick=async()=>{try{
   const choices={};for(const select of $$('[data-conflict-choice]')){if(!select.value){$('#conflict-error').textContent='Choose a version for every overlapping edit.';return}choices[result.conflicts[+select.dataset.conflictChoice].path]=select.value}
   const resolved=base?MudmakerWorldMerge.merge(base,local,server,choices).world:(choices.world==='server'?server:local);
   const mapId=selectedMapId,tile=selectedTile;world=MudmakerMapState.serialize(resolved);ensureMaps();if(world.maps.some(m=>m.id===mapId))activateMap(mapId,false);selectedTile=tile>=0&&tile<activeMap().tiles.length?tile:-1;rememberMapSelection();
   worldBases.set(world.id,structuredClone(server));worldRevisions.set(`/api/worlds/${world.id}`,revision);saveVersion++;persistWorldDraft();clearMapHistory();hideModal();render();
   try{await flushSave()}catch{}
  }catch(error){$('#conflict-error').textContent=error.message}};
 }catch(error){toast(error.message)}
}
