from __future__ import annotations

import io
import json
import os
import base64
import re
import secrets
import hashlib
import sqlite3
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import Any

import asyncssh

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .templates.content import BIOMES as BIOME_DEFINITIONS, ContentError, ENTITY_TEMPLATES, OBJECTIVE_TYPES, compile_world, merge_scaffold
from .templates.game_engine import GameEngine

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web"
DB_PATH = Path(os.getenv("MUDMAKER_DB", ROOT / "mudmaker.sqlite3"))
BIOMES = {"grassland": "Grassland", "forest": "Forest", "desert": "Desert", "tundra": "Tundra", "swamp": "Swamp", "mountain": "Mountain", "water": "Water", "road": "Road"}

app = FastAPI(title="Mudmaker", version="0.2.0")
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS worlds (id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if not con.execute("SELECT 1 FROM worlds LIMIT 1").fetchone():
            world = new_world()
            con.execute("INSERT OR IGNORE INTO worlds VALUES (?, ?)", (world["id"], json.dumps(world)))


def new_world() -> dict[str, Any]:
    width, height = 16, 12
    tiles = []
    for y in range(height):
        for x in range(width):
            biome = "grassland"
            if x in (0, width - 1) or y in (0, height - 1):
                biome = "mountain"
            elif x < 5 and y < 7:
                biome = "forest"
            elif x > 10 and y < 5:
                biome = "desert"
            elif x > 11 and y > 7:
                biome = "water"
            tiles.append({"biome": biome, "name": BIOMES[biome], "description": f"A quiet stretch of {BIOMES[biome].lower()}."})
    tiles[5 * width + 7].update(name="Wayfarer Crossroads", description="A trail meets beneath an old signpost. A campfire burns beside Mira's cart.", station="campfire", resources=[{"item": "Wild Berries", "quantity": 20}, {"item": "Herbs", "quantity": 10}])
    return {
        "id": "starting-realm", "name": "The Wayfarer Realm", "description": "Explore the old roads, help a cartographer, and uncover the secrets of the grove.",
        "width": width, "height": height, "spawn": {"x": 7, "y": 5}, "tiles": tiles, "ssh_public_keys": [],
        "emotes": [
            {"id": "wave", "verb": "wave", "broadcast": "waves", "icon": "👋"},
            {"id": "cheer", "verb": "cheer", "broadcast": "cheers", "icon": "🎉"},
            {"id": "laugh", "verb": "laugh", "broadcast": "laughs", "icon": "😂"},
            {"id": "shrug", "verb": "shrug", "broadcast": "shrugs", "icon": "🤷"},
            {"id": "dance", "verb": "dance", "broadcast": "dances", "icon": "💃"},
        ],
        "runtime_config": {"ssh_port": 2222, "mcp_bind": "127.0.0.1", "mcp_port": 2223, "mcp_allowed_hosts": ""},
        "npcs": [{"id": "mira", "name": "Mira the Cartographer", "role": "Guide", "location": {"x": 7, "y": 5}, "dialog": "The old paths are waking again. Will you help me map them?", "dialog_id": "mira-dialog"}],
        "quests": [
            {"id": "first-survey", "name": "A First Survey", "description": "Discover three locations for Mira.", "giver": "mira", "objectives": [{"type": "discover", "count": 3}], "reward": {"currency": 25, "items": ["Field Notes"]}},
            {"id": "moss-wolf", "name": "A Wolf in the Grove", "description": "Drive off a hungry wolf stalking the old path.", "giver": "mira", "prerequisites": ["first-survey"], "objectives": [{"type": "defeat", "target": "moss-wolf", "count": 1}], "reward": {"currency": 8, "items": [{"item": "Wolf Fang", "count": 1}]}},
            {"id": "trail-supplies", "name": "Trail Supplies", "description": "Craft rations at the campfire and deliver them to Mira.", "giver": "mira", "prerequisites": ["first-survey"], "objectives": [{"type": "craft", "target": "trail-rations", "count": 1}, {"type": "deliver", "target": "Trail Rations", "npc": "mira", "count": 1}], "reward": {"currency": 15, "items": [{"item": "Torch", "count": 1}]}}
        ],
        "crafting": [{"id": "trail-rations", "name": "Trail Rations", "inputs": [{"item": "Wild Berries", "count": 2}, {"item": "Herbs", "count": 1}], "output": {"item": "Trail Rations", "count": 1}, "station": "campfire"}],
        "economy": {"currency": "Crowns", "vendors": [{"id": "mira-cart", "name": "Mira's Supply Cart", "location": {"x": 7, "y": 5}, "stock": [{"item": "Field Notes", "price": 10, "quantity": 10}, {"item": "Torch", "price": 4, "quantity": 5}]}]},
        "entities": [
            {"entity_type": "item", "id": "wolf-fang", "name": "Wolf Fang", "description": "A trophy from the grove path.", "icon": "🦷", "use_effects": []},
            {"entity_type": "item", "id": "rations", "name": "Trail Rations", "description": "A bundle of berries and herbs wrapped in a leaf.", "icon": "🍲", "use_effects": [{"type": "heal", "amount": 20}]},
            {"entity_type": "item", "id": "torch", "name": "Torch", "description": "A sturdy torch for the old roads.", "icon": "🔦", "equip_slot": "hand", "value": 4, "use_effects": []},
            {"entity_type": "creature", "id": "moss-wolf", "name": "Moss Wolf", "description": "A hungry wolf circles the old path.", "icon": "🐺", "location": {"x": 9, "y": 5}, "max_hp": 15, "damage": 7, "experience": 12, "loot": [{"item": "Wolf Fang", "count": 1}], "respawn_seconds": 300},
            {"entity_type": "biome", "id": "enchanted-forest", "name": "Enchanted Forest", "symbol": "&", "color": "#7c6bac", "passable": True, "description": "Silver motes drift between violet leaves."},
            {"entity_type": "room", "id": "ancient-grove", "name": "Ancient Grove", "description": "A mossy stone shrine hums beneath ancient boughs.", "location": {"x": 8, "y": 5}, "biome": "enchanted-forest"},
            {"entity_type": "custom_entity", "id": "shrine", "name": "Stone Shrine", "description": "An old offering bowl waits at the roots of the grove.", "location": {"x": 8, "y": 5}, "actions": [{"id": "pray", "text": "The grove answers with a faint warmth.", "once": True, "effects": [{"type": "currency", "amount": 5}, {"type": "flag", "name": "grove-blessing"}]}]},
            {"entity_type": "dialog", "id": "mira-dialog", "name": "Mira's Introduction", "start": "greeting", "nodes": {"greeting": {"text": "Mira: The old roads need a new map. Will you help?", "choices": [{"text": "I will survey the roads.", "next": "thanks", "effects": [{"type": "quest", "target": "first-survey"}]}, {"text": "Tell me about the grove.", "next": "grove"}]}, "thanks": {"text": "Mira: Visit three places, and I will pay for your notes.", "choices": []}, "grove": {"text": "Mira: Travel east. The shrine remembers every visitor.", "choices": []}}}
        ],
    }


def world_revision(world: dict) -> str:
    return hashlib.sha256(json.dumps(world, sort_keys=True).encode()).hexdigest()


def save_world(world: dict[str, Any], expected_revision: str | None = None) -> dict[str, Any]:
    world = normalize_world(world)
    validate_world(world)
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        if expected_revision is not None:
            row = con.execute("SELECT data FROM worlds WHERE id=?", (world["id"],)).fetchone()
            if not row or world_revision(normalize_world(json.loads(row["data"]))) != expected_revision.strip('"'):
                raise HTTPException(409, "This realm changed in another editor or MCP client. Reload before saving; copy your draft first.")
        con.execute("INSERT INTO worlds VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (world["id"], json.dumps(world)))
    return world


def normalize_world(world: dict[str, Any]) -> dict[str, Any]:
    world.setdefault("entities", [])
    players = []
    raw_players = world.get("ssh_public_keys", [])
    if not isinstance(raw_players, list):
        raise HTTPException(422, "ssh_public_keys must be a list of player records")
    for index, player in enumerate(raw_players):
        if isinstance(player, str):
            player = {"username": f"player{index + 1}", "public_key": player}
        if isinstance(player, dict):
            players.append(player)
        else:
            raise HTTPException(422, "Each player record must be an object with username and public_key")
    world["ssh_public_keys"] = players
    return world


def validate_world(world: dict[str, Any]) -> None:
    try:
        width, height, tiles = world["width"], world["height"], world["tiles"]
    except (KeyError, TypeError):
        raise HTTPException(422, "World requires width, height, and tiles")
    if type(width) is not int or type(height) is not int or not isinstance(tiles, list) or not (4 <= width <= 64 and 4 <= height <= 64 and len(tiles) == width * height):
        raise HTTPException(422, "Map must be 4–64 tiles in each dimension and have exactly width × height tiles")
    allowed_biomes = set(BIOMES) | set(world.get("biomes", {})) | {e.get("id") for e in world.get("entities", []) if isinstance(e, dict) and e.get("entity_type") == "biome"}
    if any(not isinstance(t, dict) or t.get("biome") not in allowed_biomes for t in tiles):
        raise HTTPException(422, "Map contains an unknown biome")
    if not isinstance(world.get("name"), str) or not world["name"].strip():
        raise HTTPException(422, "World name cannot be empty")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", world.get("id", "")):
        raise HTTPException(422, "World id must use lowercase letters, numbers, and dashes (maximum 100 characters)")
    seen_users, seen_keys = set(), set()
    for player in world.get("ssh_public_keys", []):
        if not isinstance(player, dict) or not isinstance(player.get("username"), str) or not isinstance(player.get("public_key"), str):
            raise HTTPException(422, "Player records need a login name and OpenSSH public key")
        username = player.get("username", "")
        key = player.get("public_key", "").strip()
        parts = key.split()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", username) or len(parts) < 2 or not re.fullmatch(r"(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+)", parts[0]) or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", parts[1]):
            raise HTTPException(422, "Player records need a valid login name and OpenSSH public key")
        canonical_key = " ".join(parts[:2])
        try:
            asyncssh.import_public_key(key)
        except (asyncssh.KeyImportError, ValueError):
            raise HTTPException(422, f"Invalid SSH public key for {username}")
        if username.casefold() in seen_users or canonical_key in seen_keys:
            raise HTTPException(422, "Player names and SSH keys must be unique")
        seen_users.add(username.casefold())
        seen_keys.add(canonical_key)


@app.on_event("startup")
def startup() -> None:
    bind = os.getenv("MUDMAKER_BIND", "127.0.0.1")
    if bind not in ("127.0.0.1", "localhost", "::1") and not os.getenv("MUDMAKER_ADMIN_PASSWORD"):
        raise RuntimeError("Set MUDMAKER_ADMIN_PASSWORD before binding the authoring UI beyond loopback")
    init_db()
    # Remove keys saved by early development builds which stored them in SQLite.
    with db() as con:
        con.execute("DELETE FROM settings WHERE key='api_key'")


@app.middleware("http")
async def admin_auth(request, call_next):
    password = os.getenv("MUDMAKER_ADMIN_PASSWORD", "")
    if password:
        header = request.headers.get("authorization", "")
        try:
            scheme, token = header.split(" ", 1)
            username, supplied = base64.b64decode(token, validate=True).decode().split(":", 1)
        except (ValueError, UnicodeDecodeError):
            scheme, username, supplied = "", "", ""
        expected_user = os.getenv("MUDMAKER_ADMIN_USERNAME", "admin")
        if scheme.lower() != "basic" or not secrets.compare_digest(username.encode(), expected_user.encode()) or not secrets.compare_digest(supplied.encode(), password.encode()):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Mudmaker"'})
    return await call_next(request)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/worlds")
def list_worlds() -> list[dict[str, Any]]:
    with db() as con:
        return [normalize_world(json.loads(r["data"])) for r in con.execute("SELECT data FROM worlds ORDER BY id")]


@app.get("/api/worlds/{world_id}")
def get_world(world_id: str, response: Response = None) -> dict[str, Any]:
    with db() as con:
        row = con.execute("SELECT data FROM worlds WHERE id=?", (world_id,)).fetchone()
    if not row:
        raise HTTPException(404, "World not found")
    world = normalize_world(json.loads(row["data"]))
    if response is not None:
        response.headers["ETag"] = world_revision(world)
    return world


@app.put("/api/worlds/{world_id}")
def put_world(world_id: str, world: dict[str, Any], response: Response = None, if_match: str | None = Header(default=None)) -> dict[str, Any]:
    world["id"] = world_id
    result = save_world(world, if_match if isinstance(if_match, str) else None)
    if response is not None:
        response.headers["ETag"] = world_revision(result)
    return result


@app.post("/api/worlds")
def create_world(body: dict[str, str]) -> dict[str, Any]:
    name = body.get("name", "Untitled Realm").strip()
    ident = (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "untitled-realm")[:90]
    world = new_world()
    world.update(id=ident, name=name)
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        base, suffix = ident, 2
        while con.execute("SELECT 1 FROM worlds WHERE id=?", (ident,)).fetchone():
            ident = f"{base}-{suffix}"
            suffix += 1
        world["id"] = ident
        validate_world(world)
        con.execute("INSERT INTO worlds VALUES (?,?)", (ident, json.dumps(world)))
    return world



class Settings(BaseModel):
    base_url: str = ""
    model: str = ""
    json_mode: bool = False


@app.get("/api/settings")
def get_settings() -> dict[str, str]:
    with db() as con:
        values = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM settings")}
    return {"base_url": values.get("base_url", ""), "model": values.get("model", ""), "json_mode": values.get("json_mode", "false"), "has_key": str(bool(os.getenv("MUDMAKER_OPENAI_API_KEY")))}


@app.put("/api/settings")
def put_settings(settings: Settings) -> dict[str, str]:
    for key in ("base_url", "model", "json_mode"):
        with db() as con:
            value = getattr(settings, key)
            con.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value).lower() if isinstance(value, bool) else value.strip()))
    return get_settings()


