function providerProfilesEditor(profiles){return `<div id="provider-profile-list">${profiles.map(providerProfileRow).join('')}</div><button class="button button-secondary" type="button" id="provider-profile-add">＋ Add provider profile</button><div id="provider-form-error" role="alert"></div><details><summary>Advanced provider profiles JSON</summary><textarea class="textarea" id="decision-profiles" rows="5">${esc(JSON.stringify(profiles,null,2))}</textarea></details>`}
function providerProfileRow(profile){
 const field=(key,label,placeholder='')=>`<label class="form-field">${label}<input class="input" data-provider-field="${key}" value="${esc(profile[key]||'')}" placeholder="${placeholder}" autocomplete="off"></label>`;
 return `<article class="panel provider-profile-row" data-provider-row data-original="${esc(JSON.stringify(profile))}"><div class="entity-edit">${field('id','PROFILE ID','laya')}<label class="form-field">ADAPTER<select class="select" data-provider-field="adapter">${!['laya_systemone','jev_systemone'].includes(profile.adapter)?`<option value="${esc(profile.adapter||'')}" selected>Choose a supported adapter${profile.adapter?` · ${esc(profile.adapter)}`:''}</option>`:''}<option value="laya_systemone" ${profile.adapter==='laya_systemone'?'selected':''}>Laya System One</option><option value="jev_systemone" ${profile.adapter==='jev_systemone'?'selected':''}>Jev System One</option></select></label>${field('model','MODEL ID','typed-decisions')}${field('endpoint_binding','ENDPOINT ENVIRONMENT VARIABLE','LAYA_ENDPOINT')}${field('credential_binding','CREDENTIAL VARIABLE · OPTIONAL','LAYA_API_KEY')}</div><p class="small-copy">Enter variable names only. Supply their private values in each deployment.</p><button class="button button-secondary" type="button" data-provider-remove>Remove profile</button></article>`;
}
function readProviderProfiles(){
 const error=$('#provider-form-error'),fail=message=>{error.textContent=message;throw Error(message)};
 let advanced;try{advanced=JSON.parse($('#decision-profiles').value)}catch{fail('Check advanced provider JSON.');}
 if(!Array.isArray(advanced)||advanced.some(p=>!p||typeof p!=='object'||Array.isArray(p)))fail('Advanced profiles must be an array of objects.');
 const ids=new Set(),profiles=$$('[data-provider-row]').map((row,index)=>{
 const profile={...JSON.parse(row.dataset.original)},read=key=>row.querySelector(`[data-provider-field="${key}"]`).value.trim();
 for(const key of ['id','adapter','model','endpoint_binding'])profile[key]=read(key);
 const credential=read('credential_binding');if(credential)profile.credential_binding=credential;else delete profile.credential_binding;
 const prefix=`Profile ${index+1}: `;
 if(!/^[a-z][a-z0-9_-]{0,47}$/.test(profile.id)||ids.has(profile.id))fail(prefix+'Use a unique lowercase profile ID.');ids.add(profile.id);
 if(!profile.model)fail(prefix+'Set a model ID.');
 if(!['laya_systemone','jev_systemone'].includes(profile.adapter))fail(prefix+'Choose a supported adapter.');
 if(!/^[A-Z][A-Z0-9_]{0,63}$/.test(profile.endpoint_binding)||credential&&!/^[A-Z][A-Z0-9_]{0,63}$/.test(credential))fail(prefix+'Bindings must be uppercase environment variable names, not URLs or credential values.');
 if(credential===profile.endpoint_binding)fail(prefix+'Endpoint and credential need separate variables.');
 return profile;
 });
 if(profiles.length>16)fail('Use at most 16 provider profiles.');
 for(const owner of [...(world.npcs||[]),...(world.economy?.vendors||[]),...(world.entities||[])]){
 const policy=owner.decision_policy;if(policy?.provider_id&&!ids.has(policy.provider_id))fail(`Profile ${policy.provider_id} is referenced by ${owner.name||owner.id}. Change that controller's provider before removing or renaming it.`);
 }
 error.textContent='';return profiles;
}
function wireProviderProfiles(){
 const list=$('#provider-profile-list'),json=$('#decision-profiles'),error=$('#provider-form-error');if(!list||!json)return;
 const sync=()=>{try{json.value=JSON.stringify(readProviderProfiles(),null,2);error.textContent=''}catch(e){error.textContent=e.message}};
 $('#provider-profile-add').onclick=()=>{list.insertAdjacentHTML('beforeend',providerProfileRow({id:'',adapter:'laya_systemone',model:'',endpoint_binding:''}));list.lastElementChild.querySelector('[data-provider-field="id"]').focus()};
 list.addEventListener('input',sync);list.addEventListener('change',sync);list.addEventListener('click',event=>{if(event.target.closest('[data-provider-remove]')){event.target.closest('[data-provider-row]').remove();sync()}});
 json.addEventListener('input',()=>{try{const profiles=JSON.parse(json.value);if(!Array.isArray(profiles)||profiles.some(p=>!p||typeof p!=='object'||Array.isArray(p)))throw Error('Use an array of provider objects.');list.innerHTML=profiles.map(providerProfileRow).join('');error.textContent=''}catch(e){error.textContent=e.message}});
}
