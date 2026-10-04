# Game engine next steps

Planning scaffold, 2026-10-03. Based on the current checkout and existing feature plans. Progress notes record earlier implementation and verification; the next batch below is proposed work, with no new verification claimed. This is the execution order for the remaining slices in the [builder plan](builder-improvement-plan.md), [travel/building plan](travel-trade-and-building-plan.md), and [decision plan](decision-controlled-npcs-plan.md).

## Target milestone

An author can build two connected areas, populate them, configure a player home and market, attach a quest, play through the result, and export a persistent multiplayer game using guided controls.

The current foundation includes shared browser/export gameplay, submaps and travel, combat, crafting, markets, player homes, bounded NPC/market decisions, draft recovery, and a responsive map workspace. Some older planning passages still describe viewport preservation and the workspace as future work; their implementation evidence and the current modules show those foundations are present. Start with the remaining gaps below.

## Next implementation batch

Work through these tickets in order. Each is a complete builder → saved world → playtest/export slice; existing progress farther below is context rather than a request to rebuild completed features.

### N1. Recover unfinished map and settings edits

Extend `web/draft-recovery.js` beyond entity and Storysmith editors. Give map settings, tile connections, and player-building settings serializable draft contexts, including the parent form when opening a location picker. Restore fields for review before Apply, and validate recovered references against the current world. Use stable content IDs where possible so a reordered entity list cannot redirect a recovered edit.

Acceptance: edit a named arrival and a building cost, reload each unfinished form, restore it, and save once. Cancel/discard leaves saved content unchanged. A deleted map or changed world revision produces a recoverable explanation instead of applying the draft to another object. Keep per-tab and per-world isolation.

Touchpoints: `web/draft-recovery.js`, `web/app.js`, `web/building-editor.js`, `web/location-picker.js`, `scripts/verify_gui.py`.

### N2. Review references before resizing or deleting a map

Use `MudmakerMapState.resizeImpact` as the starting point for a common impact report. Include entities, spawn, named arrivals, paired exits, quest visit targets, NPC routes, and home templates. Every affected reference needs a readable label and a navigation action. Let the author cancel or explicitly repair references before applying the structural edit; never silently clamp or delete them. Treat a successful resize and its reference repairs as one undo operation.

Acceptance: shrink a map containing each reference type, follow an impact to its editor, repair it, resize, and undo the entire change. Deleting a referenced map remains blocked until the references are resolved. Validation and exported travel agree with the repaired destinations.

Touchpoints: `web/map-state.js`, `web/map-workspace.js`, `web/app.js`, `app/templates/content.py`, `tests/browser/`, `tests/test_engine.py`.

### N3. Explain concurrent edits before saving

Extend `web/world-merge.js` to report independent changes adopted from each side alongside overlapping conflicts. Present those changes using content names and field labels in `web/conflict-review.js`. Preserve structural conflicts as explicit choices and handle a further server revision arriving during review through the existing save precondition.

Acceptance: two tabs edit different NPCs and see both changes in the review; two edits to the same field require a choice. Concurrent resize versus tile edits never silently combine. Cancel preserves the local recovery draft, and a second server change cannot be overwritten by a stale resolution.

Touchpoints: `web/world-merge.js`, `web/conflict-review.js`, `app/main.py`, `tests/browser/`, `tests/test_api.py`.

### N4. Collect actionable validation issues across content families

Extend the existing structured issue collector to dialogue, recipes, exits, building settings, and decision references. Reuse the compiler's rules so the issue panel and export gate agree. Report independent errors together; skip dependent checks when a parent value has the wrong shape. Retain content paths, entity/map identity, field navigation, and checked revision.

Acceptance: one world with unrelated broken dialogue, recipe, exit, and decision references reports all four families in a single check. Each issue opens its editor or tile; fixing one leaves the others visible. Invalid input neither saves nor exports, and read-only validation leaves the draft and persisted world untouched.

Touchpoints: `app/templates/content.py`, `app/main.py`, `web/app.js`, `tests/test_api.py`, `scripts/verify_gui.py`.

### N5. Define a safe deployed-world upgrade contract

Write `docs/runtime-world-upgrades.md` before offering runtime world replacement. Inventory persisted player locations, inventory, quest progress, homes, permissions, storage, and controller state against authored IDs. Define compatible changes, changes requiring an explicit migration, and unsupported changes. Design a dry-run compatibility report tied to source and target revisions, with backup and rollback requirements. Keep upgrades operator initiated until that contract is implemented and verified.

