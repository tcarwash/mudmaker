import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import new_world
from app.templates.content import compile_world
from app.templates.game_engine import GameEngine
from app.templates import player_web


class PlayerWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "game.sqlite3"
        self.db_patch = patch.object(player_web, "DB_PATH", self.db)
        self.db_patch.start()
        world = new_world()
        world["ssh_public_keys"] = [{"username": "alice", "public_key": "ssh-ed25519 AAAA test"}, {"username": "bob", "public_key": "ssh-ed25519 BBBB test"}]
        self.engine = GameEngine(compile_world(world), self.db)
        self.sessions = {}

        async def dispatch(username, command, session):
            session.stdout.write(self.engine.execute(username, command))

        app = player_web.create_player_web(self.engine, self.sessions, world["ssh_public_keys"], dispatch, "app/templates/player_client")
        self.client = TestClient(app, base_url="https://game.test")

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_invitation_bootstraps_persistent_password_and_shared_engine_session(self):
        token = player_web.issue_invitation("alice", [{"username": "alice"}])
        response = self.client.post("/api/login", json={"username": "alice", "password": "a sufficiently long passphrase", "invitation": token})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Welcome to", "\n".join(response.json()["messages"]))
        self.assertIn("alice", self.sessions)
        action_commands = {action["command"] for action in response.json()["actions"]}
        self.assertIn("talk mira", action_commands)
        self.assertIn("quests start first-survey", action_commands)
        self.assertTrue(self.client.cookies.get(player_web.COOKIE))

        command = self.client.post("/api/command", json={"command": "inventory"})
        self.assertEqual(command.status_code, 200, command.text)
        self.assertTrue(command.json()["messages"])
        self.assertEqual(command.json()["state"]["currency"], self.engine.snapshot("alice")["currency"])
        start_x = command.json()["state"]["x"]
        moved = self.client.post("/api/command", json={"command": "east"})
        self.assertEqual(moved.json()["state"]["x"], start_x + 1)

        self.engine.queue_player_message("alice", "A delayed world event arrived.")
        events = self.client.get("/api/events")
        self.assertIn("A delayed world event arrived.", events.json()["messages"])
        self.assertNotIn("A delayed world event arrived.", self.client.get("/api/events").json()["messages"])

        self.client.post("/api/logout")
        self.assertNotIn("alice", self.sessions)
        login = self.client.post("/api/login", json={"username": "alice", "password": "a sufficiently long passphrase"})
        self.assertEqual(login.status_code, 200, login.text)
        reused = self.client.post("/api/login", json={"username": "alice", "password": "a sufficiently long passphrase", "invitation": token})
        self.assertEqual(reused.status_code, 409)

    def test_installable_shell_has_manifest_and_root_scoped_static_only_worker(self):
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json()["display"], "standalone")
        worker = self.client.get("/service-worker.js")
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker.headers["service-worker-allowed"], "/")
        self.assertIn("url.pathname.startsWith('/api/')", worker.text)
        self.assertEqual(self.client.get("/assets/icon.svg").status_code, 200)

    def test_invitation_is_bound_to_username_and_consumed_once(self):
        token = player_web.issue_invitation("alice", [{"username": "alice"}])
        wrong_player = self.client.post("/api/login", json={"username": "bob", "password": "a sufficiently long passphrase", "invitation": token})
        self.assertEqual(wrong_player.status_code, 401)
        first = self.client.post("/api/login", json={"username": "alice", "password": "a sufficiently long passphrase", "invitation": token})
        self.assertEqual(first.status_code, 200, first.text)
        self.client.post("/api/logout")
        cannot_reuse = self.client.post("/api/login", json={"username": "bob", "password": "another long passphrase", "invitation": token})
        self.assertEqual(cannot_reuse.status_code, 401)

    def test_active_character_cannot_be_claimed_twice(self):
        self.sessions["alice"] = object()
        response = self.client.post("/api/login", json={"username": "alice", "password": "a sufficiently long passphrase", "invitation": "unused"})
        self.assertEqual(response.status_code, 409)
        player_web.reset_browser_account("never-signed-in")

    def test_web_only_player_does_not_need_an_ssh_key(self):
        token = player_web.issue_invitation("browserguest")
        response = self.client.post("/api/login", json={"username": "browserguest", "password": "a sufficiently long passphrase", "invitation": token})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("browserguest", self.sessions)
        self.assertEqual(response.json()["state"]["currency"], self.engine.snapshot("browserguest")["currency"])

    def test_resetting_password_revokes_existing_browser_cookie(self):
        token = player_web.issue_invitation("alice")
        logged_in = self.client.post("/api/login", json={"username": "alice", "password": "a sufficiently long passphrase", "invitation": token})
        cookie = logged_in.headers["set-cookie"].split(";", 1)[0]
        player_web.reset_browser_account("alice")
        self.assertEqual(self.client.get("/api/session", headers={"Cookie": cookie}).status_code, 401)

    def test_login_attempts_are_throttled_and_operator_reset_clears_lockout(self):
        invitation = player_web.issue_invitation("alice")
        success = self.client.post("/api/login", json={"username": "alice", "password": "a sufficiently long passphrase", "invitation": invitation})
        self.assertEqual(success.status_code, 200)
        self.client.post("/api/logout")
        for _ in range(5):
            failed = self.client.post("/api/login", json={"username": "alice", "password": "a different wrong password"})
            self.assertEqual(failed.status_code, 401)
        throttled = self.client.post("/api/login", json={"username": "alice", "password": "a sufficiently long passphrase"})
        self.assertEqual(throttled.status_code, 429)
        player_web.reset_browser_account("alice")
        self.assertEqual(player_web.login_throttle_remaining("alice"), 0)


if __name__ == "__main__":
    unittest.main()
