"""Build a fresh exported project and exercise it through two real SSH clients."""
import asyncio
import io
import json
import os
import socket
import secrets

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from scripts.verify_mcp import call
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

import asyncssh

from app.main import make_export, new_world


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def connect(port, username, key):
    return await asyncssh.connect("127.0.0.1", port, username=username, client_keys=[key], known_hosts=None, agent_path=None)


async def command(process, text):
    process.stdin.write(text + "\n")
    return (await asyncio.wait_for(process.stdout.readuntil("\n> "), 10)).replace("\r", "")


async def verify(port, keys, mcp_port, token):
    deadline = time.monotonic() + 30
    while True:
        try:
            alice = await connect(port, "alice", keys["alice"])
            break
        except (OSError, asyncssh.Error):
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.3)
    async with alice:
        bob = await connect(port, "bob", keys["bob"])
        async with bob:
            a = await alice.create_process(term_type="vt100")
            b = await bob.create_process(term_type="vt100")
            await a.stdout.readuntil("\n> "); await b.stdout.readuntil("\n> ")
            assert "alice, bob" in await command(a, "who")
            initial_map = await command(a, "map")
            assert "@ (7,5): bob (with you)" in initial_map
            await command(b, "east")
            hidden_map = await command(a, "map")
            assert "bob" not in hidden_map and "Enchanted Forest" not in hidden_map
            await command(a, "east")
            assert "@ (8,5): bob (with you)" in await command(a, "map")
            await command(a, "west")
            assert "& (8,5): bob" in await command(a, "map")
            await command(b, "west")
            assert "alice says: hello" in await command(a, "say hello")
            assert "alice says: hello" in await asyncio.wait_for(b.stdout.readuntil("hello"), 5)
            assert "1." in await command(a, "talk mira")
            assert "Quest accepted" in await command(a, "choose 1")
            assert "Ancient Grove" in await command(a, "east")
            assert "faint warmth" in await command(a, "interact shrine pray")
            assert "already used" in await command(a, "interact shrine pray")
            assert "Quest complete" in await command(a, "east")
            await command(a, "west"); await command(a, "west")
            assert "Quest accepted" in await command(a, "quests start moss-wolf")
            await command(a, "east"); await command(a, "east")
            assert "10/15 HP" in await command(a, "attack moss-wolf")
            assert "5/15 HP" in await command(a, "attack")
            assert "alice gains 12 XP" in await command(a, "attack")
            assert "[complete] A Wolf in the Grove" in await command(a, "quests")
            assert "XP 12" in await command(a, "stats")
            await command(a, "west"); await command(a, "west")
            await command(a, "quests start trail-supplies")
            assert "Gathered 2" in await command(a, "gather Wild Berries 2")
            await command(a, "gather Herbs")
            assert "Crafted" in await command(a, "craft trail-rations")
            assert "Quest complete" in await command(a, "give Trail Rations to mira")
            assert "78 Crowns" in await command(a, "stats")
            assert "25 Crowns" in await command(b, "stats")
            assert "Bought 5" in await command(a, "buy Torch 5")
            assert "only 0" in await command(b, "buy Torch")
            assert "Sold 1" in await command(a, "sell Torch")
            assert "Bought 1" in await command(b, "buy Torch")
            async with streamablehttp_client(f"http://127.0.0.1:{mcp_port}/mcp", headers={"Authorization": "Bearer " + token}) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    announced = await call(session, "announce", {"message": "Integration celebration"})
                    assert announced["recipients"] == ["alice", "bob"]
                    assert "Game Master" in await asyncio.wait_for(a.stdout.readuntil("Integration celebration"), 5)
                    assert "Game Master" in await asyncio.wait_for(b.stdout.readuntil("Integration celebration"), 5)
            a.stdin.write("quit\n"); b.stdin.write("quit\n")
            await a.wait(); await b.wait()
    try:
        wrong = await connect(port, "bob", keys["alice"])
    except asyncssh.PermissionDenied:
        pass
    else:
        wrong.close()
        raise AssertionError("Alice key was able to impersonate Bob")
    try:
        wrong = await asyncssh.connect("127.0.0.1", port, username="alice", password="invalid", client_keys=[], known_hosts=None, agent_path=None)
    except asyncssh.PermissionDenied:
        pass
    else:
        wrong.close()
        raise AssertionError("Password authentication was accepted")
    async with await connect(port, "alice", keys["alice"]) as conn:
        result = await conn.run("stats", check=True)
        assert "60 Crowns" in result.stdout, result.stdout
        assert "Torch ×5" in (await conn.run("inventory", check=True)).stdout
        assert "[complete] Trail Supplies" in (await conn.run("quests", check=True)).stdout
        assert "Unknown command" in (await conn.run("touch /tmp/no-shell", check=True)).stdout
    async with streamablehttp_client(f"http://127.0.0.1:{mcp_port}/mcp", headers={"Authorization": "Bearer " + token}) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            event = {"id": "celebration", "name": "Celebration", "entities": [{"entity_type": "npc", "id": "celebration-host", "name": "Celebration Host", "location": {"x": 7, "y": 5}, "dialog": "The celebration has begun."}]}
            draft = await call(session, "draft_event", {"event": event})
            await call(session, "activate_event", {"event_id": "celebration", "expected_revision": draft["revision"]})
            grant = {"username": "alice", "request_id": "celebration-grant", "reason": "Festival participation", "effects": [{"type": "currency", "amount": 5}]}
            await call(session, "adjust_player", grant)
            await call(session, "adjust_player", grant)
            async with await connect(port, "alice", keys["alice"]) as conn:
                assert "celebration has begun" in (await conn.run("talk celebration-host", check=True)).stdout
                assert "65 Crowns" in (await conn.run("stats", check=True)).stdout
    return "Runtime MCP event activation and idempotent grants; creature combat and rewards; discovered-only player map; SSH login binding, password rejection, multiplayer chat, biome/room compilation, custom actions, dialogue choices, quests, gathering, crafting, shared trading, and reconnect persistence passed"


