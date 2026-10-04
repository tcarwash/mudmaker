# {{WORLD_NAME}}

Start this standalone game:

```sh
docker compose up -d --build
ssh -p 2222 <player-name>@localhost
```

Each player's login name and public key must match a record in `world/players.json`. The matching private key stays on the player's device. Password login and shell access are disabled. Invite additional players by editing that file and restarting the game. The `mud-data` volume preserves the server host key, player saves, shared stock/resources, MCP token, events, and management audit history. Keep this volume when updating the image.

If browser player access is enabled in the builder, put port 8080 behind HTTPS before sharing it. Compose binds the published web port to `127.0.0.1` by default for a TLS proxy on the host; only change `MUD_WEB_BIND` when the address is reachable only through your trusted proxy network. Issue a one-use, 24-hour invitation for any player name (SSH access is optional): `docker compose exec -T mud python /opt/mudmaker/runtime.py invite playername`. Give the printed code to that player privately. On first visit they choose a password of at least 12 characters; future visits use that password. Browser credentials and game progress use the same name, so it can be linked to an SSH player simply by choosing the same name. Reset a forgotten browser password with `docker compose exec -T mud python /opt/mudmaker/runtime.py reset-browser-password playername`, then issue a fresh invitation. Browser passwords are stored as salted hashes in `mud-data`. HTTPS is required because the browser session cookie is secure-only. The web client can be installed from supported browsers; it caches only its static shell and reports when the game connection is down. It never saves commands to send later. Guided buttons include movement, status, quests, help, and server-supplied actions for nearby characters, resources, vendors, creatures, and dialogue. Use **Use text-only mode** to hide guided buttons. Browser players can have one active controlling session; closing a tab releases it after about 45 seconds, while the Leave game button releases it immediately.

Set `MUD_SSH_PORT` to change the SSH port. The non-root runtime uses the standardized `mudmaker-base` Docker stage; it can also be built separately with `docker build --target mudmaker-base -t mudmaker-base:local .`.

## Playing

Use `help` for commands: `look`, `map`, `exits`, `journal`, `note`, `track`, cardinal movement (`north`, `n`, etc.), `talk`, `choose`, quests, `gather`, `recipes`, batch `craft`, `give`, `interact`, `shop`, `buy`, `sell`, `contracts`, `contract accept <id>`, `contract deliver <id>`, `equip`, `unequip`, `emote`, `attack`, `flee`, `drop`, `take`, `rest`, `inventory`, `inspect`, `use`, and `stats`. Chain actions with commas or semicolons, and repeat a step with a suffix count: `go west(3), go north, talk Mira`. Chains execute in order and stop on the first failed step; completed steps keep their results. Batches are capped at 50 commands. `rest` works at a campfire, inn, or authored resting place. Notes are private to your character. Ground items are shared and persist. Multiplayer commands are `who`, `say`, `tell`, and `quit`.

At a market, use `contracts` to review delivery work, `contract accept <id>` to take finite-stock cargo, and `contract deliver <id>` at the named destination market to receive its reward. Prerequisites must be completed first; expired work does not pay, and the cargo remains in your inventory.

`map` displays discovered locations only. `?` hides unexplored tiles, `@` marks you, and `&` marks other online players on discovered tiles. Its legend lists visible player names and coordinates, including players sharing your tile. Players in unexplored places remain hidden. Discovery persists across reconnects. Custom biome symbols conflicting with these markers display as `:`.

If player building is enabled by the world author, use `build status`, `build preview`, `build claim`, and `build expand <direction>`. Preview shows the currency and material cost for claiming your current terrain tile or expanding into an adjacent interior tile. Terrain-specific prices and materials may apply. Claim an eligible terrain tile to create a private template-based area; the first paid tile includes its entrance and return route. Inside your area, use `build preview <direction>` before expanding. Use `build preview remove tile <direction>` before `build remove tile <direction>` to remove an outer purchased tile. The entrance stays in place, and the tile must be unoccupied, structure-free, and connected to the rest of the home by only one edge so removal cannot split the area. Authors can configure a tile refund percentage; it defaults to zero and is calculated from the tile's current terrain cost. Authors can permit specific terrains for home painting and set its currency/material price; use `build preview paint <terrain>` and `build paint <terrain>` on a purchased tile. If the author configured structures, `build structures` lists them, `build preview place <id>` previews cost, affordability, and footprint eligibility, `build place <id>` constructs one on your current purchased tile, and `build remove <id>` removes it from its anchor tile. Construction charges the configured currency and materials; structure removal refunds its separate configured percentage (zero by default). Multi-tile footprints anchor at your current tile and extend east and south; every covered tile must be purchased and clear. Structures can provide authored resting places or crafting stations.

Each owned area has private shared storage up to the capacity configured by its author. Inside your home, use `storage` to inspect it, `store <item> [count]` to deposit items, and `retrieve <item> [count]` to withdraw them. The owner and authorized collaborators can access the chest; equipped items must be unequipped before storage.

