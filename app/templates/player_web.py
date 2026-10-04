"""Optional invitation-authenticated browser player client."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
import re
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
try:
    from game_engine import GameError
except ImportError:
    from .game_engine import GameError


DB_PATH = Path("/data/game.sqlite3")
COOKIE = "mudmaker_player_session"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def ensure_account_table(con: sqlite3.Connection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS player_web_accounts (username TEXT PRIMARY KEY, salt BLOB NOT NULL, password_hash BLOB NOT NULL, session_version TEXT NOT NULL DEFAULT '')")
    columns = {row[1] for row in con.execute("PRAGMA table_info(player_web_accounts)")}
    if "session_version" not in columns:
        con.execute("ALTER TABLE player_web_accounts ADD COLUMN session_version TEXT NOT NULL DEFAULT ''")


def login_throttle_remaining(username: str) -> int:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("CREATE TABLE IF NOT EXISTS player_web_login_failures (username TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_started INTEGER NOT NULL, locked_until INTEGER NOT NULL)")
        row = con.execute("SELECT locked_until FROM player_web_login_failures WHERE username=?", (username,)).fetchone()
    return max(0, row[0] - int(time.time())) if row else 0


def record_login_failure(username: str) -> None:
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as con:
        con.execute("CREATE TABLE IF NOT EXISTS player_web_login_failures (username TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_started INTEGER NOT NULL, locked_until INTEGER NOT NULL)")
        row = con.execute("SELECT failures, window_started FROM player_web_login_failures WHERE username=?", (username,)).fetchone()
        failures, started = (1, now) if not row or now - row[1] >= 900 else (row[0] + 1, row[1])
        locked_until = now + 900 if failures >= 5 else 0
        con.execute("INSERT INTO player_web_login_failures VALUES (?, ?, ?, ?) ON CONFLICT(username) DO UPDATE SET failures=excluded.failures, window_started=excluded.window_started, locked_until=excluded.locked_until", (username, failures, started, locked_until))


def clear_login_failures(username: str) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM player_web_login_failures WHERE username=?", (username,))


def issue_invitation(username: str, players: list[dict] | None = None) -> str:
    """Create a one-use invitation; usernames can also be web-only players."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", username):
        raise SystemExit("Player names must be 1-32 letters, numbers, dots, dashes, or underscores")
    token = secrets.token_urlsafe(32)
    with sqlite3.connect(DB_PATH) as con:
        con.execute("CREATE TABLE IF NOT EXISTS player_web_invites (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at INTEGER NOT NULL)")
        con.execute("DELETE FROM player_web_invites WHERE expires_at < ?", (int(time.time()),))
        con.execute("DELETE FROM player_web_invites WHERE username=?", (username,))
        con.execute("INSERT INTO player_web_invites VALUES (?, ?, ?)", (digest(token), username, int(time.time()) + 86400))
    return token


def reset_browser_account(username: str) -> None:
    with sqlite3.connect(DB_PATH) as con:
        ensure_account_table(con)
        con.execute("CREATE TABLE IF NOT EXISTS player_web_invites (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at INTEGER NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS player_web_login_failures (username TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_started INTEGER NOT NULL, locked_until INTEGER NOT NULL)")
        con.execute("DELETE FROM player_web_accounts WHERE username=?", (username,))
        con.execute("DELETE FROM player_web_invites WHERE username=?", (username,))
        con.execute("DELETE FROM player_web_login_failures WHERE username=?", (username,))


class Login(BaseModel):
    username: str
    password: str
    invitation: str = ""


class Command(BaseModel):
    command: str


class BrowserOutput:
    def __init__(self):
        self.lines = deque(maxlen=200)

    def write(self, text):
        for line in str(text).splitlines():
            self.lines.append(line)


class BrowserSession:
    def __init__(self, username):
        self.username = username
        self.stdout = BrowserOutput()
        self.is_browser_session = True
        self.last_seen = time.time()

    def get_extra_info(self, name):
        return self.username if name == "username" else ("dumb" if name == "term_type" else None)


