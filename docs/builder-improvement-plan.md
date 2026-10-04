# Builder improvement plan

Status: steps 1–4 have partial implementations; step 5 remains planned. No phase is complete. Updated after a current source review on 2026-10-02. Previously recorded verification is listed below; this planning review did not rerun the browser or runtime checks.

## Implementation evidence

- **Step 1 — implementing:** Map editors now read/write the explicitly selected map through `activeMap()` rather than copying its width, height, tiles, or spawn onto the world. Overview reads the default map explicitly. Saves serialize legacy top-level map fields from the default map even while editing a submap. Browser coverage verifies map navigation leaves the raw authored world unchanged and a resized 4×4 submap does not replace the legacy 16×16 world dimensions or default spawn. Entity modals work on cloned drafts. Undo snapshots include authored maps and entity collections, retain history across map navigation, restore the edited map/tile, and cover entity placement/edit/removal plus map creation/duplication/deletion. Resize displays blocking placements, named arrivals, spawn, connections, route/quest locations, and cropped authored text/services without silently clamping them. New submaps use terrain-derived room prose. Map navigation remembers session-local tile coordinates per map and restores the cursor/inspector when returning; undo updates that selection, and world switching clears it. Browser coverage switches between a 4×4 submap and 16×16 overworld and checks both restored cursors and the submap inspector. Viewport restoration remains open. Save failures now have a persistent retry/download panel; local edits stay in memory and canonical JSON can be downloaded. Save completion only marks the world saved when no newer edit generation exists. Browser coverage injects a 503 during a submap description edit, verifies the downloadable draft and default-map fields, restores the endpoint, retries through the visible control, and confirms server persistence. Artifact: `artifacts/gui/recovered-draft.json`. World edits now write a canonical browser-local recovery copy per tab. Reload offers Restore/Discard before further editing; restore recovers map/tile and keeps the original revision, while successful saving removes the copy. Browser coverage reloads a failed-save draft, restores and retries, then changes the server through a separate client and proves restored-draft saving is rejected without overwriting that server version. Discard keeps the server content. Artifact: `artifacts/gui/draft-recovery.png`. Storage failures have a persistent notice. Recovery covers edits applied to world data, not unapplied modal fields; Conflict review now loads the latest server snapshot without replacing the draft revision, combines independent edits from the saved base, and requires Local/Server choices for overlaps. Resized maps and delete-versus-edit are conservative structural conflicts. Applying uses the reviewed revision with If-Match; another concurrent edit still fails safely. Browser coverage resolves overlapping room prose while retaining an independent remote world-description change. Pure regressions cover separate tile edits, overlapping choices, additions, deletion/edit, and resize. Artifact: `artifacts/gui/conflict-review.png`; command: `node tests/browser/world-merge.test.cjs`. Detailed summaries of automatically merged changes and more specialized collection-order handling remain open.
- **Verified:** `node tests/browser/map-state.test.cjs` covers canonical serialization, map-local resize, inbound connections, routes/quests, and cropped prose. `.venv/bin/python scripts/verify_gui.py` verifies submap resize, saved default-map spawn, navigation-aware undo/redo, NPC placement undo/redo, cancellation, and the existing complete browser/export journey. Browser artifacts: `artifacts/gui/`.
- **Still open in step 1:** extend isolated drafts beyond entity modals; label/group all editing operations consistently; preserve per-map viewport; add field/object navigation from resize impacts; broader operation-level undo, recovery of unapplied editor-modal fields, and detailed automatic-merge change summaries. Whole-world undo snapshots are an interim implementation and need operation-level changes as more independent editors are introduced.
- **Step 3 — implementing:** market inventory now has guided offer rows, item suggestions, finite/unlimited starting stock, player-facing fixed prices, bounded stock-based pricing fields, restocking, production ingredient rows, and consumption controls. Advanced inventory JSON is disclosed separately, updates the form, and retains additional fields through form edits. Browser coverage authors both pricing modes and restocking through controls and confirms the exported configuration plus preservation of an advanced price-adjustment field. Rendered artifact: `artifacts/gui/market-editor.png`. Browser coverage also authors production/demand, rejects self-consuming production before save, and checks exported input quantities, schedules, and caps. Inline item creation, shared-engine batch previews, and the remaining editor families are still open.
- **Step 2 — implementing:** the map has a persistent palette for NPCs, markets, creatures, interactables, rooms, and descriptions. The selected-tile inspector lists all authored occupants, including stacked objects, with direct Edit/move and Duplicate actions. Placement highlights the active object type, Escape cancels, and choosing Inspect/Paint cancels placement. The browser walkthrough verifies palette placement, editing the correct occupant on a shared tile, stacked NPC/market inspection, and Escape cancellation, along with existing authoring/export checks. Rendered map inspection: `artifacts/gui/map.png`. Tile services now have draft-based resource rows, finite stock validation, item/station suggestions, and rest controls; advanced location JSON remains available. Browser coverage saves/reopens these controls, rejects zero stock, verifies exported values, and gathers the authored resource in playtest. Map-local search now finds names, IDs, prose, resources, stations, and connection labels, and selects/focuses the matching tile with its inspector. Session-only layers show/hide objects, connections, and resources/services without changing content or inspector access. Node regressions verify search map isolation and legacy placements; browser coverage checks result navigation, no results on another map, object visibility without deleting content, and service markers. The three-panel map workspace is now implemented: the left palette groups map controls, placeable objects, and search; the center holds the scrollable map with layers and zoom; the right inspector lists all selected-tile content. Worlds now open on the map. Session-local zoom/scroll are remembered per map; mobile uses explicit Map/Palette/Inspector tabs and placement returns to Map. Browser coverage verifies responsive layout, per-map viewport restoration across differently sized maps, zoom separation, keyboard placement cancellation, and mobile panel switching. Rendered artifacts: `artifacts/gui/map.png`, `artifacts/gui/mobile-map.png`. Buildability/terrain layer controls and reusable map/tile pickers for connections/routes/quest visits remain open.
- **Step 4 — implementing:** the full-width playtest inspector presents binding/paused status, goals, permitted observations, eligible actions, selected outcome/latency, and controller state after a step. Nested data is expandable; the raw response is under Advanced and can be downloaded. Browser coverage verifies preview, missing-binding fallback, and persisted pause/resume. Artifact: `artifacts/gui/decision-inspector.png`. NPC/market goals now have owner-specific metric rows with IDs, objective text, priority, numeric targets, item suggestions, and inline validation. Browser coverage authors/reopens an NPC delivery goal, rejects a missing required target, authors a market stock goal, and verifies both exported definitions. Advanced goals remain available and extra fields are retained. Provider profiles now have guided adapter/model/binding-name rows, inline validation, Advanced JSON, and protection against removing a referenced profile. The browser walkthrough verifies creation, rejects URL binding values and unsupported adapters without silently changing them, blocks removal of Mira's referenced profile, reloads the saved form, and checks the exported definition. Rendered form: `artifacts/gui/provider-editor.png`. Fuller action/reference validation during drafting, controller reset, repeatable failure scenarios, and wider owner coverage remain open. No real provider deployment was verified by this browser fixture.
- **Step 5:** planned; existing playtest remains the baseline for the integrated workflow improvements.