Acceptance: classify renaming display text, removing an occupied map, removing a stored item, changing an active quest, and changing a home template. Each case has an explicit preserve/migrate/reject result. A subsequent implementation must refuse incompatible upgrades before mutating saves and prove recovery after interruption in an isolated exported runtime.

Touchpoints: `app/templates/content.py`, `app/templates/game_engine.py`, `app/templates/runtime_management.py`, `app/templates/game_README.md`, `scripts/verify_export.py`.

### N6. Verify decision scheduling under multiplayer load

Finish failure and responsiveness scenarios before expanding decision features. Author controller budgets, intervals, fallback rules, and provider bindings through the builder; carry them into exports. Exercise delayed responses, timeouts, malformed answers, missing bindings, stale results, pause/resume, and reset. Record queue time, inference time, fallback reason, and command latency with two SSH clients trading and moving while controllers run.

Acceptance: ineligible or stale results cannot apply effects; transactions preserve balances and stock under concurrent trades; restart preserves intended controller state. Publish measured command latency and choose explicit pass thresholds before running the experiment. Report fixture results separately from one actual provider integration; live provider verification requires an available private binding.

Touchpoints: `app/templates/decision_runtime.py`, `app/templates/game_engine.py`, `web/decision-inspector.js`, `web/decision-goals.js`, `web/provider-editor.js`, `tests/test_decision_runtime.py`, `scripts/verify_exported_decision_runtime.py`.

Start with N1. N2–N4 close authoring reliability gaps; N5 establishes the persistence contract before upgrade code; N6 supplies evidence for scheduling defaults. These tickets require no new infrastructure or dependencies at the planning stage.

## Ordered backlog

### 1. Share location selection across the builder

Priority: first. Dependencies: existing map state and entity location picker.

- Extract a reusable picker from `web/studio.js` into `web/location-picker.js`.
- Accept a draft map/location, eligible-target rules, and an Apply callback; opening, navigating, or cancelling must not mutate authored content.
- Use it for connection endpoints, named arrivals, NPC routes, quest visit targets, and eventually playtest start. Keep coordinates available under Advanced.
- Provide map selection, passability feedback, keyboard navigation, and a readable destination summary. Show both endpoints and explicit one-way/two-way choices when authoring connections.
- Preserve map cursor, viewport, and the parent editor's unfinished fields when returning.

Current implementation note: `editTileConnections()` in `web/app.js` edits the selected source tile, destination map, and named arrival preset. `editMapSettings()` now uses the same picker for spawn and named entry tiles, while retaining direct coordinate fields. Entity placement, connection arrivals, spawn, and named entries use the shared picker; keep extending these paths rather than introducing a parallel destination schema.

Completed slices: extracted the shared picker with draft-only `{map_id, x, y}` selection; wired entity locations, connection arrivals, map spawn, named entry points, and NPC decision routes; retained coordinate fields and named arrival presets; covered Save/Cancel behavior in the browser verifier.

Completed slice boundaries:

1. Reciprocal-link identity is implemented. Paired exits carry an optional pair ID and return-exit label; one-way exits remain unchanged. Validation checks both records point back to each other. The builder and creator MCP author paired records, export retains their metadata, and GUI coverage exercises pair creation/removal, undo/redo, and duplication with internal and external pairs. Keep these regressions as the pair format evolves.
2. Persist named arrival identity. Implemented: exits can retain an optional entry-point ID while legacy coordinate-only exits remain valid. Validation, player travel, NPC route reachability, and resize-impact analysis resolve the current named point. Moving an entry updates saved arrival coordinates and relocates reciprocal return exits as part of the map-settings save.
3. Reuse the picker for quest visit targets. Implemented: the guided visit-objective editor selects a map tile and stores the existing `{map_id, x, y}` target object. Legacy room-ID targets remain readable, coordinate editing stays under Advanced, and Cancel leaves authored quest data unchanged.

Do not imply that a named arrival and an explicit coordinate are interchangeable during editing: retain the preset ID when the author picks a named point, and switch to coordinate mode only after a tile is chosen. When the named point later moves, validation should continue to resolve the preset using the current entry point, matching the runtime's existing behavior.