def contextual_actions(engine, username: str) -> list[dict]:
    """Offer safe commands based on the current server-authored location."""
    actions = [
        {"label": label, "command": command}
        for label, command in (("Look", "look"), ("Map", "map"), ("Inventory", "inventory"), ("Quests", "quests"), ("Journal", "journal"), ("Recipes", "recipes"), ("Help", "help"), ("North", "north"), ("West", "west"), ("South", "south"), ("East", "east"))
    ]
    safe_id = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
    safe_name = re.compile(r"[A-Za-z0-9 _-]{1,80}")
    with engine.lock, engine.connect() as con:
        state = engine.load(con, username)
        for npc in engine.nearby(engine.world.get("npcs", []), state):
            if safe_id.fullmatch(npc.get("id", "")):
                actions.append({"label": f"Talk: {npc['name']}", "command": f"talk {npc['id']}"})
                completed = set(state.get("completed_quests", []))
                active = set(state.get("active_quests", []))
                for quest in engine.world.get("quests", []):
                    if quest.get("giver") == npc["id"] and quest["id"] not in completed and quest["id"] not in active and not (set(quest.get("prerequisites", [])) - completed) and safe_id.fullmatch(quest.get("id", "")):
                        actions.append({"label": f"Start quest: {quest['name']}", "command": f"quests start {quest['id']}"})
        for vendor in engine.nearby(engine.world.get("economy", {}).get("vendors", []), state):
            if safe_id.fullmatch(vendor.get("id", "")):
                actions.append({"label": f"Shop: {vendor['name']}", "command": "shop"})
        tile = engine.tile(state)
        biome = engine.world["biomes"].get(tile.get("biome"), {})
        gathered = set()
        for resource in [*tile.get("resources", []), *biome.get("resources", [])]:
            item = resource.get("item", "")
            if item in gathered or not safe_name.fullmatch(item):
                continue
            key = f"resource:{state.get('map_id', engine.default_map_id)}:{state['x']},{state['y']}:{item}"
            remaining = engine.shared_count(con, key, resource.get("quantity", resource.get("count", 1)))
            if remaining > 0:
                actions.append({"label": f"Gather: {item} ({remaining})", "command": f"gather {item}"})
                gathered.add(item)
        for creature in engine.nearby(engine.world.get("creatures", []), state):
            if safe_id.fullmatch(creature.get("id", "")):
                actions.append({"label": f"Attack: {creature['name']}", "command": f"attack {creature['id']}"})
        for entity in engine.nearby(engine.world.get("interactables", []), state):
            if safe_id.fullmatch(entity.get("id", "")):
                actions.append({"label": f"Interact: {entity['name']}", "command": f"interact {entity['id']}"})
        dialog_state = state.get("dialog")
        if dialog_state:
            dialog = next((entry for entry in engine.world.get("dialogs", []) if entry.get("id") == dialog_state.get("id")), None)
            node = dialog.get("nodes", {}).get(dialog_state.get("node"), {}) if dialog else {}
            for index, choice in enumerate(node.get("choices", []), start=1):
                try:
                    engine.requirements(state, choice.get("requires", {}))
                except GameError:
                    continue
                actions.append({"label": f"Choose: {choice.get('text', index)}", "command": f"choose {index}"})
    return actions[:24]


