# Submaps, travel, markets, and player building

Status: implementation in progress. Multi-map authoring/runtime foundations, a central map-based content builder, private player areas, stock-sensitive pricing, authored restocking, decision-controlled markets, and player-to-player barter (including inside homes) are implemented in the shared engine and builder. Remaining work is listed below.

Follow the [style guide](style-guide.md) for builder controls and terminal presentation, including maps, market tables, travel feedback, and construction previews.

## Goal

Extend Mudmaker's reusable game engine and authoring workbench so an author can create connected maps, place configurable markets, define inventory and pricing rules, and optionally allow players to own and develop areas. Federation-style travel and trade are inspiration; no particular setting, commodity list, progression ladder, or sample game should dictate engine behavior.

The builder is the source of truth. Every mechanic must have saved authoring configuration, validation, browser playtesting, and standalone export support. Authors must be able to build a conventional adventure with these systems disabled, a trading game without player building, or a game combining all three.

## Current foundation

The content contract in `app/templates/content.py` currently describes one map using top-level width, height, tiles, and spawn. Locations use `{x, y}`. Vendors already support per-item purchase price, sale price, and finite shared quantity. `app/templates/game_engine.py` is shared by browser playtesting and exported SSH games, with SQLite persistence for players, shared stock, ground items, and combat. The workbench already provides map painting, entity editing, validation, and export.

Extend these existing systems rather than introducing a separate trading runtime or separate player-building engine.

## Implementation progress