## Direction

Make the map the normal place to build a game. Authors should be able to select a place, see everything there, add or change content, and immediately try it. Common tasks should require neither coordinates nor JSON knowledge.

The engine and builder remain the product. Continue the [travel and player-building plan](travel-trade-and-building-plan.md) and [decision-control plan](decision-controlled-npcs-plan.md), but prioritize making existing capabilities usable before adding more controller types or economy systems. Follow the [style guide](style-guide.md) throughout.

## Findings from the current source

The map already supports terrain painting, a selection cursor, content placement, submaps, connections, and an entity minimap picker. Keep these capabilities and improve their integration.

| Current implementation | Consequence | Next change |
| --- | --- | --- |
| Map editing uses explicit selected-map helpers and one default-map serializer; selection is remembered, viewport is not | Switching maps preserves content and cursor, but not viewing position | Preserve per-map viewport and consolidate UI state handling |
| Undo uses whole-world snapshots and includes entity changes, but coverage and operation labels remain incomplete | Independent editors need a consistent undo contract | Reversible world-edit operations with meaningful labels |
| Resize now reports map-scoped placements and cross-map references, but impacts are text-only | Finding and fixing a blocking reference is cumbersome | Link each impact to its map, object, and field |
| A growing map toolbar and modal creation flow sit alongside separate content pages | Frequent context changes while populating a location | Persistent place palette and selected-location inspector |
| Inventory/pricing, construction catalogs/materials, and several content definitions require JSON | Routine authoring requires schema knowledge | Typed forms and searchable reference selectors |
| Connections and decision routes still expose coordinate fields | Location choice is inconsistent with entity placement | Reuse a common map/tile picker everywhere |
| Decision preview has structured inspection and goal rows, but routes and some references still use coordinates or JSON | Configuring and diagnosing behavior remains fragmented | Shared location/reference pickers and repeatable reset/failure scenarios |
| Save recovery retains drafts and base revisions; conflict review combines independent edits and asks for overlap choices | Core recovery is available, but unapplied modal fields and detailed automatic-merge summaries are missing | Extend draft coverage and make automatic-merge review more detailed |