def create_player_web(engine, sessions, players, dispatch, static_dir="/opt/mudmaker/player_client"):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/assets", StaticFiles(directory=static_dir), name="player-assets")
    def current_user(request):
        token = request.cookies.get(COOKIE, "")
        record = AUTH_SESSIONS.get(digest(token)) if token else None
        if not record or record[1] < time.time():
            raise HTTPException(401, "Sign in again to continue.")
        with sqlite3.connect(DB_PATH) as con:
            account = con.execute("SELECT session_version FROM player_web_accounts WHERE username=?", (record[0],)).fetchone()
        if not account or account[0] != record[2]:
            AUTH_SESSIONS.pop(digest(token), None)
            raise HTTPException(401, "Sign in again to continue.")
        record[1] = time.time() + 43200
        player = sessions.get(record[0])
        if isinstance(player, BrowserSession):
            player.last_seen = time.time()
        return record[0]

    @app.get("/", response_class=HTMLResponse)
    def index():
        return Path(static_dir, "index.html").read_text()

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(Path(static_dir, "manifest.webmanifest"), media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})

    @app.get("/service-worker.js")
    def service_worker():
        return FileResponse(Path(static_dir, "service-worker.js"), media_type="application/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/api/session")
    def session(request: Request):
        username = current_user(request)
        if username not in sessions:
            raise HTTPException(409, "This character has no active browser session.")
        return {"username": username, "world": engine.world["name"], "currency_name": engine.world.get("economy", {}).get("currency", "Coins"), "state": engine.snapshot(username), "actions": contextual_actions(engine, username)}

    @app.get("/api/events")
    def events(request: Request):
        username = current_user(request)
        player = sessions.get(username)
        if player is None:
            raise HTTPException(409, "Browser session expired. Sign in again.")
        messages = list(player.stdout.lines)
        player.stdout.lines.clear()
        messages.extend(engine.take_notifications(username))
        return {"username": username, "world": engine.world["name"], "currency_name": engine.world.get("economy", {}).get("currency", "Coins"), "state": engine.snapshot(username), "actions": contextual_actions(engine, username), "messages": messages}

    @app.post("/api/login")
    def login(payload: Login, response: Response):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", payload.username) or len(payload.password) < 12 or len(payload.password) > 256:
            raise HTTPException(401, "Check the player name and use a password of at least 12 characters.")
        remaining = login_throttle_remaining(payload.username)
        if remaining:
            raise HTTPException(429, f"Too many sign-in attempts. Try again in {remaining} seconds.", headers={"Retry-After": str(remaining)})
        if payload.username in sessions:
            raise HTTPException(409, "This character is already connected. Disconnect the other session first.")
        with sqlite3.connect(DB_PATH) as con:
            ensure_account_table(con)
            con.execute("CREATE TABLE IF NOT EXISTS player_web_invites (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at INTEGER NOT NULL)")
            account = con.execute("SELECT salt, password_hash, session_version FROM player_web_accounts WHERE username=?", (payload.username,)).fetchone()
            if account:
                candidate = hashlib.scrypt(payload.password.encode(), salt=account[0], n=2**14, r=8, p=1)
                if not hmac.compare_digest(candidate, account[1]):
                    record_login_failure(payload.username)
                    raise HTTPException(401, "Player name or password is incorrect.")
                account_version = account[2]
                if not account_version:
                    account_version = secrets.token_urlsafe(24)
                    con.execute("UPDATE player_web_accounts SET session_version=? WHERE username=?", (account_version, payload.username))
            else:
                if len(payload.invitation) > 128:
                    record_login_failure(payload.username)
                    raise HTTPException(401, "Invitation is invalid or expired.")
                token_hash = digest(payload.invitation)
                invite = con.execute("SELECT username, expires_at FROM player_web_invites WHERE token_hash=?", (token_hash,)).fetchone()
                if not invite or invite[0] != payload.username or invite[1] < int(time.time()):
                    record_login_failure(payload.username)
                    raise HTTPException(401, "Invitation is invalid or expired.")
                salt = secrets.token_bytes(16)
                password_hash = hashlib.scrypt(payload.password.encode(), salt=salt, n=2**14, r=8, p=1)
                account_version = secrets.token_urlsafe(24)
                con.execute("INSERT INTO player_web_accounts VALUES (?, ?, ?, ?)", (payload.username, salt, password_hash, account_version))
                con.execute("DELETE FROM player_web_invites WHERE token_hash=?", (token_hash,))
        clear_login_failures(payload.username)
        session_token = secrets.token_urlsafe(32)
        AUTH_SESSIONS[digest(session_token)] = [payload.username, time.time() + 43200, account_version]
        player = BrowserSession(payload.username)
        sessions[payload.username] = player
        player.stdout.write(f"Welcome to {engine.world['name']}, {payload.username}.")
        player.stdout.write(engine.execute(payload.username, "look"))
        messages = list(player.stdout.lines)
        player.stdout.lines.clear()
        response.set_cookie(COOKIE, session_token, httponly=True, samesite="strict", secure=True, max_age=43200, path="/")
        return {"ok": True, "username": payload.username, "world": engine.world["name"], "currency_name": engine.world.get("economy", {}).get("currency", "Coins"), "state": engine.snapshot(payload.username), "actions": contextual_actions(engine, payload.username), "messages": messages}

    @app.post("/api/command")
    async def command(payload: Command, request: Request, response: Response):
        username = current_user(request)
        if payload.command.strip().lower() in {"quit", "exit"}:
            sessions.pop(username, None)
            AUTH_SESSIONS.pop(digest(request.cookies.get(COOKIE, "")), None)
            response.delete_cookie(COOKIE, path="/")
            return {"messages": ["Farewell."], "state": None, "closed": True}
        if len(payload.command) > 4096 or not payload.command.strip():
            raise HTTPException(422, "Enter a command of at most 4096 characters.")
        output = sessions.get(username)
        if output is None:
            raise HTTPException(409, "Browser session expired. Sign in again.")
        output.stdout.lines.clear()
        await dispatch(username, payload.command, output)
        return {"messages": list(output.stdout.lines), "state": engine.snapshot(username), "actions": contextual_actions(engine, username), "currency_name": engine.world.get("economy", {}).get("currency", "Coins"), "username": username, "world": engine.world["name"]}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        username = current_user(request)
        sessions.pop(username, None)
        token = request.cookies.get(COOKIE, "")
        AUTH_SESSIONS.pop(digest(token), None)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    return app


AUTH_SESSIONS: dict[str, list] = {}
