# Decision-controlled game systems

Status: implementation in progress. NPC policies now reach a bounded asynchronous Jev/Laya choice loop, isolated playtest controls, persisted traces, and deployment environment bindings. Authored NPC tasks can be started and advanced through bounded decisions and complete with validated effects. The full decision-owner feature set remains incomplete.

The builder has per-NPC/market policy fields for measurable goals, bounded actions, observation scope, active-area triggers, cooldown, timeout, memory cap, and fallback. NPC policies can run on a nearby-player trigger or five-second active-area scan, select among legal wait/move/authored-dialogue/emote/task candidates, and follow authored map-qualified destinations through passable terrain and explicit map connections. Task catalogs define bounded step counts, assignment, expiry, repeat cooldown, and validated completion effects; progress and task state persist with the controller, while one-time completion is recorded on the assigned player's state. Route targets, location, visit counts, tasks, and bounded outcome memory persist; stale responses are rejected and decisions produce sanitized traces. Market policies can pause an authored finite-stock offer at/below its target, resume after replenishment, and adjust stock-based base prices by an authored step within configured min/max bounds. Applied price overrides are persisted and used by market previews, purchases, and sales. The isolated browser inspector supports persisted pause/resume for NPC and market controllers; manual Step remains available while paused, and pausing invalidates an in-flight result. Creator and exported Game Master MCP servers now expose owner-typed status and pause/resume tools; exported Game Master operations write to the existing audit log. The SSH runtime performs inference off the command path; playtest can preview and step either policy. The endpoint and optional credential bindings are exported without their values. NPC gather/craft policies use dedicated builder allowlists and per-decision limits; finite tile resources and recipe ingredients are rechecked transactionally, while NPC inventory and activity counters persist. NPC delivery tasks have a guided builder form for item, quantity, reward, and task identity; the assigned player completes them with an exact `give` transfer that atomically updates player and NPC inventories, applies the authored reward once, closes the assignment, and increments a persisted `deliveries_completed` goal metric. Local HTTP fixtures exercise the System One request path, exact endpoint, bearer binding, question-keyed response, invalid-choice rejection, controller fallback trace, and an exported Docker/SSH runtime with delayed inference; the exported smoke test confirms the typed NPC move is persisted while an SSH `look` command responds immediately. A targeted Chromium check confirmed NPC policy save, route preview, and unconfigured-endpoint fallback in isolated playtest. Calls to a real Jev/Laya deployment and the broader two-client exported-game acceptance run remain outstanding.

Builder verification update (2026-10-02): provider profiles have guided rows for profile ID, supported adapter, model, endpoint environment-variable name, and optional credential-variable name. Inline validation rejects invalid bindings and unsupported adapters; removal/rename is blocked while an authored controller references the profile. Advanced JSON remains available. `.venv/bin/python scripts/verify_gui.py` verifies creation, invalid-input rejection, saved-form reload, reference protection, and exported profile data. The rendered form is at `artifacts/gui/provider-editor.png`. This verifies authoring and export, not a real Jev/Laya deployment.

## Goal

Let world authors configure selected NPCs and other game systems to make bounded decisions through a model such as Jev or Laya. This is a reusable engine capability, authored in the builder, validated with the world, observable in playtest, and carried into standalone game exports.

NPCs are the first controller type, but the shared decision controller should support other authored decision owners: a market deciding among configured stock adjustments, a settlement or faction coordinating approved activities, an event director choosing when to start an authored event, or a world controller selecting among bounded ambient changes. Authors define the controller's goals, observations, candidate actions, triggers, and limits for each owner. The model selects decisions; the engine owns game rules and applies state changes.

Keep the distinction between a **decision owner** (NPC, market, settlement, faction, event director, or world system) and a **decision policy** (its goals, allowed observations, candidate actions, and schedule). Reuse provider adapters, scheduling, tracing, persistence, and validation across owner types; expose owner-specific action catalogs and state through typed interfaces. Do not build these other controllers as disguised NPCs.

Follow the [style guide](style-guide.md) for NPC presentation and the [travel, trade, and building plan](travel-trade-and-building-plan.md) for map-qualified locations, economy rules, and ownership. Basic controller work can begin on the current single-map engine; cross-map travel and autonomous commerce depend on those foundations.

## Builder workflow

