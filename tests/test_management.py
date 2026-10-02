import copy
import json
import os
from unittest.mock import patch
from app.templates.mcp_support import load_token
import tempfile
import time
import unittest
from pathlib import Path

from app import main
from app.creator_mcp import mutate, revision
from app.templates.content import compile_world
from app.templates.game_engine import GameEngine
from app.templates.runtime_management import RuntimeManagement


def fair_event():
    return {'id': 'fair', 'name': 'Harvest Fair', 'entities': [
        {'entity_type': 'npc', 'id': 'fair-herald', 'name': 'Fair Herald', 'location': {'x': 7, 'y': 5}, 'dialog': 'Welcome to the harvest fair.'},
        {'entity_type': 'quest', 'id': 'fair-greeting', 'name': 'Meet the Herald', 'giver': 'fair-herald', 'objectives': [{'type': 'talk', 'target': 'fair-herald'}], 'reward': {'currency': 3}},
        {'entity_type': 'custom_entity', 'id': 'fair-bell', 'name': 'Festival Bell', 'location': {'x': 7, 'y': 5}, 'actions': [{'id': 'ring', 'text': 'The fair bell rings.', 'once': True, 'effects': [{'type': 'currency', 'amount': 2}]}]},
    ]}


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.world = compile_world(main.new_world())
        self.path = Path(self.temp.name) / 'game.sqlite3'
        self.engine = GameEngine(self.world, self.path)
        self.manager = RuntimeManagement(self.engine)
        self.engine.execute('alice', 'look')

    def tearDown(self):
        self.temp.cleanup()

    def test_generated_mcp_token_is_private_and_stable(self):
        path = Path(self.temp.name) / 'mcp-token'
        with patch.dict(os.environ, {'MUD_TEST_MCP_TOKEN': ''}):
            first = load_token('MUD_TEST_MCP_TOKEN', path)
            self.assertGreaterEqual(len(first), 24)
            self.assertEqual(first, load_token('MUD_TEST_MCP_TOKEN', path))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with patch.dict(os.environ, {'MUD_TEST_MCP_TOKEN': 'short'}):
            with self.assertRaises(ValueError):
                load_token('MUD_TEST_MCP_TOKEN', path)

    def test_event_draft_activation_quest_rewards_restart_and_end(self):
        draft = self.manager.draft(fair_event())
        self.assertNotIn('Fair Herald', self.engine.execute('alice', 'look'))
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.manager.activate('fair', 'stale')
        self.manager.activate('fair', draft['revision'])
        self.assertIn('Fair Herald', self.engine.execute('alice', 'look'))
        self.engine.execute('alice', 'quests start fair-greeting')
        self.assertIn('Quest complete', self.engine.execute('alice', 'talk fair-herald'))
        self.engine.execute('alice', 'interact fair-bell ring')
        self.assertEqual(self.engine.snapshot('alice')['currency'], 30)
        engine = GameEngine(self.world, self.path)
        manager = RuntimeManagement(engine)
        self.assertIn('Fair Herald', engine.execute('alice', 'look'))
        self.assertIn('already used', engine.execute('alice', 'interact fair-bell ring'))
        manager.end('fair')
        self.assertNotIn('Fair Herald', engine.execute('alice', 'look'))
        self.assertEqual(engine.snapshot('alice')['currency'], 30)
        with self.assertRaisesRegex(ValueError, 'Ended'):
            manager.activate('fair', draft['revision'])
        self.assertEqual([r['operation'] for r in manager.audit_log()], ['end_event', 'activate_event', 'draft_event'])

    def test_expiry_removes_content_and_active_events_cannot_be_rewritten(self):
        draft = self.manager.draft(fair_event())
        self.manager.activate('fair', draft['revision'], time.time() + 60)
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.manager.draft(fair_event())
        with self.engine.connect() as con:
            con.execute('UPDATE managed_events SET expires_at=?', (time.time() - 1,))
        self.assertNotIn('Fair Herald', self.engine.execute('alice', 'look'))
        self.assertEqual(self.manager.list_events()[0]['status'], 'ended')

    def test_player_adjustments_are_atomic_idempotent_and_audited(self):
        effects = [{'type': 'currency', 'amount': 5}, {'type': 'item', 'item': 'Fair Token', 'count': 2}]
        first = self.manager.adjust_player('alice', 'request-one', effects, {'x': 8, 'y': 5}, 'Event reward')
        self.assertEqual(self.manager.adjust_player('alice', 'request-one', effects, {'x': 8, 'y': 5}, 'Event reward'), first)
        self.assertEqual(self.engine.snapshot('alice')['currency'], 30)
        self.assertIn('8,5', first['state']['discovered'])
        with self.assertRaisesRegex(ValueError, 'different operation'):
            self.manager.adjust_player('alice', 'request-one', [], reason='Different reward')
        with self.assertRaises(ValueError):
            self.manager.adjust_player('alice', 'bad', [{'type': 'shell', 'command': 'whoami'}], reason='Invalid')
        self.assertEqual(self.engine.snapshot('alice')['currency'], 30)
        self.assertEqual(len(self.manager.audit_log()), 1)

    def test_invalid_or_cross_event_content_cannot_be_published(self):
        event = fair_event()
        event['entities'][0]['location']['x'] = 999
        with self.assertRaises(ValueError):
            self.manager.draft(event)
        self.assertEqual(self.manager.list_events(), [])
        first = self.manager.draft(fair_event())
        self.manager.activate('fair', first['revision'])
        other = {'id': 'other', 'name': 'Other', 'entities': [{'entity_type': 'quest', 'id': 'other-quest', 'name': 'Other Quest', 'objectives': [{'type': 'talk', 'target': 'fair-herald'}]}]}
        with self.assertRaises(ValueError):
            self.manager.draft(other)

    def test_map_hides_unexplored_terrain_and_other_players_then_tracks_presence(self):
        online = {'alice', 'bob', 'carol'}
        self.engine.online_players = lambda: tuple(online)
        self.engine.execute('bob', 'east')
        self.engine.execute('carol', 'east')
        hidden = self.engine.execute('alice', 'map')
        rows = hidden.splitlines()[:self.world['height']]
        self.assertEqual(sum(r.count('?') for r in rows), self.world['width'] * self.world['height'] - 1)
        self.assertNotIn('bob', hidden)
        self.assertNotIn('Enchanted', hidden)
        self.engine.execute('alice', 'east')
        self.assertIn('@ (8,5): bob, carol (with you)', self.engine.execute('alice', 'map'))
        self.engine.execute('alice', 'west')
        visible = self.engine.execute('alice', 'map')
        self.assertEqual(visible.splitlines()[5][8], '&')
        self.assertIn('& (8,5): bob, carol', visible)
        online.clear()
        offline = self.engine.execute('alice', 'map')
        self.assertNotIn('bob', offline)
        self.assertNotEqual(offline.splitlines()[5][8], '&')
        reloaded = GameEngine(self.world, self.path)
        self.assertNotEqual(reloaded.execute('alice', 'map').splitlines()[5][8], '?')


if __name__ == '__main__':
    unittest.main()