def main():
    keys = {user: asyncssh.generate_private_key("ssh-ed25519") for user in ("alice", "bob")}
    source = new_world()
    source["ssh_public_keys"] = [{"username": user, "public_key": key.export_public_key().decode()} for user, key in keys.items()]
    port = free_port()
    mcp_port = free_port()
    token = secrets.token_urlsafe(32)
    project = "mudmaker-check-" + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix="mudmaker-export-") as directory:
        with zipfile.ZipFile(io.BytesIO(make_export(source))) as archive:
            archive.extractall(directory)
        command_base = ["docker", "compose", "-p", project, "-f", str(Path(directory) / "compose.yaml")]
        env = {**os.environ, "MUD_SSH_PORT": str(port), "MUD_MCP_PORT": str(mcp_port), "MUD_MCP_TOKEN": token}
        try:
            subprocess.run(command_base + ["config", "--quiet"], env=env, check=True)
            subprocess.run(command_base + ["up", "-d", "--build"], env=env, check=True)
            print(asyncio.run(verify(port, keys, mcp_port, token)))
            subprocess.run(command_base + ["restart"], env=env, check=True)
            async def reconnect():
                for _ in range(50):
                    try:
                        async with await connect(port, "alice", keys["alice"]) as conn:
                            assert "65 Crowns" in (await conn.run("stats", check=True)).stdout
                            assert "Celebration Host" in (await conn.run("look", check=True)).stdout
                            return
                    except (OSError, asyncssh.Error):
                        await asyncio.sleep(0.2)
                raise AssertionError("Game did not recover after restart")
            asyncio.run(reconnect())
            print("Container restart preserved player, economy, and active MCP event state")
        except BaseException:
            subprocess.run(command_base + ["logs", "--no-color", "--tail", "60"], env=env)
            raise
        finally:
            subprocess.run(command_base + ["down", "--volumes", "--remove-orphans"], env=env, check=True)


if __name__ == "__main__":
    main()
