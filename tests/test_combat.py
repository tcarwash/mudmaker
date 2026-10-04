import tempfile
import unittest
from pathlib import Path

from app.main import new_world
from app.templates.content import ContentError, compile_world
from app.templates.game_engine import GameEngine


class CombatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.world = compile_world(new_world())
        self.database = Path(self.temp.name) / 'combat.sqlite3'
        self.engine = GameEngine(self.world, self.database)

    def tearDown(self):
        self.temp.cleanup()

    def command(self, player, text):
        return self.engine.execute(player, text)

    def test_combat_reward_and_defeat_quest(self):
        self.command('alice', 'look')
        self.assertIn('1.', self.command('alice', 'talk mira'))
        self.command('alice', 'choose 1')
        self.command('alice', 'east')
        self.command('alice', 'east')
        self.assertIn('15/15 HP', self.command('alice', 'look'))
        self.command('alice', 'west'); self.command('alice', 'west')
        self.assertIn('Quest accepted', self.command('alice', 'quests start moss-wolf'))
        self.command('alice', 'east'); self.command('alice', 'east')
        self.assertIn('10/15 HP', self.command('alice', 'attack moss-wolf'))
        state = self.engine.snapshot('alice')
        self.assertEqual(state['hp'], 93)
        before_enemy = self.engine.connect().execute("SELECT value FROM world_state WHERE key='enemy_hp:moss-wolf'").fetchone()[0]
        self.assertIn('in combat', self.command('alice', 'go west'))
        self.assertIn('in combat', self.command('alice', 'gather herbs'))
        self.assertEqual(self.engine.connect().execute("SELECT value FROM world_state WHERE key='enemy_hp:moss-wolf'").fetchone()[0], before_enemy)
        self.assertIn('5/15 HP', self.command('alice', 'attack'))
        self.assertIn('is defeated', self.command('alice', 'attack'))
        # The final attack both defeats the shared creature and completes the active hunt.
        state = self.engine.snapshot('alice')
        self.assertIn('moss-wolf', state['completed_quests'])
        self.assertEqual(state['experience'], 12)
        self.assertEqual(state['currency'], 58)
        self.assertEqual(state['inventory']['Wolf Fang'], 2)
        self.assertIsNone(state['combat'])
        self.assertIn('already complete', self.command('alice', 'quests start moss-wolf'))
        self.assertIn('XP 12', self.command('alice', 'stats'))

    def test_players_share_creature_health_but_only_killer_gets_rewards(self):
        self.command('alice', 'look')
        self.command('bob', 'east'); self.command('bob', 'east')
        self.command('alice', 'east'); self.command('alice', 'east')
        self.command('alice', 'attack moss-wolf')
        self.assertIn('10/15 HP', self.command('bob', 'look'))
        self.assertIn('5/15 HP', self.command('bob', 'attack moss-wolf'))
        self.assertIn('is defeated', self.command('alice', 'attack moss-wolf'))
        self.assertEqual(self.engine.snapshot('bob')['experience'], 12)
        self.assertEqual(self.engine.snapshot('alice')['experience'], 12)
        self.assertIn('defeated', self.command('alice', 'attack moss-wolf'))
        reloaded = GameEngine(self.world, self.database)
        self.assertIn('is defeated', reloaded.execute('alice', 'attack moss-wolf'))
        self.assertEqual(reloaded.snapshot('alice')['inventory']['Wolf Fang'], 1)
        self.assertEqual(reloaded.snapshot('bob')['inventory']['Wolf Fang'], 1)

    def test_flee_and_knockout_preserve_items_and_gate_other_actions(self):
        self.command('alice', 'look'); self.command('alice', 'east'); self.command('alice', 'east')
        self.command('alice', 'attack moss-wolf')
        self.assertIn('take 3 damage', self.command('alice', 'flee'))
        self.assertEqual(self.engine.snapshot('alice')['hp'], 90)
        self.command('alice', 'attack moss-wolf')
        self.assertIn('in combat', self.command('alice', 'buy Wolf Fang'))
        self.command('alice', 'use anything')
        self.assertEqual(self.engine.snapshot('alice')['combat']['creature'], 'moss-wolf')


    def test_knockout_returns_safely_to_spawn_and_respawn_is_shared(self):
        self.world['creatures'][0]['damage'] = 100
        self.command('alice', 'look'); self.command('alice', 'east'); self.command('alice', 'east')
        self.assertIn('knocked out', self.command('alice', 'attack moss-wolf'))
        state = self.engine.snapshot('alice')
        self.assertEqual((state['x'], state['y'], state['hp']), (7, 5, 50))
        self.assertEqual(state['currency'], 25)
        self.assertEqual(state['inventory'], {})
        self.assertIsNone(state['combat'])
        self.engine.world['creatures'][0]['respawn_seconds'] = 1
        with self.engine.connect() as con:
            con.execute("INSERT INTO world_state VALUES ('enemy_hp:moss-wolf',0) ON CONFLICT(key) DO UPDATE SET value=0")
            con.execute("INSERT INTO world_state VALUES ('enemy_respawn:moss-wolf',1) ON CONFLICT(key) DO UPDATE SET value=1")
        with self.engine.connect() as con:
            self.assertGreater(self.engine.creature_hp(con, self.world['creatures'][0]), 0)

    def test_bad_creature_stats_loot_and_references_fail_before_build(self):
        mutations = [
            lambda w: next(e for e in w['entities'] if e.get('id') == 'moss-wolf').update(max_hp=0),
            lambda w: next(e for e in w['entities'] if e.get('id') == 'moss-wolf').update(damage=-1),
            lambda w: next(e for e in w['entities'] if e.get('id') == 'moss-wolf').update(location={'x':999,'y':5}),
            lambda w: next(e for e in w['entities'] if e.get('id') == 'moss-wolf').update(loot=[{'item':'','count':1}]),
            lambda w: w['quests'][0].update(objectives=[{'type':'defeat','target':'missing'}]),
        ]
        for change in mutations:
            world = new_world(); change(world)
            with self.subTest(change=change), self.assertRaises(ContentError):
                compile_world(world)

    def test_quest_item_references_are_validated_before_build(self):
        mutations = [
            (lambda quest: quest.update(objectives=[{'type': 'collect', 'target': 'Missing Relic', 'count': 1}]), 'unknown item'),
            (lambda quest: quest.update(objectives=[{'type': 'deliver', 'target': 'Missing Relic', 'npc': 'mira', 'count': 1}]), 'unknown item'),
            (lambda quest: quest.update(reward={'currency': 0, 'items': ['Missing Relic']}), 'unknown item'),
            (lambda quest: quest.update(reward={'currency': 0, 'items': [{'item': 'Field Notes', 'count': 0}]}), 'positive integer'),
        ]
        for mutate, message in mutations:
            world = new_world()
            mutate(world['quests'][0])
            with self.subTest(message=message, mutation=mutate), self.assertRaisesRegex(ContentError, message):
                compile_world(world)

    def test_recipe_item_references_are_validated_before_build(self):
        mutations = [
            (lambda recipe: recipe['inputs'][0].update(item='Missing Ingredient'), 'unknown item'),
            (lambda recipe: recipe['inputs'][0].update(count=0), 'positive integer'),
            (lambda recipe: recipe['output'].update(item='Missing Product'), 'unknown item'),
        ]
        for mutate, message in mutations:
            world = new_world()
            mutate(world['crafting'][0])
            with self.subTest(message=message, mutation=mutate), self.assertRaisesRegex(ContentError, message):
                compile_world(world)

    def test_dialogue_item_effect_requires_an_authored_item(self):
        world = new_world()
        dialog = next(entity for entity in world['entities'] if entity.get('entity_type') == 'dialog')
        dialog['nodes']['greeting']['choices'][0]['effects'] = [{'type': 'item', 'item': 'Missing Relic', 'count': 1}]
        with self.assertRaisesRegex(ContentError, 'item effect references unknown item'):
            compile_world(world)


if __name__ == '__main__':
    unittest.main()