1. Choose a decision owner: an NPC, market, settlement/faction, event director, or another supported world system.
2. Select **Control: authored rules or decision model**. Keep owner-specific normal settings, such as NPC identity or market stock rules.
3. Choose a decision-provider profile and configure goals, bounded memory/state, observation scope, triggers, cadence, and timeout.
4. Enable actions from that owner's catalog and set their limits. For example, an NPC can select routes and dialogue; a market can adjust stock within price and quantity bounds; an event director can select from approved events.
5. Configure fallback behavior: keep the authored baseline, pause, follow a schedule, or use deterministic rules.
6. Use **Preview decision** to inspect the proposed observation and available actions without changing the world. Step the controller in isolated playtest, then inspect its trace.
7. Save, validate, and export. Supply deployment credentials privately through runtime configuration.

Dedicated controls should cover common authoring tasks. Advanced JSON editing may expose the same typed contract; executable model instructions are never the configuration interface.

## Authoring contract

| Configuration | Builder controls | Engine responsibility |
| --- | --- | --- |
| Provider profile | Adapter type, model identifier, deployment binding name | Resolve private connection configuration; normalize typed answers |
| Decision owner | Owner type, stable owner ID, controller ID, enabled state | Bind each policy to exactly one supported engine interface |
| Goals | Authored objectives, priorities, constraints, routines, completion conditions | Present bounded task context; measure actual outcomes |
| Observations | Owner state, nearby entities, permitted market/economy data, quest flags, visible events | Restrict information to what the controller is permitted to know |
| Triggers | Player interaction, relevant state change, scheduled tick, task completion | Debounce, coalesce, and rate-limit requests |
| Actions | Owner-specific action types, target restrictions, numeric bounds | Generate eligible candidates and validate execution through domain services |
| Memory | Allowed fact types, retention, entry/size limits | Persist structured outcomes without unlimited conversation history |
| Scheduling | Cooldown, active/offline policy, timeout, concurrency budget | Nonblocking inference and fair scheduling |
| Fallback | Wait, routine, or deterministic policy | Maintain predictable behavior during provider failures |
| Presentation | Authored dialogue/emote choices, activity labels, announcement frequency | Render meaningful changes without debug-output spam |

World files store nonsecret behavior definitions and provider references. Endpoint bindings and credentials are configured for each deployment, not copied from the creator's environment into exports. A provider can be shared by many NPCs without duplicating secrets.

## Supported decision owners and goals

Decision policies use measurable goals and hard constraints. Goals express what an owner should work toward; constraints determine what the engine will allow even when the model prefers another choice. Authors can set priorities and weights, but safety, ownership, quest, economy, and configured limits are engine-enforced constraints.

| Owner | Example goals | Example bounded actions |
| --- | --- | --- |
| NPC | Greet visitors, finish a delivery, keep a route, protect a place, find needed supplies | Move, speak, emote, start an authored task, gather/craft/trade within limits |
| Market | Keep staple goods available, reduce excess stock, favor local products, preserve a reserve | Buy/sell stock, adjust price within an authored range, pause an offer, request configured production |
| Settlement or faction | Maintain supplies, develop selected buildings, serve a role, respond to a local shortage | Allocate a bounded budget, select an approved construction/project, assign an NPC task, request a trade shipment |
| Event director | Offer variety, respond to eligible world conditions, pace events, avoid repetition | Start, defer, or end one of the authored eligible events under cooldown and duration rules |
| World system | Keep authored world variables within target ranges, choose ambient conditions, pace optional encounters | Select a bounded weather/ambient state, enable an authored encounter, change a permitted world variable |

These are owner types and examples, not unconditional features. Add each owner only when its state model and engine actions have clear transaction boundaries and playtest controls. Start with NPCs and one additional owner chosen for a real game need; avoid implementing every row as an opaque general-purpose agent.

Implementation update: NPC control and a first market controller are now present in the shared engine and builder preview. NPC route destinations have dedicated builder rows for map, name, and coordinates. The engine uses bounded BFS over passable map tiles and authored connections; each decision applies one validated step, persists the active target across restart, and records completed visits. Authored NPC tasks are selectable in the policy editor, assigned to the initiating nearby player, advanced by bounded decision steps, expired when abandoned, and completed with validated standard effects. Progress and assignment survive restart; one-time task completion and repeat cooldowns are stored per player. NPC market commerce has dedicated builder controls for allowed markets/items, starting funds and inventory, currency reserve, inventory capacity, buy/sell permissions, per-decision quantity, and spend cap. NPCs can buy or sell only while standing at an allowed public market; engine-generated candidates use shared stock, paused-offer rules, and the same marginal quotes as player trades. NPC balances, inventory, trade count, stock settlement, and traces persist transactionally. Scheduled decisions advance only while an online player is within the configured active radius. The isolated decision inspector can persistently pause/resume either owner type and invalidate in-flight results by revision. Creator and exported Game Master MCP tools inspect controller state and pause/resume by owner type; exported controls are transactionally audited. Market goals identify an offered item and stock target. The model can pause/resume sales or raise/lower an eligible stock-based offer's base price by a configured adjustment step, clamped to its authored price range. Price candidate IDs include the starting price so concurrent or delayed decisions become stale after another adjustment. Runtime quotes, player purchases, and sales use the same pricing function; the builder preview illustrates the authored base pricing curve. Request-driven restocking/production, settlements, factions, and event directors remain planned work.

