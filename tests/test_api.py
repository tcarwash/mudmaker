import base64
import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import asyncssh
from fastapi.testclient import TestClient

from app import main


class MockModel(BaseHTTPRequestHandler):
    requests = []
    finish_reason = "stop"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
        result = {"id": "keeper", "name": "Lena", "role": "Merchant", "location": {"x": 9, "y": 5}, "description": "A traveler with a hidden history.", "inventory": [{"item": "Herbs", "count": 7}]}
        data = json.dumps({"choices": [{"finish_reason": self.finish_reason, "message": {"content": "```json\n" + json.dumps(result) + "\n```"}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(data)

    def log_message(self, *_):
        pass


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockModel)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(main, "DB_PATH", Path(self.temp.name) / "workbench.sqlite3")
        self.db_patch.start()
        self.env_patch = patch.dict(os.environ, {"MUDMAKER_BIND": "127.0.0.1", "MUDMAKER_ADMIN_PASSWORD": "", "MUDMAKER_OPENAI_API_KEY": ""})
        self.env_patch.start()
        MockModel.requests.clear(); MockModel.finish_reason = "stop"
        self.client = TestClient(main.app); self.client.__enter__()
        self.client.put("/api/settings", json={"base_url": f"http://127.0.0.1:{self.server.server_port}/v1", "model": "chosen-model"}).raise_for_status()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env_patch.stop(); self.db_patch.stop(); self.temp.cleanup()

    def test_generation_uses_latest_partial_scaffold_and_keyless_provider(self):
        response = self.client.post("/api/worlds/starting-realm/generate/npc", json={"scaffold": {"id": "", "name": "", "role": "Keeper", "location": {"x": 0, "y": None}, "inventory": [{"item": "", "count": 2}]}, "instructions": "Expand the keeper's history"})
        response.raise_for_status(); result = response.json()
        self.assertEqual(result["name"], "Lena")
        self.assertEqual(result["role"], "Keeper")
        self.assertEqual(result["location"], {"x": 0, "y": 5})
        self.assertEqual(result["inventory"], [{"item": "Herbs", "count": 2}])
        request = MockModel.requests[-1]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["body"]["model"], "chosen-model")
        self.assertIsNone(request["authorization"])
        self.assertIn("Runtime template", request["body"]["messages"][0]["content"])

    def test_opt_in_json_mode_and_incomplete_responses(self):
        settings = self.client.get("/api/settings").json()
        self.client.put("/api/settings", json={**settings, "json_mode": True})
        with patch.dict(os.environ, {"MUDMAKER_OPENAI_API_KEY": "synthetic-test-key"}):
            response = self.client.post("/api/worlds/starting-realm/generate/item", json={"scaffold": {"name": "Herbs"}})
            response.raise_for_status()
        self.assertEqual(MockModel.requests[-1]["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(MockModel.requests[-1]["authorization"], "Bearer synthetic-test-key")
        MockModel.finish_reason = "length"
        self.assertEqual(self.client.post("/api/worlds/starting-realm/generate/quest", json={}).status_code, 502)

    def test_partial_drafts_save_but_invalid_builds_report_the_field(self):
        world = self.client.get("/api/worlds/starting-realm").json()
        world["crafting"][0]["inputs"][0]["item"] = ""
        self.assertEqual(self.client.put("/api/worlds/starting-realm", json=world).status_code, 200)
        response = self.client.get("/api/worlds/starting-realm/export")
        self.assertEqual(response.status_code, 422)
        self.assertIn("ingredient", response.json()["detail"])

    def test_player_keys_are_parsed_and_duplicates_ignore_comments(self):
        world = self.client.get("/api/worlds/starting-realm").json()
        key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
        world["ssh_public_keys"] = [{"username": "alice", "public_key": key}, {"username": "bob", "public_key": key + " alternate-comment"}]
        self.assertEqual(self.client.put("/api/worlds/starting-realm", json=world).status_code, 422)
        world["ssh_public_keys"] = [{"username": "alice", "public_key": "ssh-ed25519 AAAA"}]
        self.assertEqual(self.client.put("/api/worlds/starting-realm", json=world).status_code, 422)

    def test_export_is_complete_and_does_not_include_builder_credentials(self):
        with patch.dict(os.environ, {"MUDMAKER_OPENAI_API_KEY": "never-export-this-key"}):
            response = self.client.get("/api/worlds/starting-realm/export")
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertTrue({"runtime.py", "game_engine.py", "Dockerfile", "compose.yaml", "world/players.json", "world/world.json"} <= set(archive.namelist()))
            built = json.loads(archive.read("world/world.json"))
            self.assertEqual(built["schema_version"], 1)
            self.assertTrue(built["interactables"])
            self.assertIn("FROM mudmaker-base AS game", archive.read("Dockerfile").decode())
            self.assertNotIn(b"never-export-this-key", b"".join(archive.read(n) for n in archive.namelist()))

    def test_preview_uses_engine_and_leaves_source_unchanged(self):
        before = self.client.get("/api/worlds/starting-realm").json()
        response = self.client.post("/api/worlds/starting-realm/preview", json={"command": "east"})
        response.raise_for_status()
        self.assertIn("Ancient Grove", response.json()["output"])
        self.assertEqual(response.json()["state"]["x"], 8)
        self.assertEqual(self.client.get("/api/worlds/starting-realm").json(), before)

    def test_duplicate_world_names_remain_unique(self):
        ids = [self.client.post("/api/worlds", json={"name": "My Realm"}).json()["id"] for _ in range(3)]
        self.assertEqual(ids, ["my-realm", "my-realm-2", "my-realm-3"])

    def test_browser_revision_prevents_overwriting_mcp_edits(self):
        from app.creator_mcp import mutate
        response = self.client.get("/api/worlds/starting-realm")
        revision = response.headers['etag']
        draft = response.json()
        mutate('starting-realm', revision, lambda w: w.update(description='Edited through MCP'))
        draft['description'] = 'Stale browser edit'
        stale = self.client.put('/api/worlds/starting-realm', json=draft, headers={'If-Match': revision})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get('/api/worlds/starting-realm').json()['description'], 'Edited through MCP')

    def test_admin_auth_handles_nonascii_and_protects_api(self):
        with patch.dict(os.environ, {"MUDMAKER_ADMIN_PASSWORD": "synthetic-password"}):
            self.assertEqual(self.client.get("/api/worlds").status_code, 401)
            good = base64.b64encode(b"admin:synthetic-password").decode()
            self.assertEqual(self.client.get("/api/worlds", headers={"Authorization": "Basic " + good}).status_code, 200)
            bad = base64.b64encode("管理员:invalid".encode()).decode()
            self.assertEqual(self.client.get("/", headers={"Authorization": "Basic " + bad}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