These findings reflect `web/app.js`, `web/studio.js`, and the extracted map/editor modules. Each implementation slice should begin by reproducing its remaining gap in the browser; retain the existing walkthrough as regression coverage.

## Next delivery slices

Work in this order. Finish each visible authoring journey before expanding its scope; these slices complete the broader phases below rather than start a separate feature roadmap.

| Priority | Concrete delivery | Completion check |
| --- | --- | --- |
| 1 | Canonical map state, preserved per-map cursor/viewport, consistent draft Apply/Cancel, and durable save failure/retry state | Edit two maps, cancel a draft, undo a move, fail a save, recover, reload, and export with the original spawn intact |
| 2 | Map-centered palette and inspector, plus one shared map/tile picker for connections, arrivals, routes, and quest targets | Build two connected areas and populate them without typing coordinates; stacked objects and both exit endpoints remain editable |
| 3 | Guided player-building editor, completing market forms with inline item creation and runtime-backed price previews | Configure a buildable entrance, first-tile cost with included entrance, expansion costs, materials, structures, access, and storage; exercise them in playtest and export |
| 4 | Guided dialogue, quest, recipe, and decision references, retaining unfinished drafts while opening linked content | Attach a conversation/task and recipe to map content, configure controller goals/routes, then preview, step, pause, reset, and inspect fallback |
| 5 | Selected-location playtest, clickable validation, terminal presentation preview, and export readiness | Complete one browser authoring journey and an exported two-player home/trade scenario, including restart persistence |

Canonical map reads/writes, per-map selection restoration, and the core save-recovery/conflict path are now implemented with browser regressions. The next foundation ticket is **per-map viewport preservation and consistent editing operations**: retain the viewing position when navigating or opening related content, complete undo coverage with meaningful labels, and extend draft recovery to unapplied modal fields. Then restructure the map workspace around the persistent palette and inspector without losing those guarantees.

For layout work, keep the map visible while editing a selected location. Move the growing toolbar into a left palette and contextual right inspector; retain keyboard focus and provide explicit Map / Palette / Inspector tabs on narrow screens. Apply the style guide throughout: icons with labels, semantic color, distinct cursor/player markers, restrained transitions, and reduced-motion support.

Every delivery must include builder configuration, saved world data, validation, shared-runtime behavior where applicable, and export parity. An engine feature alone does not satisfy the completion check. Keep changes focused; no framework rewrite is needed for this plan.

## Intended workspace

- **Left: maps and place palette.** Search maps; expand interiors/home templates; choose terrain, NPC, market, creature, resource, station, connection, or interactable. Access reusable items, recipes, quests, and dialogues here too.
- **Center: map.** Pan/zoom, select, paint, and place with a visible preview. Toggle terrain, occupants, connections, resources, services, and buildability layers. Show a count and inspectable list when several objects share a tile.
- **Right: inspector.** Show location name/prose, biome, all occupants and services, exits, and building eligibility. Selecting an occupant opens its typed editor in the same context. Include move, duplicate, remove, and related-content actions.
- **Top: world context and status.** World/map name, undo/redo, saved/unsaved/error status, validation, playtest, and export.
- **Playtest drawer.** Start an isolated character at the selected tile, issue normal runtime commands, inspect outcomes, and return to the same editor selection.