Goal fields should be typed and owner-specific: target metric or objective, desired range/value, priority, evaluation window, and completion or stop condition. Constraints include budget, stock bounds, rate limits, allowed maps, event cooldowns, construction permissions, and maximum changes per decision. The builder should explain how goals interact when they conflict, show preview candidates, and report when constraints make a goal infeasible.

## Typed decision loop

1. A permitted trigger schedules the NPC, subject to cooldown and global request limits.
2. The engine snapshots relevant world state and computes currently eligible actions. Include `wait` as a valid choice.
3. Build typed questions referencing stable candidate IDs and bounded parameters. Attach a decision ID, NPC state revision, observation timestamp, and content revision.
4. Call the provider outside database transactions and outside SSH/browser input handlers.
5. Parse the response through the adapter and validate every answer against the question schema. Reject unknown candidates, invalid values, and unsupported answer shapes.
6. Re-check eligibility against current state inside a short transaction. Apply one authorized action atomically or reject it as stale.
7. Persist outcome, bounded memory, cooldown, and a trace. Schedule the next decision only when policy requires it.

For the first version, choose one action candidate per decision, regardless of owner. Avoid independently selected answers that create incompatible action/target combinations. Parameterized actions can follow with explicit cross-field validation.

Example internal candidate list, not a provider-specific API request:

```json
{
  "npc_id": "bridge-keeper",
  "state_revision": 42,
  "candidates": [
    {"id": "wait", "type": "wait"},
    {"id": "greet-mira", "type": "dialogue", "dialogue_id": "welcome", "target": "mira"},
    {"id": "walk-market", "type": "move", "destination": {"map_id": "riverlands", "x": 4, "y": 7}}
  ]
}
```

Returning a candidate ID authorizes only that precomputed action, subject to current engine checks. Free-form model text, shell commands, Python, SQL, arbitrary effects, and fabricated item grants are not executable actions.

The candidate is dispatched through the owner's typed domain API (for example, `npc.move`, `market.restock`, or `event.start`). A model never receives a generic state-mutation tool. Every action carries expected owner/world revisions and is checked again at application time.

## Provider adapters

Define a small provider-neutral interface accepting an observation, named typed questions, request metadata, and deadline, and returning normalized answers plus optional diagnostic metadata. This layer is independent of decision-owner type. Implement a deterministic fake adapter first for tests and playtest fixtures.

