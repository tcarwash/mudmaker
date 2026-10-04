// View state is session-local and never serialized into the authored world.
const mapViewports=new Map();
let mapWorkspaceTab='map';
function mapViewState(){return mapViewports.get(selectedMapId)||{zoom:32,left:0,top:0}}
function workspaceTabs(){return `<nav class="map-workspace-tabs" aria-label="Map workspace panels">${[['map','🗺 Map'],['palette','＋ Palette'],['inspector','⌖ Inspector']].map(([id,label])=>`<button class="tool-button" type="button" data-workspace-tab="${id}" aria-pressed="${mapWorkspaceTab===id}">${label}</button>`).join('')}</nav>`}
function mapViewportTools(){return `<div class="map-view-tools"><button class="tool-button" id="map-zoom-out" aria-label="Zoom out">−</button><output id="map-zoom-level">${Math.round(mapViewState().zoom/32*100)}%</output><button class="tool-button" id="map-zoom-in" aria-label="Zoom in">＋</button><button class="tool-button" id="map-fit">Fit map</button><span>${esc(activeMap().name)} · ${activeMap().width}×${activeMap().height}</span></div>`}
function selectWorkspaceTab(tab){mapWorkspaceTab=tab;const workspace=$('.map-workspace');if(workspace)workspace.dataset.activePanel=tab;$$('[data-workspace-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.workspaceTab===tab)));if(tab==='map')restoreMapViewport()}
function restoreMapViewport(){const viewport=$('#map-viewport');if(!viewport||!viewport.clientWidth)return;const state=mapViewState();viewport.scrollLeft=state.left;viewport.scrollTop=state.top}
function wireMapViewport(){
 const viewport=$('#map-viewport');if(!viewport)return;
 const id=selectedMapId;
 restoreMapViewport();
 viewport.addEventListener('scroll',()=>mapViewports.set(id,{...mapViewports.get(id)||{zoom:32},left:viewport.scrollLeft,top:viewport.scrollTop}));
 const zoom=value=>{mapViewports.set(id,{...mapViewState(),zoom:Math.max(12,Math.min(72,value))});render()};
 $('#map-zoom-in').onclick=()=>zoom(mapViewState().zoom+8);
 $('#map-zoom-out').onclick=()=>zoom(mapViewState().zoom-8);
 $('#map-fit').onclick=()=>{mapViewports.set(id,{zoom:Math.max(12,Math.min(72,Math.floor((viewport.clientWidth-14-(activeMap().width-1)*3)/activeMap().width))),left:0,top:0});render()};
 $$('[data-workspace-tab]').forEach(button=>button.onclick=()=>selectWorkspaceTab(button.dataset.workspaceTab));
}