The location picker is implemented in `web/location-picker.js`. Entity placement, connection arrivals, map spawn, named entries, and NPC decision destinations share its map grid, keyboard navigation, terrain description, and passability status. Connection drafts remain modal-local until Save; the form retains a named preset until an explicit tile pick. Saved named exits carry the entry ID and follow its current coordinates in validation, runtime travel, NPC route reachability, and resize analysis. The browser verifier exercises destination selection, Cancel, spawn, and named-entry authoring. Reciprocal exits carry a paired identity and validation plus GUI lifecycle coverage passes.

Done when: an author creates two connected areas, uses a named arrival and a picked tile as separate destinations, creates and removes a reciprocal pair, and edits both endpoints without typing coordinates. Save/reopen and export preserve the selected destination semantics. Cancel leaves the source and destination unchanged; one undo removes the complete reciprocal pair.

Likely touchpoints: `web/studio.js`, `web/map-state.js`, `web/map-workspace.js`, `web/app.js`, `app/templates/content.py`.

### 2. Finish guided player-building configuration

Priority: second. Depends on ticket 1 for template/entrance selection.

- Extract building settings into `web/building-editor.js` with guided currency/material rows and searchable item references.
- Show effective terrain defaults and per-tile overrides; add a buildability layer and inspector controls for inherit/allow/block.
- Cover home templates, first-tile and expansion costs, allowed terrain, structure recipes, storage, limits, access defaults, and removal refunds using the existing content contract.
- Preview costs using the shared engine's rules through an isolated preview API; avoid a separate browser pricing formula.

Progress: the complete player-building settings panel now lives in `web/building-editor.js`. Template selection, costs, paintable terrain, storage, tile limits, access, room editing, service restrictions, and refunds remain on the existing world contract. Tile, paint, terrain-override, and structure materials use searchable authored-item rows; terrain overrides and structure recipes have guided forms that retain unknown advanced fields. Effective terrain defaults update as base costs and materials are edited. The map buildability layer displays effective allow/protect state, and the tile inspector supports inherit/allow/block. The isolated preview compiles draft rules and calls `GameEngine.building_cost` for each terrain without saving. The browser workflow covers guided override and structure authoring; API and gameplay checks cover engine pricing and construction rules. Exported Compose verification now covers claiming with the entrance included, paid expansion, storage, visitor and collaborator permissions, visitor denial for restricted actions, and persisted balance, access, and storage after container restart. The SSH verifier recognizes the runtime's username-qualified prompt. Remaining: extend the exported flow to additional templates and build policies, including preview-to-charge parity across terrain overrides and structures.
- Keep the existing rule: the first purchased tile includes its entrance without an extra entrance fee.
- Preserve unknown advanced fields and apply a complete settings change as one undoable edit.

Done when: configure a home through forms, claim it, expand it, build storage, invite a visitor/collaborator, reject an unauthorized action, and retain purchases and contents after an exported runtime restart. Previewed and charged costs match.

Likely touchpoints: `web/app.js`, `web/map-workspace.js`, `app/main.py`, `app/templates/content.py`, `app/templates/game_engine.py`.

### 3. Finish a quest/dialogue/crafting authoring journey

Priority: third. Depends on ticket 1.

- Add focused `web/quest-editor.js`, `web/dialogue-editor.js`, and `web/recipe-editor.js` modules.
- Provide typed objective/effect rows and searchable NPC, item, quest, station, and dialogue references.
- Support inline creation of linked content while preserving the parent draft; begin with items shared by recipe and market editors.
- Validate references and counts before Apply, retain Advanced JSON, and preserve fields the form does not edit.
- Finish market batch/stock previews using engine quotes, including finite stock and production ingredient requirements.