Spatial content is placed on a tile. Nonspatial content opens an editor and can then be linked to a placed object—for example, attach a dialogue to an NPC or a recipe to a station. Content-library pages remain available for searching and bulk management.

On narrow screens, use explicit Map / Palette / Inspector views with preserved selection and drafts. Do not squeeze three desktop columns into a phone viewport.

## Implementation order

### 1. Make editing trustworthy

Deliver this before major layout changes.

- Separate authored world data from UI state: selected map/tile, active tool, viewport, open editor, and draft fields.
- Read/write maps through map-qualified helpers. Serialize legacy default-map fields only from the actual default map; changing the viewed map must not change runtime spawn or export identity.
- Use isolated editor drafts. Cancel discards changes; Apply validates and commits one reversible operation. Preserve unrecognized advanced fields when editing known fields.
- Cover terrain strokes, placement, move, duplicate, removal, room/service edits, and connections with undo/redo. Group a brush stroke into one operation; retain valid history across map navigation and label the next undo action.
- Scope resize/delete impact analysis to the affected map and its inbound/outbound references. Include entities, spawn, named entries, connections, visit targets, decision routes, and home-template references. Show what blocks the operation and jump to each affected object; never silently clamp or discard authored content.
- Show persistent saving/error/conflict state. Keep unsaved drafts recoverable, provide retry, and preserve both the local draft and latest server revision during conflicts. Do not overwrite a newer revision silently.
- Extract state, map operations, and serialization into focused modules as these paths change. Keep a reproducible browser entry point; avoid an unrelated framework rewrite.

Acceptance: edit a submap, switch maps, undo/redo, reload, and export without changing the default map. Cancel an editor without mutation. Reproduce save failure and revision conflict and recover the draft. A resize reports only relevant placements plus genuine cross-map references.

### 2. Make the map the complete build workspace

- Introduce the palette/inspector layout above; make the map the default destination when opening a world.
- Offer visible Select, Paint, Place, and Connect modes. Show the active mode, placement footprint/target, valid/blocked state, and Escape-to-cancel hint.
- Let authors add and edit every tile service from the inspector: resources, rest, crafting stations, entrances, and buildability overrides alongside ordinary entities.
- Add map-local search and content layers. Selecting a search result centers the map and opens the corresponding inspector.
- Reuse one keyboard-accessible location picker for entities, arrival points, connections, decision routes, quest visit targets, and playtest start. Put coordinates under Advanced.
- Draw source/destination context for connections, offer explicit one-way/two-way creation, and let authors jump to either endpoint. Editing/removing a linked pair must show whether both ends will change.
- Retain cursor, viewport, and draft context when opening related content and returning to the map.

Acceptance: build two connected areas, place an NPC and market, add gatherables and a station, move an occupant, and edit both ends of a connection without leaving the map workspace or typing JSON/coordinates. Verify stacked occupants, keyboard use, and mobile navigation.

### 3. Replace routine JSON with guided content editors

Ship complete editor families in this order:

1. **Markets and items:** searchable item rows; finite/unlimited inventory where supported; player-facing purchase/sale labels; fixed or stock-based price fields; live stock-level and batch-total preview; restock/production/consumption forms; delivery contracts. Item creation should be available inline and preserve the unfinished market draft.
2. **Player building:** template map preview; allowed terrain; base and per-terrain costs; material rows; limits; access/storage settings; structure cards with footprints, costs, removal refunds, and service configuration. Preview the first-tile price with its included entrance and show later expansion costs separately.
3. **NPCs, dialogue, quests, and crafting:** dialogue nodes/choices, task/objective rows, rewards, recipe ingredients/output, and reference pickers. Open linked content and return without losing edits.

