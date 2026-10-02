# Mudmaker

Mudmaker is a browser based workbench for creating multiplayer text adventures. It includes a biome map painter, editable world entities, SSH public key access lists, an optional OpenAI compatible content assistant, and a self contained Docker Compose export for each game.

## Run the workbench

```sh
docker compose up --build
```

Open [http://localhost:8081](http://localhost:8081). The named `mudmaker-data` volume stores worlds and nonsecret authoring settings. The UI binds to loopback by default. To expose it to your network, copy `.env.example` to `.env`, set `MUDMAKER_BIND=0.0.0.0`, and set a private `MUDMAKER_ADMIN_PASSWORD`; the app enforces HTTP Basic authentication whenever an admin password is set and refuses non-loopback binding without one. Keep `.env` private. For local Python development, install `pyproject.toml` dependencies and run `uvicorn app.main:app --reload --port 8080`.

## Build a game

Use the left navigation to paint the map, edit characters and quests, define recipes and vendors, and add player SSH public keys. Storysmith accepts partial JSON scaffolds and focused instructions. Set the Chat Completions compatible base URL and model in the settings panel; provide `MUDMAKER_OPENAI_API_KEY` through the private `.env` file. The key is not stored in the database or game exports. Generated content remains an editable draft until applied.

Select **Export game** to download a project ZIP. Extract it, then run `docker compose up -d --build` in that directory. The exported project builds a standalone runtime image from its standardized Python base, exposes SSH on port 2222, and mounts the authored world read only. `world/players.json` binds each login name to one SSH public key; a persistent server host key and player save database are stored in the Compose volume. The runtime accepts public key login only.

The runtime supports simultaneous SSH sessions and `help`, `look`, `map`, `exits`, `journal`, `note`, `track`, cardinal `go`, `talk`, dialogue `choose`, quests, gathering, inventory, item use, vendor buy/sell, recipe lookup, batch `craft`, `drop`/`take` shared ground items, `attack`/`flee` combat, safe `rest`, and `stats`. Per-player location, discovered places, currency, inventory, and quest status persist in SQLite. Quest objectives support discovery, visits, conversations, collection, crafting, delivery, and custom interactions. Structured dialogue choices can grant items, set flags, or accept quests. Finite resource supplies and vendor stock are shared between players. Custom biomes and room scaffolds compile into the playable map.

## Project layout

- `app/main.py`: HTTP API, sample world, AI scaffold completion, and game project export.
- `web/`: single page authoring interface and styles.
- `compose.yaml`, `Dockerfile`: authoring workbench deployment.

## Complete a partial scaffold

Open any entity editor, change its JSON, and select AI assistance. The assistant receives the current draft. Missing fields, empty strings, null values, and empty collections are filled; authored values (including `false` and `0`) are preserved. Partial nested objects and list entries are completed recursively. Review and edit the result before applying it.

Supported templates include NPCs, quests, recipes, vendors, items, biomes, rooms, dialogues, descriptions, worlds, and custom entities. Custom entity actions use typed effects rather than executable code. AI operates during authoring; deployed games require no model service. Local OpenAI-compatible providers can run without a key. Optional JSON mode is available for providers that support it.

Use **Playtest** to exercise the same engine used by exports, and **Validate build** to catch broken references, invalid objectives, and incomplete playable content. Drafts may remain incomplete while editing; export requires valid content.

## Development and verification

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn app.main:app --reload --port 8080
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m scripts.verify_export
```

The final command builds a temporary exported game, connects two real SSH clients, checks authentication, quests, crafting, shared trading, chat, and restart persistence, then removes its temporary containers and volume.

The portable content contract lives in `app/templates/content.py`; `game_engine.py` supplies both browser playtesting and deployed gameplay. Exported projects include a reusable Docker build stage: `docker build --target mudmaker-base -t mudmaker-base:local .`. Set `MUD_SSH_PORT` to change an exported game's host port.

## MCP for AI authoring and live management

`docker compose up -d --build` also starts the creator MCP service at `http://localhost:8082/mcp`, sharing the workbench's world database. Both creator and game servers use Streamable HTTP with a bearer token. Retrieve the automatically generated creator token privately:

```sh
docker compose exec -T creator-mcp cat /data/creator-mcp-token
```

Set your MCP client's URL and `Authorization: Bearer <token>` header. Alternatively, supply `MUDMAKER_MCP_TOKEN` in the private `.env` (at least 24 characters). Set `MUDMAKER_MCP_PORT` to change the host port. `MUDMAKER_MCP_BIND` defaults to loopback; use `MUDMAKER_MCP_ALLOWED_HOSTS` for additional proxy/private-network Host headers. Use your TLS proxy when exposing the endpoint beyond the local host.

Creator tools list/create/read/save worlds, upsert any entity, edit map tiles, complete partial scaffolds, validate, playtest, and export a ZIP. Read `get_world` for the revision before writing. Stale edits are rejected, including browser saves after an MCP edit. Copy any unsaved browser draft before reloading. `complete_scaffold` can merge content generated by the connected AI client or call the workbench's configured OpenAI-compatible provider. Completion returns a draft; saving is a separate tool call.

Exported games expose their own MCP endpoint on loopback port 2223 with a separate token. Game Master tools inspect players, grant typed effects, teleport characters, announce messages, and manage special events. The `special_event` prompt and `event_schema` resource provide the AI client with the playable content contract. The client can generate NPCs, dialogues, quests, crafting, vendors, and custom interactions, then draft, review, and activate an event. Events persist across restarts and may have an expiry time. Grants use idempotent request ids, and management actions are audited. Each exported README contains full setup instructions.

The runtime `map` command hides undiscovered locations with `?`. It marks you with `@` and online players on discovered tiles with `&`, with names and coordinates in the legend. Offline players and players in unexplored places are not shown.

The builder exposes item icons and equipment slots, creature icons and combat values, player emotes, and exported SSH/MCP bind and port defaults. Edit realm connections and emotes under **Realm details**; edit item or creature options in **Other entities**. Playtest shows an SSH prompt preview with equipped icons. These saved world settings carry through validation and Docker export. Use `equip <item>` / `unequip <item|slot>` and `emote <id>`; nearby multiplayer clients receive emote broadcasts. The MCP token remains a private runtime secret.

Use **Style & plugins** to choose the currency icon, terminal color behavior, and ASCII or icon maps. Biomes can define their own color, one-character ASCII symbol, and optional emoji map icon. `NO_COLOR` and dumb terminals are respected in automatic color mode. These settings are stored with the world and included in exports.

Mechanics plugins are trusted Python modules stored in the world and exported under `plugins/`. Each module defines `register(api)` and can register commands with `api.command(name, handler, help_text)` or custom action effects with `api.effect(name, handler)`. Command handlers receive `(engine, sqlite_connection, player_state, username, argument)`; effect handlers receive `(engine, player_state, effect)`. Declare custom effect ids in the plugin editor so world validation accepts them. Plugin code executes inside the creator during playtest and inside the game container at runtime; use only code you trust. Disable a plugin to keep it out of runtime exports.

Additional checks:

```sh
.venv/bin/python -m scripts.verify_mcp
.venv/bin/python -m scripts.verify_gui
```

The GUI check uses an isolated database and a fixture OpenAI-compatible endpoint. It uses an installed Chrome/Chromium browser (`MUDMAKER_BROWSER` can select its executable); alternatively install Playwright's Chromium with `.venv/bin/playwright install chromium`. Screenshots and the downloaded game are written to `artifacts/gui/`. The MCP check uses real HTTP connections through the official MCP client and isolated creator/runtime data.

## Exploration and playtesting

The map editor has undo/redo buttons and Ctrl+Z/Ctrl+Shift+Z history. Tiles show the spawn, NPCs, vendors, and interactions; custom location details include a resting-place switch. Content lists support text search, entity type filters, and duplication. The playtest has a movement pad, command shortcuts, ↑/↓ command history, a clearable log, and live health, currency, position, inventory, and quest counts. Use Ctrl+S to save.

At campfires, inns, and marked rest areas, `rest` restores health. `journal` lists discovered locations and your notes; write with `note <text>` and clear with `note clear`. `track <quest>` pins active objective progress to `look`; use `track off` to clear it. `recipes` shows ingredient counts, station requirements, and available batches; `craft <recipe> <count>` makes multiple batches atomically. `drop <item> <count>` and `take <item> <count>` put supplies on or take supplies from shared ground at the current location.


Creatures support shared health, turn based attacks, fleeing, safe recovery after knockout, XP, item drops, respawn timers, and `defeat` quest objectives. Creatures retain shared health and defeated state across sessions. Every player who contributed damage receives the authored XP and loot when the creature falls.
