import json
import asyncio
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from pathlib import Path

from app.main import new_world
from app.templates.content import compile_world
from app.templates.decision_runtime import DecisionProviderError, choose
from app.templates.game_engine import GameEngine


class SystemOneHandler(BaseHTTPRequestHandler):
    response = {"answers": {"action": {"choice": "greet"}}, "model": "fixture-model"}
    requests = []
    status = 200

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).requests.append((self.path, dict(self.headers), json.loads(body)))
        payload = json.dumps(type(self).response).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


class DecisionRuntimeProtocolTests(unittest.TestCase):
    def setUp(self):
        SystemOneHandler.requests = []
        SystemOneHandler.status = 200
        SystemOneHandler.response = {"answers": {"action": {"choice": "greet"}}, "model": "fixture-model"}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), SystemOneHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/v1/systemone"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def profile(self, credential=True):
        profile = {"model": "typed-decisions", "endpoint_binding": "MUD_DECISION_TEST_ENDPOINT"}
        if credential:
            profile["credential_binding"] = "MUD_DECISION_TEST_KEY"
        return profile

    def env(self, key="fixture-secret"):
        values = {"MUD_DECISION_TEST_ENDPOINT": self.endpoint}
        if key is not None:
            values["MUD_DECISION_TEST_KEY"] = key
        return patch.dict("os.environ", values, clear=False)

    def test_posts_documented_systemone_payload_and_parses_named_choice(self):
        state = {"npc": {"id": "mira"}, "location": {"map_id": "overworld", "x": 2, "y": 3}}
        criteria = {"wait": "Wait safely", "greet": "Greet the nearby player"}
        with self.env():
            choice, metadata = choose(self.profile(), state, criteria, 2)
        self.assertEqual(choice, "greet")
        self.assertEqual(metadata, {"model": "fixture-model"})
        path, headers, request = SystemOneHandler.requests[0]
        self.assertEqual(path, "/v1/systemone")
        self.assertEqual(headers["Authorization"], "Bearer fixture-secret")
        self.assertEqual(request, {
            "model": "typed-decisions",
            "state": state,
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": "Choose the single action that best advances the authored goals while respecting the supplied game state. Choose wait when no action is clearly useful.",
                    "criteria": criteria,
                }
            },
        })

    def test_rejects_unknown_choice_without_executing_it(self):
        SystemOneHandler.response = {"answers": {"action": {"choice": "mint-unbounded-currency"}}}
        with self.env(), self.assertRaisesRegex(DecisionProviderError, "unrecognized typed choice"):
            choose(self.profile(), {"npc": "mira"}, {"wait": "Wait", "greet": "Greet"}, 2)

    def test_rejects_missing_question_answer(self):
        SystemOneHandler.response = {"answers": {"other": {"choice": "greet"}}}
        with self.env(), self.assertRaisesRegex(DecisionProviderError, "missing or unrecognized"):
            choose(self.profile(), {}, {"wait": "Wait", "greet": "Greet"}, 2)

    def test_missing_credential_binding_fails_before_http_request(self):
        with self.env(key=None), self.assertRaisesRegex(DecisionProviderError, "credential binding is not configured"):
            choose(self.profile(), {}, {"wait": "Wait", "greet": "Greet"}, 2)
        self.assertEqual(SystemOneHandler.requests, [])

    def test_reports_authentication_rejection_without_exposing_response_body(self):
        SystemOneHandler.status=401
        with self.env(), self.assertRaisesRegex(DecisionProviderError,"HTTP status 401") as raised:
            choose(self.profile(),{}, {"wait":"Wait","greet":"Greet"},2)
        self.assertNotIn("fixture-secret",str(raised.exception))

    def test_controller_records_fallback_for_an_invalid_live_protocol_choice(self):
        SystemOneHandler.response = {"answers": {"action": {"choice": "grant-free-stock"}}}
        source=new_world()
        source["decision_providers"]=[{"id":"fixture","adapter":"laya_systemone","model":"typed-decisions","endpoint_binding":"MUD_DECISION_TEST_ENDPOINT","credential_binding":"MUD_DECISION_TEST_KEY"}]
        vendor=source["economy"]["vendors"][0]
        vendor["decision_policy"]={"enabled":True,"provider_id":"fixture","goals":[{"id":"reserve","description":"Keep Field Notes available","metric":"stock_level","item":"Field Notes","target":10}],"actions":["market_stock"],"observations":["own_state"],"triggers":["player_nearby"],"cooldown_seconds":0,"timeout_seconds":3,"fallback":"wait","memory_limit":5}
        world=compile_world(source)
        with tempfile.TemporaryDirectory() as directory:
            engine=GameEngine(world,Path(directory)/"decision.sqlite3")
            engine.execute("alice","look")
            player=engine.snapshot("alice"); player.update(map_id=world["default_map_id"],x=vendor["location"]["x"],y=vendor["location"]["y"])
            with self.env():
                asyncio.run(engine._run_market_decision("alice",vendor,player,lambda _message:None,force=True))
            with engine.connect() as con:
                owner=engine._market_decision_state(con,vendor["id"])
                trace=con.execute("SELECT status,selected,outcome FROM decision_traces ORDER BY created_at DESC LIMIT 1").fetchone()
            self.assertFalse(owner.get("paused_offers",{}).get("Field Notes",False))
            self.assertEqual(trace[0],"fallback")
            self.assertEqual(trace[1],"wait")
            self.assertIn("unrecognized typed choice",trace[2])

    def test_first_decision_can_arrive_at_npcs_authored_start_location(self):
        SystemOneHandler.response = {"answers": {"action": {"choice": "arrive-crossroads"}}, "model": "fixture-model"}
        source = new_world()
        source["decision_providers"] = [{
            "id": "fixture", "adapter": "laya_systemone", "model": "typed-decisions",
            "endpoint_binding": "MUD_DECISION_TEST_ENDPOINT", "credential_binding": "MUD_DECISION_TEST_KEY",
        }]
        npc = source["npcs"][0]
        npc["decision_policy"] = {
            "enabled": True, "provider_id": "fixture", "goals": [], "actions": ["move"],
            "observations": ["own_state"], "triggers": ["player_nearby"],
            "routes": [{"id": "crossroads", "name": "Crossroads", "location": {"x": 7, "y": 5}}],
            "cooldown_seconds": 0, "timeout_seconds": 3, "fallback": "wait", "memory_limit": 5,
        }
        world = compile_world(source)
        with tempfile.TemporaryDirectory() as directory:
            engine = GameEngine(world, Path(directory) / "decision.sqlite3")
            engine.execute("alice", "look")
            player = engine.snapshot("alice")
            with self.env():
                asyncio.run(engine._run_npc_decision("alice", npc, player, lambda _message: None, force=True))
            with engine.connect() as con:
                owner = engine._decision_state(con, npc["id"])
                trace = con.execute("SELECT status,selected,outcome FROM decision_traces ORDER BY created_at DESC LIMIT 1").fetchone()
        self.assertEqual(trace[0], "selected")
        self.assertEqual(trace[1], "arrive-crossroads")
        self.assertIn("arrived at Crossroads", trace[2])
        self.assertEqual(owner["location"], {"x": 7, "y": 5})
        self.assertEqual(owner["route_visits"], 1)


if __name__ == "__main__":
    unittest.main()