Progress: quests now use a focused typed objective editor for discovery, visits, NPC talk, item collection, crafting, delivery, interaction, and defeating creatures. It uses authored-reference pickers, keeps the full quest scaffold as Advanced JSON, and preserves unedited objective fields. Visit destinations continue to use the shared map-tile picker. Quest compilation checks collect/deliver and reward item references against authored items and vendor stock, and checks reward item counts before export. Dialogue now has a standalone builder view with editable nodes, player choices, destinations, and typed item/currency/heal/flag/quest effects. NPC forms can link an authored dialogue. Recipe editing uses guided ingredient/output/count/station controls; authors can create draft-local items inline, which apply with the recipe as one save/undo action and disappear if the editor is canceled. Recipe validation checks referenced items and positive counts against authored definitions, vendor stock, gathered resources, loot, and starting inventory. Dialogue item effects also validate their item references. Market editing now requests up-to-date batch quotes from the shared engine resolver and simulates finite-stock production over three cycles of the slowest configured process; it shows stock, remaining ingredients, and why production stopped. The preview uses a temporary database and does not save market changes. Browser coverage creates and reopens a two-node conversation, links it to an NPC, and checks preserved advanced choice requirements; it also verifies inline item cancellation, recipe save, recipe-created item availability in the market editor, an engine-backed batch preview, and collect plus visit objectives with preservation of an unknown objective field. Focused compiler/API regressions cover unknown quest, recipe, and dialogue-effect items, nonpositive counts, batch-price parity, production ingredient consumption, and read-only preview behavior. The browser playtest now completes the survey and dialogue-gated supply quests by gathering, crafting, delivering, and receiving the reward. The exported Compose/SSH verifier independently exercises the same dialogue-started supply quest and reward. Complete: the dialogue editor now saves text fields on input without rerendering the choice controls on blur, so Add choice and Add effect remain attached through the click sequence. The browser verifier authors the gated branch with structured choice and effect controls, reopens the quest and dialogue to check links and advanced fields, and completes the supply quest in playtest. The exported Compose/SSH workflow independently starts the gated quest through dialogue and verifies gathering, crafting, delivery, and reward.

Done when: author an NPC conversation that starts a quest, gather ingredients, craft at a station, deliver the result, and receive the authored reward in playtest and an export. Reopening editors retains all links and advanced fields.

Likely touchpoints: `web/studio.js`, `web/market-editor.js`, `app/templates/content.py`, `app/templates/game_engine.py`.

### 4. Connect validation, playtesting, and export readiness

Priority: fourth. Depends on tickets 1–3.

- Return structured validation issues with severity, content path, entity/map identity, and an editor navigation target. Retain the existing export validation gate.
- Replace toast-only build results with a persistent issue panel that opens the offending field or tile.
- Add isolated playtest start/reset at the selected location, preserving authoring selection and drafts. Reset must affect only the chosen preview session.
- Show which saved world revision is being tested/exported and flush pending saves before either operation.
- Provide an export readiness checklist for content, player public keys, runtime settings, optional provider bindings, and persistent storage instructions.

Progress: the build validator accepts a read-only draft snapshot and returns structured issues with severity, content path, entity identity, and editor navigation. Quest errors identify the objective field; map-tile errors include coordinates, and the issue panel opens the corresponding field or tile. The Playtest build check flushes pending saves first; if a broken draft cannot be saved, it validates that in-memory snapshot without writing it. Issues stay in a global panel across view changes and show the checked revision hash. The browser verifier follows a malformed quest item to the objective control, fixes it, and confirms the issue clears; it also follows a malformed tile to its map cursor. API coverage confirms invalid drafts are not saved. A supplemental validation pass now reports independent unknown item references across quest objectives and unknown biomes across map tiles; API and browser checks verify multiple issues appear together. Playtest includes the shared map/tile picker and an explicit fresh-session reset. Preview databases are isolated by world and player name; reset removes only the chosen player's preview state, and blocked terrain is rejected. API and browser verification cover selected starts, reset, and a second player's retained state. Build checks display the checked revision; exports return the saved world revision in a response header and the UI reports it after download. The Playtest view now includes an export readiness checklist for content, SSH public keys, runtime ports, optional provider environment bindings, and persistent volume retention. API and GUI verification cover revision identity and checklist rendering. The exported integration verifier now saves the authored scenario to an isolated workbench database, validates its revision, asserts the ZIP carries that exact saved source, and exercises both quest completion and home claim/expansion/access/storage in that runtime through SSH. The run passed, including a container restart; the verifier reports the saved revision digest from each run. Remaining: extend multi-issue collection across the other validator families.

Done when: introduce a broken reference, navigate from the issue to its editor, fix it, start a fresh character at the selected submap tile, complete the quest/home scenario, and export the same saved revision. The builder issue flow, selected-location reset, and same-saved-revision exported quest/home journey now have API, browser, and Docker/SSH evidence; broader validation issue aggregation remains open.

Likely touchpoints: `app/templates/content.py`, `app/main.py`, `web/app.js`, `web/map-workspace.js`, `app/templates/game_README.md`.

### 5. Make edits and world upgrades dependable

Priority: fifth; advance a specific migration earlier if a preceding ticket changes persisted data.