- **Started — terrain buildability authoring:** built-in and custom terrain have a `buildable` default, and map tiles can inherit or override it as buildable/protected. The map builder exposes both controls and content validation checks their types.
- **Started — author-created submaps and cross-map runtime locations:** worlds can contain named maps while keeping the original top-level default-map fields. The builder can create a submap from a selected tile and add reciprocal exits. Player positions, discovery, notes, nearby visibility, resource depletion, and ground items now carry map identity; legacy player and ground-item data are migrated to the default map.
- **Started — player-owned areas:** the builder exposes world-level enablement, a map template, base currency and material costs per tile, per-biome currency/material overrides, storage capacity, area and tile limits, and whether service tiles may host entrances. In the runtime, `build claim`, `build expand <direction>`, `build preview [direction]`, and `build status` create a persistent private area, restrict it to its owner, show the applicable acquisition cost, charge claims and expansions, and keep expansion adjacent to purchased tiles. Claiming from eligible terrain creates a return link; the entrance is included with the first tile at no extra fee. Owners can inspect, deposit, and withdraw items from a capacity-bounded chest persisted with their home. Visitor access and painting purchased tiles with author-permitted terrain are implemented; richer in-area map editing, production, and instance workflows remain open.
- **Started — private home storage and access:** world building settings define item capacity per area and whether owners can grant visitors or collaborators. `home access grant/list/revoke` persists per-home grants. Visitors can enter and traverse purchased tiles; collaborators can also expand and use storage. Storage transfers update player state and home data in the same transaction. Access is private by default.
- **Started — bounded home structures:** the builder's Player Building rules include a validated structure catalog, per-home structure limit, currency/material costs, removal refund percentage, bounded rectangular footprints, and decoration/rest/crafting-station services. Owners and collaborators can list catalog entries, preview cost, affordability, and footprint eligibility with `build preview place <id>`, place an authored structure with `build place <id>`, and remove one from its anchor tile with `build remove <id>`. Every footprint tile must be purchased and clear. Payment/refunds, material use, service activation/restoration, and footprint release occur together. Per-tile painting, production, and richer structure service integrations remain open.
- **Started — bounded home terrain and room editing:** the builder can list permitted paintable terrain, configure per-tile currency/material costs, set a separate tile-removal refund percentage (zero by default), and opt into room text editing. Owners and collaborators can preview and repaint a purchased tile, rename or describe a purchased room from inside the home, and preview/remove an outer purchased tile with `build preview remove tile <direction>` and `build remove tile <direction>`. Terrain descriptions follow the current biome while custom room prose remains independent; both persist with the private map. Removal protects the entrance and area connectivity, and rejects occupied tiles, structures, and custom exits; refunds and tile removal persist together. Unpurchased tiles, unauthorized actors, unconfigured biomes, and impassable terrain are rejected for painting.
- **Started — stock-based market quotes:** vendor editing now exposes the stock offer list, including inventory quantity and typed fixed/stock-based pricing fields, with a live stock-level price preview. Stock-based markets calculate bounded buy quotes from current stock and settle batches at marginal per-unit prices; sales update stock and use the same bounded pricing rule. Authors must set a finite quantity and valid price bounds.
- **Started — explicit market selection:** shop output shows each nearby vendor's stable ID. When offers for an item are ambiguous, buy/sell requires `at <market-id>`; the short command remains valid when one offer matches. Settlement uses only the selected market's shared stock.
- **Started — authored market restocking:** finite-stock offers can define an interval, amount, and maximum stock. The world builder configures whether downtime advances the market and a per-update catch-up limit. Shared SQLite stock replenishes on market access and is used by quotes, trades, and decision candidates in both the preview and exported engine.
- **Started — authored market production:** finite-stock output offers can define an interval, amount, cap, and input items drawn from the same vendor's finite shared stock. Production consumes inputs and creates outputs atomically, pauses when inputs are unavailable or output storage is full, and uses the configured bounded catch-up and offline-simulation policies. It is evaluated on market access and decision-candidate refresh in both playtest and exports.
- **Started — authored market consumption:** finite-stock offers can define interval-based demand. Elapsed cycles reduce shared stock without going below zero; offline and catch-up handling follows the same world economy settings as restocking and production. The stock is updated in the shared transaction used by market access.
- **Started — decision-controlled markets:** an enabled market policy can name an offered item and stock target; its decision model selects only engine-generated pause/resume or bounded stock-based price adjustments. Paused offers reject purchases while still accepting player sales, allowing stock to recover; price changes are clamped to the authored range, persisted, and rechecked against the starting base price before applying a response.
- **Started — player-to-player barter:** the builder can enable expiring offers for authored items and world currency, and separately allow them inside private homes. Players must share a map tile; home trades additionally require current access for both participants. Acceptance rechecks both participants' location, home access, equipped items, and balances inside one database transaction. Offers transfer nothing until accepted and can be declined, cancelled, or allowed to expire.
- **Started — map entry and connection authoring:** map settings expose named arrival points and spawn coordinates with in-bounds validation. A selected tile can connect to any authored map and named arrival point through a dedicated connection editor, while existing resource, station, and other tile fields remain in the location inspector. A targeted Chromium check confirmed that creating a submap from a selected tile saves reciprocal exits. Full multi-map runtime playthrough and end-to-end exported-game verification remain outstanding.
- **Started — map lifecycle authoring:** authors can rename a map, duplicate its terrain and connections (with self-links retargeted), or delete it after its references are removed. Default maps and maps referenced by entities, home templates, or incoming connections cannot be deleted. Duplicating a map intentionally does not duplicate placed NPCs or vendors.
- **Started — map-centered content authoring:** the World map is the central build surface. Its create/place flow opens any authored content type; location-bearing NPCs, vendors, creatures, interactables, rooms, and descriptions are placed on a selected tile, while nonspatial content opens its structured editor from the same page. Existing spatial entities have a terrain-colored, keyboard-navigable minimap picker with direct coordinates under an advanced disclosure. The Creator MCP can create maps and connect named entries using revision-checked tools.
- **Started — export file readability:** generated ZIP entries now carry readable Unix file modes, and the runtime image explicitly makes copied Python modules readable before switching to its unprivileged user. This prevents a freshly extracted export from failing at startup because its runtime or world data is mode `0600`.
- **Started — cross-map market contracts:** Markets expose structured contract rows for finite-stock cargo, destination market, reward, prerequisites, and optional deadline. Runtime acceptance removes cargo from shared origin stock, persists active/completed state, allows delivery only at the destination market, pays once, and leaves cargo with the player if the contract expires. Unit coverage exercises the full map transition, restart persistence, expiry, and invalid contract references. Browser coverage verifies form authoring and exported world data; shared exported-runtime checks pass, with dedicated contract playthrough in the packaged container still outstanding.
- **Still required:** full map-aware builder/entity and management APIs, richer in-area player map editing, broader browser playtesting, and end-to-end exported-game verification.

## Authoring workflow

1. Create a world and its initial map.
2. Add more maps from the map selector: a town, interior, island, planet, dungeon, or reusable home template. Set dimensions, terrain, descriptions, and entry points independently.
3. Place connections on tiles and select a destination map and entry point. Configure their travel rules.
4. Place a vendor or market on any map. Select tradable items, initial inventory, purchase and sale rules, and optional stock regeneration or production. Add delivery contracts with a finite-stock cargo item, destination market, quantity, reward, prerequisite contract IDs, and optional time limit. Configure player-to-player barter, expiry, and whether it is allowed inside homes.
5. Preview prices at different stock levels and simulate trades in an isolated playtest.
6. Enable player building and mark terrain as buildable. Configure per-tile costs, area templates, expansion limits, storage, access, and production. Players can establish an entrance on eligible terrain without the author placing a separate plot entity. Explicit reserved plots remain an optional authoring tool.
7. Validate the complete world, playtest across maps with multiple players, and export the same configuration and engine.