For every family, show validation next to the offending field, document units/defaults, and distinguish required from optional settings. Advanced JSON remains an explicit disclosure with schema help and validation; switching modes must preserve values. Unsupported engine capabilities must be labelled as planned rather than presented as working controls.

Acceptance: configure an example of each family using visible controls, reload it, validate, playtest its behavior, and inspect identical configuration in the export. Test preserving advanced fields through a form edit.

### 4. Make decision control understandable

- Use the same Behavior section on each supported owner, showing authored behavior or decision control, provider selection, goals, permissions, limits, and fallback.
- Replace raw goal/provider configuration with typed rows and owner-specific options. Keep private credential values outside the world and export.
- Select routes on maps; select markets, recipes, resources, and delivery items from authored content. Explain why a target is unavailable.
- Present the inspector as: current state/goals → permitted observations → eligible actions → selected action → result. Show cooldown, paused state, fallback reason, and recent decisions in readable rows.
- Provide preview, step, pause/resume, and isolated test-state reset. Add repeatable provider-unavailable/delayed-response test scenarios as supported by the shared preview backend. Keep raw trace JSON downloadable under Advanced.
- Distinguish configuration validity, missing deployment binding, fixture success, and verified real-provider behavior. Do not imply that a local fixture proves a live Jev/Laya deployment.

Acceptance: configure an NPC route and market stock goal, preview candidates, step, simulate provider failure, pause/resume, reload state, and export through visible controls. Ordinary player output stays free of provider diagnostics.

### 5. Close the build–playtest–export loop

- Open playtest at the selected map/tile with an explicit isolated test character. Provide reset scopes for character, economy, homes, and controllers and explain which preview state will be cleared.
- Group validation results by map/object/field; clicking a result opens the exact editor. Separate errors from advisory warnings.
- Add authored-template previews and two-character home scenarios: claim, expand, build, invite, store, barter, and denied access.
- Preview terminal output at narrow/wide widths with rich icons/ASCII and color settings. Use the shared runtime/renderer so preview cannot silently diverge from exported behavior.
- Show export readiness and deployment requirements, including player-key configuration and missing decision bindings. Verify extracted file permissions and startup as the unprivileged container user.

Acceptance: one setting-neutral fixture covers map authoring, commerce, home building/access, and decision control through the browser and two exported SSH clients, including restart persistence. Keep real-provider verification as a separate named gate when its deployment is available.

## Presentation and interaction requirements

Apply these in every phase, not as a final cosmetic pass:

- Consistent application icons plus text; authored emoji for game objects; readable labels and semantic color.
- Selection, hover, keyboard focus, placement preview, and runtime player position are visually distinct.
- Keyboard navigation and focus restoration for pickers, panels, and dialogs; accessible names for icon buttons.
- Short 120–200 ms transitions for panel/placement changes; respect reduced motion. No continuous decorative movement.
- Persistent actionable errors and clear empty states. Include examples where a rule is unfamiliar, such as marginal stock pricing or first-tile acquisition.
- Inline help for ordinary controls, with links to schema documentation for advanced content.

## First implementation milestone

Combine phase 1 with the minimal phase 2 shell: reliable map-qualified state, draft/apply/cancel semantics, placement undo, persistent selection, a place palette, and a selected-tile inspector. Prove this journey first:

1. Open a world directly on its map.
2. Create a submap and connect it to the starting area using the location picker.
3. Place an NPC and market on a chosen tile; inspect both from the tile panel.
4. Move the NPC, undo the move, switch maps and return.
5. Cancel an unfinished edit, reload the world, and confirm the saved state.
6. Playtest the selected place and export with the original default-map spawn intact.

Then implement the market editor and player-building editor before expanding runtime mechanics. This ordering directly improves the features already available and establishes reusable forms/state handling for decision-control work.

## Evidence and completion tracking

Track each phase as planned / implementing / verified, with test commands and screenshot/artifact paths recorded when it lands. Existing passing scripts establish a baseline, not proof of the proposed workflows. Add regression checks for actual editing failures, browser checks for complete authoring journeys, and shared-runtime/export checks for semantic parity. Update the earlier feature plans as acceptance evidence accumulates; do not equate an implemented engine command with a finished builder feature.
