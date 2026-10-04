// Each tab keeps a separate recovery copy so parallel editors cannot erase it.
let draftTabId;
try {
 draftTabId=sessionStorage.getItem('mudmaker-draft-tab')||crypto.randomUUID();
 sessionStorage.setItem('mudmaker-draft-tab',draftTabId);
} catch { draftTabId=crypto.randomUUID(); }
function draftKey(id){return `mudmaker-draft:${id}:${draftTabId}`}
function persistWorldDraft(){
 try {
  localStorage.setItem(draftKey(world.id),JSON.stringify({world:MudmakerMapState.serialize(world),base:worldBases.get(world.id),revision:worldRevisions.get(`/api/worlds/${world.id}`),mapId:selectedMapId,tile:selectedTile,time:Date.now()}));
  $('#draft-storage-status').hidden=true;
 } catch {
  const panel=$('#draft-storage-status');panel.hidden=false;
  panel.textContent='Browser draft storage is unavailable or full. Keep this page open until saving succeeds, or download your draft from the save-error panel.';
 }
}
function clearWorldDraft(id){try{localStorage.removeItem(draftKey(id))}catch{}}
function lockDraftChoice(locked){for(const node of [$('#view'),$('.topbar'),$('.sidebar')])if(node)node.inert=locked}
function offerRecoveredDraft(){
 lockDraftChoice(false);const panel=$('#draft-recovery');panel.hidden=true;
 let draft;try{draft=JSON.parse(localStorage.getItem(draftKey(world.id)||'null'))}catch{offerRecoveredModalDraft();return}
 if(!draft||draft.world?.id!==world.id||!Array.isArray(draft.world.maps)||!draft.revision){offerRecoveredModalDraft();return}
 const current=MudmakerMapState.serialize(world);
 if(JSON.stringify(draft.world)===JSON.stringify(current)){clearWorldDraft(world.id);offerRecoveredModalDraft();return}
 panel.hidden=false;lockDraftChoice(true);
 const changed=draft.revision!==worldRevisions.get(`/api/worlds/${world.id}`);
 panel.innerHTML=`<strong>Unsaved draft available</strong><p>Recover edits from ${esc(new Date(draft.time).toLocaleString())}.${changed?' The server has changed since this draft started. Restoring keeps its original revision; saving will require conflict resolution.':''}</p><button class="button button-secondary" id="draft-restore">Restore draft</button> <button class="button button-secondary" id="draft-discard">Discard draft and keep server version</button>`;
 $('#draft-discard').onclick=()=>{clearWorldDraft(world.id);panel.hidden=true;lockDraftChoice(false);offerRecoveredModalDraft()};
 $('#draft-restore').focus();
 $('#draft-restore').onclick=()=>{
  clearTimeout(saveTimer);world=structuredClone(draft.world);ensureMaps();
  if(world.maps.some(map=>map.id===draft.mapId))activateMap(draft.mapId,false);
  selectedTile=Number.isInteger(draft.tile)&&draft.tile>=0&&draft.tile<activeMap().tiles.length?draft.tile:-1;rememberMapSelection();
  worldRevisions.set(`/api/worlds/${world.id}`,draft.revision);if(draft.base)worldBases.set(world.id,structuredClone(draft.base));else worldBases.delete(world.id);saveVersion++;clearMapHistory();
  previewLines=[];previewState=null;panel.hidden=true;lockDraftChoice(false);go('map');status('Restored unsaved draft');
  showSaveFailure(new Error(changed?'Restored draft conflicts with a newer server revision':'Restored local draft has not been saved'));offerRecoveredModalDraft();
 };
}