Routine configuration needs dedicated controls; JSON remains an advanced editing option.

## Feature and configuration contract

| System | Builder controls | Engine behavior |
| --- | --- | --- |
| Maps | Stable ID, name, dimensions, terrain, rooms, entry points, optional parent map | Independent coordinate spaces, map-local discovery, entity lookup, and rendering |
| Connections | Source tile, destination entry, label, directionality, fare, duration, requirements | Validated movement between maps with atomic costs and persisted arrival state |
| Markets | Placement, item rows, buy/sell permission, starting stock, capacity, pricing mode | Shared stock, explicit market selection, transaction-safe trading |
| Player barter | Enablement, private-home permission, offer expiry | Typed item/currency offer, explicit acceptance, location/access checks, atomic transfer |
| Pricing | Fixed prices or bounded stock-based rules, spread, rounding, quantity pricing | Deterministic quotes and authoritative transaction totals |
| Stock changes | Restock interval/amount, production inputs/outputs, consumption, storage limits | Persisted simulation with a defined clock and bounded catch-up |
| Carrying capacity | Optional item weight, inventory or vehicle capacity, transfer rules | Capacity checks during purchases, gathering, crafting, and transfers |
| Delivery contracts | Origin market, finite-stock cargo, destination market, quantity, reward, prerequisites, optional deadline | Persistent acceptance/completion, shared-stock withdrawal, destination-checked delivery, single-use reward; expired cargo remains with the player |
| Buildable terrain | Biome `buildable` attribute, per-tile overrides, map restrictions, area template, ownership limits | Eligible world tiles can become entrances to persistent player-owned submaps |
| Area expansion | Per-tile currency/material costs, per-biome overrides, maximum size, access policy, cost preview, home storage capacity | First purchased interior tile includes an entrance at no additional charge; later tiles use configured costs and private home storage obeys capacity |
| Construction | Allowed structures/tiles, footprint, separate structure costs, prerequisites, limits, removal policy | Bounded player edits, atomic payment and material consumption, persistent changes |
| Home services | Storage, rest, crafting stations, optional production and shop access | Existing gameplay services operating inside player-owned maps |

Connections may model doors, roads, ships, ferries, or portals. Vehicles and cargo rules are optional extensions; crossing maps must not require a space-travel model.

## Data and persistence design

- Introduce a versioned canonical `maps` collection and a `default_map_id`. Each map owns its dimensions, tiles, and named entry points.
- Use map-qualified locations: `{map_id, x, y}`. Connections target a map and named entry point. Keep stable IDs independent of editable names.
- Migrate legacy worlds into one default map and resolve old `{x, y}` references to it. Existing worlds must retain movement, stock, quest progress, and saves after migration.
- Qualify player position, discovery, notes, ground items, presence, combat state, and location-based objectives with map identity. Audit all systems using coordinates, including GM teleport, events, and plugins.
- Preserve existing vendor fields through normalization into the expanded market contract. Fixed pricing and existing stock behavior remain compatibility defaults.
- Separate authored definitions from mutable runtime state. Stock, economy timestamps, ownership, player construction, inventories, and travel state belong in the runtime database; export includes definitions and starting values, never accidental playtest state.
- Distinguish authored map/template IDs from runtime instance IDs. Two players using the same home template must not share storage, discovery, creatures, or construction state accidentally.
- Store `buildable` on biome definitions with an optional tile override. Store building eligibility and cost policies with the world/maps; persist entrance claims, purchased interior tiles, and ownership in runtime state. Passability and buildability are separate attributes.
- Version database migrations and apply them transactionally. Unknown newer content versions should fail clearly rather than silently losing fields.

The exact schema should be agreed during the first phase and recorded with example fixtures before downstream UI and runtime work.

## Market rules

Support two initial pricing modes:

1. **Fixed:** the author sets what the market charges players and what it pays players, matching current vendors.
2. **Stock-based:** the author sets base price, target stock, sensitivity, minimum/maximum price, and spread. Lower stock raises price; higher stock lowers it. Show a chart or table of representative stock levels and calculated prices in the builder.

