"""Build an export and verify its SSH decision loop against a local System One fixture."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import asyncssh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ProviderHandler(BaseHTTPRequestHandler):
    received = threading.Event()
    valid_request = False

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size))
        criteria = body.get("questions", {}).get("action", {}).get("criteria", {})
        valid = self.path == "/v1/systemone" and "wait" in criteria and (
            "move-east" in criteria or any(key.startswith("buy-mira-cart-field-notes-1-") for key in criteria)
            or any(key.startswith(("gather-wild-berries-", "craft-trail-rations-")) for key in criteria)
            or "start-task-herb-bundle" in criteria
        )
        type(self).valid_request = type(self).valid_request or valid
        type(self).received.set()
        time.sleep(2)
        choice = next((key for key in criteria if key.startswith("buy-mira-cart-field-notes-1-")), None)
        choice = choice or next((key for key in criteria if key.startswith("craft-trail-rations-")), None)
        choice = choice or next((key for key in criteria if key.startswith("gather-wild-berries-2-")), None)
        choice = choice or ("start-task-herb-bundle" if "start-task-herb-bundle" in criteria else None)
        location=body.get("state",{}).get("owner",{}).get("location",{})
        choice = choice or ("move-east" if "move-east" in criteria and location.get("x")==7 else "wait")
        payload = json.dumps({"answers": {"action": {"choice": choice}}, "model": "fixture-model"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def compose(root: Path, project: str, env: dict[str, str], *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", project, *args],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
    )


async def main() -> None:
    scratch = tempfile.TemporaryDirectory(prefix="mudmaker-export-decision-")
    previous_db = os.environ.get("MUDMAKER_DB")
    os.environ["MUDMAKER_DB"] = str(Path(scratch.name) / "builder.sqlite3")
    provider = None
    provider_thread = None
    root = None
    project = "mudmaker-e2e-" + uuid.uuid4().hex[:10]
    env = None
    try:
        from app.main import make_export, new_world

        gateway = subprocess.check_output(
            ["docker", "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}"],
            text=True,
        ).strip()
        provider = ThreadingHTTPServer((gateway, 0), ProviderHandler)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()

        ssh_port, mcp_port = free_port(), free_port()
        key = asyncssh.generate_private_key("ssh-ed25519")
        world = new_world()
        world.setdefault("starting_inventory", {})["Herbs"] = 2
        world["ssh_public_keys"] = [{"username": "alice", "public_key": key.export_public_key().decode().strip()}]
        world["decision_providers"] = [{
            "id": "fixture",
            "adapter": "laya_systemone",
            "model": "typed-decisions",
            "endpoint_binding": "TEST_ENDPOINT",
        }]
        world["npcs"][0]["decision_policy"] = {
            "enabled": True,
            "provider_id": "fixture",
            "goals": [],
            "actions": ["move"],
            "observations": ["own_state"],
            "triggers": ["player_nearby"],
            "active_radius": 8,
            "cooldown_seconds": 0,
            "timeout_seconds": 5,
            "fallback": "wait",
            "memory_limit": 5,
        }
        trader = copy.deepcopy(world["npcs"][0])
        trader.update(id="market-runner", name="Mira's Supply Runner")
        trader["decision_policy"] = {
            "enabled": True,
            "provider_id": "fixture",
            "goals": [{"id": "supplies", "description": "Keep Field Notes in stock", "metric": "inventory_level", "item": "Field Notes", "target": 1}],
            "actions": ["trade"],
            "observations": ["own_state"],
            "triggers": ["scheduled", "player_nearby"],
            "active_radius": 8,
            "cooldown_seconds": 0,
            "timeout_seconds": 5,
            "fallback": "wait",
            "memory_limit": 5,
            "commerce": {
                "markets": ["mira-cart"],
                "items": ["Field Notes"],
                "starting_currency": 50,
                "starting_inventory": {},
                "currency_reserve": 0,
                "inventory_capacity": 1,
                "max_units_per_decision": 1,
                "max_spend_per_decision": 10,
                "allow_buy": True,
                "allow_sell": True,
            },
        }
        world["npcs"].append(trader)
        artisan = copy.deepcopy(world["npcs"][0])
        artisan.update(id="camp-cook", name="Crossroads Cook")
        artisan["decision_policy"] = {
            "enabled": True, "provider_id": "fixture", "goals": [],
            "actions": ["gather", "craft"], "observations": ["own_state"],
            "triggers": ["scheduled", "player_nearby"], "active_radius": 8,
            "cooldown_seconds": 0, "timeout_seconds": 5, "fallback": "wait",
            "memory_limit": 5,
            "gathering": {"items": ["Wild Berries"], "max_units_per_decision": 2},
            "crafting": {"recipes": ["trail-rations"], "max_batches_per_decision": 1},
            "resources": {"starting_inventory": {"Herbs": 1}, "inventory_capacity": 10},
        }
        world["npcs"].append(artisan)
        delivery_npc = copy.deepcopy(world["npcs"][0])
        delivery_npc.update(id="herb-steward", name="Herb Steward")
        delivery_npc["decision_policy"] = {
            "enabled": True, "provider_id": "fixture", "goals": [],
            "actions": ["task"], "observations": ["own_state"],
            "triggers": ["scheduled", "player_nearby"], "active_radius": 8,
            "cooldown_seconds": 0, "timeout_seconds": 5, "fallback": "wait",
            "memory_limit": 5,
            "resources": {"inventory_capacity": 10, "starting_inventory": {}},
            "tasks": [{
                "id": "herb-bundle", "name": "Herb bundle", "description": "Bring herbs for camp",
                "steps": 1, "delivery": {"item": "Herbs", "count": 2},
                "effects": [{"type": "currency", "amount": 10}], "once": True,
            }],
        }
        world["npcs"].append(delivery_npc)

        root = Path(scratch.name) / "export"
        root.mkdir()
        with zipfile.ZipFile(BytesIO(make_export(world))) as archive:
            archive.extractall(root)

        env = {
            **os.environ,
            "TEST_ENDPOINT": f"http://{gateway}:{provider.server_port}/v1/systemone",
            "MUD_SSH_PORT": str(ssh_port),
            "MUD_MCP_PORT": str(mcp_port),
            "MUD_MCP_BIND": "127.0.0.1",
        }
        print("Building exported Docker runtime…", flush=True)
        result = compose(root, project, env, "up", "-d", "--build", timeout=600)
        if result.returncode:
            print(result.stdout[-8000:], flush=True)
            raise RuntimeError(f"exported Docker build failed: {result.returncode}")

        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", ssh_port), timeout=0.5) as probe:
                    probe.settimeout(0.5)
                    if probe.recv(64).startswith(b"SSH-2.0-"):
                        break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError("exported SSH runtime did not become ready")

        print("Exported SSH runtime ready; testing delayed provider inference…", flush=True)
        try:
            conn = await asyncssh.connect(
                "127.0.0.1", port=ssh_port, username="alice", client_keys=[key], known_hosts=None
            )
        except Exception:
            print(compose(root, project, env, "logs", "--no-color").stdout[-6000:], flush=True)
            raise

        async with conn:
            process = await conn.create_process(term_type="xterm", term_size=(120, 40), encoding="utf-8")
            await asyncio.wait_for(process.stdout.readuntil("> "), timeout=20)
            if not await asyncio.to_thread(ProviderHandler.received.wait, 8):
                raise RuntimeError("exported runtime did not call the local provider")

            started = time.monotonic()
            process.stdin.write("look\n")
            await process.stdin.drain()
            response = await asyncio.wait_for(process.stdout.readuntil("> "), timeout=1.2)
            latency = time.monotonic() - started
            print(f"Command returned during delayed inference ({latency:.2f}s).", flush=True)
            if "Wayfarer Crossroads" not in response:
                raise RuntimeError("look command failed during provider inference")

            query = (
                "import sqlite3,json; c=sqlite3.connect('/data/game.sqlite3'); "
                "print(json.dumps({'states':c.execute(\"select owner_id,state from decision_owner_state "
                "where owner_type='npc'\").fetchall(), 'traces':c.execute("
                "\"select owner_id,selected,outcome,status from decision_traces where owner_type='npc' "
                "and owner_id in ('mira','market-runner','camp-cook','herb-steward') order by created_at\").fetchall(), "
                "'stock':c.execute(\"select value from world_state where key='stock:mira-cart:Field Notes'\").fetchone(), "
                "'resource':c.execute(\"select value from world_state where key='resource:overworld:7,5:Wild Berries'\").fetchone()}))"
            )
            resource_key = f"resource:{world.get('default_map_id', 'overworld')}:7,5:Wild Berries"
            query = query.replace("resource:overworld:7,5:Wild Berries", resource_key)
            deadline = time.monotonic() + 15
            traces = []
            states = {}
            stock = None
            while time.monotonic() < deadline:
                result = subprocess.run(
                    ["docker", "compose", "-p", project, "exec", "-T", "mud", "python", "-c", query],
                    cwd=root,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                if result.returncode == 0 and result.stdout.strip():
                    data = json.loads(result.stdout)
                    traces = data["traces"]
                    states = {owner_id: json.loads(state) for owner_id, state in data["states"]}
                    stock = data["stock"][0] if data["stock"] else None
                    resource = data["resource"][0] if data["resource"] else None
                    if (any(t[0]=="mira" and t[1]=="move-east" and t[3]=="selected" for t in traces)
                        and any(t[0]=="market-runner" and t[1].startswith("buy-") and t[3]=="selected" for t in traces)
                        and any(t[0]=="camp-cook" and t[1].startswith("craft-trail-rations-") and t[3]=="selected" for t in traces)
                        and any(t[0]=="herb-steward" and t[1]=="start-task-herb-bundle" and t[3]=="selected" for t in traces)):
                        break
                # Poll persisted state without injecting extra commands into the
                # SSH stream; command responsiveness is already checked above.
                await asyncio.sleep(0.3)

            moved = next((trace for trace in traces if trace[0]=="mira" and trace[1]=="move-east" and trace[3]=="selected"),None)
            traded = next((trace for trace in traces if trace[0]=="market-runner" and trace[1].startswith("buy-") and trace[3]=="selected"),None)
            gathered = next((trace for trace in traces if trace[0]=="camp-cook" and trace[1].startswith("gather-wild-berries-2-") and trace[3]=="selected"),None)
            crafted = next((trace for trace in traces if trace[0]=="camp-cook" and trace[1].startswith("craft-trail-rations-") and trace[3]=="selected"),None)
            delivery_started = next((trace for trace in traces if trace[0]=="herb-steward" and trace[1]=="start-task-herb-bundle" and trace[3]=="selected"),None)
            print(f"Provider request valid={ProviderHandler.valid_request}; move={moved}; trade={traded}; gather={gathered}; craft={crafted}; delivery={delivery_started}.", flush=True)
            if not ProviderHandler.valid_request or not moved or states.get("mira",{}).get("location")!={"x":8,"y":5}:
                raise RuntimeError("exported NPC movement decision was not applied through the game engine")
            if not traded or states.get("market-runner",{}).get("currency")!=40 or states.get("market-runner",{}).get("inventory",{}).get("Field Notes")!=1 or stock!=9:
                raise RuntimeError("exported NPC market trade did not atomically settle currency, inventory, and shared stock")
            if not gathered or not crafted or states.get("camp-cook",{}).get("inventory")!={"Trail Rations":1} or states.get("camp-cook",{}).get("crafted_batches")!=1 or resource!=18:
                raise RuntimeError(
                    "exported NPC gathering/crafting mismatch: "
                    f"gather={bool(gathered)}, craft={bool(crafted)}, "
                    f"inventory={states.get('camp-cook',{}).get('inventory')}, "
                    f"crafted_batches={states.get('camp-cook',{}).get('crafted_batches')}, "
                    f"remaining_resource={resource}"
                )
            if not delivery_started or states.get("herb-steward",{}).get("active_task",{}).get("assigned_to")!="alice":
                raise RuntimeError("exported NPC did not assign the authored delivery to the nearby player")
            process.stdin.write("give Herbs 2 to Herb Steward\n")
            await process.stdin.drain()
            delivery_response=await asyncio.wait_for(process.stdout.readuntil("> "),timeout=2)
            if "Completed Herb bundle" not in delivery_response or "Delivered 2 Herbs" not in delivery_response:
                raise RuntimeError("exported player delivery did not complete through the SSH game command")
            delivery_query=(
                "import sqlite3,json; c=sqlite3.connect('/data/game.sqlite3'); "
                "print(json.dumps({'npc':json.loads(c.execute(\"select state from decision_owner_state where owner_type='npc' and owner_id='herb-steward'\").fetchone()[0]),"
                "'player':json.loads(c.execute(\"select state from players where username='alice'\").fetchone()[0])}))"
            )
            delivery_result=subprocess.run(["docker","compose","-p",project,"exec","-T","mud","python","-c",delivery_query],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,check=False)
            delivery_state=json.loads(delivery_result.stdout)
            if delivery_result.returncode or delivery_state["npc"].get("inventory",{}).get("Herbs")!=2 or delivery_state["npc"].get("deliveries_completed")!=1 or delivery_state["npc"].get("active_task") is not None or delivery_state["player"].get("currency",0)<35:
                raise RuntimeError("exported NPC delivery did not atomically transfer goods, pay the reward, and close the task")

            process.stdin.write("quit\n")
            await process.stdin.drain()
            await asyncio.wait_for(process.wait(), timeout=3)
        print("EXPORTED_RUNTIME_E2E=PASS", flush=True)
    finally:
        if root and env:
            compose(root, project, env, "down", "-v", "--rmi", "local")
        if provider:
            provider.shutdown()
            provider.server_close()
        if provider_thread:
            provider_thread.join(timeout=2)
        if previous_db is None:
            os.environ.pop("MUDMAKER_DB", None)
        else:
            os.environ["MUDMAKER_DB"] = previous_db
        scratch.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
