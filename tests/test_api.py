import base64
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
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

    def test_generation_accepts_server_root_and_preserves_explicit_paths(self):
        for base_path, expected in (("/", "/v1/chat/completions"), ("/v1/", "/v1/chat/completions"), ("/custom/api", "/custom/api/chat/completions"), ("/v1/chat/completions", "/v1/chat/completions")):
            with self.subTest(base_path=base_path):
                self.client.put("/api/settings", json={"base_url": f"http://127.0.0.1:{self.server.server_port}{base_path}", "model": "chosen-model"}).raise_for_status()
                self.client.post("/api/worlds/starting-realm/generate/crafting_recipe", json={}).raise_for_status()
                self.assertEqual(MockModel.requests[-1]["path"], expected)

    def test_generation_reports_provider_status_without_leaking_secrets(self):
        for status in (400, 401, 403, 404, 429, 503):
            with self.subTest(status=status):
                error = urllib.error.HTTPError("https://private-provider.example/secret", status, "secret message", {}, io.BytesIO(b"secret body"))
                with patch.object(main.urllib.request, "urlopen", side_effect=error), self.assertLogs(main.logger, level="WARNING") as logs:
                    response = self.client.post("/api/worlds/starting-realm/generate/crafting_recipe", json={})
                self.assertEqual(response.status_code, 502)
                self.assertIn(f"HTTP {status}", response.json()["detail"])
                output = response.text + " ".join(logs.output)
                self.assertNotIn("secret", output)
                self.assertNotIn("private-provider", output)

    def test_partial_drafts_save_but_invalid_builds_report_the_field(self):
        world = self.client.get("/api/worlds/starting-realm").json()
        world["crafting"][0]["inputs"][0]["item"] = ""
        self.assertEqual(self.client.put("/api/worlds/starting-realm", json=world).status_code, 200)
        response = self.client.get("/api/worlds/starting-realm/export")
        self.assertEqual(response.status_code, 422)
        self.assertIn("ingredient", response.json()["detail"])

    def test_future_schema_import_is_rejected_without_replacing_saved_world(self):
        saved = self.client.get("/api/worlds/starting-realm").json()
        future = {**saved, "schema_version": 2, "name": "Future format"}
        response = self.client.put("/api/worlds/starting-realm", json=future)
        self.assertEqual(response.status_code, 422)
        self.assertIn("newer than supported version", response.json()["detail"])
        self.assertEqual(self.client.get("/api/worlds/starting-realm").json(), saved)

    def test_legacy_world_import_keeps_source_and_compiles_migrated_copy(self):
        source = main.new_world()
        source["id"] = "legacy-harbor"
        original = json.loads(json.dumps(source))
        imported = self.client.post("/api/worlds/import", json={"world":source})
        imported.raise_for_status()
        self.assertEqual(imported.json(), original)
        self.assertEqual(source, original)
        report = self.client.post("/api/worlds/legacy-harbor/validate").json()
        self.assertTrue(report["ready"], report)
        stored = self.client.get("/api/worlds/legacy-harbor").json()
        self.assertNotIn("maps", stored)
        self.assertNotIn("schema_version", stored)
        duplicate = self.client.post("/api/worlds/import", json={"world":source})
        duplicate.raise_for_status()
        self.assertEqual(duplicate.json()["id"], "legacy-harbor-2")

    def test_future_schema_import_is_rejected_without_storing_a_world(self):
        source = main.new_world()
        source.update(id="future-harbor", schema_version=2)
        response = self.client.post("/api/worlds/import", json={"world":source})
        self.assertEqual(response.status_code, 422)
        self.assertIn("newer than supported version", response.json()["detail"])
        self.assertEqual(self.client.get("/api/worlds/future-harbor").status_code, 404)

    def test_player_keys_are_parsed_and_duplicates_ignore_comments(self):
        world = self.client.get("/api/worlds/starting-realm").json()
        key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
        world["ssh_public_keys"] = [{"username": "alice", "public_key": key}, {"username": "bob", "public_key": key + " alternate-comment"}]
        self.assertEqual(self.client.put("/api/worlds/starting-realm", json=world).status_code, 422)
        world["ssh_public_keys"] = [{"username": "alice", "public_key": "ssh-ed25519 AAAA"}]
        self.assertEqual(self.client.put("/api/worlds/starting-realm", json=world).status_code, 422)

    def test_export_is_complete_and_does_not_include_builder_credentials(self):
        revision = self.client.post("/api/worlds/starting-realm/validate", json={}).json()["revision"]
        with patch.dict(os.environ, {"MUDMAKER_OPENAI_API_KEY": "never-export-this-key"}):
            response = self.client.get("/api/worlds/starting-realm/export")
        response.raise_for_status()
        self.assertEqual(response.headers["x-mudmaker-world-revision"], revision)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertTrue({"runtime.py", "game_engine.py", "Dockerfile", "compose.yaml", "world/players.json", "world/world.json"} <= set(archive.namelist()))
            for name in ("runtime.py", "game_engine.py", "Dockerfile", "compose.yaml", "world/players.json", "world/world.json"):
                info=archive.getinfo(name)
                self.assertEqual(info.create_system,3)
                self.assertEqual((info.external_attr >> 16) & 0o777,0o644,name)
            built = json.loads(archive.read("world/world.json"))
            self.assertEqual(built["schema_version"], 1)
            self.assertTrue(built["interactables"])
            self.assertIn("FROM mudmaker-base AS game", archive.read("Dockerfile").decode())
            self.assertNotIn(b"never-export-this-key", b"".join(archive.read(n) for n in archive.namelist()))

    def test_optional_player_web_is_packaged_and_only_published_when_enabled(self):
        disabled = self.client.get("/api/worlds/starting-realm/export")
        with zipfile.ZipFile(io.BytesIO(disabled.content)) as archive:
            compose = archive.read("compose.yaml").decode()
            self.assertNotIn(":8080'", compose)
            self.assertTrue({"player_web.py", "player_client/index.html", "player_client/player.js", "player_client/player.css", "player_client/manifest.webmanifest", "player_client/icon.svg", "player_client/service-worker.js"} <= set(archive.namelist()))
            self.assertIn("COPY player_client ./player_client", archive.read("Dockerfile").decode())

        world = self.client.get("/api/worlds/starting-realm").json()
        world["runtime_config"]["player_web"] = {"enabled": True, "port": 8091}
        saved = self.client.put("/api/worlds/starting-realm", json=world)
        self.assertEqual(saved.status_code, 200, saved.text)
        enabled = self.client.get("/api/worlds/starting-realm/export")
        with zipfile.ZipFile(io.BytesIO(enabled.content)) as archive:
            compose = archive.read("compose.yaml").decode()
            self.assertIn("${MUD_WEB_BIND:-127.0.0.1}:${MUD_WEB_PORT:-8091}:8080", compose)
            self.assertIn("EXPOSE 2222 8001 8080", archive.read("Dockerfile").decode())

    def test_preview_uses_engine_and_leaves_source_unchanged(self):
        before = self.client.get("/api/worlds/starting-realm").json()
        response = self.client.post("/api/worlds/starting-realm/preview", json={"command": "east"})
        response.raise_for_status()
        self.assertIn("Ancient Grove", response.json()["output"])
        self.assertEqual(response.json()["state"]["x"], 8)
        self.assertEqual(self.client.get("/api/worlds/starting-realm").json(), before)

    def test_preview_reset_starts_at_selected_tile_and_is_player_scoped(self):
        world = self.client.get("/api/worlds/starting-realm").json()
        start = {"map_id": "overworld", "x": 7, "y": 5}
        first = self.client.post("/api/worlds/starting-realm/preview/reset", json={"player": "preview-a", "start": start})
        first.raise_for_status()
        self.assertEqual((first.json()["state"]["map_id"], first.json()["state"]["x"], first.json()["state"]["y"]), (start["map_id"], 7, 5))
        self.client.post("/api/worlds/starting-realm/preview", json={"player": "preview-a", "command": "east"}).raise_for_status()
        other_start = {"map_id": "overworld", "x": 2, "y": 2}
        other = self.client.post("/api/worlds/starting-realm/preview/reset", json={"player": "preview-b", "start": other_start})
        other.raise_for_status()
        reset = self.client.post("/api/worlds/starting-realm/preview/reset", json={"player": "preview-a", "start": start})
        reset.raise_for_status()
        self.assertEqual((reset.json()["state"]["x"], reset.json()["state"]["y"]), (7, 5))
        self.assertEqual(self.client.post("/api/worlds/starting-realm/preview", json={"player": "preview-b", "command": "look"}).json()["state"]["x"], 2)
        blocked = self.client.post("/api/worlds/starting-realm/preview/reset", json={"player": "preview-a", "start": {"map_id": "overworld", "x": 12, "y": 8}})
        self.assertEqual(blocked.status_code, 422)
        self.assertEqual(self.client.get("/api/worlds/starting-realm").json(), world)

    def test_building_preview_uses_shared_cost_resolver_without_saving(self):
        before = self.client.get('/api/worlds/starting-realm').json()
        policy = {'enabled': True, 'tile_cost': 40, 'tile_materials': [{'item':'Trail Rations','count':2}],
                  'tile_cost_overrides': [{'biome':'forest','currency':75,'materials':[{'item':'Trail Rations','count':3}]}]}
        response = self.client.post('/api/worlds/starting-realm/building-preview', json={'building':policy})
        response.raise_for_status()
        prices = {entry['biome']:entry for entry in response.json()['prices']}
        self.assertEqual(prices['grassland']['currency'],40)
        self.assertEqual(prices['grassland']['materials'],[{'item':'Trail Rations','count':2}])
        self.assertEqual(prices['forest']['currency'],75)
        self.assertEqual(prices['forest']['materials'],[{'item':'Trail Rations','count':3}])
        self.assertEqual(self.client.get('/api/worlds/starting-realm').json(), before)

    def test_market_preview_quotes_batches_and_production_without_saving(self):
        before = self.client.get('/api/worlds/starting-realm').json()
        vendor = {
            'id':'preview-market', 'name':'Preview Market', 'location':{'x':7,'y':5},
            'stock':[
                {'item':'Preview Wood','price':10,'sell_price':5,'quantity':4,'pricing':{'mode':'stock_based','base_price':10,'target_stock':4,'sensitivity':1,'min_price':1,'max_price':50,'spread':2}},
                {'item':'Preview Cord','price':6,'sell_price':3,'quantity':0,'production':{'interval_seconds':60,'amount':2,'max_quantity':8,'inputs':[{'item':'Preview Wood','count':2}]}},
            ],
        }
        response = self.client.post('/api/worlds/starting-realm/market-preview', json={'vendor':vendor,'units':3,'cycles':3})
        response.raise_for_status()
        result = response.json()
        wood = next(row for row in result['quotes'] if row['item']=='Preview Wood')
        self.assertEqual([quote['total'] for quote in wood['buy']], [10,22,37])
        cord = result['production'][0]
        self.assertEqual((cord['starting_stock'],cord['ending_stock'],cord['produced']), (0,4,4))
        self.assertEqual(cord['inputs'][0]['remaining_stock'],0)
        self.assertEqual(cord['blocked_by'], ['Preview Wood (0 left; 2 needed per cycle)'])
        self.assertEqual(self.client.get('/api/worlds/starting-realm').json(), before)

    def test_decision_inspector_pause_is_persistent_and_owner_typed(self):
        world=self.client.get('/api/worlds/starting-realm').json()
        world['decision_providers']=[{'id':'test','adapter':'laya_systemone','model':'typed-decisions','endpoint_binding':'TEST_ENDPOINT'}]
        npc=next(e for e in world['npcs'] if e.get('id')=='mira')
        npc['decision_policy']={'enabled':True,'provider_id':'test','goals':[],'actions':['wait'],'observations':['own_state'],'triggers':['player_nearby'],'cooldown_seconds':30,'timeout_seconds':5,'fallback':'wait','memory_limit':5}
        self.client.put('/api/worlds/starting-realm',json=world).raise_for_status()
        changed=self.client.post('/api/worlds/starting-realm/decision-control',json={'owner_type':'npc','owner_id':'mira','action':'pause'})
        changed.raise_for_status(); self.assertTrue(changed.json()['paused'])
        preview=self.client.post('/api/worlds/starting-realm/decision-preview',json={'owner_type':'npc','owner_id':'mira'})
        preview.raise_for_status(); self.assertTrue(preview.json()['paused'])
        resumed=self.client.post('/api/worlds/starting-realm/decision-control',json={'owner_type':'npc','owner_id':'mira','action':'resume'})
        resumed.raise_for_status(); self.assertFalse(resumed.json()['paused'])
        from app.creator_mcp import create_server
        mcp=create_server()
        mcp_pause=mcp._tool_manager.get_tool('pause_decision_controller').fn
        paused=mcp_pause('starting-realm','mira','npc')
        self.assertTrue(paused['paused'])
        audit=mcp._tool_manager.get_tool('decision_audit_log').fn('starting-realm')
        self.assertEqual(audit[0]['operation'],'pause_decision_controller')

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

    def test_creator_mcp_can_create_maps_and_connect_named_entries(self):
        from app.creator_mcp import create_server
        mcp=create_server()
        create_map=mcp._tool_manager.get_tool('upsert_map').fn
        connect=mcp._tool_manager.get_tool('connect_maps').fn
        world=mcp._tool_manager.get_tool('get_world').fn('starting-realm')
        harbor=create_map('starting-realm',world['revision'],'harbor','Harbor',8,8,'grassland',{'x':3,'y':4})
        self.assertIn('harbor',[area['id'] for area in harbor['world']['maps']])
        connected=connect('starting-realm',harbor['revision'],'overworld',7,5,'harbor','harbor','start',True,'return')
        canonical=connected['world']
        source=next(area for area in canonical['maps'] if area['id']=='overworld')
        destination=next(area for area in canonical['maps'] if area['id']=='harbor')
        forward=source['tiles'][5*source['width']+7]['exits']['harbor']
        reverse=destination['tiles'][4*destination['width']+3]['exits']['return']
        self.assertEqual({key:forward[key] for key in ('map_id','x','y')},{'map_id':'harbor','x':3,'y':4})
        self.assertEqual(forward['entry_point'],'start')
        self.assertEqual({key:reverse[key] for key in ('map_id','x','y')},{'map_id':'overworld','x':7,'y':5})
        self.assertEqual(forward['pair_id'],reverse['pair_id'])
        self.assertEqual(forward['return_exit'],'return')
        self.assertEqual(reverse['return_exit'],'harbor')
        self.client.put('/api/worlds/starting-realm',json=canonical).raise_for_status()
        self.client.post('/api/worlds/starting-realm/validate').raise_for_status()
        exported=self.client.get('/api/worlds/starting-realm/export')
        exported.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            built=json.loads(archive.read('world/world.json'))
        harbor=next(area for area in built['maps'] if area['id']=='harbor')
        self.assertEqual(harbor['tiles'][4*harbor['width']+3]['exits']['return']['pair_id'],forward['pair_id'])

    def test_admin_auth_handles_nonascii_and_protects_api(self):
        with patch.dict(os.environ, {"MUDMAKER_ADMIN_PASSWORD": "synthetic-password"}):
            self.assertEqual(self.client.get("/api/worlds").status_code, 401)
            good = base64.b64encode(b"admin:synthetic-password").decode()
            self.assertEqual(self.client.get("/api/worlds", headers={"Authorization": "Basic " + good}).status_code, 200)
            bad = base64.b64encode("管理员:invalid".encode()).decode()
            self.assertEqual(self.client.get("/", headers={"Authorization": "Basic " + bad}).status_code, 401)

    def test_validation_reports_structured_draft_issue_without_saving_it(self):
        world = self.client.get('/api/worlds/starting-realm').json()
        world['quests'][0]['objectives'] = [{'type':'collect','target':'Unobtainium','count':1}]
        response = self.client.post('/api/worlds/starting-realm/validate', json={'draft':world})
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertFalse(report['ready'])
        issue = report['issues'][0]
        self.assertEqual(issue['severity'], 'error')
        self.assertEqual(issue['entity_type'], 'quest')
        self.assertEqual(issue['entity_id'], 'first-survey')
        self.assertEqual(issue['path'], 'quest first-survey.objectives[0].target')
        self.assertEqual(issue['navigation'], {'view':'quests','entity_id':'first-survey','field_path':'objectives[0].target'})
        self.assertIn('Unobtainium', issue['message'])
        saved = self.client.get('/api/worlds/starting-realm').json()
        self.assertEqual(saved['quests'][0]['objectives'][0]['type'], 'discover')

    def test_validation_navigates_to_the_exact_invalid_map_tile(self):
        world = self.client.get('/api/worlds/starting-realm').json()
        world['tiles'][2]['biome'] = 'missing-biome'
        report = self.client.post('/api/worlds/starting-realm/validate', json={'draft':world}).json()
        issue = report['issues'][0]
        self.assertEqual(issue['path'], 'map overworld.tiles[2].biome')
        self.assertEqual(issue['navigation'], {'view':'map','map_id':'overworld','x':2,'y':0})

    def test_validation_collects_independent_unknown_quest_items(self):
        world = self.client.get('/api/worlds/starting-realm').json()
        world['quests'][0]['objectives'] = [{'type':'collect','target':'Moonstone','count':1}]
        world['quests'][1]['objectives'] = [{'type':'deliver','target':'Sunsteel','npc':'mira','count':1}]
        report = self.client.post('/api/worlds/starting-realm/validate', json={'draft':world}).json()
        self.assertFalse(report['ready'])
        issues = {issue['entity_id']:issue for issue in report['issues']}
        self.assertEqual(set(issues), {'first-survey', world['quests'][1]['id']})
        self.assertEqual(issues['first-survey']['navigation']['field_path'], 'objectives[0].target')
        self.assertIn('Moonstone', issues['first-survey']['message'])
        self.assertIn('Sunsteel', issues[world['quests'][1]['id']]['message'])
if __name__ == "__main__":
    unittest.main()
