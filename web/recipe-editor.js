(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const allItems = world => [...new Set([
    ...(world.items||[]).map(item=>item.name),
    ...(world.entities||[]).filter(item=>item.entity_type==='item').map(item=>item.name),
    ...(world.economy?.vendors||[]).flatMap(vendor=>(vendor.stock||[]).map(stock=>stock.item)),
    ...(world.biomes?Object.values(world.biomes).flatMap(biome=>(biome.resources||[]).map(row=>row.item)):[]),
    ...(world.maps||[]).flatMap(map=>(map.tiles||[]).flatMap(tile=>(tile.resources||[]).map(row=>row.item))),
    ...(world.creatures||[]).flatMap(creature=>(creature.loot||[]).map(row=>row.item)),
    ...(world.entities||[]).filter(entity=>entity.entity_type==='creature').flatMap(creature=>(creature.loot||[]).map(row=>row.item)),
    ...(world.crafting||[]).flatMap(recipe=>[recipe.output?.item,...(recipe.inputs||[]).map(part=>part.item)]),
  ].filter(value=>typeof value==='string'&&value.trim()))].sort((a,b)=>a.localeCompare(b));
  const stations = world => [...new Set((world.maps||[]).flatMap(map=>(map.tiles||[]).map(tile=>tile.station)).filter(value=>typeof value==='string'&&value.trim()))].sort((a,b)=>a.localeCompare(b));
  const itemSelect = (value, options) => `<select class="select" data-recipe-item><option value="">Choose an authored item</option>${options.map(item=>`<option value="${esc(item)}" ${item===value?'selected':''}>${esc(item)}</option>`).join('')}${value&&!options.includes(value)?`<option value="${esc(value)}" selected>Missing: ${esc(value)}</option>`:''}</select>`;
  const count = (value, attr) => `<input class="input" type="number" min="1" step="1" value="${esc(value??1)}" data-recipe-count="${attr}">`;

  function markup() { return `<section class="panel" id="recipe-editor" style="margin:12px 0;padding:14px"><div class="panel-heading"><h3>🧪 Recipe</h3></div><p class="small-copy">Choose authored ingredients, output, quantities, and an optional crafting station.</p><div data-recipe-form></div><div class="panel-heading" style="margin-top:12px"><b>Ingredients</b><button type="button" class="button button-secondary" data-recipe-add-input>＋ Add ingredient</button></div><div data-recipe-inputs></div><div class="panel-heading" style="margin-top:12px"><b>Output</b></div><div class="entity-edit" data-recipe-output></div><div class="form-field" style="margin-top:12px"><label>CRAFTING STATION</label><select class="select" data-recipe-station><option value="">Anywhere</option></select><small>Station names come from tiles that have a station assigned. Keep a custom station in Advanced JSON if it is not placed yet.</small></div><details style="margin-top:12px"><summary>Add an item to this recipe</summary><p class="small-copy">New items stay in this draft until you save the recipe. Cancelling the editor discards them.</p><div class="entity-edit"><input class="input" data-new-item-name placeholder="New item name"><input class="input" data-new-item-description placeholder="Short description (optional)"><button type="button" class="button button-secondary" data-recipe-create-item>＋ Add item</button></div><div data-recipe-pending-items class="small-copy"></div></details></section>`; }

  function mount({root,world,getValue,setValue}) {
    const pending=[];
    const draft=()=>{const value=getValue();value.inputs??=[];value.output??={item:'',count:1};return value};
    const render=()=>{
      const value=draft(),items=[...new Set([...allItems(world),...pending.map(item=>item.name)])];
      root.querySelector('[data-recipe-inputs]').innerHTML=value.inputs.map((part,index)=>`<div class="entity-edit panel" style="padding:10px;margin:8px 0" data-recipe-input-row="${index}">${itemSelect(part.item,items)}${count(part.count,`input:${index}`)}<button type="button" class="entity-action" data-recipe-remove-input="${index}" aria-label="Remove ingredient">×</button></div>`).join('')||'<div class="empty-note">Add at least one ingredient.</div>';
      root.querySelector('[data-recipe-output]').innerHTML=`${itemSelect(value.output.item,items)}${count(value.output.count,'output')}`;
      const station=root.querySelector('[data-recipe-station]'),old=value.station||'';station.innerHTML=`<option value="">Anywhere</option>${stations(world).map(name=>`<option value="${esc(name)}" ${name===old?'selected':''}>${esc(name)}</option>`).join('')}${old&&!stations(world).includes(old)?`<option value="${esc(old)}" selected>Custom: ${esc(old)}</option>`:''}`;
      root.querySelector('[data-recipe-pending-items]').textContent=pending.length?`Pending items: ${pending.map(item=>item.name).join(', ')}`:'';
    };
    root.addEventListener('change',event=>{
      const select=event.target.closest('[data-recipe-item]');
      if(select){const row=select.closest('[data-recipe-input-row]'),value=draft();if(row)value.inputs[Number(row.dataset.recipeInputRow)].item=select.value;else value.output.item=select.value;setValue(value);return;}
      const quantity=event.target.closest('[data-recipe-count]');if(quantity){const number=Number(quantity.value);if(!Number.isInteger(number)||number<1){quantity.setCustomValidity('Enter a positive whole number.');quantity.reportValidity();return;}quantity.setCustomValidity('');const value=draft(),which=quantity.dataset.recipeCount;if(which==='output')value.output.count=number;else value.inputs[Number(which.split(':')[1])].count=number;setValue(value);}
      const station=event.target.closest('[data-recipe-station]');if(station){const value=draft();if(station.value)value.station=station.value;else delete value.station;setValue(value);}
    });
    root.addEventListener('click',event=>{
      const value=draft();
      if(event.target.closest('[data-recipe-add-input]')){value.inputs.push({item:'',count:1});setValue(value);render();return;}
      const remove=event.target.closest('[data-recipe-remove-input]');if(remove){value.inputs.splice(Number(remove.dataset.recipeRemoveInput),1);setValue(value);render();return;}
      if(event.target.closest('[data-recipe-create-item]')){const name=root.querySelector('[data-new-item-name]').value.trim();if(!name)return;const all=[...allItems(world),...pending.map(item=>item.name)];if(all.some(item=>item.toLocaleLowerCase()===name.toLocaleLowerCase())){root.querySelector('[data-new-item-name]').setCustomValidity('That item already exists.');root.querySelector('[data-new-item-name]').reportValidity();return;}pending.push({name,description:root.querySelector('[data-new-item-description]').value.trim()});root.querySelector('[data-new-item-name]').value='';root.querySelector('[data-new-item-description]').value='';render();}
    });
    render();
    return {applyPending:()=>pending.map(item=>({entity_type:'item',id:item.name.toLowerCase().trim().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,48),name:item.name,description:item.description,icon:'🎒',use_effects:[]}))};
  }
  window.MudmakerRecipeEditor={markup,mount};
})();
