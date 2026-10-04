import copy
import tempfile
import unittest
from pathlib import Path

from app.main import new_world
from app.templates.content import BIOMES, ContentError, compile_world, merge_scaffold
from app.templates.game_engine import GameEngine


class ContentTests(unittest.TestCase):
    def test_legacy_world_migration_is_idempotent_and_preserves_default_spawn(self):
        source = new_world()
        source.pop("schema_version", None)
        source["default_map_id"] = "old-country"
        original_spawn = copy.deepcopy(source["spawn"])
        built = compile_world(source)
        self.assertEqual(built["schema_version"], 1)
        self.assertEqual(built["default_map_id"], "old-country")
        self.assertEqual(built["maps"][0]["spawn"], original_spawn)
        self.assertEqual(built["maps"][0]["entry_points"]["start"], original_spawn)
        self.assertEqual(built["spawn"], original_spawn)
        self.assertEqual(compile_world(built), built)
        self.assertNotIn("maps", source)
        self.assertNotIn("schema_version", source)

    def test_migration_selects_and_preserves_spawn_of_modern_default_map(self):
        source = compile_world(new_world())
        source["schema_version"] = 0
        source["maps"].append({"id":"harbor","name":"Harbor","width":4,"height":4,"tiles":[{"biome":"grassland"} for _ in range(16)],"spawn":{"x":1,"y":1}})
        source["default_map_id"] = "harbor"
        built = compile_world(source)
        self.assertEqual(built["default_map_id"], "harbor")
        self.assertEqual(built["spawn"], {"x":1,"y":1})
        self.assertEqual(built["maps"][1]["spawn"], {"x":1,"y":1})
        self.assertEqual(compile_world(built), built)

    def test_unsupported_world_schema_versions_are_rejected(self):
        source = new_world()
        for version in (2, -1, "1", True):
            with self.subTest(version=version), self.assertRaisesRegex(ContentError, "schema_version"):
                compile_world({**source, "schema_version":version})

    def test_player_web_settings_are_optional_and_validated(self):
        source = new_world()
        self.assertEqual(compile_world(source)["runtime_config"]["player_web"], {"enabled": False, "port": 8080})
        for settings in ({"enabled": "yes", "port": 8080}, {"enabled": True, "port": 0}):
            with self.subTest(settings=settings), self.assertRaisesRegex(ContentError, "runtime_config.player_web"):
                compile_world({**source, "runtime_config": {**source["runtime_config"], "player_web": settings}})

    def test_partial_terrain_rules_inherit_presentation_defaults(self):
        source = new_world()
        source["biomes"] = {ident: {"buildable": False} for ident in BIOMES}
        source["biomes"]["water"]["color"] = "#123456"
        built = compile_world(source)
        for ident, defaults in BIOMES.items():
            self.assertEqual(built["biomes"][ident]["color"], "#123456" if ident == "water" else defaults["color"])
            self.assertEqual(built["biomes"][ident]["symbol"], defaults["symbol"])
            self.assertFalse(built["biomes"][ident]["buildable"])
        self.assertNotIn("color", source["biomes"]["desert"])

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
            lambda w: next(e for e in w["entities"] if e.get("id") == "shrine")["actions"][0].update(effects=[{"type": "shell", "command": "touch /tmp/bad"}]),
            lambda w: w["quests"][0].update(prerequisites=["trail-supplies"]),
            lambda w: next(e for e in w["entities"] if e.get("id") == "ancient-grove").update(location={"x": 999, "y": 0}),
            lambda w: w.setdefault("building", {}).update(structures=[{"id":"Bad Id","name":"Unsafe Workshop","service":{"type":"execute","command":"touch /tmp/bad"}}]),
            lambda w: w.setdefault("building", {}).update(structures=[{"id":"giant","name":"Oversized Hall","footprint":{"width":64,"height":64}}]),
            lambda w: w.setdefault("building", {}).update(paintable_biomes=["missing-terrain"]),
            lambda w: w["npcs"][0].update(decision_policy={"routes":[{"id":"lost","map_id":"missing-map","location":{"x":0,"y":0}}]}),
            lambda w: w["economy"]["vendors"][0]["stock"][0].update(restock={"interval_seconds":0,"amount":2}),
            lambda w: w["economy"]["vendors"][0]["stock"][0].update(production={"interval_seconds":60,"amount":1,"max_quantity":20,"inputs":[{"item":"Unknown","count":1}]}),
            lambda w: w["economy"]["vendors"][0]["stock"][0].update(consumption={"interval_seconds":0,"amount":1}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                world = new_world(); mutation(world)
                with self.assertRaises(ContentError):
                    compile_world(world)

    def test_reciprocal_exits_require_a_matching_pair(self):
        world = compile_world(new_world())
        source = world['maps'][0]
        destination = {
            'id':'test-area','name':'Test Area','width':4,'height':4,
            'spawn':{'x':0,'y':0},'entry_points':{'start':{'x':0,'y':0}},
            'tiles':[{'biome':'grassland','name':'Grassland','description':'A quiet stretch of grassland.'} for _ in range(16)],
        }
        world['maps'].append(destination)
        source['tiles'][0]['exits']={'enter-test':{'map_id':'test-area','x':0,'y':0,'pair_id':'pair-fixture','return_exit':'return-test'}}
        destination['tiles'][0]['exits']={'return-test':{'map_id':source['id'],'x':0,'y':0,'pair_id':'pair-fixture','return_exit':'enter-test'}}
        compiled = compile_world(world)
        self.assertEqual(compiled['maps'][0]['tiles'][0]['exits']['enter-test']['pair_id'],'pair-fixture')
        broken = copy.deepcopy(world)
        del broken['maps'][1]['tiles'][0]['exits']['return-test']
        with self.assertRaisesRegex(ContentError,'expected exactly two matching exits'):
            compile_world(broken)
        broken = copy.deepcopy(world)
        broken['maps'][1]['tiles'][0]['exits']['return-test']['x'] = 1
        with self.assertRaisesRegex(ContentError,'does not point back to its source'):
            compile_world(broken)

    def test_named_arrival_must_resolve_and_be_passable(self):
        world = compile_world(new_world())
        destination = {'id':'arrival-test','name':'Arrival Test','width':4,'height':4,'spawn':{'x':0,'y':0},'entry_points':{'dock':{'x':2,'y':2}},'tiles':[{'biome':'grassland'} for _ in range(16)]}
        world['maps'].append(destination)
        world['maps'][0]['tiles'][0]['exits']={'dock':{'map_id':'arrival-test','x':0,'y':0,'entry_point':'dock'}}
        compile_world(world)
        broken = copy.deepcopy(world)
        broken['maps'][0]['tiles'][0]['exits']['dock']['entry_point']='missing'
        with self.assertRaisesRegex(ContentError, 'unknown entry point'):
            compile_world(broken)

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

    def test_named_arrival_uses_current_entry_coordinates(self):
        world = copy.deepcopy(self.world)
        destination = {'id':'arrival-test','name':'Arrival Test','width':4,'height':4,'spawn':{'x':0,'y':0},'entry_points':{'dock':{'x':3,'y':2}},'tiles':[{'biome':'grassland'} for _ in range(16)]}
        world['maps'].append(destination)
        source=world['maps'][0];source['tiles'][source['spawn']['y']*source['width']+source['spawn']['x']]['exits']={'dock':{'map_id':'arrival-test','x':2,'y':2,'entry_point':'dock'}}
        self.engine = GameEngine(compile_world(world), self.database)
        self.execute('go dock')
        state=self.engine.snapshot('alice')
        self.assertEqual((state['map_id'],state['x'],state['y']),('arrival-test',3,2))

    def test_look_uses_terrain_title_when_tile_title_is_blank(self):
        tile = self.engine.tile(self.engine.snapshot("alice"))
        tile["name"] = ""
        tile["description"] = ""
        terrain = self.world["biomes"][tile["biome"]]
        self.assertIn(terrain["name"], self.execute("look"))
        self.assertIn(terrain["description"], self.execute("look"))

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
