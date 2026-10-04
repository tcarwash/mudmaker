(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const TYPES = [
    ['discover', 'Discover locations'], ['visit', 'Visit a place'], ['talk', 'Talk to an NPC'],
    ['collect', 'Collect an item'], ['craft', 'Craft a recipe'], ['deliver', 'Deliver an item'],
    ['interact', 'Use an interaction'], ['defeat', 'Defeat a creature'],
  ];
  const list = (world, key, kind) => [
    ...(world[key] || []).filter(value => !kind || value.entity_type === kind),
    ...(world.entities || []).filter(value => value.entity_type === kind),
  ];
  const choices = (world, type) => {
    if (type === 'talk') return list(world, 'npcs').map(value => [value.id, value.name || value.id]);
    if (type === 'collect' || type === 'deliver') {
      const items = [...list(world, 'items').map(value => value.name), ...(world.economy?.vendors || []).flatMap(vendor => (vendor.stock || []).map(stock => stock.item))];
      return [...new Set(items.filter(Boolean).map(value => [value, value]))];
    }
    if (type === 'craft') return list(world, 'crafting').map(value => [value.id, value.name || value.id]);
    if (type === 'interact') return list(world, 'interactables').map(value => [value.id, value.name || value.id]);
    if (type === 'defeat') return list(world, 'creatures').map(value => [value.id, value.name || value.id]);
    return [];
  };
  const select = (name, value, options, label) => `<label class="form-field"><span>${label}</span><select class="select" data-objective-field="${name}"><option value="">Choose…</option>${options.map(([id, text]) => `<option value="${esc(id)}" ${id === value ? 'selected' : ''}>${esc(text)}</option>`).join('')}${value && !options.some(([id]) => id === value) ? `<option value="${esc(value)}" selected>Missing: ${esc(value)}</option>` : ''}</select></label>`;

  function markup(objectives = []) {
    return `<section class="panel" style="margin:12px 0;padding:14px" id="quest-objectives-editor"><div class="panel-heading"><h3>🧭 Quest objectives</h3><button type="button" class="button button-secondary" data-objective-add>Add objective</button></div><p class="small-copy">Choose an objective type and link it to authored content. Visit locations are selected on the map below.</p><div data-objective-list>${objectives.map((objective, index) => `<div class="panel quest-objective-row" data-objective-index="${index}" style="margin:10px 0;padding:12px"><div class="panel-heading"><b>Objective ${index + 1}</b><button type="button" class="entity-action" data-objective-remove="${index}" aria-label="Remove objective">×</button></div></div>`).join('') || '<div class="empty-note">No objectives yet.</div>'}</div></section>`;
  }

  function mount({ root, world, getValue, setValue }) {
    const parse = () => {
      const draft = getValue();
      if (!Array.isArray(draft.objectives)) draft.objectives = [];
      return draft;
    };
    const commit = draft => setValue(draft);
    const render = () => {
      const draft = parse(), objectives = draft.objectives;
      root.querySelector('[data-objective-list]').innerHTML = objectives.length ? objectives.map((objective, index) => {
        const kind = objective.type, typeOptions = [...TYPES];
        if (kind && !TYPES.some(([id]) => id === kind)) typeOptions.push([kind, `Unsupported: ${kind}`]);
        const typeSelect = `<label class="form-field"><span>OBJECTIVE TYPE</span><select class="select" data-objective-field="type">${typeOptions.map(([id, text]) => `<option value="${esc(id)}" ${kind === id ? 'selected' : ''}>${esc(text)}</option>`).join('')}</select></label>`;
        const refs = choices(world, kind);
        let fields = '';
        if (kind === 'talk') fields = select('target', objective.target, refs, 'NPC');
        else if (kind === 'collect' || kind === 'craft' || kind === 'interact' || kind === 'defeat') fields = select('target', objective.target, refs, kind === 'collect' ? 'ITEM' : kind === 'craft' ? 'RECIPE' : kind === 'interact' ? 'INTERACTION' : 'CREATURE');
        else if (kind === 'deliver') fields = `${select('target', objective.target, refs, 'ITEM')}${select('npc', objective.npc, choices(world, 'talk'), 'DELIVER TO NPC')}`;
        else if (kind === 'visit') fields = '<div class="small-copy">Choose the destination in the map picker below. Visit objectives always require one place.</div>';
        else if (kind === 'discover') fields = '<div class="small-copy">Count distinct map locations discovered.</div>';
        const count = kind === 'visit' ? 1 : (objective.count ?? 1);
        return `<div class="panel quest-objective-row" data-objective-index="${index}" style="margin:10px 0;padding:12px"><div class="panel-heading"><b>Objective ${index + 1}</b><button type="button" class="entity-action" data-objective-remove="${index}" aria-label="Remove objective">×</button></div><div class="entity-edit">${typeSelect}${fields}<label class="form-field"><span>COUNT</span><input class="input" data-objective-field="count" type="number" min="1" step="1" value="${esc(count)}" ${kind === 'visit' ? 'disabled' : ''}></label></div></div>`;
      }).join('') : '<div class="empty-note">No objectives yet.</div>';
    };
    root.addEventListener('click', event => {
      if (event.target.closest('[data-objective-add]')) {
        const draft = parse(); draft.objectives.push({ type: 'discover', count: 1 }); commit(draft); render();
      }
      const remove = event.target.closest('[data-objective-remove]');
      if (remove) { const draft = parse(); draft.objectives.splice(Number(remove.dataset.objectiveRemove), 1); commit(draft); render(); }
    });
    root.addEventListener('change', event => {
      const field = event.target.closest('[data-objective-field]');
      if (!field) return;
      const row = field.closest('[data-objective-index]'), index = Number(row.dataset.objectiveIndex), draft = parse(), objective = draft.objectives[index];
      if (field.dataset.objectiveField === 'type') {
        const next = field.value;
        delete objective.target; delete objective.npc;
        objective.type = next;
        if (next === 'visit') { objective.target = { map_id: world.default_map_id, x: world.spawn?.x ?? 0, y: world.spawn?.y ?? 0 }; objective.count = 1; }
        else if (next === 'discover') delete objective.count;
        else objective.count ??= 1;
      } else if (field.dataset.objectiveField === 'count') {
        const value = Number(field.value);
        if (!Number.isInteger(value) || value < 1) { field.setCustomValidity('Enter a positive whole number.'); field.reportValidity(); return; }
        field.setCustomValidity(''); objective.count = value;
      } else objective[field.dataset.objectiveField] = field.value;
      commit(draft);
      if (field.dataset.objectiveField === 'type') render();
    });
    render();
  }
  window.MudmakerQuestEditor = { markup, mount };
})();
