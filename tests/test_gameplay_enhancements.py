import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import new_world
from app.templates.content import ContentError, compile_world
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


class MarketDeliveryContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        source = new_world()
        origin = source['economy']['vendors'][0]
        source = compile_world(source)
        harbor = copy.deepcopy(source['maps'][0])
        harbor.update(id='harbor', name='Harbor', spawn={'x': 1, 'y': 1}, entry_points={'dock': {'x': 1, 'y': 1}})
        source['maps'].append(harbor)
        source['maps'][0]['tiles'][5 * source['maps'][0]['width'] + 7].setdefault('exits', {})['harbor'] = {'map_id': 'harbor', 'x': 1, 'y': 1}
        origin = next(v for v in source['economy']['vendors'] if v['id'] == 'mira-cart')
        origin['contracts'] = [{
            'id': 'field-notes-run', 'name': 'Field Notes Run', 'item': 'Field Notes',
            'count': 2, 'destination_market_id': 'harbor-market', 'reward_currency': 30,
            'deadline_seconds': 3600,
        }]
        source['economy']['vendors'].append({
            'id': 'harbor-market', 'name': 'Harbor Market', 'map_id': 'harbor',
            'location': {'x': 2, 'y': 1}, 'stock': [{'item': 'Fish', 'price': 3, 'quantity': 4}],
        })
        self.world = compile_world(source)
        self.path = Path(self.temp.name) / 'market-contracts.sqlite3'
        self.engine = GameEngine(self.world, self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_market_contract_draws_finite_cargo_persists_and_pays_once_across_maps(self):
        self.assertIn('Field Notes Run', self.engine.execute('alice', 'contracts'))
        before = self.engine.snapshot('alice')
        self.assertIn('Accepted Field Notes Run', self.engine.execute('alice', 'contract accept field-notes-run'))
        state = self.engine.snapshot('alice')
        self.assertEqual(state['inventory']['Field Notes'], 2)
        self.assertEqual(state['active_contracts']['field-notes-run']['destination_market_id'], 'harbor-market')
        origin = next(v for v in self.engine.world['economy']['vendors'] if v['id'] == 'mira-cart')
        with self.engine.connect() as con:
            stock = con.execute("SELECT value FROM world_state WHERE key='stock:mira-cart:Field Notes'").fetchone()[0]
        self.assertEqual(stock, 8)
        self.engine = GameEngine(self.world, self.path)
        self.assertIn('field-notes-run', self.engine.execute('alice', 'contracts'))
        self.engine.execute('alice', 'go harbor')
        self.engine.execute('alice', 'east')
        self.assertIn('Contract complete: Field Notes Run', self.engine.execute('alice', 'contract deliver field-notes-run'))
        after = self.engine.snapshot('alice')
        self.assertEqual(after['inventory'].get('Field Notes', 0), 0)
        self.assertEqual(after['currency'], before['currency'] + 30)
        self.assertIn('field-notes-run', after['completed_contracts'])
        self.assertIn('already completed', self.engine.execute('alice', 'contract accept field-notes-run'))
        self.assertEqual(self.engine.snapshot('alice')['currency'], before['currency'] + 30)

    def test_expired_delivery_retains_cargo_and_never_pays(self):
        now = [1000]
        self.engine.clock = lambda: now[0]
        self.engine.execute('alice', 'contract accept field-notes-run')
        now[0] += 3601
        self.assertIn('expired', self.engine.execute('alice', 'contracts'))
        self.assertEqual(self.engine.snapshot('alice')['inventory']['Field Notes'], 2)
        self.engine.execute('alice', 'go harbor')
        self.engine.execute('alice', 'east')
        self.assertIn('not active', self.engine.execute('alice', 'contract deliver field-notes-run'))
        self.assertEqual(self.engine.snapshot('alice')['currency'], self.world['starting_currency'])

    def test_contract_validation_rejects_missing_destination_and_prerequisite_cycle(self):
        invalid = copy.deepcopy(self.world)
        invalid['economy']['vendors'][0]['contracts'][0]['destination_market_id'] = 'missing'
        with self.assertRaisesRegex(ContentError, 'different authored market'):
            compile_world(invalid)
        cyclic = copy.deepcopy(self.world)
        first = cyclic['economy']['vendors'][0]['contracts'][0]
        first['prerequisites'] = ['second-run']
        second = copy.deepcopy(first)
        second.update(id='second-run', name='Second run', prerequisites=['field-notes-run'])
        cyclic['economy']['vendors'][0]['contracts'].append(second)
        with self.assertRaisesRegex(ContentError, 'cycle'):
            compile_world(cyclic)


if __name__ == '__main__':
    unittest.main()


class NPCDecisionRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        world=compile_world(new_world())
        harbor=copy.deepcopy(world['maps'][0]); harbor.update(id='harbor',name='Harbor',spawn={'x':1,'y':1},entry_points={'dock':{'x':1,'y':1}})
        world['maps'].append(harbor)
        world['maps'][0]['tiles'][5*world['width']+7].setdefault('exits',{})['harbor']={'map_id':'harbor','x':1,'y':1}
        npc=world['npcs'][0]
        npc['decision_policy']={'enabled':True,'provider_id':'route-test','goals':[{'id':'visit-market','description':'Visit the harbor market','metric':'route_visits','target':1}],'actions':['move'],'observations':['own_state'],'triggers':['scheduled','player_nearby'],'active_radius':8,'cooldown_seconds':0,'timeout_seconds':2,'fallback':'wait','memory_limit':5,'routes':[{'id':'harbor-market','name':'Harbor Market','map_id':'harbor','location':{'x':3,'y':1}}]}
        world['decision_providers']=[{'id':'route-test','adapter':'laya_systemone','model':'typed-decisions','endpoint_binding':'TEST_ROUTE_ENDPOINT'}]
        self.world=compile_world(world)
        self.path=Path(self.temp.name)/'npc-routes.sqlite3'
        self.engine=GameEngine(self.world,self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_npc_routes_through_connections_persist_and_complete_authored_destination(self):
        npc=self.world['npcs'][0]; messages=[]
        def choose_route(profile,observation,criteria,timeout):
            route=next((candidate for candidate in criteria if candidate in ('route-harbor-market','continue-harbor-market','arrive-harbor-market')),None)
            return route or 'wait',{}
        async def decision(engine):
            await engine._run_npc_decision('alice',npc,engine.snapshot('alice'),messages.append,force=True,trigger='scheduled')
        with patch('app.templates.game_engine.choose_decision',side_effect=choose_route):
            asyncio.run(decision(self.engine))
            owner=self.engine.npc_runtime_state[npc['id']]
            self.assertEqual(owner['map_id'],'harbor')
            self.assertEqual(owner['location'],{'x':1,'y':1})
            self.assertEqual(owner['route_target']['id'],'harbor-market')
            self.engine=GameEngine(self.world,self.path)
            self.engine.execute('alice','go harbor')
            asyncio.run(decision(self.engine))
            self.assertEqual(self.engine.npc_runtime_state[npc['id']]['location'],{'x':2,'y':1})
            self.engine.execute('alice','east')
            asyncio.run(decision(self.engine))
        owner=self.engine.npc_runtime_state[npc['id']]
        self.assertEqual(owner['map_id'],'harbor')
        self.assertEqual(owner['location'],{'x':3,'y':1})
        self.assertIsNone(owner['route_target'])
        self.assertEqual(owner['route_visits'],1)
        self.assertTrue(any('arrived at Harbor Market' in message for message in messages))


class NPCTaskDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        source=new_world(); world=compile_world(source)
        npc=world['npcs'][0]; npc['location']=dict(world['spawn'])
        npc['decision_policy']={'enabled':True,'provider_id':'task-test','goals':[{'id':'deliver','description':'Complete the supply handoff','metric':'task_progress','target':1}],'actions':['task'],'observations':['own_state'],'triggers':['player_nearby'],'cooldown_seconds':0,'timeout_seconds':2,'fallback':'wait','memory_limit':5,'tasks':[{'id':'supply-handoff','name':'Supply handoff','description':'Confirm the prepared supplies with this traveler.','steps':2,'effects':[{'type':'currency','amount':7}],'once':True}]}
        world['decision_providers']=[{'id':'task-test','adapter':'laya_systemone','model':'typed-decisions','endpoint_binding':'TEST_TASK_ENDPOINT'}]
        self.world=compile_world(world); self.engine=GameEngine(self.world,Path(self.temp.name)/'npc-tasks.sqlite3')

    def tearDown(self): self.temp.cleanup()

    def test_authored_task_progress_and_completion_effect_are_atomic_and_persistent(self):
        self.engine.execute('alice','look'); npc=self.world['npcs'][0]; messages=[]
        def choose_task(profile,observation,criteria,timeout):
            choice=next((key for key in criteria if key.startswith('start-task-') or key.startswith('advance-task-')),None)
            return choice or 'wait',{}
        async def step(): await self.engine._run_npc_decision('alice',npc,self.engine.snapshot('alice'),messages.append,force=True,trigger='player_nearby')
        before=self.engine.snapshot('alice')['currency']
        with patch('app.templates.game_engine.choose_decision',side_effect=choose_task):
            asyncio.run(step())
            owner=self.engine.npc_runtime_state[npc['id']]
            self.assertEqual(owner['active_task']['assigned_to'],'alice')
            asyncio.run(step())
            self.assertEqual(self.engine.npc_runtime_state[npc['id']]['active_task']['progress'],1)
            self.engine=GameEngine(self.world,Path(self.temp.name)/'npc-tasks.sqlite3')
            self.assertEqual(self.engine.npc_runtime_state[npc['id']]['active_task']['progress'],1)
            asyncio.run(step())
        after=self.engine.snapshot('alice')
        self.assertEqual(after['currency'],before+7)
        self.assertTrue(after['flags'][f"npc-task:{npc['id']}:supply-handoff"])
        self.assertIsNone(self.engine.npc_runtime_state[npc['id']]['active_task'])
        self.assertTrue(any('Completed task Supply handoff' in message for message in messages))
        reloaded=GameEngine(self.world,Path(self.temp.name)/'npc-tasks.sqlite3')
        self.assertFalse(any(c['type']=='task' for c in reloaded.decision_preview('alice',npc['id'])['candidates']))

    def test_delivery_task_requires_real_goods_and_pays_once(self):
        source=copy.deepcopy(self.world); source['starting_inventory']={'Herbs':2}
        self.world=source
        npc=self.world['npcs'][0]
        npc['decision_policy']['goals']=[{'id':'deliver-herbs','description':'Complete the herb bundle','metric':'deliveries_completed','target':1}]
        npc['decision_policy']['tasks']=[{'id':'herb-bundle','name':'Herb bundle','description':'Bring herbs for the camp','steps':1,'delivery':{'item':'Herbs','count':2},'effects':[{'type':'currency','amount':10}],'once':True}]
        self.world=compile_world(self.world)
        self.engine=GameEngine(self.world,Path(self.temp.name)/'npc-tasks.sqlite3')
        self.engine.execute('alice','look'); self.engine.execute('bob','look')
        self.assertTrue(any(c['id']=='start-task-herb-bundle' and 'give Herbs 2' in c['description'] for c in self.engine.decision_preview('alice',npc['id'])['candidates']))
        self.assertEqual(self.engine.decision_preview('alice',npc['id'])['observation']['owner']['resources']['deliveries_completed'],0)
        messages=[]
        async def start():
            def choose(profile,observation,criteria,timeout): return 'start-task-herb-bundle',{}
            with patch('app.templates.game_engine.choose_decision',side_effect=choose):
                await self.engine._run_npc_decision('alice',npc,self.engine.snapshot('alice'),messages.append,force=True,trigger='player_nearby')
        asyncio.run(start())
        active=self.engine.npc_runtime_state[npc['id']]['active_task']
        self.assertEqual(active['assigned_to'],'alice'); self.assertEqual(active['delivery'],{'item':'Herbs','count':2})
        self.assertEqual(self.engine.snapshot('bob')['inventory']['Herbs'],2)
        denied=self.engine.execute('bob','give Herbs 2 to Mira')
        self.assertIn('assigned to another player',denied)
        self.assertEqual(self.engine.snapshot('bob')['inventory']['Herbs'],2)
        alice_before=self.engine.snapshot('alice')['currency']
        wrong=self.engine.execute('alice','give Herbs to Mira')
        self.assertIn('exactly 2 Herbs',wrong)
        self.assertEqual(self.engine.snapshot('alice')['inventory']['Herbs'],2)
        completed=self.engine.execute('alice','give Herbs 2 to Mira')
        self.assertIn('Completed Herb bundle',completed)
        self.assertEqual(self.engine.snapshot('alice')['currency'],alice_before+10)
        self.assertEqual(self.engine.snapshot('alice')['inventory']['Herbs'],0)
        owner=self.engine.npc_runtime_state[npc['id']]
        self.assertEqual(owner['inventory']['Herbs'],2); self.assertEqual(owner['deliveries_completed'],1)
        self.assertIsNone(owner['active_task'])
        self.assertTrue(self.engine.snapshot('alice')['flags'][f"npc-task:{npc['id']}:herb-bundle"])
        self.engine=GameEngine(self.world,Path(self.temp.name)/'npc-tasks.sqlite3')
        self.assertEqual(self.engine.npc_runtime_state[npc['id']]['inventory']['Herbs'],2)
        self.assertFalse(any(c['id']=='start-task-herb-bundle' for c in self.engine.decision_preview('alice',npc['id'])['candidates']))
        self.assertEqual(self.engine.decision_preview('alice',npc['id'])['observation']['owner']['resources']['deliveries_completed'],1)


class NPCCommerceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        source=new_world(); npc=source['npcs'][0]; vendor=source['economy']['vendors'][0]
        npc['location']=dict(source['spawn']); vendor['location']=dict(source['spawn'])
        npc['decision_policy']={'enabled':True,'provider_id':'commerce-test','goals':[],'actions':['trade'],'observations':['own_state'],'triggers':['player_nearby'],'cooldown_seconds':0,'timeout_seconds':2,'fallback':'wait','memory_limit':5,'commerce':{'markets':[vendor['id']],'items':['Field Notes'],'starting_currency':50,'starting_inventory':{},'currency_reserve':5,'inventory_capacity':4,'max_units_per_decision':2,'max_spend_per_decision':25,'allow_buy':True,'allow_sell':True}}
        source['decision_providers']=[{'id':'commerce-test','adapter':'laya_systemone','model':'typed-decisions','endpoint_binding':'TEST_COMMERCE_ENDPOINT'}]
        self.world=compile_world(source); self.path=Path(self.temp.name)/'npc-commerce.sqlite3'; self.engine=GameEngine(self.world,self.path)

    def tearDown(self): self.temp.cleanup()

    def test_npc_trades_use_shared_market_quotes_and_persist_resources(self):
        self.engine.execute('alice','look'); npc=self.world['npcs'][0]; vendor=self.world['economy']['vendors'][0]; messages=[]
        preview=self.engine.decision_preview('alice',npc['id'])
        self.assertEqual(preview['observation']['owner']['commerce']['currency'],50)
        self.assertTrue(any(candidate['id'].startswith('buy-mira-cart-field-notes-2-') for candidate in preview['candidates']))
        async def step(engine,selector):
            def choose(profile,observation,criteria,timeout): return selector(criteria),{}
            with patch('app.templates.game_engine.choose_decision',side_effect=choose):
                await engine._run_npc_decision('alice',npc,engine.snapshot('alice'),messages.append,force=True,trigger='player_nearby')
        asyncio.run(step(self.engine,lambda criteria:next(key for key in criteria if key.startswith('buy-mira-cart-field-notes-2-'))))
        state=self.engine.npc_runtime_state[npc['id']]
        self.assertEqual(state['currency'],30); self.assertEqual(state['inventory']['Field Notes'],2)
        with self.engine.connect() as con: self.assertEqual(self.engine.shared_count(con,f"stock:{vendor['id']}:Field Notes",10),8)
        self.engine=GameEngine(self.world,self.path)
        asyncio.run(step(self.engine,lambda criteria:next(key for key in criteria if key.startswith('sell-mira-cart-field-notes-1-'))))
        state=self.engine.npc_runtime_state[npc['id']]
        self.assertEqual(state['currency'],35); self.assertEqual(state['inventory']['Field Notes'],1)
        with self.engine.connect() as con: self.assertEqual(self.engine.shared_count(con,f"stock:{vendor['id']}:Field Notes",10),9)
        self.assertTrue(any('Mira the Cartographer bought 2 Field Notes' in message for message in messages))
        self.assertTrue(any('Mira the Cartographer sold 1 Field Notes' in message for message in messages))


class NPCGatherCraftTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        source=new_world(); npc=source['npcs'][0]; npc['location']=dict(source['spawn'])
        npc['decision_policy']={'enabled':True,'provider_id':'gather-craft-test','goals':[],'actions':['gather','craft'],'observations':['own_state'],'triggers':['player_nearby'],'cooldown_seconds':0,'timeout_seconds':2,'fallback':'wait','memory_limit':5,'gathering':{'items':['Wild Berries'],'max_units_per_decision':2},'crafting':{'recipes':['trail-rations'],'max_batches_per_decision':1},'resources':{'starting_inventory':{'Herbs':1},'inventory_capacity':10}}
        source['decision_providers']=[{'id':'gather-craft-test','adapter':'laya_systemone','model':'typed-decisions','endpoint_binding':'TEST_GATHER_CRAFT_ENDPOINT'}]
        self.world=compile_world(source); self.path=Path(self.temp.name)/'npc-gather-craft.sqlite3'; self.engine=GameEngine(self.world,self.path)

    def tearDown(self): self.temp.cleanup()

    def test_npc_gathers_finite_tile_resources_then_crafts_at_station_and_persists(self):
        self.engine.execute('alice','look'); npc=self.world['npcs'][0]; messages=[]
        preview=self.engine.decision_preview('alice',npc['id'])
        self.assertTrue(any(c['id']=='gather-wild-berries-2-20' for c in preview['candidates']))
        self.assertIn('Herbs',preview['observation']['owner']['resources']['inventory'])
        async def step(engine,prefix):
            def choose(profile,observation,criteria,timeout): return next((key for key in criteria if key.startswith(prefix)),'wait'),{}
            with patch('app.templates.game_engine.choose_decision',side_effect=choose):
                await engine._run_npc_decision('alice',npc,engine.snapshot('alice'),messages.append,force=True,trigger='player_nearby')
        asyncio.run(step(self.engine,'gather-wild-berries-2-'))
        owner=self.engine.npc_runtime_state[npc['id']]
        self.assertEqual(owner['inventory'],{'Herbs':1,'Wild Berries':2})
        with self.engine.connect() as con: self.assertEqual(self.engine.shared_count(con,'resource:overworld:7,5:Wild Berries',20),18)
        asyncio.run(step(self.engine,'craft-trail-rations-1'))
        owner=self.engine.npc_runtime_state[npc['id']]
        self.assertEqual(owner['inventory'],{'Trail Rations':1})
        self.assertEqual(owner['crafted_batches'],1); self.assertEqual(owner['gathered_items'],2)
        reloaded=GameEngine(self.world,self.path)
        self.assertEqual(reloaded.npc_runtime_state[npc['id']]['inventory'],{'Trail Rations':1})
        self.assertEqual(reloaded.npc_runtime_state[npc['id']]['crafted_batches'],1)
        self.assertTrue(any('gathered 2 Wild Berries' in message for message in messages))
        self.assertTrue(any('crafted 1 Trail Rations' in message for message in messages))

    def test_gather_and_craft_allowlists_must_resolve_to_authored_content(self):
        source=new_world(); npc=source['npcs'][0]
        npc['decision_policy']={'actions':['gather'],'gathering':{'items':['Imaginary Ore']},'resources':{}}
        with self.assertRaisesRegex(ContentError,'authored gatherable'):
            compile_world(source)
        source=new_world(); npc=source['npcs'][0]
        npc['decision_policy']={'actions':['craft'],'crafting':{'recipes':['missing-recipe']},'resources':{}}
        with self.assertRaisesRegex(ContentError,'authored recipe ids'):
            compile_world(source)


class MarketSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        source=new_world(); source['economy']['vendors'].append({'id':'east-cart','name':'East Cart','location':{'x':7,'y':5},'stock':[{'item':'Field Notes','price':2,'sell_price':1,'quantity':4}]})
        self.world=compile_world(source); self.engine=GameEngine(self.world,Path(self.temp.name)/'markets.sqlite3')

    def tearDown(self): self.temp.cleanup()

    def test_same_item_requires_explicit_market_and_selector_targets_buy_and_sell(self):
        listing=self.engine.execute('alice','shop')
        self.assertIn('East Cart [east-cart]',listing)
        self.assertIn('Mira\'s Supply Cart [mira-cart]',listing)
        self.assertIn('multiple markets',self.engine.execute('alice','buy Field Notes'))
        self.assertIn('Bought 2',self.engine.execute('alice','buy Field Notes at east-cart 2'))
        with self.engine.connect() as con: self.assertEqual(self.engine.shared_count(con,'stock:east-cart:Field Notes',4),2)
        self.assertIn('Sold 1',self.engine.execute('alice','sell Field Notes at east-cart'))
        with self.engine.connect() as con: self.assertEqual(self.engine.shared_count(con,'stock:east-cart:Field Notes',4),3)


class MarketRestockTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); now=[1000.0]
        source=new_world(); offer=source['economy']['vendors'][0]['stock'][0]
        offer.update(quantity=3,restock={'interval_seconds':60,'amount':2,'max_quantity':100})
        source['economy']['market_simulation']={'offline_simulation':True,'max_catchup_cycles':2}
        self.world=compile_world(source); self.path=Path(self.temp.name)/'restock.sqlite3'; self.engine=GameEngine(self.world,self.path)
        self.now=now; self.engine.clock=lambda:now[0]; self.engine._economy_started_at=now[0]

    def tearDown(self):
        self.temp.cleanup()

    def stock(self,engine=None):
        engine=engine or self.engine
        with engine.connect() as con: return engine.shared_count(con,'stock:mira-cart:Field Notes',3)

    def test_market_restock_uses_capped_cycles_and_persists_shared_stock(self):
        self.engine.execute('alice','shop')
        self.assertEqual(self.stock(),3)
        self.now[0]=1120
        self.assertIn('stock 7',self.engine.execute('alice','shop'))
        self.assertEqual(self.stock(),7)
        self.assertIn('Bought 1 📜 Field Notes',self.engine.execute('alice','buy Field Notes'))
        self.assertEqual(self.stock(),6)
        self.now[0]=1600
        self.engine.execute('alice','shop')
        self.assertEqual(self.stock(),10)
        self.engine.execute('alice','shop')
        self.assertEqual(self.stock(),10)
        reloaded=GameEngine(self.world,self.path); reloaded.clock=lambda:self.now[0]; reloaded._economy_started_at=self.now[0]
        self.assertEqual(self.stock(reloaded),10)
        self.now[0]+=60; reloaded.execute('bob','shop')
        self.assertEqual(self.stock(reloaded),12)

    def test_offline_disabled_skips_downtime_but_keeps_live_timer(self):
        self.world['economy']['market_simulation']['offline_simulation']=False
        self.engine.execute('alice','shop'); self.assertEqual(self.stock(),3)
        self.now[0]=1120
        restarted=GameEngine(self.world,self.path); restarted.clock=lambda:self.now[0]; restarted._economy_started_at=self.now[0]
        restarted.execute('alice','shop'); self.assertEqual(self.stock(restarted),3)
        self.now[0]=1180; restarted.execute('alice','shop')
        self.assertEqual(self.stock(restarted),5)

    def test_market_production_consumes_shared_inputs_and_obeys_output_cap(self):
        vendor=self.world['economy']['vendors'][0]
        vendor['stock'][0]['quantity']=3
        vendor['stock'][0].pop('restock')
        vendor['stock'][0]['production']={'interval_seconds':60,'amount':2,'max_quantity':7,'inputs':[{'item':'Torch','count':1}]}
        with self.engine.connect() as con:
            con.execute("INSERT INTO world_state(key,value) VALUES ('stock:mira-cart:Torch',5)")
        self.engine.execute('alice','shop')
        self.now[0]=1120
        shop=self.engine.execute('alice','shop')
        self.assertIn('Field Notes',shop); self.assertIn('stock 7',shop)
        self.assertEqual(self.stock(),7)
        with self.engine.connect() as con:
            self.assertEqual(self.engine.shared_count(con,'stock:mira-cart:Torch',5),3)
        self.now[0]=1600
        self.engine.execute('alice','shop')
        self.assertEqual(self.stock(),7)
        with self.engine.connect() as con:
            self.assertEqual(self.engine.shared_count(con,'stock:mira-cart:Torch',5),3)

    def test_authored_market_demand_depletes_stock_on_a_persisted_clock(self):
        offer=self.world['economy']['vendors'][0]['stock'][0]
        offer['quantity']=10; offer.pop('restock')
        offer['consumption']={'interval_seconds':60,'amount':2}
        self.engine.execute('alice','shop')
        self.now[0]=1120
        self.engine.execute('alice','shop')
        self.assertEqual(self.stock(),6)
        self.engine.execute('alice','shop')
        self.assertEqual(self.stock(),6)
        restarted=GameEngine(self.world,self.path); restarted.clock=lambda:self.now[0]; restarted._economy_started_at=self.now[0]
        self.now[0]+=60; restarted.execute('bob','shop')
        self.assertEqual(self.stock(restarted),4)


class PlayerTradeTests(unittest.TestCase):
    def setUp(self):
        import re
        self.re = re
        self.temp = tempfile.TemporaryDirectory()
        source = new_world()
        source['starting_inventory'] = {'Trail Rations': 3, 'Field Notes': 1}
        source['economy']['player_trade'] = {'enabled': True, 'allow_in_homes': True, 'offer_timeout_seconds': 600}
        self.world = compile_world(source)
        self.engine = GameEngine(self.world, Path(self.temp.name) / 'trades.sqlite3')
        self.engine.execute('alice', 'look')
        self.engine.execute('bob', 'look')

    def tearDown(self):
        self.temp.cleanup()

    def test_item_and_currency_trade_moves_only_after_acceptance(self):
        offered = self.engine.execute('alice', 'trade offer bob Trail Rations 2 for Crowns 5')
        trade_id = self.re.search(r'Trade offer ([a-f0-9]{10})', offered).group(1)
        self.assertEqual(self.engine.snapshot('alice')['inventory']['Trail Rations'], 3)
        self.assertEqual(self.engine.snapshot('bob')['currency'], 25)
        self.assertIn(trade_id, self.engine.execute('bob', 'trade offers'))
        self.assertIn('Trade complete', self.engine.execute('bob', f'trade accept {trade_id}'))
        self.assertEqual(self.engine.snapshot('alice')['inventory']['Trail Rations'], 1)
        self.assertEqual(self.engine.snapshot('alice')['currency'], 30)
        self.assertEqual(self.engine.snapshot('bob')['inventory']['Trail Rations'], 5)
        self.assertEqual(self.engine.snapshot('bob')['currency'], 20)
        self.assertIn('completed', self.engine.execute('bob', f'trade accept {trade_id}'))

    def test_home_trading_obeys_grants_and_builder_permission(self):
        self.world['building'].update(enabled=True, tile_cost=0, allow_visitors=True)
        self.world['economy']['player_trade']['allow_in_homes'] = False
        self.engine.execute('alice', 'north')
        self.engine.execute('bob', 'north')
        claim = self.engine.execute('alice', 'build claim')
        entrance = self.re.search(r'go (enter-home-[a-f0-9]+)', claim).group(1)
        self.engine.execute('alice', f'go {entrance}')
        self.engine.execute('alice', 'home access grant bob visitor')
        self.assertIn(entrance, self.engine.execute('bob', 'exits'))
        self.engine.execute('bob', f'go {entrance}')
        denied = self.engine.execute('alice', 'trade offer bob Trail Rations for Field Notes')
        self.assertIn('disabled inside private homes', denied)
        self.world['economy']['player_trade']['allow_in_homes'] = True
        offered = self.engine.execute('alice', 'trade offer bob Trail Rations for Field Notes')
        trade_id = self.re.search(r'Trade offer ([a-f0-9]{10})', offered).group(1)
        self.assertIn('Trade complete', self.engine.execute('bob', f'trade accept {trade_id}'))

    def test_home_trade_requires_same_interior_tile_and_access_at_acceptance(self):
        self.world['building'].update(enabled=True, tile_cost=0, allow_visitors=True)
        self.engine.execute('alice', 'north')
        self.engine.execute('bob', 'north')
        claim = self.engine.execute('alice', 'build claim')
        entrance = self.re.search(r'go (enter-home-[a-f0-9]+)', claim).group(1)
        self.engine.execute('alice', f'go {entrance}')
        self.engine.execute('alice', 'build expand east')
        self.engine.execute('alice', 'home access grant bob visitor')
        self.engine.execute('bob', f'go {entrance}')
        self.engine.execute('bob', 'east')
        self.assertIn('same map tile', self.engine.execute('alice', 'trade offer bob Trail Rations for Field Notes'))
        self.engine.execute('bob', 'west')
        offered = self.engine.execute('alice', 'trade offer bob Trail Rations for Field Notes')
        trade_id = self.re.search(r'Trade offer ([a-f0-9]{10})', offered).group(1)
        self.engine.execute('alice', 'home access revoke bob')
        self.assertIn('no longer has access', self.engine.execute('bob', f'trade accept {trade_id}'))


class PlayerConstructionTests(unittest.TestCase):
    def setUp(self):
        import re
        self.re = re
        self.temp = tempfile.TemporaryDirectory()
        source = new_world()
        source['starting_inventory'] = {'Field Notes': 2}
        source['tiles'][0].update(rest=False,station='campfire')
        source.setdefault('building',{}).update(enabled=True, tile_cost=0, template_map_id='overworld', structure_refund_percent=50, paintable_biomes=['forest','water'], paint_cost=4, paint_materials=[{'item':'Field Notes','count':1}], structures=[{'id':'bedroll','name':'Bedroll','icon':'🛏️','cost':5,'materials':[{'item':'Field Notes','count':2}],'service':{'type':'rest'}}])
        self.world = compile_world(source)
        self.engine = GameEngine(self.world, Path(self.temp.name) / 'building.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_home_painting_is_authored_costed_bounded_and_persistent(self):
        self.engine.execute('alice','north')
        claim=self.engine.execute('alice','build claim')
        entrance=self.re.search(r'go (enter-home-[a-f0-9]+)',claim).group(1)
        self.engine.execute('alice',f'go {entrance}')
        before=self.engine.snapshot('alice')
        preview=self.engine.execute('alice','build preview paint forest')
        self.assertIn('4 Crowns + materials: Field Notes ×1',preview)
        self.assertIn('can afford',preview)
        self.assertEqual(before,self.engine.snapshot('alice'))
        self.assertIn('Home terrain must remain passable',self.engine.execute('alice','build paint water'))
        self.assertEqual(before,self.engine.snapshot('alice'))
        self.assertIn('Painted this tile as',self.engine.execute('alice','build paint forest'))
        after=self.engine.snapshot('alice')
        self.assertEqual(after['currency'],before['currency']-4)
        self.assertEqual(after['inventory']['Field Notes'],1)
        home_id=after['map_id']
        self.assertEqual(self.engine.dynamic_maps[home_id]['tiles'][0]['biome'],'forest')
        reloaded=GameEngine(self.world,Path(self.temp.name)/'building.sqlite3')
        self.assertEqual(reloaded.dynamic_maps[home_id]['tiles'][0]['biome'],'forest')

    def test_room_text_editing_is_opt_in_owner_scoped_and_persistent(self):
        self.world['building']['allow_room_editing'] = True
        self.engine.execute('alice', 'north')
        claim = self.engine.execute('alice', 'build claim')
        entrance = self.re.search(r'go (enter-home-[a-f0-9]+)', claim).group(1)
        self.engine.execute('alice', f'go {entrance}')
        self.assertIn('Reading Nook', self.engine.execute('alice', 'build rename Reading Nook'))
        self.assertIn('Fresh maps and a warm lamp.', self.engine.execute('alice', 'build describe Fresh maps and a warm lamp.'))
        self.assertIn('Reading Nook', self.engine.execute('alice', 'look'))
        self.assertIn('Fresh maps and a warm lamp.', self.engine.execute('alice', 'look'))
        reloaded = GameEngine(self.world, Path(self.temp.name) / 'building.sqlite3')
        self.assertIn('Reading Nook', reloaded.execute('alice', 'look'))
        self.assertIn('Fresh maps and a warm lamp.', reloaded.execute('alice', 'look'))
        reloaded.world['building']['allow_room_editing'] = False
        self.assertIn('disabled home room editing', reloaded.execute('alice', 'build rename Intruder Room'))

    def test_home_terrain_description_tracks_paint_while_custom_room_text_stays(self):
        self.world['building']['allow_room_editing'] = True
        self.world['building']['paintable_biomes'].append('grassland')
        template = self.world['maps'][0]['tiles'][0]
        template.update(biome='grassland', description='A quiet stretch of grassland.')
        self.engine.execute('alice', 'north')
        claim = self.engine.execute('alice', 'build claim')
        entrance = self.re.search(r'go (enter-home-[a-f0-9]+)', claim).group(1)
        self.engine.execute('alice', f'go {entrance}')
        self.assertIn('A quiet stretch of grassland.', self.engine.execute('alice', 'look'))
        self.engine.execute('alice', 'build paint forest')
        look = self.engine.execute('alice', 'look')
        self.assertIn('A quiet stretch of forest.', look)
        self.assertNotIn('A quiet stretch of grassland.', look)
        self.engine.execute('alice', 'build describe A mossy room beneath the trees.')
        self.engine.execute('alice', 'build paint grassland')
        look = self.engine.execute('alice', 'look')
        self.assertIn('A quiet stretch of grassland.', look)
        self.assertIn('A mossy room beneath the trees.', look)

    def test_authored_structure_costs_resources_persists_and_enables_service(self):
        self.engine.execute('alice','north')
        claim=self.engine.execute('alice','build claim')
        entrance=self.re.search(r'go (enter-home-[a-f0-9]+)',claim).group(1)
        self.engine.execute('alice',f'go {entrance}')
        self.assertIn('Bedroll',self.engine.execute('alice','build structures'))
        before=self.engine.snapshot('alice')
        preview=self.engine.execute('alice','build preview place bedroll')
        self.assertIn('5 Crowns + materials: Field Notes ×2',preview)
        self.assertIn('can afford',preview)
        self.assertEqual(self.engine.snapshot('alice')['currency'],before['currency'])
        self.assertEqual(self.engine.snapshot('alice')['inventory'],before['inventory'])
        self.assertIn('Built Bedroll',self.engine.execute('alice','build place bedroll'))
        self.assertEqual(self.engine.snapshot('alice')['currency'],20)
        self.assertNotIn('Field Notes',self.engine.snapshot('alice')['inventory'])
        self.assertIn('Bedroll',self.engine.execute('alice','look'))
        self.assertIn('rest here',self.engine.execute('alice','look'))
        reloaded=GameEngine(self.world,Path(self.temp.name)/'building.sqlite3')
        self.assertIn('Bedroll',reloaded.execute('alice','look'))
        self.assertIn('Removed Bedroll',reloaded.execute('alice','build remove bedroll'))
        self.assertEqual(reloaded.snapshot('alice')['currency'],22)
        self.assertEqual(reloaded.snapshot('alice')['inventory']['Field Notes'],1)
        look=reloaded.execute('alice','look')
        self.assertIn('Crafting station: campfire',look)
        self.assertNotIn('You can rest here.',look)
        self.assertIn('Find a campfire',reloaded.execute('alice','rest'))

    def test_multitile_structure_requires_owned_clear_footprint_and_releases_tiles(self):
        self.world['building']['structures'] += [
            {'id':'shed','name':'Shed','footprint':{'width':2,'height':1},'cost':0,'service':{'type':'decoration'}},
            {'id':'bench','name':'Bench','cost':0,'service':{'type':'decoration'}},
        ]
        self.engine.execute('alice','north')
        claim=self.engine.execute('alice','build claim')
        entrance=self.re.search(r'go (enter-home-[a-f0-9]+)',claim).group(1)
        self.engine.execute('alice',f'go {entrance}')
        self.assertIn('Every tile under this structure must be purchased',self.engine.execute('alice','build preview place shed'))
        self.engine.execute('alice','build expand east')
        self.assertIn('can afford',self.engine.execute('alice','build preview place shed'))
        self.engine.execute('alice','build place shed')
        self.engine.execute('alice','east')
        self.assertIn('Part of Shed',self.engine.execute('alice','look'))
        self.assertIn('overlaps an existing structure',self.engine.execute('alice','build place bench'))
        self.engine.execute('alice','west')
        self.engine.execute('alice','build remove shed')
        self.engine.execute('alice','east')
        self.assertIn('Built Bench',self.engine.execute('alice','build place bench'))

    def test_outer_home_tile_can_be_removed_with_authored_refund_and_persists(self):
        self.engine.execute('alice','north')
        claim=self.engine.execute('alice','build claim')
        entrance=self.re.search(r'go (enter-home-[a-f0-9]+)',claim).group(1)
        self.engine.execute('alice',f'go {entrance}')
        area=self.engine.dynamic_maps[self.engine.snapshot('alice')['map_id']]
        area['tiles'][1]['biome']='forest'
        self.world['building'].update(tile_cost=10,tile_materials=[{'item':'Field Notes','count':2}],tile_refund_percent=50)
        self.engine.execute('alice','build expand east')
        self.engine.execute('alice','east')
        self.assertIn('entrance tile cannot be removed',self.engine.execute('alice','build remove tile west'))
        self.engine.execute('alice','west')
        before=self.engine.snapshot('alice')
        preview=self.engine.execute('alice','build preview remove tile east')
        self.assertIn('refund 5 Crowns + materials: Field Notes ×1 (50%)',preview)
        self.assertEqual(self.engine.snapshot('alice'),before)
        self.assertIn('Removed the Forest tile',self.engine.execute('alice','build remove tile east'))
        after=self.engine.snapshot('alice')
        self.assertEqual(after['currency'],before['currency']+5)
        self.assertEqual(after['inventory'].get('Field Notes',0),before['inventory'].get('Field Notes',0)+1)
        reloaded=GameEngine(self.world,Path(self.temp.name)/'building.sqlite3')
        self.assertNotIn(1,reloaded.dynamic_maps[after['map_id']]['purchased_tiles'])

    def test_home_tile_removal_refuses_to_disconnect_purchased_area(self):
        self.engine.execute('alice','north')
        claim=self.engine.execute('alice','build claim')
        entrance=self.re.search(r'go (enter-home-[a-f0-9]+)',claim).group(1)
        self.engine.execute('alice',f'go {entrance}')
        self.engine.execute('alice','build expand east')
        self.engine.execute('alice','build expand south')
        self.engine.execute('alice','east')
        self.engine.execute('alice','build expand south')
        self.engine.execute('alice','west')
        self.assertIn('one home connection',self.engine.execute('alice','build remove tile east'))
        self.assertIn(1,self.engine.dynamic_maps[self.engine.snapshot('alice')['map_id']]['purchased_tiles'])


class MarketDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        source=new_world()
        source['decision_providers']=[{'id':'test','adapter':'laya_systemone','model':'typed-decisions','endpoint_binding':'TEST_ENDPOINT'}]
        source['npcs'][0]['decision_policy']={'enabled':True,'provider_id':'test','goals':[],'actions':['wait'],'observations':['own_state'],'triggers':['player_nearby'],'cooldown_seconds':30,'timeout_seconds':5,'fallback':'wait','memory_limit':5}
        vendor=source['economy']['vendors'][0]
        vendor['stock'][0]['pricing']={'mode':'stock_based','base_price':18,'target_stock':10,'sensitivity':1,'min_price':5,'max_price':20,'spread':2,'adjustment_step':3}
        vendor['decision_policy']={'enabled':True,'provider_id':'test','goals':[{'id':'reserve','description':'Keep Field Notes available','metric':'stock_level','item':'Field Notes','target':8}],'actions':['market_price'],'observations':['own_state','public_markets'],'triggers':['player_nearby'],'cooldown_seconds':0,'timeout_seconds':5,'fallback':'wait','memory_limit':5}
        self.world=compile_world(source)
        self.engine=GameEngine(self.world,Path(self.temp.name)/'decision.sqlite3')
        self.engine.execute('alice','look')

    def tearDown(self):
        self.temp.cleanup()

    def test_market_decision_adjusts_price_within_authored_bounds_and_quotes_use_it(self):
        import asyncio
        from unittest.mock import patch
        vendor=self.world['economy']['vendors'][0]
        notices=[]
        with patch('app.templates.game_engine.choose_decision',return_value=('price-raise-field-notes-18',{})):
            asyncio.run(self.engine._run_market_decision('alice',vendor,self.engine.snapshot('alice'),notices.append,force=True))
        with self.engine.connect() as con:
            owner=self.engine._market_decision_state(con,vendor['id'])
            candidates=self.engine._market_decision_candidates(con,vendor,owner)
        self.assertEqual(owner['base_prices']['Field Notes'],20)
        self.assertFalse(any(c['id'].startswith('price-raise-field-notes-') for c in candidates))
        self.assertTrue(any(c['id'].startswith('price-lower-field-notes-') for c in candidates))
        self.assertIn('base price from 18 to 20',notices[0])
        with patch('app.templates.game_engine.choose_decision',return_value=('price-raise-field-notes-18',{})):
            asyncio.run(self.engine._run_market_decision('alice',vendor,self.engine.snapshot('alice'),notices.append,force=True))
        with self.engine.connect() as con:
            owner=self.engine._market_decision_state(con,vendor['id'])
            stale=con.execute("SELECT status FROM decision_traces ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertEqual(owner['base_prices']['Field Notes'],20)
        self.assertEqual(stale,'stale')
        self.assertIn('buy 20, sell 18',self.engine.execute('alice','shop'))
        self.assertIn('for 🪙 20',self.engine.execute('alice','buy Field Notes'))

    def test_controller_pause_persists_and_invalidates_an_inflight_result(self):
        import asyncio
        from threading import Event
        from unittest.mock import patch
        vendor=self.world['economy']['vendors'][0]
        paused=self.engine.set_decision_paused('market',vendor['id'],True)
        self.assertTrue(paused['paused'])
        self.assertTrue(GameEngine(self.world,self.engine.database).decision_preview('alice',vendor['id'],'market')['paused'])
        npc_id=self.world['npcs'][0]['id']
        self.assertTrue(self.engine.set_decision_paused('npc',npc_id,True)['paused'])
        self.assertTrue(GameEngine(self.world,self.engine.database).decision_preview('alice',npc_id,'npc')['paused'])
        self.engine.set_decision_paused('npc',npc_id,False)
        self.engine.set_decision_paused('market',vendor['id'],False)
        entered=Event(); release=Event()
        def delayed_choice(profile,observation,criteria,timeout):
            entered.set(); release.wait(2); return 'price-raise-field-notes-18',{}
        async def run_and_pause():
            task=asyncio.create_task(self.engine._run_market_decision('alice',vendor,self.engine.snapshot('alice'),lambda message:None,force=True))
            self.assertTrue(await asyncio.to_thread(entered.wait,2))
            self.engine.set_decision_paused('market',vendor['id'],True)
            release.set()
            await task
        with patch('app.templates.game_engine.choose_decision',side_effect=delayed_choice):
            asyncio.run(run_and_pause())
        with self.engine.connect() as con:
            owner=self.engine._market_decision_state(con,vendor['id'])
            status=con.execute("SELECT status FROM decision_traces ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        self.assertTrue(owner['paused'])
        self.assertNotIn('Field Notes',owner.get('base_prices',{}))
        self.assertEqual(status,'stale')
