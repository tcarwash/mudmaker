# {{WORLD_NAME}}

Start this standalone game:

```sh
docker compose up -d --build
ssh -p 2222 <player-name>@localhost
```

Each player's login name and public key must match a record in `world/players.json`. The matching private key stays on the player's device. Password login and shell access are disabled. Invite additional players by editing that file and restarting the game. The `mud-data` volume preserves the server host key, player saves, shared stock/resources, MCP token, events, and management audit history. Keep this volume when updating the image.

Set `MUD_SSH_PORT` to change the SSH port. The non-root runtime uses the standardized `mudmaker-base` Docker stage; it can also be built separately with `docker build --target mudmaker-base -t mudmaker-base:local .`.

## Playing

Use `help` for commands: `look`, `map`, `exits`, `journal`, `note`, `track`, cardinal movement (`north`, `n`, etc.), `talk`, `choose`, quests, `gather`, `recipes`, batch `craft`, `give`, `interact`, `shop`, `buy`, `sell`, `equip`, `unequip`, `emote`, `attack`, `flee`, `drop`, `take`, `rest`, `inventory`, `inspect`, `use`, and `stats`. `rest` works at a campfire, inn, or authored resting place. Notes are private to your character. Ground items are shared and persist. Multiplayer commands are `who`, `say`, `tell`, and `quit`.

`map` displays discovered locations only. `?` hides unexplored tiles, `@` marks you, and `&` marks other online players on discovered tiles. Its legend lists visible player names and coordinates, including players sharing your tile. Players in unexplored places remain hidden. Discovery persists across reconnects. Custom biome symbols conflicting with these markers display as `:`.

`world/world.json` contains compiled gameplay content. `world/source.json` retains the authoring document. The game needs no creator server or model provider to run.

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

Tools include `world_context`, `event_schema`, `list_players`, `complete_event_scaffold`, `draft_event`, `list_events`, `activate_event`, `end_event`, `adjust_player`, `announce`, and `audit_log`. The `special_event` prompt guides an AI client through creating an event. The AI client generates content using its model; the game validates and executes the resulting declarative content.

Draft an event with a slug `id`, `name`, and `entities` array. Entity ids must start with the event id followed by `-`. Entities can include NPCs, dialogues, structured quests, vendors, recipes, items, biomes, rooms, and custom actions. Reference base content or entities within the same event. Review the returned draft, then activate it using its revision. Activation can include `expires_at` as a future Unix timestamp. Active events survive restarts; ending or expiry removes event content and retains earned rewards. Use a new event id for another occurrence.

Player grants require a unique `request_id` and a reason. Retrying the same request cannot duplicate rewards. Management actions are recorded in SQLite. No management tool executes shell commands.


Additional exploration commands: `journal` lists discovered places and personal notes. Write a location note with `note <text>`; clear it with `note clear`. Use `track <quest>` to pin progress to `look`; use `track off` to stop. `recipes` lists ingredients, output, station, and the batches you can make here. `craft <recipe> <count>` crafts multiple batches atomically. Use `drop <item> <count>` and `take <item> <count>` to leave shared supplies for other players. `exits` lists only reachable exits.


Combat starts with `attack <creature>`; repeat `attack` to strike back and take damage. Use `flee` to disengage. Defeating a creature grants its authored XP and loot and advances `defeat` quest objectives. At zero HP you wake at your starting place with 50 HP; your items and currency stay safe. Creature health and respawns are shared by the world, so multiple players can fight one creature together.