Home access is private by default. If the author enables grants, the owner can use `home access grant <player> visitor` to allow entry, or grant `collaborator` for entry, expansion, and shared storage. Use `home access list` to review grants and `home access revoke <player>` to remove one. A visitor can move only through purchased home tiles and cannot use storage or build.

If the author enables player trading, barter with someone in the same map tile using `trade offer <player> <item> [count] for <item> [count]`, for example `trade offer Mira Trail Rations 2 for Crowns 5`. Use the configured currency name in place of an item to trade money. Nothing transfers until the recipient runs `trade accept <id>`; `trade offers`, `trade decline <id>`, and `trade cancel <id>` manage pending offers. Offers expire after the configured interval and are rechecked against both inventories at acceptance. Trading inside a private home also requires current access for both players.

`world/world.json` contains compiled gameplay content. `world/source.json` retains the authoring document. Decision-enabled NPCs can use the configured Jev/Laya System One endpoint; endpoint and optional credential bindings are listed in `.env.example`. Fill those values privately to enable decisions. Missing bindings trigger the authored fallback and do not prevent login or scripted gameplay.

Items can declare an `icon` emoji and an `equip_slot` (`head`, `body`, `hand`, `offhand`, `feet`, `neck`, or `ring`). Use `equip <item>` and `unequip <item|slot>`. Equipped icons appear in your SSH prompt; `inventory` lists item icons and equipped slots. Emotes are `emote wave`, `cheer`, `laugh`, `shrug`, and `dance`, visible to players sharing your location. In the sample, buy a Torch from Mira and use `equip Torch`.

The world presentation settings choose a currency icon, terminal color mode (`auto`, `on`, or `off`), and ASCII or biome-icon maps. Automatic color respects `NO_COLOR` and dumb terminals. `inventory` shows the purse balance. Game-master grants queue a colored, icon-rich notice for the player's next command.

## Mechanics plugins

Enabled plugins live in `plugins/<id>.py` and define `register(api)`. Register player commands with `api.command(name, handler, help_text)` and custom action effects with `api.effect(name, handler)`. Declare custom effect ids in `world/source.json` under the plugin's `effect_types`. Plugins run as Python code in the game container and have the container process's permissions. Install only trusted code.

## Game Master MCP

The container also serves authenticated Streamable HTTP MCP at `http://localhost:2223/mcp`. The host port binds to loopback by default. Set `MUD_MCP_PORT` to change it. A private token is generated on first start in the data volume. Retrieve it privately for your MCP client's bearer header:

```sh
docker compose exec -T mud cat /data/runtime-mcp-token
```

Alternatively, set `MUD_MCP_TOKEN` in a private `.env` before starting (at least 24 characters). Configure the MCP client with the URL and `Authorization: Bearer <token>`. SSH player keys do not grant management access. To expose MCP through your own TLS proxy or private network, set `MUD_MCP_BIND` and allow its Host header with `MUD_MCP_ALLOWED_HOSTS` (comma-separated, such as `game.home:*`).

Tools include `world_context`, `event_schema`, `list_players`, `complete_event_scaffold`, `draft_event`, `list_events`, `activate_event`, `end_event`, `adjust_player`, `announce`, `audit_log`, `decision_inspector`, `decision_controller`, `pause_decision_controller`, and `resume_decision_controller`. Controller inspection reports owner state, goals, memory, and recent bounded traces without returning credentials or raw provider responses. Pause and resume are persisted and audited; pausing invalidates in-flight decisions. The `special_event` prompt guides an AI client through creating an event. The AI client generates content using its model; the game validates and executes the resulting declarative content.

Draft an event with a slug `id`, `name`, and `entities` array. Entity ids must start with the event id followed by `-`. Entities can include NPCs, dialogues, structured quests, vendors, recipes, items, biomes, rooms, and custom actions. Reference base content or entities within the same event. Review the returned draft, then activate it using its revision. Activation can include `expires_at` as a future Unix timestamp. Active events survive restarts; ending or expiry removes event content and retains earned rewards. Use a new event id for another occurrence.

Player grants require a unique `request_id` and a reason. Retrying the same request cannot duplicate rewards. Management actions are recorded in SQLite. No management tool executes shell commands.


Additional exploration commands: `journal` lists discovered places and personal notes. Write a location note with `note <text>`; clear it with `note clear`. Use `track <quest>` to pin progress to `look`; use `track off` to stop. `recipes` lists ingredients, output, station, and the batches you can make here. `craft <recipe> <count>` crafts multiple batches atomically. Use `drop <item> <count>` and `take <item> <count>` to leave shared supplies for other players. `exits` lists only reachable exits.


Creature definitions may include a `spawn_area` rectangle (`x`, `y`, `width`, `height`) on their configured map. Their authored location is the initial spawn; after a defeat and respawn timer, they appear at a random passable tile inside the area.

Combat starts with `attack <creature>`; repeat `attack` to strike back and take damage. Use `flee` to disengage. Defeating a creature grants its authored XP and loot and advances `defeat` quest objectives. At zero HP you wake at your starting place with 50 HP; your items and currency stay safe. Creature health and respawns are shared by the world, so multiple players can fight one creature together.
