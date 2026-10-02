import copy
import tempfile
import unittest
from pathlib import Path

from app.main import new_world
from app.templates.content import ContentError, compile_world, merge_scaffold
from app.templates.game_engine import GameEngine


class ContentTests(unittest.TestCase):
    def test_partial_nested_scaffold_fills_blanks_and_preserves_authored_values(self):
        draft = {"id": "", "name": "", "role": "keeper", "location": {"x": 0, "y": None}, "inputs": [{"item": "", "count": 2}], "enabled": False}
        generated = {"id": "keeper", "name": "Lena", "role": "merchant", "location": {"x": 4, "y": 5}, "inputs": [{"item": "Herbs", "count": 5}, {"item": "Water", "count": 1}], "enabled": True}
        result = merge_scaffold(generated, draft)
        self.assertEqual(result["name"], "Lena")
        self.assertEqual(result["role"], "keeper")
        self.assertEqual(result["location"], {"x": 0, "y": 5})
        self.assertEqual(result["inputs"], [{"item": "Herbs", "count": 2}, {"item": "Water", "count": 1}])
        self.assertFalse(result["enabled"])
        self.assertEqual(draft["name"], "")

    def test_custom_entities_become_executable_content(self):
        source = new_world()
        built = compile_world(source)
        self.assertEqual(built["tiles"][5*16+8]["id"], "ancient-grove")
        self.assertEqual(built["tiles"][5*16+8]["biome"], "enchanted-forest")
        self.assertEqual(built["biomes"]["enchanted-forest"]["symbol"], "&")
        self.assertEqual(next(item["name"] for item in built["items"] if item["id"] == "rations"), "Trail Rations")
        self.assertEqual(built["dialogs"][0]["id"], "mira-dialog")
        self.assertEqual(built["interactables"][0]["id"], "shrine")
        self.assertNotIn("entities", built)
        self.assertEqual(compile_world(built), built)
        self.assertIn("entities", source)

    def test_bad_content_fails_before_export(self):
        mutations = [
            lambda w: w["quests"][0].update(objectives=["Do something vague"]),
            lambda w: w["quests"][0].update(objectives=[{"type": "shell", "target": "rm -rf"}]),
            lambda w: w["crafting"][0]["inputs"][0].update(count=-2),
            lambda w: w["economy"]["vendors"][0]["stock"][0].update(sell_price=999),
            lambda w: w["entities"][5]["actions"][0].update(effects=[{"type": "shell", "command": "touch /tmp/bad"}]),
            lambda w: w["quests"][0].update(prerequisites=["trail-supplies"]),
            lambda w: w["entities"][4].update(location={"x": 999, "y": 0}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                world = new_world(); mutation(world)
                with self.assertRaises(ContentError):
                    compile_world(world)

    def test_legacy_survey_objective_migrates(self):
        world = new_world(); world["quests"][0]["objectives"] = ["Discover three distinct locations"]
        self.assertEqual(compile_world(world)["quests"][0]["objectives"], [{"type": "discover", "count": 3}])


class GameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.world = compile_world(new_world())
        self.database = Path(self.temp.name) / "game.sqlite3"
        self.engine = GameEngine(self.world, self.database)

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, command, player="alice"):
        return self.engine.execute(player, command)

    def test_complete_journey_survives_reconnection_without_duplicate_rewards(self):
        self.assertIn("prerequisite", self.execute("quests start trail-supplies"))
        self.assertIn("1.", self.execute("talk mira"))
        self.assertIn("Quest accepted", self.execute("choose 1"))
        self.assertIn("Ancient Grove", self.execute("east"))
        self.assertIn("faint warmth", self.execute("interact shrine pray"))
        self.assertIn("already used", self.execute("interact shrine pray"))
        self.assertIn("Quest complete", self.execute("east"))
        self.execute("west"); self.execute("west")
        self.execute("quests start trail-supplies")
        self.execute("gather Wild Berries 2"); self.execute("gather Herbs")
        self.assertIn("Crafted", self.execute("craft trail-rations"))
        self.assertIn("Quest complete", self.execute("give Trail Rations to mira"))
        state = self.engine.snapshot("alice")
        self.assertEqual(state["currency"], 70)
        self.assertEqual(state["inventory"]["Torch"], 1)
        self.assertEqual(set(state["completed_quests"]), {"first-survey", "trail-supplies"})
        self.engine = GameEngine(self.world, self.database)
        self.execute("look"); self.execute("quests")
        self.assertEqual(self.engine.snapshot("alice")["currency"], 70)
        self.assertIn("already complete", self.execute("quests start first-survey"))

    def test_talk_visit_collect_and_interact_objectives(self):
        source = new_world()
        source["quests"].append({"id": "grove-report", "name": "Grove Report", "giver": "mira", "objectives": [{"type": "talk", "target": "mira"}, {"type": "visit", "target": "ancient-grove"}, {"type": "collect", "target": "Field Notes"}, {"type": "interact", "target": "shrine"}], "reward": {"currency": 7}})
        self.engine = GameEngine(compile_world(source), self.database)
        self.execute("talk mira")  # Events before quest acceptance must not count.
        self.execute("quests start grove-report")
        self.execute("buy Field Notes")
        self.execute("east"); self.execute("interact shrine"); self.execute("west")
        self.assertNotIn("grove-report", self.engine.snapshot("alice")["completed_quests"])
        self.assertIn("Grove Report", self.execute("talk mira"))
        self.assertIn("grove-report", self.engine.snapshot("alice")["completed_quests"])

    def test_shared_economy_depletion_and_player_isolation(self):
        self.assertIn("Bought 5", self.execute("buy Torch 5"))
        before = self.engine.snapshot("bob")
        self.assertIn("only 0", self.execute("buy Torch", "bob"))
        self.assertEqual(self.engine.snapshot("bob"), before)
        self.execute("sell Torch")
        self.assertIn("Bought 1", self.execute("buy Torch", "bob"))
        self.assertEqual(self.engine.snapshot("alice")["inventory"]["Torch"], 4)
        self.assertEqual(self.engine.snapshot("bob")["inventory"]["Torch"], 1)
        self.execute("gather Wild Berries 20")
        self.assertIn("Only 0", self.execute("gather Wild Berries", "bob"))

    def test_failed_crafting_rolls_back_and_duplicate_ingredients_are_aggregated(self):
        self.execute("gather Wild Berries")
        self.execute("gather Herbs")
        self.world["crafting"][0]["inputs"] = [{"item": "Wild Berries", "count": 1}, {"item": "Wild Berries", "count": 1}]
        before = self.engine.snapshot("alice")
        self.assertIn("need 2", self.execute("craft Trail Rations"))
        self.assertEqual(self.engine.snapshot("alice"), before)
        self.execute("gather Wild Berries")
        self.assertIn("Crafted", self.execute("craft Trail Rations"))
        self.assertEqual(self.engine.snapshot("alice")["inventory"]["Wild Berries"], 0)

    def test_station_and_presence_requirements(self):
        self.execute("gather Wild Berries 2"); self.execute("gather Herbs"); self.execute("east")
        self.assertIn("station", self.execute("craft Trail Rations"))
        self.assertIn("No matching", self.execute("talk mira"))

        self.assertIn("quest giver", self.execute("quests start first-survey"))
        self.assertIn("Unknown command", self.execute("touch /tmp/engine-must-not-execute"))


if __name__ == "__main__":
    unittest.main()