function modalDraftKey(id){return `mudmaker-modal-draft:${id}:${draftTabId}`}
function modalFieldSnapshot(){return [...$('#modal').querySelectorAll('input[id],select[id],textarea[id]')].map(field=>({id:field.id,value:field.value,checked:field.type==='checkbox'||field.type==='radio'?field.checked:undefined}))}
function captureCurrentModalDraft(){
 if(!editorModalContext)return null;
 const context=structuredClone(editorModalContext),fields=modalFieldSnapshot();
 if(context.type==='entity'){
  try{context.entity=JSON.parse($('#entity-json')?.value||'{}')}catch{}
 }else if(context.type==='workbench'){
  context.kind=$('#ai-kind')?.value||context.kind;
  try{context.scaffold=JSON.parse($('#ai-scaffold')?.value||'{}')}catch{}
  context.instructions=$('#ai-instructions')?.value||'';
  context.concept=$('#ai-concept')?.value||'';
 }
 return {context,fields,view:viewName,time:Date.now()};
}
function clearModalRecoveryDraft(id){if(!id)return;try{localStorage.removeItem(modalDraftKey(id))}catch{}}
let modalRecoveryTimer;
function writeModalRecoveryDraft(){if(!world?.id||!editorModalContext||$('#modal-backdrop')?.classList.contains('hidden'))return;const draft=captureCurrentModalDraft();if(!draft)return;try{localStorage.setItem(modalDraftKey(world.id),JSON.stringify({worldId:world.id,...draft}))}catch{}}
function persistModalRecoveryDraft(){clearTimeout(modalRecoveryTimer);modalRecoveryTimer=setTimeout(writeModalRecoveryDraft,180)}
window.addEventListener('pagehide',writeModalRecoveryDraft);
for(const eventName of ['input','change','click'])document.addEventListener(eventName,event=>{if(event.target.closest?.('#modal'))persistModalRecoveryDraft()},true);
function applyModalFields(fields=[]){for(const saved of fields){const field=document.getElementById(saved.id);if(!field)continue;if(saved.checked!==undefined)field.checked=saved.checked;else field.value=saved.value}}
function restoreModalContext(draft){
 const context=draft.context;
 if(context.type==='entity'){
  viewName=context.view||draft.view||'entities';render();editEntity(context.key,context.index,context.entity||context.initial);applyModalFields(draft.fields);return;
 }
 if(context.type==='workbench'){
  const parent=context.parent;
  if(parent?.context?.type==='entity'){
   const owner=parent.context;viewName=owner.view||draft.view||'entities';render();editEntity(owner.key,owner.index,owner.entity||owner.initial);applyModalFields(parent.fields);
   assistantModal({kind:context.kind,scaffold:context.scaffold,instructions:context.instructions,concept:context.concept,parent,apply:result=>editEntity(owner.key,owner.index,result)});
  }else assistantModal({kind:context.kind,scaffold:context.scaffold,instructions:context.instructions,concept:context.concept});
  applyModalFields(draft.fields);
 }
}
function offerRecoveredModalDraft(){
 const panel=$('#draft-recovery');let draft;try{draft=JSON.parse(localStorage.getItem(modalDraftKey(world.id))||'null')}catch{return}
 if(!draft||draft.worldId!==world.id||!draft.context)return;
 panel.hidden=false;lockDraftChoice(true);
 const kind=draft.context.type==='entity'?'editor':draft.context.parent?'nested workbench':'workbench';
 panel.innerHTML=`<strong>Unapplied ${kind} draft available</strong><p>Recover the open ${kind} from ${esc(new Date(draft.time).toLocaleString())}. It will reopen for review and will not be applied to the world automatically.</p><button class="button button-secondary" id="modal-draft-restore">Restore ${kind}</button> <button class="button button-secondary" id="modal-draft-discard">Discard ${kind} draft</button>`;
 $('#modal-draft-discard').onclick=()=>{clearModalRecoveryDraft(world.id);panel.hidden=true;lockDraftChoice(false)};
 $('#modal-draft-restore').focus();
 $('#modal-draft-restore').onclick=()=>{clearModalRecoveryDraft(world.id);panel.hidden=true;lockDraftChoice(false);restoreModalContext(draft)};
}