- Extend recovery to unapplied modal drafts, including parent/child editor context.
- Label and group undo operations consistently; one terrain stroke or connection-pair edit should be one action. Replace whole-world snapshots incrementally where their cost or semantics become a problem.
- Link resize/delete impacts to affected references and add a readable summary of automatically merged conflict changes.
- Audit normalization in `app/templates/content.py`, which currently assigns `schema_version = 1`; define supported versions and explicit migrations before introducing a new version.
- Reject unsupported future versions clearly, retain the original import, and verify migration is idempotent and leaves the default-map spawn intact.
- Define how authored world updates interact with existing player saves; document unsupported changes before offering runtime world replacement.

Progress: runtime compilation applies a copy-on-write v0→v1 migration for legacy flat maps, carries the selected default-map spawn through the migration, and produces idempotent canonical output. Unsupported, negative, and non-integer schema versions are rejected; save validation rejects future versions before replacing stored world data. The world switcher can import JSON as a separate world copy; it keeps the uploaded file on the user's device and stores the source schema as supplied, while validation/export compile a migrated copy. Unit, API, and browser coverage check legacy and multi-map spawn preservation, idempotence, duplicate import IDs, future-version rejection, retaining the source after rejection, and launching the imported world. Unapplied entity-editor drafts are saved per browser tab and world, then reopened for review after reload. Nested Storysmith drafts retain the parent entity and both editors' field values; restoring never applies them automatically. GUI coverage reloads an unfinished new entity, restores it, then repeats through the nested workbench and applies the reviewed result. Remaining: extend recovery to map and settings modals; connect resize/delete impact reports to their references; summarize automatically merged conflict changes; and define how deployed player saves behave during future world upgrades.

Done when: recover an unfinished nested editor after reload, resolve concurrent edits, and import a legacy fixture through a repeatable migration without losing map references or advanced fields. Import and nested-editor recovery now have API and browser evidence; concurrent-edit summaries and reference-aware resize/delete handling remain.

### 6. Harden existing decision controllers

Priority: sixth. Depends on the guided references and preview workflow.

- Add isolated controller reset and repeatable timeout, malformed-answer, missing-binding, and stale-result scenarios.
- Expose queue/latency/fallback information and validate authored goals/actions/references before saving.
- Measure command responsiveness with multiple enabled controllers and delayed inference; choose scheduling defaults from that evidence.
- Verify one actual supported provider deployment before claiming Jev/Laya compatibility. Keep credentials in private runtime bindings.
- Keep decisions restricted to eligible engine-generated candidates and transactionally checked effects.

Done when: NPC and market decisions remain bounded through provider failure, pause/resume, concurrent trades, and restart, while two SSH clients remain responsive. Fixture evidence and actual-provider evidence are reported separately.

Likely touchpoints: `web/decision-inspector.js`, `web/decision-goals.js`, `web/provider-editor.js`, `app/templates/decision_runtime.py`, `app/templates/game_engine.py`, `app/templates/runtime_management.py`.

## Optional player interfaces

The [player access plan](player-access-plan.md) adds an optional browser journey alongside primary SSH access. Start by extracting shared sessions and identity binding, then add the browser transcript and guided controls. Carry access configuration through the builder, playtest, and standalone Compose exports; this is a separate feature track from the reliability batch above.

## Delivery contract

For each ticket: reproduce the gap, implement the smallest complete authoring journey, cover its regression, exercise the visible browser path, and check saved data plus exported behavior. Runtime changes require builder controls, portable configuration, validation, and the same behavior in playtest and export. Preserve existing uncommitted work.

Use the existing verification entry points documented in the README: the Python unit suite, `tests/browser/` regressions, `scripts.verify_gui`, `scripts.verify_submap_creation`, `scripts.verify_terrain_edit`, `scripts.verify_export`, `scripts.verify_mcp`, and `scripts/verify_exported_decision_runtime.py`. Run the relevant subset per change; Docker/SSH checks are needed for multiplayer and restart claims.

The shared picker now covers entity locations, connection arrivals, spawn, named entries, NPC routes, and quest visit objectives with browser coverage. Reciprocal-link identity and persisted named-arrival identity are implemented through editor, validator, runtime, and export. Quest visit targets keep their existing serialized contract, with room IDs still supported and coordinates available under Advanced. This removes repeated coordinate editing and becomes the foundation for home templates and selected-location playtesting.