@app.post("/api/worlds/{world_id}/generate/{kind}")
def generate(world_id: str, kind: str, request: dict[str, Any] | None = None) -> dict[str, Any]:
    world = get_world(world_id)
    request = request or {}
    scaffold = request.get("scaffold") or {}
    if not isinstance(scaffold, dict):
        raise HTTPException(422, "scaffold must be a partial entity object")
    with db() as con:
        opts = {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM settings")}
    opts["api_key"] = os.getenv("MUDMAKER_OPENAI_API_KEY", "")
    if not all(opts.get(k) for k in ("base_url", "model")):
        raise HTTPException(400, "Configure an OpenAI-compatible endpoint and model in Settings")
    kind = kind if kind in ENTITY_TEMPLATES else "custom_entity"
    system = f"You design multiplayer text adventure content. Complete the user's partial {kind} scaffold. Preserve nonblank authored values recursively. Fill empty strings, nulls, empty lists, and missing fields. Return one complete JSON object with no markdown. Runtime template: {json.dumps(ENTITY_TEMPLATES[kind])}. Quest objectives are structured objects: discover(count), visit(target coordinates or room id), talk(target NPC id), collect(target item NAME, count), craft(target recipe id, count), deliver(target item NAME, npc NPC id, count), interact(target entity id), defeat(target creature id, count). Effects are declarative objects of type item(item,count), currency(amount), heal(amount), flag(name,value), or quest(target quest id). Never emit code or shell commands. Give new entities stable slug ids and valid locations inside the map. Use existing references when possible."
    context = {"name": world["name"], "description": world["description"], "width": world["width"], "height": world["height"], "spawn": world["spawn"], "npcs": world.get("npcs", []), "quests": world.get("quests", []), "recipes": world.get("crafting", []), "entities": world.get("entities", []), "biomes": list(BIOMES) + list(world.get("biomes", {}))}
    body = {"model": opts["model"], "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps({"world": context, "scaffold": scaffold, "instructions": request.get("instructions", "")})}]}
    if opts.get("json_mode") == "true":
        body["response_format"] = {"type": "json_object"}
    url = opts["base_url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Base URL must use http or https")
    headers = {"Content-Type": "application/json"}
    if opts["api_key"]:
        headers["Authorization"] = f"Bearer {opts['api_key']}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            choice = json.load(resp)["choices"][0]
            if choice.get("finish_reason") in ("length", "content_filter") or choice["message"].get("refusal"):
                raise HTTPException(502, "Model response was incomplete or refused; adjust the scaffold and retry")
            result = choice["message"]["content"].strip()
            if result.startswith("```"):
                result = re.sub(r"^```(?:json)?\s*|\s*```$", "", result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Generation endpoint failed: {type(e).__name__}")
    try:
        entity = json.loads(result)
    except json.JSONDecodeError:
        raise HTTPException(502, "Model returned invalid JSON entity")
    if not isinstance(entity, dict):
        raise HTTPException(502, "Model response must be a JSON object")
    return {**merge_scaffold(entity, scaffold), "entity_type": kind}


@app.get("/api/schema")
def schema():
    return {"templates": ENTITY_TEMPLATES, "biomes": BIOME_DEFINITIONS, "objective_types": sorted(OBJECTIVE_TYPES)}


def compiled(world):
    try:
        return compile_world(world)
    except (ContentError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(422, f"Content is not ready to build: {exc}")


@app.post("/api/worlds/{world_id}/validate")
def validate_build(world_id: str):
    world = compiled(get_world(world_id))
    return {"ready": True, "npcs": len(world["npcs"]), "quests": len(world["quests"]), "items": len(world["items"]), "interactables": len(world["interactables"]), "biomes": len(world["biomes"])}


@app.post("/api/worlds/{world_id}/preview")
def preview(world_id: str, request: dict[str, Any]):
    world = compiled(get_world(world_id))
    username = request.get("player", "builder")
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", username):
        raise HTTPException(422, "Invalid preview player name")
    database = DB_PATH.parent / "previews" / f"{world_id}.sqlite3"
    engine = GameEngine(world, database)
    command = request.get("command", "look")
    if not isinstance(command, str) or len(command) > 4096:
        raise HTTPException(422, "Preview command must be at most 4096 characters")
    return {"output": engine.execute(username, command), "state": engine.snapshot(username)}


def runtime_py() -> str:
    return (ROOT / "app" / "templates" / "mud_runtime.py").read_text()

def make_export(world: dict[str, Any]) -> bytes:
    validate_world(world)
    source = world
    world = compiled(world)
    runtime_config = world["runtime_config"]
    buf = io.BytesIO()
    # The generated project is self-contained and builds a reusable SSH MUD runtime image.
    files = {
        "compose.yaml": f"services:\n  mud:\n    build: .\n    ports:\n      - '${{MUD_SSH_PORT:-{runtime_config['ssh_port']}}}:2222'\n      - '${{MUD_MCP_BIND:-{runtime_config['mcp_bind']}}}:${{MUD_MCP_PORT:-{runtime_config['mcp_port']}}}:8001'\n    environment:\n      MUD_MCP_TOKEN: ${{MUD_MCP_TOKEN:-}}\n      MUD_MCP_ALLOWED_HOSTS: ${{MUD_MCP_ALLOWED_HOSTS:-{runtime_config['mcp_allowed_hosts']}}}\n      MUD_PLUGIN_DIR: /plugins\n    volumes:\n      - ./world:/world:ro\n      - ./plugins:/plugins:ro\n      - mud-data:/data\n    read_only: true\n    tmpfs:\n      - /tmp\n    cap_drop: [ALL]\n    restart: unless-stopped\n    healthcheck:\n      test: ['CMD', 'python', '-c', 'import socket; s=socket.create_connection((\"127.0.0.1\",2222),3); s.close()']\n      interval: 30s\n      timeout: 5s\n      start_period: 10s\nvolumes:\n  mud-data:\n",
        "Dockerfile": (ROOT / "app" / "templates" / "runtime.Dockerfile").read_text(),
        "runtime.py": runtime_py(),
        "plugin_api.py": (ROOT / "app" / "templates" / "plugin_api.py").read_text(),
        "game_engine.py": (ROOT / "app" / "templates" / "game_engine.py").read_text(),
        "world/source.json": json.dumps(source, indent=2, ensure_ascii=False) + "\n",
        "content.py": (ROOT / "app" / "templates" / "content.py").read_text(),
        "runtime_management.py": (ROOT / "app" / "templates" / "runtime_management.py").read_text(),
        "mcp_support.py": (ROOT / "app" / "templates" / "mcp_support.py").read_text(),
        "world/world.json": json.dumps({**world, "plugins": [{k: v for k, v in plugin.items() if k != "source"} for plugin in world.get("plugins", [])]}, indent=2, ensure_ascii=False) + "\n",
        "world/players.json": json.dumps(world.get("ssh_public_keys", []), indent=2) + "\n",
        "README.md": (ROOT / "app" / "templates" / "game_README.md").read_text().replace("{{WORLD_NAME}}", world['name']),
        ".env.example": f"MUD_SSH_PORT={runtime_config['ssh_port']}\nMUD_MCP_BIND={runtime_config['mcp_bind']}\nMUD_MCP_PORT={runtime_config['mcp_port']}\nMUD_MCP_TOKEN=\nMUD_MCP_ALLOWED_HOSTS={runtime_config['mcp_allowed_hosts']}\n",
        ".gitignore": ".env\n",
    }
    files["plugins/.keep"] = "Plugins listed in world/world.json load from this directory.\n"
    for plugin in world.get("plugins", []):
        if plugin.get("enabled", True):
            files[f"plugins/{plugin['id']}.py"] = plugin.get("source", "")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


@app.get("/api/worlds/{world_id}/export")
def export(world_id: str) -> StreamingResponse:
    world = get_world(world_id)
    return StreamingResponse(io.BytesIO(make_export(world)), media_type="application/zip", headers={"Content-Disposition": f"attachment; filename={world_id}.zip"})
