// Three-way merge; structural changes are kept together rather than guessed.
const MudmakerWorldMerge=(()=>{
 const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
 const object=value=>value&&typeof value==='object'&&!Array.isArray(value);
 const keyed=value=>Array.isArray(value)&&value.every(entry=>object(entry)&&typeof entry.id==='string')&&new Set(value.map(entry=>entry.id)).size===value.length;
 function merge(base,local,server,choices={}){
  const conflicts=[];
  function visit(b,l,s,path,key){
   if(same(l,s)||same(b,s))return structuredClone(l);
   if(same(b,l))return structuredClone(s);
   const resized=[b,l,s].every(object)&&[b,l,s].every(v=>Array.isArray(v.tiles))&&(!same([b.width,b.height],[l.width,l.height])||!same([b.width,b.height],[s.width,s.height]));
   if(!resized&&[b,l,s].every(object)){
    const out={};for(const k of new Set([...Object.keys(b),...Object.keys(l),...Object.keys(s)])){const value=visit(b[k],l[k],s[k],path?`${path}.${k}`:k,k);if(value!==undefined)out[k]=value}return out;
   }
   if([b,l,s].every(keyed)){
    const out=[];for(const id of new Set([...s,...l,...b].map(e=>e.id))){const value=visit(b.find(e=>e.id===id),l.find(e=>e.id===id),s.find(e=>e.id===id),`${path}[${id}]`,key);if(value!==undefined)out.push(value)}return out;
   }
   if(key==='tiles'&&[b,l,s].every(Array.isArray)&&b.length===l.length&&b.length===s.length)return b.map((_,i)=>visit(b[i],l[i],s[i],`${path}[${i}]`,'tile'));
   conflicts.push({path,base:b,local:l,server:s});return structuredClone(choices[path]==='server'?s:l);
  }
  // These fields duplicate the default map, not separate authored content.
  const clean=value=>{const out=structuredClone(value);for(const key of ['width','height','tiles','spawn'])delete out[key];return out};
  return {world:visit(clean(base),clean(local),clean(server),'','world'),conflicts};
 }
 return {merge};
})();
if(typeof module!=='undefined')module.exports=MudmakerWorldMerge;
