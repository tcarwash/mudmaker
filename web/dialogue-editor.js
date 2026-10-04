(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const EFFECTS = [['item','Give item'],['currency','Give currency'],['heal','Heal player'],['flag','Set flag'],['quest','Start quest']];
  const rows = values => [...new Set((values || []).filter(value => typeof value === 'string' && value.trim()))].sort((a,b)=>a.localeCompare(b));
  const authoredItems = world => rows([...(world.items || []).map(item=>item.name),...(world.entities || []).filter(item=>item.entity_type==='item').map(item=>item.name),...(world.economy?.vendors || []).flatMap(vendor=>(vendor.stock || []).map(stock=>stock.item))]);
  const select = (path, value, options, label) => `<label class="form-field"><span>${label}</span><select class="select" data-dialog-field="${path}"><option value="">Choose…</option>${options.map(([id,name])=>`<option value="${esc(id)}" ${id===value?'selected':''}>${esc(name)}</option>`).join('')}${value&&!options.some(([id])=>id===value)?`<option value="${esc(value)}" selected>Missing: ${esc(value)}</option>`:''}</select></label>`;
  const input = (path, value, label, type='text', min='') => `<label class="form-field"><span>${label}</span><input class="input" data-dialog-field="${path}" type="${type}" ${min?`min="${min}"`:''} value="${esc(value)}"></label>`;

  function markup() {
    return `<section class="panel" id="dialog-editor" style="margin:12px 0;padding:14px"><div class="panel-heading"><h3>💬 Dialogue flow</h3><button type="button" class="button button-secondary" data-dialog-add-node>＋ Add node</button></div><p class="small-copy">Connect authored dialogue nodes and attach effects to a player's choice. Keep any fields this editor does not expose in Advanced JSON.</p><div data-dialog-start></div><div data-dialog-nodes></div></section>`;
  }

  function mount({root, world, getValue, setValue}) {
    const draft = () => {
      const value = getValue();
      if (!value.nodes || typeof value.nodes !== 'object' || Array.isArray(value.nodes)) value.nodes = {};
      if (!Object.keys(value.nodes).length) value.nodes.greeting = {text:'',choices:[]};
      if (!value.start || !value.nodes[value.start]) value.start = Object.keys(value.nodes)[0];
      return value;
    };
    const questRefs = [...(world.quests||[]).map(value=>[value.id,value.name||value.id]),...(world.entities||[]).filter(value=>value.entity_type==='quest').map(value=>[value.id,value.name||value.id])];
    const itemRefs = authoredItems(world).map(value=>[value,value]);
    const renderEffect = (effect, nodeId, choiceIndex, effectIndex) => {
      const base=`nodes.${nodeId}.choices.${choiceIndex}.effects.${effectIndex}`, type=effect.type;
      let fields='';
      if(type==='item') fields=`${select(`${base}.item`,effect.item,itemRefs,'ITEM')}${input(`${base}.count`,effect.count??1,'COUNT','number','1')}`;
      else if(type==='currency'||type==='heal') fields=input(`${base}.amount`,effect.amount??0,'AMOUNT','number','0');
      else if(type==='flag') fields=input(`${base}.name`,effect.name,'FLAG NAME');
      else if(type==='quest') fields=select(`${base}.target`,effect.target,questRefs,'QUEST');
      else fields=`<div class="small-copy">This effect type is not in the standard editor. Keep and edit it in Advanced JSON.</div>`;
      const types=[...EFFECTS];if(type&&!EFFECTS.some(([id])=>id===type))types.push([type,`Advanced: ${type}`]);
      return `<div class="panel" style="padding:10px;margin:8px 0" data-effect-row><div class="entity-edit">${select(`${base}.type`,type,types,'EFFECT')}${fields}</div><button type="button" class="entity-action" data-dialog-remove-effect="${base}" aria-label="Remove effect">Remove effect</button></div>`;
    };
    const render = () => {
      const value=draft(), ids=Object.keys(value.nodes), nodeOptions=ids.map(id=>[id,id]);
      root.querySelector('[data-dialog-start]').innerHTML=select('start',value.start,nodeOptions,'START NODE');
      root.querySelector('[data-dialog-nodes]').innerHTML=ids.map(nodeId=>{
        const node=value.nodes[nodeId]||{}, choices=node.choices||[];
        return `<article class="panel" style="padding:12px;margin:12px 0" data-dialog-node="${esc(nodeId)}"><div class="panel-heading"><h4>${esc(nodeId)}</h4><button type="button" class="entity-action" data-dialog-remove-node="${esc(nodeId)}" aria-label="Remove node">Remove node</button></div><label class="form-field"><span>NODE TEXT</span><textarea class="textarea" rows="3" data-dialog-field="nodes.${esc(nodeId)}.text">${esc(node.text||'')}</textarea></label><div class="panel-heading"><b>Player choices</b><button type="button" class="button button-secondary" data-dialog-add-choice="${esc(nodeId)}">＋ Add choice</button></div>${choices.map((choice,index)=>`<section class="panel" style="padding:10px;margin:8px 0" data-dialog-choice="${index}"><div class="panel-heading"><b>Choice ${index+1}</b><button type="button" class="entity-action" data-dialog-remove-choice="${esc(nodeId)}:${index}" aria-label="Remove choice">×</button></div><div class="entity-edit">${input(`nodes.${nodeId}.choices.${index}.text`,choice.text,'CHOICE TEXT')}${select(`nodes.${nodeId}.choices.${index}.next`,choice.next||'__end__',[["__end__",'End dialogue'],...nodeOptions],'NEXT NODE')}</div><div class="panel-heading"><b>Choice effects</b><button type="button" class="button button-secondary" data-dialog-add-effect="${esc(nodeId)}:${index}">＋ Add effect</button></div>${(choice.effects||[]).map((effect,effectIndex)=>renderEffect(effect,nodeId,index,effectIndex)).join('')||'<div class="empty-note">No effects</div>'}</section>`).join('')||'<div class="empty-note">Add choices to this node.</div>'}</article>`;
      }).join('');
    };
    const writePath = (object,path,value) => {const parts=path.split('.');let target=object;for(const part of parts.slice(0,-1))target=target[part];if(parts.at(-1)==='next'&&value==='__end__')delete target.next;else target[parts.at(-1)]=value;};
    const writeField = (field, rerender) => {
      const value=draft(),path=field.dataset.dialogField;
      if(path.endsWith('.type')&&path.includes('.effects.')){const parts=path.split('.');parts.pop();let effect=value;for(const part of parts)effect=effect[part];for(const key of ['item','count','amount','name','target'])delete effect[key];}
      if(field.type==='number'){const number=Number(field.value);if(!Number.isInteger(number)||number<(field.min?Number(field.min):0)){if(rerender){field.setCustomValidity('Enter a valid whole number.');field.reportValidity()}return;}field.setCustomValidity('');writePath(value,path,number)}else writePath(value,path,field.value);
      setValue(value);if(rerender)render();
    };
    root.addEventListener('input',event=>{
      const field=event.target.closest('[data-dialog-field]');if(!field||field.tagName==='SELECT'||field.value==='')return;
      writeField(field,false);
    });
    root.addEventListener('change',event=>{
      const field=event.target.closest('[data-dialog-field]');if(!field)return;
      const rerender=field.tagName==='SELECT'&&field.dataset.dialogField.endsWith('.type')&&field.dataset.dialogField.includes('.effects.');
      writeField(field,rerender);
    });
    root.addEventListener('click',event=>{
      const value=draft(),addNode=event.target.closest('[data-dialog-add-node]');
      if(addNode){let n=1;while(value.nodes[`node-${n}`])n++;value.nodes[`node-${n}`]={text:'',choices:[]};setValue(value);render();return;}
      const addChoice=event.target.closest('[data-dialog-add-choice]');if(addChoice){const node=value.nodes[addChoice.dataset.dialogAddChoice];if(!node)return;node.choices??=[];node.choices.push({text:'',next:'',effects:[]});setValue(value);render();return;}
      const removeNode=event.target.closest('[data-dialog-remove-node]');if(removeNode){const id=removeNode.dataset.dialogRemoveNode;if(Object.keys(value.nodes).length<2){value.nodes[id]={text:'',choices:[]};}else{delete value.nodes[id];for(const node of Object.values(value.nodes))for(const choice of node.choices||[])if(choice.next===id)delete choice.next;if(value.start===id)value.start=Object.keys(value.nodes)[0];}setValue(value);render();return;}
      const removeChoice=event.target.closest('[data-dialog-remove-choice]');if(removeChoice){const [id,index]=removeChoice.dataset.dialogRemoveChoice.split(':');value.nodes[id].choices.splice(Number(index),1);setValue(value);render();return;}
      const addEffect=event.target.closest('[data-dialog-add-effect]');if(addEffect){const [id,index]=addEffect.dataset.dialogAddEffect.split(':');value.nodes[id].choices[index].effects??=[];value.nodes[id].choices[index].effects.push({type:'currency',amount:0});setValue(value);render();return;}
      const removeEffect=event.target.closest('[data-dialog-remove-effect]');if(removeEffect){const path=removeEffect.dataset.dialogRemoveEffect.split('.'),index=Number(path.pop());let target=value;for(const part of path)target=target[part];target.splice(index,1);setValue(value);render();}
    });
    render();
  }
  window.MudmakerDialogueEditor={markup,mount};
})();