Use typed, validated rule parameters instead of evaluating arbitrary formula strings. Define integer currency rounding and buy/sell terminology explicitly in the UI. Prevent negative prices and reject configurations where the market pays more than it charges at the same stock level, unless an explicit subsidy policy is added later.

For stock-based batch trades, calculate the marginal price for each unit as stock changes, then display the total. The same calculation must power builder previews, playtest, SSH commands, and actual settlement. Revalidate balance, capacity, and stock inside the transaction; a displayed quote cannot reserve stale inventory.

Markets need explicit IDs in trade commands when multiple markets occupy a tile, while retaining convenient existing commands when there is only one matching offer. The runtime selector is `buy <item> at <market-id> [count]` or `sell <item> at <market-id> [count]`; the `shop` listing must show that ID.

Stock replenishment, production, and consumption are separate optional rules. Default to simulation on access using persisted timestamps, capped by authored storage and catch-up limits. Builder settings must define whether offline time advances the economy; tests use an injected clock. Input shortages and full output storage pause production without creating or destroying goods unexpectedly.

## Maps and travel

The builder needs a map list with create, duplicate, rename, resize, and delete actions, plus map-local undo/redo and entity filtering. Duplication must remap appropriate internal references. Resizing or deleting a map must identify affected placements and connections before applying changes.

Connections should be inspectable from both ends. Bidirectional creation is a convenience that creates explicit linked exits; one-way routes remain supported. Validation should identify missing maps, invalid entries, blocked arrivals, unmet references, and unreachable authored areas. Intentional locked or private areas should be distinguished from broken routes.

Implement immediate travel first. Add timed travel later with persisted departure/arrival, reconnect behavior, and one-time charging. Failed travel must leave position and resources unchanged. Define commands allowed during transit and avoid sleeping a session handler while a journey completes.

Maps, nearby chat, emotes, vendors, ground items, and combat must respect the current map instance. Coordinates alone are never sufficient for proximity.

## Player-owned areas and construction

Author-created submaps are a core feature and ship before player construction. Player-owned maps are an optional policy layered on the same map engine.

Normal terrain should expose a `buildable` attribute in the biome editor. New-world ordinary terrain presets should generally be buildable; authors can disable building for water, roads, protected areas, or any other terrain. A per-tile override allows exceptions without creating another biome. Existing worlds keep player building disabled until the author enables it, preserving current behavior.

When player building is enabled, a player standing on an eligible, unclaimed world tile can establish an area there. That world tile becomes the entrance to their owned submap, with a return connection to the same world location. The author need not pre-place a plot. World/map policies may reserve tiles, disallow entrances on existing connection or service tiles, limit areas per player, and optionally allow building inside owned areas; nested ownership is disabled by default.

The author defines an area template, allowed interior terrain and structures, maximum dimensions/tile count, visitor permissions, and tile purchase costs. The default acquisition model is per interior tile: the player pays for the first tile, and that first purchase includes the entrance and return connection for free. There is no separate entrance fee. Further interior tiles cost the configured amount. For example, at 100 coins per tile, an area with four purchased tiles costs 400 coins total, including its entrance. Templates may scaffold descriptions and layout, but must not grant usable unpaid tiles accidentally.

Expose base currency and optional material costs per tile in the builder, with optional terrain-specific overrides and a clear total-cost preview. Keep tile acquisition costs separate from structure construction costs so an author can charge for land, buildings, or both. Define whether tile removal refunds any cost; default to no refunds. Expansions must remain connected to the entrance and within authored bounds.

Initial acquisition atomically checks eligibility, ownership limits, and resources, charges for the first tile, records the exclusive entrance claim, creates the runtime instance, and creates both connections. Concurrent claims on the same world tile must have exactly one winner. A failed claim or expansion changes no currency, materials, ownership, or tiles. A world can use private homes, shared guild areas, or public settlements without changing engine code.

Start with template-based construction: players place approved structures or purchase approved room upgrades within bounded plots. Structures provide configured storage, rest, crafting, or production services. Later, allow restricted tile painting and room editing through a player-facing interface, using the same validation rules as the builder.

Owners may grant visitor or collaborator access according to the authored policy. Only authorized players may withdraw from storage or modify buildings. Validate footprints, collisions, reachable entrances, maximum construction counts, and resource costs. Define removal refunds and contents handling explicitly; never delete stored items silently.

Editing an authored template after homes exist must not overwrite player changes. For the initial implementation, instances retain their creation-time template version; an explicit migration policy can follow later.