The initial adapter uses the shared System One protocol: JSON `{state, questions, model}`, with a named `choice` question and a question-keyed `answers` response. Jev and Laya publish compatible `/v1/systemone` request and response shapes ([Jev API](https://system-one.dev/en/api), [Laya protocol reference](https://laya.studio/glossary/systemone-protocol)). Provider model IDs and endpoint URLs remain deployment-specific; the adapter reads the exact endpoint from its environment binding and does not append a path. A live call from both an exported game and browser playtest is still required before claiming end-to-end provider validation.

Expose adapter capabilities for supported question types. The architecture may support named choices, booleans, bounded numbers, and other verified typed outputs; NPC policy decides which types a behavior needs. Do not force decision models through a text-generation interface. Confidence or probabilities can be inspected when available, but must not grant additional permissions.

## Initial action vocabulary

Start with:

- `wait`: remain idle until the next eligible trigger.
- `move`: take one legal step toward an authored target, respecting passability and map permissions.
- `dialogue`: select an authored branch or response directed at an eligible nearby player.
- `emote`: select an authored emote within a broadcast cooldown.
- `task`: start or advance an approved task with engine-defined effects and completion conditions.

NPC task catalogs are authored in the policy editor. A task is assigned to the nearby player who starts it, advances one bounded step per selected decision, and completes only while that player remains near the NPC. The engine applies only validated authored completion effects. One-time tasks remain complete for that player; repeatable tasks observe an authored cooldown. An expired assignment can be cleared through another bounded task candidate.

Gathering and crafting use explicit persistent NPC inventory. Gathering consumes finite public-tile resources; crafting consumes authored recipe inputs at the required station. Delivery tasks assign a nearby player an exact authored item and quantity; the player must return and issue `give <item> <count> to <NPC>`. The engine transfers the item into the NPC inventory, pays authored effects once, records task completion, and rejects the wrong player, item quantity, expired assignment, or an NPC that lacks capacity. Player-to-player trade policies and bounded combat remain future extensions. Resource limits are engine-enforced and models never issue free-form commands.

Movement uses engine pathfinding; the model chooses a destination or task, not every pathfinding calculation. Trading spends the NPC's own funds and capacity, checks current quotes, and obeys authored purchase limits. NPCs cannot change market rules, enter private homes, build on behalf of owners, or bypass quest conditions without an explicit supported policy.

Decision-controlled dialogue initially selects authored text and branches. Generated conversation is a separate optional future feature with its own configuration, latency, and validation requirements.

## Observation and memory boundaries

Give each NPC a bounded, structured view: location, current task, own resources, relevant nearby entities, permitted public facts, and recent observed outcomes. Explicitly configure whether remote market knowledge or world-wide events are available. Never expose undiscovered maps, hidden quest answers, another player's private inventory/notes, private home contents, or deployment secrets by default.

Treat player speech and authored prose as observed content, not permission to add actions. A player asking an NPC to mint currency must not expand its candidate list. Persist useful facts such as last greeting time, active delivery, or task failure count with retention limits. Avoid storing complete transcripts unless an author explicitly enables bounded history.

Trace entries record what was observable at decision time so later debugging does not accidentally substitute current state for the original context.

## Scheduling, persistence, and failure behavior

Use one in-flight decision per NPC, bounded provider/global concurrency, a fair queue, trigger debounce, and configurable request budgets. Normal gameplay must remain responsive when inference is slow. Default to active-area execution with offline NPC progression disabled; authors can explicitly enable background simulation with limits.

Keep controller state, task progress, memory, last outcome, cooldown, and decision/application IDs in SQLite. A unique application ID prevents a repeated response or restarted worker from applying an action twice. A stale response must be revalidated or discarded, not retried blindly against changed state.

On restart, resume persisted tasks, recompute eligible actions, and expire abandoned requests. Cancellation, NPC removal, event expiry, provider-profile changes, or behavior disablement invalidate outstanding decisions. Pause/resume controls preserve state and invalidate unsafe in-flight results.

Timeouts, malformed answers, unreachable providers, and budget exhaustion invoke the authored fallback. Use bounded retries with backoff and a circuit breaker for repeated provider failure. Playtest should support an injected clock and fake provider, including delayed responses. Avoid holding database locks while waiting for a model.

## Playtest and management tools

Implementation update: the browser inspector now uses a full-width structured view for provider binding status, paused state, goals, observations, candidates, selection/outcome, and post-step state. Nested observations are expandable and the complete response is downloadable under Advanced. Goal authoring uses owner-specific metric rows with stable IDs, objectives, targets, priorities, and optional item references; Advanced JSON remains available. Browser checks verify NPC delivery and market stock goals survive save/export. Browser verification exercises missing-binding fallback and persisted pause/resume; this is not live Jev/Laya deployment verification.

Add a controller inspector for any supported owner, showing current goals, owner state, next scheduled decision, provider status, fallback state, and recent decisions. NPC-specific fields include activity, location, and resources. Provide step, pause/resume, dry-run, reset test state, and simulated provider-failure controls in isolated playtest.

For each decision show trigger, permitted observation, candidate list, normalized selection, timing, validation result, and resulting state change. Keep raw response excerpts bounded and redact credentials. Authors should be able to distinguish a model choice, an engine rejection, and a fallback action.

Creator MCP and exported Game Master MCP servers expose owner-typed status and pause/resume; the creator uses its bearer-authenticated server and records control actions, while exported runtime controls use the management audit log. Export trace replay fixtures for debugging; replay applies recorded choices to a test world and does not call the model.

Player-facing output follows the style guide: NPC icons, authored emotes, concise activity labels, and meaningful arrival/task updates. Provider errors and inference diagnostics belong in management views, not ordinary room output.

## Exports and deployment

Export behavior definitions, adapter code, validated policies, owner-specific action catalogs, and the shared controller alongside the engine. Provide safe environment examples for binding profiles to endpoints/models and secret credentials. Support external decision services; do not require bundling a model into every game image.

The builder can use a privately configured provider for live testing. Each exported deployment supplies its own binding. Export validation checks behavior completeness; startup reports missing bindings and uses configured fallback without breaking scripted NPCs or player login. The existing authoring content assistant remains separate from runtime decision providers.

## Implementation phases

### 1. Shared decision-policy contract and deterministic controller

Add versioned decision-owner and policy contracts, validation, fake/rules-based providers, owner state revisions, scheduling, and typed action dispatch. Implement bounded NPC `wait`/dialogue/emote actions first. Add builder editors and a step-through inspector in the same slice.

Acceptance: an author configures one NPC through visible controls, steps it in playtest, inspects candidates/outcomes, saves/reloads, and exports identical behavior. The generic contract admits another owner type without NPC-specific assumptions. Scripted NPCs remain unchanged.

### 2. Verified Jev/Laya integration

Verify provider contracts, add adapters and private deployment bindings, nonblocking requests, typed parsing, timeouts, request limits, fallbacks, and diagnostics.

Acceptance: perform a live decision call with the intended provider in browser playtest and an exported runtime. Demonstrate invalid-answer and provider-offline fallbacks. Fake-provider tests alone do not establish integration success.

### 3. Movement and durable tasks

Add legal movement, target selection, pathfinding, map permissions, task progress, bounded memory, stale-response rejection, and restart recovery. Add cross-map travel when the multi-map foundation is ready.

Acceptance: two players can observe the same NPC following a configured route across connected maps; movement respects passability and map permissions; restart resumes the route; stale or duplicate decisions cannot duplicate route completion effects.

### 4. Additional decision owners

Add one owner type at a time through the same contract. A market controller can pursue authored stock targets and budgets. A settlement/faction controller can prioritize approved projects. An event director can select from existing validated events. Expose each owner's goals, observations, actions, fallback, schedule, and trace in the builder.

Acceptance: each new owner has a dedicated state/action adapter, typed policy editor, preview and inspection controls, deterministic fallback, persistence semantics, and exported configuration. Its policy cannot bypass the owner subsystem's ordinary rules. Adding it does not require an NPC-shaped proxy.

### 5. Economy and optional interaction extensions

NPC gathering, crafting, and delivery now have builder controls, shared engine state, persistence, and revalidation. Continue with optional bounded combat behavior, then other decision owners through their own state and transaction adapters.

Acceptance: an NPC gathers only authored finite resources at its current public tile, then crafts an allowed recipe only when it has the inputs and required station. Those actions consume shared resource/actor inventory state and persist across restart. A delivery task moves the exact required item from its assigned player to the NPC and pays the authored reward once. An NPC trades using real funds and stock under the same pricing/capacity rules as players. Market controllers adjust stock only within authored targets and budgets. Concurrent NPC/player trades conserve resources. Costs, limits, and provider budgets are editable in the builder and exported.

### 6. Operational polish

Add management/MCP controls, trace replay, load testing, provider health, fair scheduling diagnostics, and polished terminal activity feedback.

Acceptance: slow or unavailable inference does not stall player commands, many enabled NPCs respect global budgets, and pause/resume or event expiry safely invalidates pending work.

## Verification

- Contract tests for decision owners and policies, goal/constraint fields, provider capabilities, action bounds, and legacy NPC compatibility.
- Provider fixture tests for question-keyed answers, missing/unknown choices, malformed responses, deadlines, authentication failure, and optional metadata.
- Engine tests for permitted observations, action eligibility, fallback, resource conservation, stale snapshots, concurrent decisions, and idempotent application.
- Persistence tests for paused controllers, memory limits, restart recovery, cooldowns, and task completion.
- Browser checks for behavior editing, provider selection, dry-run, stepping, trace inspection, and save/reload.
- Real two-client SSH export checks for shared NPC presence/actions, restart, provider outage, and responsive input during inference.
- `.venv/bin/python scripts/verify_exported_decision_runtime.py` builds the export and uses a local System One fixture over the Docker bridge to check authenticated SSH login, a persisted decision, and command responsiveness during delayed inference. This does not replace a call to a real Jev/Laya deployment.
- Load checks for queue fairness and command latency, using representative NPC counts and delayed inference. Measure before selecting default budgets.

No live provider call is needed to build/test most engine behavior; at least one real supported-provider run is required before claiming that adapter works end to end.

## Scope boundaries

Initial scope excludes unrestricted autonomous agents, model-created executable plugins, generated world edits, self-modifying goals, unrestricted long-term memory, and a chat-generation dependency. Authors control what each decision owner may know and do; models choose within those boundaries.
