import tempfile
import unittest
from pathlib import Path

from app.main import new_world
from app.templates.content import compile_world
from app.templates.game_engine import GameEngine


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.world = compile_world(new_world())
        self.path = Path(self.temp.name) / 'game.sqlite3'
        self.engine = GameEngine(self.world, self.path)

    def tearDown(self):
        self.temp.cleanup()

    def run_command(self, command, user='alice'):
        return self.engine.execute(user, command)

    def test_journal_notes_track_only_discovered_places_and_persist(self):
        self.assertNotIn('Ancient Grove', self.run_command('journal'))
        self.assertIn('note saved', self.run_command('note Meet friends by the campfire'))
        self.assertIn('Meet friends', self.run_command('journal'))
        self.assertIn('Accept that quest', self.run_command('track first-survey'))
        self.run_command('quests start first-survey')
        self.assertIn('Tracking:', self.run_command('track first-survey'))
        self.run_command('east')
        self.assertIn('discover locations: 2/3', self.run_command('look'))
        self.assertIn('Ancient Grove', self.run_command('journal'))
        reloaded = GameEngine(self.world, self.path)
        self.assertIn('Meet friends', reloaded.execute('alice', 'journal'))
        self.assertEqual(reloaded.snapshot('alice')['tracked_quest'], 'first-survey')
        self.assertIn('Quest complete', reloaded.execute('alice', 'east'))
        self.assertIsNone(reloaded.snapshot('alice')['tracked_quest'])
        self.assertIn('[locked] Trail Supplies', self.run_command('quests', 'bob'))

    def test_batch_crafting_is_atomic_and_recipe_book_shows_requirements(self):
        before = self.engine.snapshot('alice')
        self.assertIn('Wild Berries 0/2', self.run_command('recipes trail-rations'))
        self.assertIn('need 6 Wild Berries', self.run_command('craft trail-rations 3'))
        self.assertEqual(before['inventory'], self.engine.snapshot('alice')['inventory'])
        self.run_command('gather Wild Berries 6')
        self.run_command('gather Herbs 3')
        self.assertIn('Can craft here: 3', self.run_command('recipes'))
        self.assertIn('Crafted 3 Trail Rations', self.run_command('craft trail-rations 3'))
        state = self.engine.snapshot('alice')
        self.assertEqual(state['inventory']['Trail Rations'], 3)
        self.assertEqual(state['events']['craft:trail-rations'], 3)
        self.assertEqual(state['inventory']['Wild Berries'], 0)
        self.run_command('east')
        self.assertIn('visit the station', self.run_command('recipes'))
        self.assertIn('requires the campfire', self.run_command('craft trail-rations 2'))

    def test_ground_items_are_shared_local_persistent_and_cannot_be_duplicated(self):
        self.run_command('gather Herbs 3')
        self.assertIn('Dropped 2 Herbs', self.run_command('drop Herbs 2'))
        self.assertIn('On the ground: Herbs x2', self.run_command('look', 'bob'))
        self.run_command('east', 'bob')
        self.assertIn('No matching', self.run_command('take Herbs', 'bob'))
        self.run_command('west', 'bob')
        self.assertIn('Only 2', self.run_command('take Herbs 3', 'bob'))
        reloaded = GameEngine(self.world, self.path)
        self.assertIn('Took 2 Herbs', reloaded.execute('bob', 'take Herbs 2'))
        self.assertIn('No matching', self.run_command('take Herbs'))
        self.assertEqual(reloaded.snapshot('bob')['inventory']['Herbs'], 2)
        self.assertEqual(self.engine.snapshot('alice')['inventory']['Herbs'], 1)
        self.assertNotIn('On the ground', self.run_command('look'))

    def test_rest_requires_a_resting_place_and_exit_list_omits_water(self):
        import json
        self.run_command('look')
        state = self.engine.snapshot('alice'); state['hp'] = 40
        with self.engine.connect() as con:
            con.execute('UPDATE players SET state=? WHERE username=?', (json.dumps(state), 'alice'))
        self.assertIn('recover 60 HP', self.run_command('rest'))
        self.assertIn('fully rested', self.run_command('rest'))
        self.run_command('east')
        self.assertIn('Find a campfire', self.run_command('rest'))
        self.world['tiles'][5*16+9]['biome'] = 'water'
        self.assertNotIn('east', self.run_command('exits'))
        self.assertIn('west', self.run_command('exits'))


if __name__ == '__main__':
    unittest.main()
