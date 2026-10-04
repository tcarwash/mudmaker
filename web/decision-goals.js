function decisionGoalEditor(goals,ownerType){
 return `<section id="decision-goal-editor" data-owner-type="${ownerType}"><h3>Measurable goals</h3><p class="small-copy">Goals guide choices; permitted actions and resource limits remain enforced by the engine.</p><div id="decision-goal-list">${goals.map(goal=>decisionGoalRow(goal,ownerType)).join('')}</div><button type="button" class="button button-secondary" id="decision-goal-add">＋ Add goal</button><div id="decision-goal-error" role="alert"></div><details><summary>Advanced goals JSON</summary><textarea class="textarea" id="decision-goals">${esc(JSON.stringify(goals,null,2))}</textarea></details></section>`;
}
function decisionGoalRow(goal,type){
 const metrics=type==='economy'?['stock_level','stock_target','supply_level']:['visitors_greeted','task_progress','deliveries_completed','route_visits','inventory_level','currency_balance','market_trades'];
 return `<article class="panel decision-goal-row" data-goal-row data-original="${esc(JSON.stringify(goal))}"><div class="entity-edit"><label class="form-field">GOAL ID<input class="input" data-goal-id value="${esc(goal.id||'')}"></label><label class="form-field">METRIC<select class="select" data-goal-metric>${metrics.map(metric=>`<option value="${metric}" ${goal.metric===metric?'selected':''}>${metric.replaceAll('_',' ')}</option>`).join('')}</select></label><label class="form-field full">OBJECTIVE<input class="input" data-goal-description value="${esc(goal.description||'')}"></label><label class="form-field">TARGET · BLANK IF OPTIONAL<input class="input" type="number" min="0" step="any" data-goal-target value="${goal.target??''}"></label><label class="form-field">PRIORITY<input class="input" type="number" min="1" step="1" data-goal-priority value="${goal.priority??1}"></label><label class="form-field" data-goal-item-field>ITEM<input class="input" data-goal-item value="${esc(goal.item||'')}" list="decision-goal-items"></label></div><button type="button" class="button button-secondary" data-goal-remove>Remove goal</button></article>`;
}
function readDecisionGoals(){
 const advanced=JSON.parse($('#decision-goals').value);if(!Array.isArray(advanced)||advanced.some(goal=>!goal||typeof goal!=='object'||Array.isArray(goal)))throw Error('Advanced goals must be an array of goal objects.');
 const seen=new Set(),type=$('#decision-goal-editor').dataset.ownerType;
 const goals=$$('[data-goal-row]').map((row,index)=>{
 const get=key=>row.querySelector(`[data-goal-${key}]`).value.trim(),goal={...JSON.parse(row.dataset.original),id:get('id'),description:get('description'),metric:get('metric'),priority:Number(get('priority'))};
 const fail=message=>{throw Error(`Goal ${index+1}: ${message}`)};
 if(!/^[a-z][a-z0-9_-]{0,47}$/.test(goal.id)||seen.has(goal.id))fail('Use a unique lowercase goal ID.');seen.add(goal.id);
 if(!goal.description)fail('Describe the objective.');if(!Number.isSafeInteger(goal.priority)||goal.priority<1)fail('Priority must be a positive whole number.');
 const target=get('target');if(target){goal.target=Number(target);if(!Number.isFinite(goal.target)||goal.target<0)fail('Target must be a nonnegative number.')}else delete goal.target;
 const needsItem=type==='economy'||goal.metric==='inventory_level';if(needsItem){goal.item=get('item');if(!goal.item)fail('Choose the item being measured.');}else delete goal.item;
 if((type==='economy'||['inventory_level','currency_balance','market_trades','deliveries_completed'].includes(goal.metric))&&goal.target===undefined)fail('This metric requires a target.');
 return goal;
 });
 if(goals.length>16)throw Error('Use at most 16 goals.');return goals;
}
function wireDecisionGoals(){
 const host=$('#decision-goal-editor');if(!host)return;const type=host.dataset.ownerType,list=$('#decision-goal-list'),json=$('#decision-goals'),error=$('#decision-goal-error');
 const update=()=>{for(const row of list.querySelectorAll('[data-goal-row]'))row.querySelector('[data-goal-item-field]').hidden=type!=='economy'&&row.querySelector('[data-goal-metric]').value!=='inventory_level';};
 const sync=()=>{update();try{json.value=JSON.stringify(readDecisionGoals(),null,2);error.textContent=''}catch(e){error.textContent=e.message}};
 const items=type==='economy'?(()=>{try{return JSON.parse($('#market-stock').value).map(offer=>offer.item)}catch{return []}})():[...new Set((world.economy?.vendors||[]).flatMap(v=>(v.stock||[]).map(offer=>offer.item)))];
 list.insertAdjacentHTML('afterend',`<datalist id="decision-goal-items">${items.filter(Boolean).map(item=>`<option value="${esc(item)}"></option>`).join('')}</datalist>`);
 $('#decision-goal-add').onclick=()=>{list.insertAdjacentHTML('beforeend',decisionGoalRow({id:'',description:'',priority:1},type));update();list.lastElementChild.querySelector('[data-goal-id]').focus()};
 list.addEventListener('input',sync);list.addEventListener('change',sync);list.addEventListener('click',event=>{if(event.target.closest('[data-goal-remove]')){event.target.closest('[data-goal-row]').remove();sync()}});
 json.addEventListener('input',()=>{try{const goals=JSON.parse(json.value);if(!Array.isArray(goals)||goals.some(goal=>!goal||typeof goal!=='object'||Array.isArray(goal)))throw Error('Use an array of goal objects.');list.innerHTML=goals.map(goal=>decisionGoalRow(goal,type)).join('');update();error.textContent=''}catch(e){error.textContent=e.message}});update();
}
