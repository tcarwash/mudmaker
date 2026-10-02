"""Standalone SSH transport. Content and rules never require the builder service."""
import asyncio
import json
import os
from pathlib import Path

import asyncssh
import uvicorn

from runtime_management import RuntimeManagement, create_runtime_server
from mcp_support import BearerAuth, load_token

from game_engine import GameEngine

WORLD = json.loads(Path("/world/world.json").read_text())
PLAYERS = json.loads(Path("/world/players.json").read_text())
KEYS_BY_USER = {}
for player in PLAYERS:
    KEYS_BY_USER.setdefault(player["username"], []).append(asyncssh.import_public_key(player["public_key"]))
ENGINE = GameEngine(WORLD, "/data/game.sqlite3")
SESSIONS = {}
MANAGER = RuntimeManagement(ENGINE)
ENGINE.online_players = lambda: tuple(SESSIONS)


class Server(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        return key in KEYS_BY_USER.get(username, [])


def write(process, text):
    mode = ENGINE.presentation.get("color", "auto")
    term = process.get_extra_info("term_type") or ""
    enabled = mode == "on" or (mode == "auto" and term.lower() not in ("", "dumb") and "NO_COLOR" not in os.environ)
    if enabled:
        palette = {"black": 30, "red": 31, "green": 32, "yellow": 33, "blue": 34, "magenta": 35, "cyan": 36, "white": 37}
        configured = ENGINE.presentation.get("palette", {})
        lines = []
        for line in text.splitlines():
            role = "success" if line.startswith(("Quest complete", "Received", "Gathered", "Crafted", "Bought", "Sold", "Equipped", "📣")) else "info" if line.startswith(("Quest", "Inventory:", "Purse:", "Exploration journal")) else "warning" if line.startswith(("You are in combat", "This character", "[Game Master]")) else "error" if line.startswith(("You need", "You do not", "Unknown", "That ", "Only ")) else None
            color = palette.get(configured.get(role)) if role else None
            lines.append(f"\033[{color}m{line}\033[0m" if color else line)
        text = "\n".join(lines)
    process.stdout.write(text + "\n")


async def game_session(process):
    username = process.get_extra_info("username")
    if process.command:
        # SSH exec mode accepts a game command only; no shell is ever launched.
        write(process, ENGINE.execute(username, process.command))
        process.exit(0)
        return
    if username in SESSIONS:
        write(process, "This character already has an active session.")
        process.exit(1)
        return
    SESSIONS[username] = process
    try:
        write(process, f"\n{WORLD['name']}\nWelcome, {username}. Type help for commands.")
        write(process, ENGINE.execute(username, "look"))
        while True:
            process.stdout.write("\n" + ENGINE.prompt(username))
            line = await process.stdin.readline()
            if not line:
                break
            parts = line.strip().split(maxsplit=1)
            command = parts[0].lower() if parts else "look"
            argument = parts[1] if len(parts) > 1 else ""
            if command in ("quit", "exit"):
                write(process, "Farewell.")
                break
            if command == "who":
                write(process, "Online: " + ", ".join(sorted(SESSIONS)))
            elif command == "say":
                if not argument:
                    write(process, "Use: say <message>.")
                    continue
                position = ENGINE.snapshot(username)
                for peer, target in list(SESSIONS.items()):
                    other = ENGINE.snapshot(peer)
                    if (position['x'], position['y']) == (other['x'], other['y']):
                        write(target, f"\n{username} says: {argument[:1000]}")
            elif command == "tell":
                peer, _, message = argument.partition(" ")
                if peer not in SESSIONS or not message:
                    write(process, "Use: tell <online player> <message>.")
                else:
                    write(SESSIONS[peer], f"\n{username} tells you: {message[:1000]}")
                    write(process, f"You tell {peer}: {message[:1000]}")
            elif command == "emote":
                message = ENGINE.execute(username, line[:4096])
                write(process, message)
                emotes = {item["id"].casefold(): item for item in ENGINE.world.get("emotes", [])}
                if argument.casefold() in emotes and message.startswith("You "):
                    position = ENGINE.snapshot(username)
                    for peer, target in list(SESSIONS.items()):
                        if peer != username:
                            other = ENGINE.snapshot(peer)
                            if (position['x'], position['y']) == (other['x'], other['y']):
                                emote = emotes[argument.casefold()]
                                write(target, f"\n{username} {emote['broadcast']} {emote['icon']}")
            else:
                write(process, ENGINE.execute(username, line[:4096]))
    except (asyncssh.Error, ConnectionError):
        pass
    finally:
        if SESSIONS.get(username) is process:
            del SESSIONS[username]
        process.exit(0)


def broadcast(message):
    recipients = []
    for username, process in list(SESSIONS.items()):
        try:
            write(process, "\n[Game Master] " + message)
            recipients.append(username)
        except (asyncssh.Error, ConnectionError):
            pass
    return recipients


async def main():
    host_key = Path("/data/host_key")
    if not host_key.exists():
        host_key.write_bytes(asyncssh.generate_private_key("ssh-ed25519").export_private_key())
        os.chmod(host_key, 0o600)
    ssh_server = await asyncssh.create_server(
        Server, "0.0.0.0", 2222, server_host_keys=[str(host_key)],
        process_factory=game_session, password_auth=False, kbdint_auth=False,
        max_line_length=4096,
    )
    print(f"{WORLD['name']} listening on :2222; invited players: {len(KEYS_BY_USER)}", flush=True)
    mcp = create_runtime_server(MANAGER, SESSIONS, broadcast)
    token = load_token("MUD_MCP_TOKEN", "/data/runtime-mcp-token")
    http = uvicorn.Server(uvicorn.Config(BearerAuth(mcp.streamable_http_app(), token), host="0.0.0.0", port=8001, log_level="warning"))
    async def expire_events():
        while True:
            MANAGER.refresh()
            await asyncio.sleep(5)
    expiry_task = asyncio.create_task(expire_events())
    try:
        await http.serve()
    finally:
        expiry_task.cancel()
        await asyncio.gather(expiry_task, return_exceptions=True)
        ssh_server.close()
        await ssh_server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