## Implementation phases and acceptance criteria

### 1. Versioned multi-map foundation

Add canonical map/location contracts, legacy content and save migrations, shared engine map lookup, and builder map selection/creation/editing. Update entity placement, map rendering, discovery, events, MCP tools, and export.

Acceptance: an existing single-map world behaves as before; an author can create two independent maps, place entities at identical coordinates, and export both without state or entity leakage.

### 2. Connections and immediate travel

Add entry points, connection placement, destination selectors, fares and typed prerequisites, map-aware quest targets, and isolated playtest spawn selection.

Acceptance: an author connects two maps entirely in the UI; browser and SSH players travel identically; invalid destinations and failed requirements consume nothing; location persists across restart.

### 3. Market authoring and configurable pricing

Extend vendors with dedicated stock editors, buy/sell toggles, capacities, fixed and stock-based rules, price previews, batch quotes, and explicit market selection. Add deterministic transaction settlement and compatibility normalization.

Acceptance: an author places markets on different maps, changes their inventory and rules, and observes the configured prices in playtest and export. Concurrent buyers cannot oversell stock, double-spend currency, or receive different calculation rules than the preview. When barter is enabled, a recipient must accept an unexpired offer while both players remain in the same tile; inventory and currency exchange atomically, and both players must have access when trading in a home.

### 4. Economy simulation and trade logistics

Add optional restocking, production/consumption, clock policies, capacity rules, and authored delivery contracts. Expose economy inspection and reset controls in isolated playtest.

Acceptance: production consumes configured inputs, honors capacities, and survives restart; clock advancement gives reproducible results; contract rewards settle once; disabling simulation preserves static vendor behavior.

### 5. Ownership, home templates, and storage

Add biome/tile buildability controls, area template and cost editors, entrance claim policies, runtime map instances, access controls, and storage transfers. Support optional reserved plots using the same policies.

Acceptance: an author enables building on ordinary terrain through the UI. Two players establish separate homes from one template, each paying for one interior tile with its entrance included for free. Competing claims on one entrance tile settle once. Non-buildable and reserved tiles reject claims without charging. Owners and explicitly authorized collaborators deposit and withdraw items within configured storage capacity; visitors and ungranted players cannot inspect or change storage. Home access, storage, and ownership survive restart. Authored shared maps continue to function normally.

### 6. Bounded construction and home economy

Per-tile expansion, cost previews, a basic structure catalog and policy editor, currency/material costs, one-structure-per-tile validation, bounded rectangular footprints, per-home placement limits, rest/crafting-station service activation, and configurable removal refunds are implemented. Continue with player-facing map editing, production, and configured market/service integration.

Acceptance: additional tiles charge the configured per-tile price without another entrance fee; expansion remains connected and respects limits. Players build only what the author permits; simultaneous edits settle safely; failed placement spends nothing; home production follows the same economy rules as public markets.

Each phase is a complete builder/engine/validation/playtest/export slice. Do not defer the builder controls until after runtime behavior exists.

## Validation and verification

- Contract tests: legacy migration, map references, pricing bounds, production inputs, construction policies, and precise validation paths.
- Engine tests: map-local interactions, failed travel rollback, deterministic quotes, batch rounding, stock and currency conservation, timed economy behavior, buildability overrides, first-tile payment with free entrance, expansion costs, concurrent entrance claims, ownership permissions, and instance isolation.
- Persistence tests: migrated saves, restart during travel, economy timestamps, home state, and idempotent contract/claim settlement.
- API/MCP tests: map-aware authoring, revision conflict handling, event placement, GM teleport, and export inclusion.
- Browser checks: create submap, connect maps, place market, edit stock/pricing, preview, save/reload, validate, and playtest using visible controls.
- Export checks: exercise the same fixture through two real SSH clients, concurrent trading, cross-map proximity, and container restart. Extend the existing verification scripts as each phase lands.

Keep one small setting-neutral integration fixture for exercising these capabilities. Its values demonstrate editable configuration, not special engine branches.

## Scope boundaries

Initial work excludes futures trading, auctions, multiple currencies, autonomous NPC shipping fleets, unrestricted player scripting, and a universal economic simulation. Vehicles, advanced route risk, public player shops, and player-facing map editing can follow the core authoring and persistence foundations.

Defaults: existing worlds stay single-map; markets keep fixed prices; replenishment, timed travel, capacity limits, and player construction are opt-in. New features should compose with quests, crafting, combat, plugins, and authored events.
