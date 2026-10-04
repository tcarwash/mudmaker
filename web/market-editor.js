// Guided inventory forms. Unknown offer fields survive edits through the advanced scaffold.
function marketInventoryEditor(offers){
 return `<div id="market-offers">${offers.map(renderMarketOfferRow).join('')}</div><button type="button" class="button button-secondary" id="market-offer-add">＋ Add item offer</button><div id="market-form-error" role="alert"></div><details style="margin-top:12px"><summary>Advanced inventory JSON</summary><p class="small-copy">Use this for additional offer fields. Valid edits update the form and preserve extra fields.</p><textarea class="textarea" id="market-stock" style="min-height:190px;font:12px 'DM Mono'">${esc(JSON.stringify(offers,null,2))}</textarea></details>`;
}
function renderMarketOfferRow(offer={}){
 const rule=offer.pricing||{},restock=offer.restock||{},production=offer.production||{},consumption=offer.consumption||{};
 const field=(key,label,value,min=0,max='')=>`<label class="form-field">${label}<input class="input" data-offer-field="${key}" type="number" min="${min}" ${max!==''?`max="${max}"`:''} step="${key==='sensitivity'?'0.1':'1'}" value="${esc(value??'')}"></label>`;
 return `<article class="panel market-offer-row" data-market-offer data-original="${esc(JSON.stringify(offer))}" style="padding:14px;margin-bottom:12px"><div class="panel-heading"><h3>🛒 Item offer</h3><button type="button" class="button button-secondary" data-offer-remove aria-label="Remove item offer">Remove</button></div><div class="entity-edit"><label class="form-field">ITEM NAME<input class="input" data-offer-field="item" list="market-item-names" value="${esc(offer.item||'')}" placeholder="Select or enter an item"></label>${field('quantity','STARTING STOCK · BLANK = UNLIMITED',offer.quantity)}${field('price','YOU PAY · FIXED UNIT PRICE',offer.price??1)}${field('sell_price','YOU RECEIVE · FIXED UNIT PRICE',offer.sell_price??Math.floor((offer.price??1)/2))}<label class="form-field">PRICING MODE<select class="select" data-offer-field="mode"><option value="fixed" ${rule.mode!=='stock_based'?'selected':''}>Fixed price</option><option value="stock_based" ${rule.mode==='stock_based'?'selected':''}>Stock-based price</option></select></label></div><div class="entity-edit" data-stock-pricing ${rule.mode==='stock_based'?'':'hidden'}>${field('base_price','BASE PRICE',rule.base_price??offer.price??1,1)}${field('target_stock','TARGET STOCK',rule.target_stock??offer.quantity??20,1)}${field('sensitivity','SENSITIVITY · 0–4',rule.sensitivity??1,0,4)}${field('min_price','MINIMUM PRICE',rule.min_price??0)}${field('max_price','MAXIMUM PRICE',rule.max_price??1000,1)}${field('spread','BUY / SELL SPREAD',rule.spread??1)}</div><label class="small-copy"><input type="checkbox" data-offer-field="restock" ${offer.restock?'checked':''}> Replenish this item on a schedule</label><div class="entity-edit" data-restock-fields ${offer.restock?'':'hidden'}>${field('interval_seconds','RESTOCK INTERVAL · SECONDS',restock.interval_seconds??300,1,31536000)}${field('amount','UNITS PER INTERVAL',restock.amount??1,1)}${field('max_quantity','MAXIMUM STOCK',restock.max_quantity??offer.quantity??20)}</div><label class="small-copy"><input type="checkbox" data-offer-field="production" ${offer.production?'checked':''}> Produce this item from market inventory</label><div data-production-fields ${offer.production?'':'hidden'}><p class="small-copy">Each cycle consumes ingredients from this market's finite stock. Production pauses when ingredients are missing or the output cap is reached.</p><div class="entity-edit">${field('production_interval','PRODUCTION INTERVAL · SECONDS',production.interval_seconds??300,1,31536000)}${field('production_amount','OUTPUT UNITS PER CYCLE',production.amount??1,1)}${field('production_max','MAXIMUM OUTPUT STOCK',production.max_quantity??offer.quantity??20)}</div><div data-production-inputs>${(production.inputs||[]).map(renderProductionInput).join('')}</div><button type="button" class="button button-secondary" data-production-add>＋ Add ingredient</button></div><label class="small-copy"><input type="checkbox" data-offer-field="consumption" ${offer.consumption?'checked':''}> Consume stock on a demand schedule</label><div class="entity-edit" data-consumption-fields ${offer.consumption?'':'hidden'}>${field('consumption_interval','DEMAND INTERVAL · SECONDS',consumption.interval_seconds??3600,1,31536000)}${field('consumption_amount','UNITS CONSUMED PER CYCLE',consumption.amount??1,1)}</div></article>`;
}
function renderProductionInput(part={}){
 return `<div class="entity-edit" data-production-input data-original="${esc(JSON.stringify(part))}"><label class="form-field">INGREDIENT ITEM<input class="input" data-production-item list="market-stock-items" value="${esc(part.item||'')}"></label><label class="form-field">UNITS PER CYCLE<input class="input" data-production-count type="number" min="1" step="1" value="${part.count??1}"></label><button type="button" class="button button-secondary" data-production-remove>Remove ingredient</button></div>`;
}
function readMarketOfferRows(){
 const offers=[...document.querySelectorAll('[data-market-offer]')].map((row,index)=>{
   const original=JSON.parse(row.dataset.original),offer=structuredClone(original),input=key=>row.querySelector(`[data-offer-field="${key}"]`),number=key=>Number(input(key).value);
   const fail=message=>{throw Error(`Offer ${index+1}: ${message}`)};
   const integer=(key,min=0,max=Number.MAX_SAFE_INTEGER)=>{const value=number(key);if(!input(key).value.trim()||!Number.isSafeInteger(value)||value<min||value>max)fail(`${key.replaceAll('_',' ')} must be an integer from ${min} to ${max}.`);return value};
   offer.item=input('item').value.trim();if(!offer.item)fail('Choose an item name.');
   offer.price=integer('price');const sell=integer('sell_price');if(sell>offer.price)fail('The sale price cannot exceed the purchase price.');
   offer.sell_price=sell;
   if(input('quantity').value.trim())offer.quantity=integer('quantity');else delete offer.quantity;
   if(input('mode').value==='stock_based'){
     if(offer.quantity===undefined)fail('Stock-based pricing requires finite starting stock.');
     offer.pricing={...(original.pricing||{}),mode:'stock_based',base_price:integer('base_price',1),target_stock:integer('target_stock',1),sensitivity:number('sensitivity'),min_price:integer('min_price'),max_price:integer('max_price',1),spread:integer('spread')};
     if(!input('sensitivity').value.trim()||!Number.isFinite(offer.pricing.sensitivity)||offer.pricing.sensitivity<0||offer.pricing.sensitivity>4)fail('Sensitivity must be between 0 and 4.');
     if(offer.pricing.min_price>offer.pricing.max_price||offer.pricing.spread>offer.pricing.max_price)fail('Check minimum, maximum, and spread.');
   }else if(original.pricing)offer.pricing={...original.pricing,mode:'fixed'};
   if(input('restock').checked){
     if(offer.quantity===undefined)fail('Restocking requires finite starting stock.');
     offer.restock={...(original.restock||{}),interval_seconds:integer('interval_seconds',1,31536000),amount:integer('amount',1),max_quantity:integer('max_quantity')};
     if(offer.restock.max_quantity<offer.quantity)fail('Maximum stock cannot be below starting stock.');
   }else delete offer.restock;
   if(input('production').checked){
     if(offer.quantity===undefined)fail('Production requires finite output stock.');
     const inputs=[...row.querySelectorAll('[data-production-input]')].map(part=>{
       const countInput=part.querySelector('[data-production-count]'),count=Number(countInput.value),item=part.querySelector('[data-production-item]').value.trim();
       if(!item||!countInput.value.trim()||!Number.isSafeInteger(count)||count<1)fail('Every ingredient needs an item and a positive whole quantity.');
       return {...JSON.parse(part.dataset.original),item,count};
     });
     if(!inputs.length)fail('Add at least one production ingredient.');
     offer.production={...(original.production||{}),interval_seconds:integer('production_interval',1,31536000),amount:integer('production_amount',1),max_quantity:integer('production_max'),inputs};
     if(offer.production.max_quantity<offer.quantity)fail('Production cap cannot be below starting stock.');
   }else delete offer.production;
   if(input('consumption').checked){
     if(offer.quantity===undefined)fail('Demand requires finite stock.');
     offer.consumption={...(original.consumption||{}),interval_seconds:integer('consumption_interval',1,31536000),amount:integer('consumption_amount',1)};
   }else delete offer.consumption;
   return offer;
 });
 const byName=new Map(offers.map(offer=>[offer.item,offer]));
 for(const [index,offer] of offers.entries()){
   const seen=new Set();
   for(const part of offer.production?.inputs||[]){
     if(part.item===offer.item||seen.has(part.item))throw Error(`Offer ${index+1}: Ingredients cannot repeat or consume their own output.`);
     if(!byName.has(part.item)||byName.get(part.item).quantity===undefined)throw Error(`Offer ${index+1}: Ingredient ${part.item} must have finite stock in this market.`);
     seen.add(part.item);
   }
 }
 return offers;
}
function wireMarketInventory(){
 const list=document.querySelector('#market-offers'),json=document.querySelector('#market-stock'),error=document.querySelector('#market-form-error');if(!list||!json)return;
 const names=[...new Set([...(world.items||[]).map(item=>item.name),...(world.entities||[]).filter(item=>item.entity_type==='item').map(item=>item.name),...(world.economy?.vendors||[]).flatMap(market=>(market.stock||[]).map(offer=>offer.item)),...Object.keys(world.starting_inventory||{})].filter(Boolean))];
 list.insertAdjacentHTML('afterend',`<datalist id="market-item-names">${names.map(name=>`<option value="${esc(name)}"></option>`).join('')}</datalist><datalist id="market-stock-items"></datalist>`);
 const refreshIngredients=()=>{const finite=[...list.querySelectorAll('[data-market-offer]')].filter(row=>row.querySelector('[data-offer-field="quantity"]').value.trim()).map(row=>row.querySelector('[data-offer-field="item"]').value.trim()).filter(Boolean);document.querySelector('#market-stock-items').innerHTML=[...new Set(finite)].map(item=>`<option value="${esc(item)}"></option>`).join('')};
 const sync=()=>{refreshIngredients();try{const offers=readMarketOfferRows();json.value=JSON.stringify(offers,null,2);error.textContent='';renderMarketPricePreview()}catch(e){error.textContent=e.message}};
 const update=row=>{row.querySelector('[data-stock-pricing]').hidden=row.querySelector('[data-offer-field="mode"]').value!=='stock_based';row.querySelector('[data-restock-fields]').hidden=!row.querySelector('[data-offer-field="restock"]').checked;row.querySelector('[data-production-fields]').hidden=!row.querySelector('[data-offer-field="production"]').checked;row.querySelector('[data-consumption-fields]').hidden=!row.querySelector('[data-offer-field="consumption"]').checked};
 list.addEventListener('input',event=>{const row=event.target.closest('[data-market-offer]');if(row){update(row);sync()}});
 list.addEventListener('change',event=>{const row=event.target.closest('[data-market-offer]');if(row){update(row);sync()}});
 list.addEventListener('click',event=>{if(event.target.closest('[data-production-add]')){const row=event.target.closest('[data-market-offer]');row.querySelector('[data-production-inputs]').insertAdjacentHTML('beforeend',renderProductionInput());row.querySelector('[data-production-inputs]').lastElementChild.querySelector('[data-production-item]').focus();sync()}if(event.target.closest('[data-production-remove]')){event.target.closest('[data-production-input]').remove();sync()}if(event.target.closest('[data-offer-remove]')){event.target.closest('[data-market-offer]').remove();sync()}});
 document.querySelector('#market-offer-add').onclick=()=>{list.insertAdjacentHTML('beforeend',renderMarketOfferRow({item:'',price:1,quantity:20}));list.lastElementChild.querySelector('[data-offer-field="item"]').focus()};
 json.addEventListener('input',()=>{try{const offers=JSON.parse(json.value);if(!Array.isArray(offers)||offers.some(offer=>!offer||typeof offer!=='object'||Array.isArray(offer)))throw Error('Inventory must be an array of item offers.');list.innerHTML=offers.map(renderMarketOfferRow).join('');refreshIngredients();error.textContent='';renderMarketPricePreview()}catch(e){error.textContent=e.message}});
 refreshIngredients();
}
