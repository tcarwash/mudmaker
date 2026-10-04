"""Exercise the real authoring GUI against an isolated database and model fixture."""
import json
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import asyncssh
import uvicorn
from playwright.sync_api import sync_playwright, expect

from app import main as app_module


class ModelFixture(BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        context = json.loads(body['messages'][-1]['content'])
        self.requests.append(context)
        generated = {
            'id': 'keeper', 'name': 'Lena', 'role': 'Merchant',
            'location': {'x': 9, 'y': 5}, 'dialog': 'The grove remembers every traveler.',
            'description': 'A traveler with a hidden history.',
            'inventory': [{'item': 'Herbs', 'count': 7}],
        }
        data = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(generated)}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


def run_browser(base, model_url, output):
    errors = []
    with sync_playwright() as p:
        executable = os.getenv('MUDMAKER_BROWSER') or shutil.which('google-chrome') or shutil.which('chromium')
        browser = p.chromium.launch(executable_path=executable, headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base)
        expect(page.locator('#world-name-nav')).to_have_text('The Wayfarer Realm')
        expect(page.locator('.map-workspace')).to_be_visible()
        assert page.locator('.map-workspace').evaluate('(node) => getComputedStyle(node).display') == 'grid'
        expect(page.locator('.map-viewport')).to_be_visible()
        assert page.locator('.map-cell').first.evaluate('(node) => getComputedStyle(node).width') != 'auto'
        # Saving buildability-only overrides must retain each terrain's colors.
        page.locator('#terrain-rules').click()
        page.locator('#save-terrain-rules').click()
        expected_colors = {'desert': 'rgb(223, 196, 120)', 'water': 'rgb(118, 181, 198)', 'mountain': 'rgb(146, 149, 138)'}
        for ident, color in expected_colors.items():
            assert page.locator(f'[data-biome="{ident}"] .legend-dot').evaluate('(node) => getComputedStyle(node).backgroundColor') == color
        assert page.locator('.map-cell').evaluate_all('(nodes) => new Set(nodes.map(node => getComputedStyle(node).backgroundColor)).size') >= 4
        page.locator('#settings-shortcut').click()
        page.locator('#api-base').fill(model_url)
        page.locator('#api-model').fill('fixture-model')
        page.locator('#save-settings').click()
        expect(page.locator('#toast')).to_have_text('Endpoint and model saved')

        page.locator('[data-view="map"]').click()
        tile = page.locator('[data-cell="88"]')
        page.locator('[data-cell="87"]').focus()
        page.keyboard.press('ArrowRight')
        expect(page.locator(':focus')).to_have_attribute('data-cell', '88')
        expect(page.locator('[data-cell="88"]')).to_have_class('map-cell cursor')
        page.keyboard.press('ArrowLeft')
        original_color = tile.evaluate('(element) => element.style.background')
        tile.click()
        assert tile.evaluate('(element) => element.style.background') == original_color
        page.locator('#tile-name').fill('Browser Grove')
        page.locator('#tile-desc').fill('A clearing authored in the browser.')
        page.locator('[data-biome="forest"]').click()
        page.locator('[data-cell="86"]').click()
        expect(page.locator('#tile-desc')).to_have_value('A quiet stretch of forest.')
        page.locator('#select-tool').click()
        page.locator('[data-cell="88"]').click()
        page.locator('[data-place-kind="npc"]').click()
        expect(page.locator('.map-pick-banner')).to_be_visible()
        page.locator('[data-cell="90"]').click()
        page.locator('[data-location-map-cell="89"]').click()
        assert json.loads(page.locator('#entity-json').input_value())['location'] == {'x': 9, 'y': 5}
        expect(page.locator('#entity-location-readout')).to_contain_text('9, 5')
        assert page.locator('#entity-x').is_hidden()
        page.locator('.entity-coordinate-advanced summary').click()
        expect(page.locator('#entity-x')).to_be_visible()
        page.locator('#entity-name').fill('Map Placed Scout')
        page.locator('#entity-id').fill('map-scout')
        page.locator('[data-entity-field="role"]').fill('Scout')
        page.locator('[data-entity-field="dialog"]').fill('The old road bends east.')
        page.locator('#decision-route-add').click()
        route = page.locator('[data-decision-route]').last
        route.locator('[data-route-id]').fill('grove-patrol')
        route.locator('[data-route-name]').fill('Grove patrol')
        route.locator('.decision-route-picker > summary').click()
        route.locator('[data-location-picker-cell="89"]').click()
        expect(route.locator('[data-route-x]')).to_have_value('9')
        expect(route.locator('[data-route-y]')).to_have_value('5')
        page.locator('.decision-action[value="move"]').check()
        page.locator('#entity-save').click()
        assert next(n for n in page.evaluate('world.npcs') if n['id']=='map-scout')['decision_policy']['routes'][0]['location'] == {'x':9,'y':5}
        assert 'Map Placed Scout' in page.locator('[data-cell="89"]').get_attribute('title')
        page.locator('#map-search-input').fill('map-scout')
        expect(page.locator('.map-search-result')).to_have_count(1)
        page.locator('.map-search-result').click()
        expect(page.locator('[data-cell="89"]')).to_have_class('map-cell cursor')
        expect(page.locator('#tile-content')).to_contain_text('Map Placed Scout')
        page.locator('#map-search-input').fill('')
        page.locator('[data-map-layer="objects"]').uncheck()
        assert 'Map Placed Scout' not in page.locator('[data-cell="89"]').get_attribute('title')
        expect(page.locator('#tile-content')).to_contain_text('Map Placed Scout')
        page.locator('[data-map-layer="objects"]').check()
        page.locator('#tile-content .tile-content-card').filter(has_text='Map Placed Scout').locator('[data-tile-edit]').click()
        expect(page.locator('#entity-name')).to_have_value('Map Placed Scout')
        page.locator('.modal-cancel').click()
        page.locator('[data-cell="87"]').click()
        expect(page.locator('#tile-content')).to_contain_text('Mira the Cartographer')
        expect(page.locator('#tile-content')).to_contain_text("Mira's Supply Cart")
        expect(page.locator('#tile-content .tile-content-card')).to_have_count(2)
        page.locator('[data-place-kind="vendor"]').click()
        expect(page.locator('.map-pick-banner')).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.locator('.map-pick-banner')).to_have_count(0)

        page.locator('[data-view="npcs"]').click()
        expect(page.locator('#view').get_by_text('Map Placed Scout', exact=True)).to_be_visible()
        page.locator('[data-view="quests"]').click()
        quest_count = page.evaluate('world.quests.length')
        page.locator('[data-add="quests"]').click()
        expect(page.locator('#quest-visit-add')).to_be_visible()
        page.locator('#quest-visit-add').click()
        page.locator('.quest-visit-row [data-location-picker-cell="18"]').click()
        draft_target = page.evaluate("JSON.parse(document.querySelector('#entity-json').value).objectives.at(-1).target")
        assert draft_target == {'map_id':'overworld','x':2,'y':1}
        page.locator('.modal-cancel').click()
        assert page.evaluate('world.quests.length') == quest_count
        page.locator('[data-add="quests"]').click()
        page.locator('#entity-name').fill('Browser Visit Quest')
        page.locator('#entity-id').fill('browser-visit-quest')
        page.evaluate("""() => { const field=document.querySelector('#entity-json'); const draft=JSON.parse(field.value); draft.objectives[0].editor_extension='preserve me'; field.value=JSON.stringify(draft,null,2); field.dispatchEvent(new Event('input',{bubbles:true})); }""")
        page.locator('[data-objective-index="0"] [data-objective-field="type"]').select_option('collect')
        page.locator('[data-objective-index="0"] [data-objective-field="target"]').select_option('Field Notes')
        page.locator('[data-objective-index="0"] [data-objective-field="count"]').fill('2')
        page.locator('#quest-visit-add').click()
        page.locator('.quest-visit-row [data-location-picker-cell="18"]').click()
        page.locator('#entity-save').click()
        saved_visit = page.evaluate("world.quests.find(quest => quest.id === 'browser-visit-quest').objectives.at(-1)")
        assert saved_visit == {'type':'visit','target':{'map_id':'overworld','x':2,'y':1},'count':1}
        saved_collect = page.evaluate("world.quests.find(quest => quest.id === 'browser-visit-quest').objectives[0]")
        assert saved_collect == {'type':'collect','count':2,'target':'Field Notes','editor_extension':'preserve me'}
        page.locator('.entity-card').filter(has_text='Browser Visit Quest').locator('[data-edit]').click()
        reopened_quest = page.evaluate("JSON.parse(document.querySelector('#entity-json').value)")
        assert reopened_quest['objectives'][0]['editor_extension'] == 'preserve me'
        assert reopened_quest['objectives'][1]['target'] == {'map_id':'overworld','x':2,'y':1}
        page.locator('.modal-cancel').click()
        page.locator('[data-view="dialogs"]').click()
        page.locator('[data-add="dialogs"]').click()
        page.locator('#entity-name').fill('Browser Dialogue')
        page.locator('#entity-id').fill('browser-dialogue')
        page.locator('[data-dialog-field="nodes.greeting.text"]').fill('What would you like to know?')
        page.locator('[data-dialog-add-node]').click()
        page.locator('[data-dialog-field="nodes.greeting.choices.0.text"]').fill('Tell me about the survey')
        page.locator('[data-dialog-field="nodes.greeting.choices.0.next"]').select_option('node-1')
        page.locator('[data-dialog-add-effect="greeting:0"]').click()
        page.locator('[data-dialog-field="nodes.greeting.choices.0.effects.0.type"]').select_option('quest')
        page.locator('[data-dialog-field="nodes.greeting.choices.0.effects.0.target"]').select_option('first-survey')
        page.locator('[data-dialog-field="nodes.node-1.text"]').fill('Mira needs a field survey.')
        page.locator('[data-dialog-add-choice="node-1"]').click()
        page.locator('[data-dialog-field="nodes.node-1.choices.0.text"]').fill('I can prepare trail supplies.')
        page.locator('[data-dialog-add-effect="node-1:0"]').click()
        page.locator('[data-dialog-field="nodes.node-1.choices.0.effects.0.type"]').select_option('quest')
        page.locator('[data-dialog-field="nodes.node-1.choices.0.effects.0.target"]').select_option('trail-supplies')
        page.evaluate("""() => { const field=document.querySelector('#entity-json');const draft=JSON.parse(field.value);draft.nodes['node-1'].choices[0].requires={quest:'first-survey'};field.value=JSON.stringify(draft,null,2);field.dispatchEvent(new Event('input',{bubbles:true})); }""")
        page.locator('#entity-save').focus()
        page.keyboard.press('Enter')
        saved_dialogue = page.evaluate("world.dialogs.find(dialog => dialog.id === 'browser-dialogue')")
        assert saved_dialogue['nodes']['greeting']['choices'][0]['next'] == 'node-1'
        assert saved_dialogue['nodes']['greeting']['choices'][0]['effects'] == [{'type':'quest','target':'first-survey'}]
        assert saved_dialogue['nodes']['node-1']['text'] == 'Mira needs a field survey.'
        assert saved_dialogue['nodes']['node-1']['choices'][0]['requires'] == {'quest':'first-survey'}
        page.locator('.entity-card').filter(has_text='Browser Dialogue').locator('[data-edit]').click()
        reopened_dialogue = page.evaluate("JSON.parse(document.querySelector('#entity-json').value)")
        assert reopened_dialogue['nodes']['node-1']['choices'][0]['effects'] == [{'type':'quest','target':'trail-supplies'}]
        assert reopened_dialogue['nodes']['node-1']['choices'][0]['requires'] == {'quest':'first-survey'}
        page.locator('.modal-cancel').click()
        page.locator('[data-view="npcs"]').click()
        page.locator('[data-edit="0"]').click()
        page.locator('#npc-dialog-link').select_option('browser-dialogue')
        page.locator('#entity-save').click()
        assert page.evaluate("world.npcs[0].dialog_id") == 'browser-dialogue'
        page.locator('[data-view="crafting"]').click()
        page.locator('[data-add="crafting"]').click()
        page.locator('#recipe-editor summary').click()
        page.locator('[data-new-item-name]').fill('Canceled Fiber')
        page.locator('[data-recipe-create-item]').click()
        page.locator('.modal-cancel').click()
        assert page.evaluate("!(world.entities || []).some(entity => entity.name === 'Canceled Fiber')")
        page.locator('[data-add="crafting"]').click()
        page.locator('#entity-name').fill('Browser Cord recipe')
        page.locator('#entity-id').fill('browser-cord-recipe')
        page.locator('#recipe-editor summary').click()
        page.locator('[data-new-item-name]').fill('Browser Fiber')
        page.locator('[data-recipe-create-item]').click()
        page.locator('[data-new-item-name]').fill('Browser Cord')
        page.locator('[data-new-item-description]').fill('A length of twisted fiber.')
        page.locator('[data-recipe-create-item]').click()
        page.locator('[data-recipe-input-row="0"] [data-recipe-item]').select_option('Browser Fiber')
        page.locator('[data-recipe-count="input:0"]').fill('2')
        page.locator('[data-recipe-output] [data-recipe-item]').select_option('Browser Cord')
        page.locator('[data-recipe-station]').select_option('campfire')
        page.locator('#entity-save').click()
        saved_recipe = page.evaluate("world.crafting.find(recipe => recipe.id === 'browser-cord-recipe')")
        assert saved_recipe['inputs'] == [{'item':'Browser Fiber','count':2}]
        assert saved_recipe['output'] == {'item':'Browser Cord','count':1}
        assert saved_recipe['station'] == 'campfire'
        assert page.evaluate("world.entities.filter(entity => entity.entity_type === 'item' && ['Browser Fiber','Browser Cord'].includes(entity.name)).length") == 2
        page.locator('[data-view="economy"]').click()
        page.locator('[data-edit="0"]').click()
        expect(page.locator('#market-item-names option[value="Browser Cord"]')).to_have_count(1)
        market_row = page.locator('[data-market-offer]').first
        market_row.locator('[data-offer-field="mode"]').select_option('stock_based')
        for field, value in [('quantity','4'),('base_price','10'),('target_stock','4'),('sensitivity','1'),('min_price','1'),('max_price','50'),('spread','2')]:
            market_row.locator(f'[data-offer-field="{field}"]').fill(value)
        expect(page.locator('#market-price-preview')).to_contain_text('Engine quote preview', timeout=15000)
        expect(page.locator('#market-price-preview')).to_contain_text('Buy batches:')
        page.locator('.modal-cancel').click()
        page.locator('[data-view="map"]').click()
        page.locator('[data-map-layer="buildability"]').check()
        expect(page.locator('[data-cell="0"]')).to_have_class(re.compile('buildability-allowed'))
        expect(page.locator('[data-cell="0"]')).to_have_attribute('title', re.compile('buildable'))
        page.locator('[data-map-layer="buildability"]').uncheck()
        page.locator('#map-place').click()
        page.locator('#map-place-kind').select_option('vendor')
        page.locator('#map-place-start').click()
        expect(page.locator('.map-pick-banner')).to_be_visible()
        page.locator('[data-cell="92"]').click()
        page.locator('#entity-name').fill('Map Placed Trader')
        page.locator('#entity-id').fill('map-market')
        page.locator('[data-offer-remove]').click()
        page.locator('#market-offer-add').click()
        first_offer = page.locator('[data-market-offer]').nth(0)
        first_offer.locator('[data-offer-field="item"]').fill('Field Notes')
        first_offer.locator('[data-offer-field="price"]').fill('7')
        first_offer.locator('[data-offer-field="sell_price"]').fill('3')
        first_offer.locator('[data-offer-field="quantity"]').fill('8')
        page.locator('#market-offer-add').click()
        second_offer = page.locator('[data-market-offer]').nth(1)
        second_offer.locator('[data-offer-field="item"]').fill('Torch')
        second_offer.locator('[data-offer-field="price"]').fill('4')
        second_offer.locator('[data-offer-field="sell_price"]').fill('2')
        second_offer.locator('[data-offer-field="quantity"]').fill('5')
        second_offer.locator('[data-offer-field="mode"]').select_option('stock_based')
        second_offer.locator('[data-offer-field="base_price"]').fill('4')
        second_offer.locator('[data-offer-field="target_stock"]').fill('10')
        second_offer.locator('[data-offer-field="min_price"]').fill('1')
        second_offer.locator('[data-offer-field="max_price"]').fill('12')
        second_offer.locator('[data-offer-field="spread"]').fill('2')
        second_offer.locator('[data-offer-field="restock"]').check()
        second_offer.locator('[data-offer-field="interval_seconds"]').fill('60')
        second_offer.locator('[data-offer-field="amount"]').fill('2')
        second_offer.locator('[data-offer-field="max_quantity"]').fill('10')
        second_offer.locator('[data-offer-field="production"]').check()
        second_offer.locator('[data-offer-field="production_interval"]').fill('120')
        second_offer.locator('[data-offer-field="production_amount"]').fill('1')
        second_offer.locator('[data-offer-field="production_max"]').fill('10')
        second_offer.locator('[data-production-add]').click()
        second_offer.locator('[data-production-item]').fill('Torch')
        expect(page.locator('#market-form-error')).to_contain_text('consume their own output')
        page.locator('#entity-save').click()
        expect(page.locator('#entity-save')).to_be_visible()
        second_offer.locator('[data-production-item]').fill('Field Notes')
        second_offer.locator('[data-production-count]').fill('2')
        first_offer.locator('[data-offer-field="consumption"]').check()
        first_offer.locator('[data-offer-field="consumption_interval"]').fill('600')
        first_offer.locator('[data-offer-field="consumption_amount"]').fill('1')
        expect(page.locator('#market-form-error')).to_be_empty()
        expect(page.locator('#market-price-preview')).to_contain_text('Torch')
        page.get_by_text('Advanced inventory JSON', exact=True).click()
        advanced_offers = json.loads(page.locator('#market-stock').input_value())
        advanced_offers[1]['pricing']['adjustment_step'] = 2
        page.locator('#market-stock').fill(json.dumps(advanced_offers))
        second_offer.locator('[data-offer-field="base_price"]').fill('5')
        second_offer.locator('[data-offer-field="base_price"]').fill('4')
        assert json.loads(page.locator('#market-stock').input_value())[1]['pricing']['adjustment_step'] == 2
        page.get_by_text('Advanced inventory JSON', exact=True).click()
        page.screenshot(path=str(output / 'market-editor.png'), full_page=True)
        page.locator('#market-contract-add').click()
        page.locator('[data-contract-id]').fill('browser-notes-run')
        page.locator('[data-contract-name]').fill('Browser Notes Run')
        page.locator('[data-contract-item]').select_option(label='Field Notes')
        page.locator('[data-contract-destination]').select_option('mira-cart')
        page.locator('[data-contract-count]').fill('2')
        page.locator('[data-contract-reward]').fill('17')
        page.locator('[data-contract-deadline]').fill('900')
        page.locator('#decision-goal-add').click()
        page.locator('[data-goal-id]').fill('notes-reserve')
        page.locator('[data-goal-description]').fill('Keep Field Notes available')
        page.locator('[data-goal-metric]').select_option('stock_level')
        page.locator('[data-goal-item]').fill('Field Notes')
        page.locator('[data-goal-target]').fill('4')
        expect(page.locator('#decision-goal-error')).to_be_empty()

        page.locator('#entity-save').click()
        assert 'Map Placed Trader' in page.locator('[data-cell="92"]').get_attribute('title')
        page.locator('#map-place').click()
        page.locator('#map-place-kind').select_option('creature')
        page.locator('#map-place-start').click()
        page.locator('[data-cell="94"]').click()
        page.locator('#entity-name').fill('Map Placed Wisp')
        page.locator('#entity-id').fill('map-wisp')
        page.locator('#entity-save').click()
        assert 'Map Placed Wisp' in page.locator('[data-cell="94"]').get_attribute('title')
        page.locator('#map-place').click()
        page.locator('#map-place-kind').select_option('item')
        page.locator('#map-place-start').click()
        expect(page.locator('#entity-json')).to_be_visible()
        page.locator('#entity-name').fill('Map Authored Token')
        page.locator('#entity-id').fill('map-token')
        page.locator('#entity-save').click()
        page.locator('[data-view="economy"]').click()
        expect(page.get_by_text('Map Placed Trader', exact=True)).to_be_visible()
        page.locator('[data-view="map"]').click()
        page.locator('[data-cell="88"]').click()
        page.locator('#set-spawn').click()
        assert 'spawn' in page.locator('[data-cell="88"]').get_attribute('class').split()
        page.locator('#map-undo').click()
        assert 'spawn' in page.locator('[data-cell="87"]').get_attribute('class').split()
        page.locator('#map-redo').click()
        assert 'spawn' in page.locator('[data-cell="88"]').get_attribute('class').split()
        assert '♟' in page.locator('[data-cell="87"]').inner_text()
        page.screenshot(path=str(output / 'map.png'), full_page=True)
        page.locator('#tile-details').click()
        while page.locator('[data-resource-remove]').count():
            page.locator('[data-resource-remove]').first.click()
        page.locator('#tile-resource-add').click()
        page.locator('[data-resource-item]').fill('Herbs')
        page.locator('[data-resource-quantity]').fill('0')
        page.locator('#tile-services-save').click()
        expect(page.locator('#tile-service-error')).to_contain_text('positive whole number')
        page.locator('[data-resource-quantity]').fill('6')
        page.locator('#tile-service-station').fill('campfire')
        page.locator('#tile-service-rest').check()
        page.locator('#tile-services-save').click()
        page.locator('[data-map-layer="services"]').check()
        assert 'Herbs' in page.locator('[data-cell="88"]').get_attribute('title')
        assert 'Station: campfire' in page.locator('[data-cell="88"]').get_attribute('title')
        page.locator('[data-map-layer="services"]').uncheck()
        page.locator('#tile-details').click()
        expect(page.locator('[data-resource-quantity]')).to_have_value('6')
        expect(page.locator('#tile-service-station')).to_have_value('campfire')
        page.screenshot(path=str(output / 'tile-services.png'), full_page=True)
        page.locator('.modal-cancel').click()


        # Submap edits persist canonical default-map aliases and retain undo across navigation.
        for _ in range(5):
            page.locator('#map-zoom-in').click()
        expect(page.locator('#map-zoom-level')).to_have_text('225%')
        page.locator('#map-viewport').evaluate('(node) => {node.scrollLeft=123;node.scrollTop=145}')
        page.wait_for_function('mapViewports.get("overworld")?.left === 123 && mapViewports.get("overworld")?.top === 145')
        page.locator('#map-add').click()
        page.locator('#new-map-name').fill('Builder Lab')
        page.locator('#create-map-save').click()
        expect(page.locator('#map-select')).to_have_value('builder-lab')
        page.locator('[data-cell="0"]').click()
        before_connection_draft = page.evaluate('JSON.stringify(world)')
        page.locator('#tile-connections').click()
        connection = page.locator('[data-connection-row]').first
        connection.locator('.connection-arrival-picker > summary').click()
        arrival_grid = connection.locator('.connection-arrival-picker [role="grid"]')
        arrival_map_width = page.evaluate("world.maps.find(map => map.id === 'overworld').width")
        arrival_cell = arrival_map_width + 1
        expect(arrival_grid.locator('[data-location-picker-cell]')).to_have_count(page.evaluate("world.maps.find(map => map.id === 'overworld').width * world.maps.find(map => map.id === 'overworld').height"))
        arrival_grid.locator(f'[data-location-picker-cell="{arrival_cell}"]').click()
        expect(connection.locator('[data-connection-map]')).to_have_value('overworld')
        expect(connection.locator('[data-connection-x]')).to_have_value('1')
        expect(connection.locator('[data-connection-y]')).to_have_value('1')
        expect(connection.locator('.connection-arrival-picker [id$="-readout"]')).to_contain_text('passable')
        page.locator('.modal-cancel').click()
        assert page.evaluate('JSON.stringify(world)') == before_connection_draft
        page.locator('#map-settings').click()
        page.locator('#map-spawn-picker-grid').evaluate("node => node.closest('details').open = true")
        page.locator('#map-spawn-picker-grid [data-location-picker-cell="9"]').click()
        entry_row = page.locator('#map-entry-list [data-entry-row]').first
        entry_row.locator(':scope > details > summary').click()
        entry_row.locator('[data-location-picker-cell="18"]').click()
        expect(page.locator('#map-spawn-x')).to_have_value('1')
        expect(entry_row.locator('[data-entry-x]')).to_have_value('2')
        page.locator('#map-settings-save').click()
        assert page.evaluate("world.maps.find(map => map.id === 'builder-lab').spawn") == {'x':1,'y':1}
        assert page.evaluate("world.maps.find(map => map.id === 'builder-lab').entry_points.start") == {'x':2,'y':2}
        page.locator('[data-cell="0"]').click()
        original_submap_pair = page.evaluate("world.maps.find(map => map.id === 'builder-lab').tiles[0].exits.return.pair_id")
        page.locator('#tile-connections').click()
        paired = page.locator('[data-connection-row]').first
        paired.locator('[data-connection-reciprocal]').check()
        paired.locator('[data-connection-return-label]').fill('lab-return')
        paired.locator('.connection-arrival-picker > summary').click()
        paired.locator('[data-location-picker-cell="17"]').click()
        page.locator('#connections-save').click()
        pair_state = page.evaluate("""() => {
          const area = world.maps.find(map => map.id === 'builder-lab');
          const exit = area.tiles[0].exits.return;
          const reverse = world.maps.find(map => map.id === 'overworld').tiles[17].exits['lab-return'];
          return {pair_id:exit.pair_id, return_exit:exit.return_exit, reverse_pair_id:reverse.pair_id, reverse_target:reverse};
        }""")
        assert pair_state['pair_id'] and pair_state['pair_id'] == pair_state['reverse_pair_id']
        assert pair_state['return_exit'] == 'lab-return'
        assert pair_state['reverse_target']['return_exit'] == 'return'
        page.locator('#map-settings').click()
        page.locator('#map-duplicate').click()
        duplicate_state = page.evaluate("""() => {
          const copy = world.maps.find(map => map.id !== 'builder-lab' && map.name === 'Builder Lab Copy');
          const original = world.maps.find(map => map.id === 'builder-lab');
          return {copyId:copy.id, copiedPair:copy.tiles[0].exits.return.pair_id ?? null, originalPair:original.tiles[0].exits.return.pair_id};
        }""")
        assert duplicate_state['copiedPair'] is None
        assert duplicate_state['originalPair'] == pair_state['pair_id']
        page.locator('#map-undo').click()
        assert page.evaluate("world.maps.some(map => map.id === 'builder-lab-copy')") is False
        page.locator('#map-undo').click()
        assert page.evaluate("world.maps.find(map => map.id === 'builder-lab').tiles[0].exits.return.pair_id") == original_submap_pair
        assert page.evaluate("world.maps.find(map => map.id === 'overworld').tiles[17].exits?.['lab-return']") is None
        page.locator('#map-redo').click()
        page.locator('#tile-connections').click()
        page.locator('[data-connection-row] [data-connection-remove]').click()
        page.locator('#connections-save').click()
        assert page.evaluate("world.maps.find(map => map.id === 'builder-lab').tiles[0].exits") is None
        assert page.evaluate("world.maps.find(map => map.id === 'overworld').tiles[17].exits?.['lab-return']") is None
        page.locator('#tile-connections').click()
        page.locator('#connection-add').click()
        internal = page.locator('[data-connection-row]').last
        internal.locator('[data-connection-label]').fill('inside')
        internal.locator('[data-connection-reciprocal]').check()
        internal.locator('[data-connection-return-label]').fill('back')
        internal.locator('.connection-arrival-picker > summary').click()
        internal.locator('[data-location-picker-cell="1"]').click()
        page.locator('#connections-save').click()
        original_internal_pair = page.evaluate("world.maps.find(map => map.id === 'builder-lab').tiles[0].exits.inside.pair_id")
        page.locator('#map-settings').click()
        page.locator('#map-duplicate').click()
        cloned_internal = page.evaluate("""() => {
          const copy = world.maps.find(map => map.id === 'builder-lab-copy');
          return {forward:copy.tiles[0].exits.inside, reverse:copy.tiles[1].exits.back};
        }""")
        assert cloned_internal['forward']['pair_id'] != original_internal_pair
        assert cloned_internal['forward']['pair_id'] == cloned_internal['reverse']['pair_id']
        assert cloned_internal['forward']['return_exit'] == 'back'
        assert page.evaluate("""async () => {
          await flushSave();
          const response = await fetch(`/api/worlds/${world.id}/validate`, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
          return response.status;
        }""") == 200
        page.locator('#map-undo').click()
        page.locator('#map-undo').click()
        page.locator('#map-search-input').fill('map-scout')
        expect(page.locator('#map-search-results')).to_contain_text('No matching places or objects')
        page.locator('#map-search-input').fill('')
        page.locator('#resize-map').click()
        page.locator('#map-w').fill('4')
        page.locator('#map-h').fill('4')
        expect(page.locator('#resize-save')).to_be_enabled()
        page.locator('#resize-save').click()
        expect(page.locator('.map-cell')).to_have_count(16)
        expect(page.locator('#map-zoom-level')).to_have_text('100%')
        page.locator('#map-zoom-in').click()
        expect(page.locator('#map-zoom-level')).to_have_text('125%')
        page.locator('[data-cell="5"]').click()
        page.locator('#tile-name').fill('Lab Room')
        before_navigation = page.evaluate('JSON.stringify(world)')
        assert page.evaluate('world.width') == 16
        assert page.evaluate('world.spawn') == {'x': 8, 'y': 5}
        assert page.evaluate('activeMap().width') == 4
        page.locator('#map-select').select_option('overworld')
        assert page.evaluate('JSON.stringify(world)') == before_navigation
        expect(page.locator('#map-zoom-level')).to_have_text('225%')
        assert page.locator('#map-viewport').evaluate('(node) => [node.scrollLeft,node.scrollTop]') == [123,145]
        page.locator('[data-cell="88"]').click()
        page.locator('#map-select').select_option('builder-lab')
        expect(page.locator('[data-cell="5"]')).to_have_class(re.compile(r'\bcursor\b'))
        expect(page.locator('#map-zoom-level')).to_have_text('125%')
        expect(page.locator('#tile-name')).to_have_value('Lab Room')
        page.locator('#map-select').select_option('overworld')
        expect(page.locator('[data-cell="88"]')).to_have_class(re.compile(r'\bcursor\b'))
        page.locator('#map-undo').click()
        expect(page.locator('#map-select')).to_have_value('builder-lab')
        expect(page.locator('#tile-name')).to_have_value('Grassland')
        page.locator('#map-redo').click()
        expect(page.locator('#tile-name')).to_have_value('Lab Room')
        world_url = base + '/api/worlds/' + page.evaluate('world.id')
        def reject_world_save(route):
            if route.request.method == 'PUT':
                route.fulfill(status=503, content_type='application/json', body=json.dumps({'detail':'Temporary save outage'}))
            else:
                route.continue_()
        page.route(world_url, reject_world_save)
        page.locator('#tile-desc').fill('Recover this local room draft.')
        expect(page.locator('#save-recovery')).to_be_visible()
        expect(page.locator('#save-recovery')).to_contain_text('Temporary save outage')
        with page.expect_download() as draft_download:
            page.locator('#save-download-draft').click()
        draft_path = output / 'recovered-draft.json'
        draft_download.value.save_as(str(draft_path))
        draft = json.loads(draft_path.read_text())
        assert next(m for m in draft['maps'] if m['id']=='builder-lab')['tiles'][5]['description'] == 'Recover this local room draft.'
        assert draft['width'] == 16
        page.reload()
        expect(page.locator('#draft-recovery')).to_be_visible()
        assert page.locator('#view').evaluate('(node) => node.inert')
        page.locator('#draft-restore').click()
        assert not page.locator('#view').evaluate('(node) => node.inert')
        expect(page.locator('#map-select')).to_have_value('builder-lab')
        expect(page.locator('#tile-desc')).to_have_value('Recover this local room draft.')
        expect(page.locator('#save-recovery')).to_be_visible()
        page.screenshot(path=str(output / 'draft-recovery.png'), full_page=True)
        page.unroute(world_url, reject_world_save)
        page.locator('#save-retry').click()
        expect(page.locator('#save-recovery')).to_be_hidden()
        persisted = page.request.get(world_url).json()
        assert next(m for m in persisted['maps'] if m['id']=='builder-lab')['tiles'][5]['description'] == 'Recover this local room draft.'
        # A recovered draft retains its original revision and cannot overwrite another editor.
        page.route(world_url, reject_world_save)
        page.locator('#tile-desc').fill('Unsaved conflicting description.')
        expect(page.locator('#save-recovery')).to_contain_text('Temporary save outage')
        newer_server = page.request.get(world_url).json()
        newer_server['description'] += ' Updated by another editor.'
        next(m for m in newer_server['maps'] if m['id']=='builder-lab')['tiles'][5]['description'] = 'Server room revision.'
        assert page.request.put(world_url, data=newer_server).ok
        page.reload()
        expect(page.locator('#draft-recovery')).to_contain_text('server has changed')
        page.locator('#draft-restore').click()
        expect(page.locator('#tile-desc')).to_have_value('Unsaved conflicting description.')
        page.unroute(world_url, reject_world_save)
        page.locator('#save-retry').click()
        expect(page.locator('#save-recovery')).to_contain_text('another editor')
        unchanged = page.request.get(world_url).json()
        assert unchanged['description'] == newer_server['description']
        assert next(m for m in unchanged['maps'] if m['id']=='builder-lab')['tiles'][5]['description'] == 'Server room revision.'
        page.locator('#save-review-conflict').click()
        expect(page.locator('.conflict-review')).to_contain_text('Builder Lab · tile 1, 1 · description')
        expect(page.locator('.conflict-review')).to_contain_text('Server room revision.')
        page.locator('#conflict-apply').click()
        expect(page.locator('#conflict-error')).to_contain_text('Choose a version')
        page.locator('[data-conflict-choice]').select_option('local')
        page.screenshot(path=str(output / 'conflict-review.png'), full_page=True)
        page.locator('#conflict-apply').click()
        expect(page.locator('#save-recovery')).to_be_hidden()
        merged = page.request.get(world_url).json()
        assert merged['description'] == newer_server['description']
        assert next(m for m in merged['maps'] if m['id']=='builder-lab')['tiles'][5]['description'] == 'Unsaved conflicting description.'
        expect(page.locator('#tile-desc')).to_have_value('Unsaved conflicting description.')
        page.locator('#map-place').click()
        page.locator('#map-place-kind').select_option('npc')
        page.locator('#map-place-start').click()
        page.locator('[data-cell="6"]').click()
        page.locator('#entity-name').fill('Lab Scout')
        page.locator('#entity-id').fill('lab-scout')
        page.locator('#entity-save').click()
        page.locator('#map-undo').click()
        assert 'Lab Scout' not in page.locator('[data-cell="6"]').get_attribute('title')
        page.locator('#map-redo').click()
        assert 'Lab Scout' in page.locator('[data-cell="6"]').get_attribute('title')
        page.evaluate('flushSave()')
        source = page.request.get(base + '/api/worlds/' + page.evaluate('world.id')).json()
        assert source['width'] == 16 and source['spawn'] == {'x': 8, 'y': 5}
        assert next(m for m in source['maps'] if m['id']=='builder-lab')['width'] == 4
        page.locator('#map-select').select_option('overworld')
        page.locator('[data-view="npcs"]').click()
        before_cancel = page.evaluate('JSON.stringify(world.npcs[0])')
        page.locator('[data-edit="0"]').click()
        page.locator('#entity-name').fill('Discard this edit')
        page.locator('.modal-cancel').click()
        assert page.evaluate('JSON.stringify(world.npcs[0])') == before_cancel

        page.locator('[data-view="npcs"]').click()
        page.locator('[data-add="npcs"]').click()
        draft = {'id': 'keeper', 'name': 'Authored Keeper', 'role': 'Guide',
                 'location': {'x': 8, 'y': None}, 'dialog': '', 'inventory': [{'item': '', 'count': 2}]}
        page.locator('#entity-json').fill(json.dumps(draft))
        page.locator('#modal-ai').click()
        assert json.loads(page.locator('#ai-scaffold').input_value()) == draft
        page.locator('#ai-instructions').fill('Complete the missing dialogue and nested fields.')
        page.locator('#ai-generate').click()
        expect(page.locator('#ai-output')).to_be_visible()
        completed = json.loads(page.locator('#ai-output').input_value())
        assert completed['name'] == 'Authored Keeper'
        assert completed['location'] == {'x': 8, 'y': 5}
        assert completed['inventory'] == [{'item': 'Herbs', 'count': 2}]
        assert ModelFixture.requests[-1]['scaffold'] == draft
        page.screenshot(path=str(output / 'scaffold.png'), full_page=True)
        page.locator('#ai-apply').click()
        page.locator('#entity-save').click()
        expect(page.get_by_text('Authored Keeper', exact=True)).to_be_visible()

        page.locator('#entity-search').fill('no matching character')
        expect(page.locator('#entity-no-matches')).to_be_visible()
        page.locator('#entity-search').fill('Authored Keeper')
        expect(page.locator('#entity-match-count')).to_have_text('1 shown')
        page.locator('.entity-card:not(.hidden) [data-duplicate]').click()
        copied = json.loads(page.locator('#entity-json').input_value())
        assert copied['id'] == 'keeper-copy'
        page.locator('#entity-name').fill('Second Keeper')
        assert json.loads(page.locator('#entity-json').input_value())['name'] == 'Second Keeper'
        page.screenshot(path=str(output / 'entity-editor.png'), full_page=True)
        page.locator('#entity-save').click()
        expect(page.locator('#entity-match-count')).to_have_text('5 shown')
        page.locator('[data-view="access"]').click()
        page.locator('#add-key').click()
        page.locator('#player-name').fill('builder')
        key = asyncssh.generate_private_key('ssh-ed25519').export_public_key().decode()
        page.locator('#public-key').fill(key)
        page.locator('#key-save').click()
        expect(page.locator('.key-row .badge')).to_have_text('builder')

        page.locator('[data-view="customize"]').click()
        page.locator('#building-enabled').select_option('true')
        page.locator('#building-room-editing').select_option('true')
        page.locator('#building-cost').fill('40')
        page.locator('#building-materials [data-building-material-add]').click()
        page.locator('#building-materials [data-building-material-item]').fill('Trail Rations')
        page.locator('#building-materials [data-building-material-count]').fill('2')
        expect(page.locator('[data-building-override="forest"] [data-building-default]')).to_contain_text('Default: 40 Crowns')
        expect(page.locator('[data-building-override="forest"] [data-building-default]')).to_contain_text('Trail Rations ×2')
        forest_override = page.locator('[data-building-override="forest"]')
        forest_override.locator('[data-building-override-enabled]').check()
        forest_override.locator('[data-building-override-cost]').fill('75')
        forest_override.locator('[data-building-material-row] [data-building-material-item]').last.fill('Trail Rations')
        forest_override.locator('[data-building-material-row] [data-building-material-count]').last.fill('3')
        page.locator('#preview-building').click()
        expect(page.locator('#building-cost-preview')).to_contain_text('Grassland: 40 Crowns')
        expect(page.locator('#building-cost-preview')).to_contain_text('Trail Rations ×2')
        expect(page.locator('#building-cost-preview')).to_contain_text('Forest: 75 Crowns')
        expect(page.locator('#building-cost-preview')).to_contain_text('Trail Rations ×3')
        page.locator('[data-building-structure-add]').click()
        structure = page.locator('[data-building-structure-row]').last
        structure.locator('[data-structure-field="id"]').fill('campfire-hut')
        structure.locator('[data-structure-field="name"]').fill('Campfire Hut')
        structure.locator('[data-structure-field="cost"]').fill('12')
        structure.locator('[data-structure-field="width"]').fill('2')
        structure.locator('[data-structure-field="height"]').fill('1')
        structure.locator('[data-structure-field="service"]').select_option('station')
        structure.locator('[data-structure-field="station"]').fill('campfire')
        structure.locator('[data-building-material-add]').click()
        structure.locator('[data-building-material-item]').fill('Trail Rations')
        structure.locator('[data-building-material-count]').fill('4')
        page.locator('#save-building').click()
        saved_building = page.evaluate("world.building")
        assert saved_building['tile_materials'] == [{'item':'Trail Rations','count':2}]
        assert saved_building['tile_cost_overrides'] == [{'biome':'forest','currency':75,'materials':[{'item':'Trail Rations','count':3}]}]
        assert saved_building['structures'][0]['footprint'] == {'width':2,'height':1}
        assert saved_building['structures'][0]['service'] == {'type':'station','station':'campfire'}
        assert saved_building['structures'][0]['materials'] == [{'item':'Trail Rations','count':4}]
        page.locator('#trade-enabled').select_option('true')
        page.locator('#trade-homes').select_option('true')
        page.locator('#save-player-trade').click()
        page.locator('#provider-profile-add').click()
        page.locator('[data-provider-field="id"]').fill('commerce-fixture')
        page.locator('[data-provider-field="adapter"]').select_option('laya_systemone')
        page.locator('[data-provider-field="model"]').fill('typed-decisions')
        page.locator('[data-provider-field="endpoint_binding"]').fill('https://example.invalid')
        expect(page.locator('#provider-form-error')).to_contain_text('environment variable names')
        page.locator('[data-provider-field="endpoint_binding"]').fill('TEST_COMMERCE_ENDPOINT')
        expect(page.locator('#provider-form-error')).to_be_empty()
        page.locator('#decision-profiles').locator('..').locator('summary').click()
        page.locator('#decision-profiles').fill(json.dumps([{'id':'commerce-fixture','adapter':'unsupported_adapter','model':'typed-decisions','endpoint_binding':'TEST_COMMERCE_ENDPOINT'}]))
        page.locator('#save-decision-profiles').click()
        expect(page.locator('#provider-form-error')).to_contain_text('supported adapter')
        expect(page.locator('[data-provider-field="adapter"]')).to_have_value('unsupported_adapter')
        page.locator('[data-provider-field="adapter"]').select_option('laya_systemone')
        expect(page.locator('#provider-form-error')).to_be_empty()
        page.locator('#decision-profiles').locator('..').locator('summary').click()
        page.locator('[data-provider-row]').screenshot(path=str(output / 'provider-editor.png'))
        page.locator('#save-decision-profiles').click()
        page.locator('[data-view="npcs"]').click()
        page.locator('[data-edit="0"]').click()
        page.locator('#decision-enabled').select_option('true')
        page.locator('#decision-provider').select_option('commerce-fixture')
        page.locator('.decision-action[value="trade"]').check()
        page.locator('.decision-action[value="gather"]').check()
        page.locator('.decision-action[value="craft"]').check()
        page.locator('.decision-gather-item[value="Wild Berries"]').check()
        page.locator('.decision-craft-recipe[value="trail-rations"]').check()
        page.locator('#npc-gather-units').fill('2')
        page.locator('#npc-craft-batches').fill('1')
        page.locator('#npc-resource-capacity').fill('12')
        page.locator('#npc-resource-inventory').fill('{}')
        page.locator('#npc-commerce-inventory').fill('{"Herbs":1}')
        page.locator('#delivery-task-id').fill('herb-bundle')
        page.locator('#delivery-task-name').fill('Herb bundle')
        page.locator('#delivery-task-description').fill('Bring herbs for camp')
        page.locator('#delivery-task-item').select_option(label='Herbs')
        page.locator('#delivery-task-count').fill('2')
        page.locator('#delivery-task-reward').fill('10')
        page.locator('#delivery-task-add').click()
        page.locator('#decision-goal-add').click()
        page.locator('[data-goal-id]').fill('herb-deliveries')
        page.locator('[data-goal-description]').fill('Complete an herb supply delivery')
        page.locator('[data-goal-metric]').select_option('deliveries_completed')
        expect(page.locator('#decision-goal-error')).to_contain_text('requires a target')
        page.locator('[data-goal-target]').fill('1')
        page.locator('[data-goal-priority]').fill('2')
        expect(page.locator('#decision-goal-error')).to_be_empty()

        page.locator('.decision-market[value="mira-cart"]').check()
        page.locator('.decision-commerce-item[value="Field Notes"]').check()
        page.locator('#npc-commerce-currency').fill('60')
        page.locator('#npc-commerce-reserve').fill('8')
        page.locator('#npc-commerce-capacity').fill('7')
        page.locator('#npc-commerce-units').fill('2')
        page.locator('#npc-commerce-spend').fill('25')
        page.locator('#entity-save').click()
        page.locator('[data-edit="0"]').click()
        assert page.locator('.decision-action[value="trade"]').is_checked()
        assert page.locator('.decision-action[value="gather"]').is_checked()
        assert page.locator('.decision-action[value="craft"]').is_checked()
        assert page.locator('.decision-action[value="task"]').is_checked()
        assert page.locator('.decision-gather-item[value="Wild Berries"]').is_checked()
        assert page.locator('.decision-craft-recipe[value="trail-rations"]').is_checked()
        expect(page.locator('#npc-gather-units')).to_have_value('2')
        expect(page.locator('#npc-commerce-inventory')).to_contain_text('Herbs')
        assert json.loads(page.locator('#decision-tasks').input_value())[0]['delivery'] == {'item':'Herbs','count':2}
        assert page.locator('.decision-market[value="mira-cart"]').is_checked()
        assert page.locator('.decision-commerce-item[value="Field Notes"]').is_checked()
        expect(page.locator('#npc-commerce-currency')).to_have_value('60')
        expect(page.locator('#npc-commerce-reserve')).to_have_value('8')
        expect(page.locator('[data-goal-id]')).to_have_value('herb-deliveries')
        expect(page.locator('[data-goal-target]')).to_have_value('1')
        page.screenshot(path=str(output / 'npc-commerce-editor.png'), full_page=True)
        page.locator('.modal-cancel').click()

        page.locator('[data-view="customize"]').click()
        expect(page.locator('[data-provider-field="id"]')).to_have_value('commerce-fixture')
        page.locator('[data-provider-remove]').click()
        page.locator('#save-decision-profiles').click()
        expect(page.locator('#provider-form-error')).to_contain_text('Mira the Cartographer')
        page.locator('[data-view="playtest"]').click()
        expect(page.locator('#preview-log')).to_contain_text('Browser Grove')
        page.locator('#preview-player').fill('quest-author-flow')
        def playtest_command(command, expected):
            page.locator('#preview-command').fill(command)
            page.locator('#preview-form button').click()
            expect(page.locator('#preview-log')).to_contain_text(expected, timeout=15000)
        for command, expected in [
            ('west','You travel west.'),
            ('talk mira','What would you like to know?'),
            ('choose 1','Quest accepted: A First Survey'),
            ('east','You travel east.'),
            ('east','Quest complete: A First Survey'),
            ('west','You travel west.'),
            ('west','You travel west.'),
            ('talk mira','What would you like to know?'),
            ('choose 1','Mira needs a field survey.'),
            ('choose 1','Quest accepted: Trail Supplies'),
            ('gather Wild Berries 2','Gathered 2'),
            ('gather Herbs','Gathered 1 Herbs'),
            ('craft trail-rations','Crafted 1'),
            ('give Trail Rations to mira','Quest complete: Trail Supplies'),
            ('inventory','Torch ×1'),
        ]:
            playtest_command(command, expected)
        page.locator('#preview-player').fill('builder')
        page.locator('#preview-command').fill('gather Herbs 2')
        page.locator('#preview-form button').click()
        expect(page.locator('#preview-log')).to_contain_text('Gathered 2 Herbs')
        expect(page.locator('#preview-dashboard')).to_contain_text('Herbs ×2')
        page.locator('#preview-command').fill('talk keeper')
        page.locator('#preview-form button').click()
        expect(page.locator('#preview-log')).to_contain_text('The grove remembers every traveler.')
        expect(page.locator('#preview-dashboard')).to_contain_text('100/100')
        page.locator('[data-preview-command="east"]').click()
        expect(page.locator('#preview-dashboard')).to_contain_text('9, 5')
        page.locator('[data-preview-command="west"]').click()
        expect(page.locator('#preview-dashboard')).to_contain_text('8, 5')
        page.locator('[data-preview-command="map"]').click()
        expect(page.locator('#preview-log')).to_contain_text('? unexplored')
        page.locator('#preview-command').press('ArrowUp')
        expect(page.locator('#preview-command')).to_have_value('map')
        page.locator('#preview-command').fill('')
        page.locator('[data-preview-command="west"]').click()
        expect(page.locator('#preview-dashboard')).to_contain_text('7, 5')
        page.locator('#decision-preview-owner').select_option('npc:mira')
        page.locator('#decision-preview').click()
        expect(page.locator('#decision-preview-output')).to_contain_text('Eligible actions')
        expect(page.locator('#decision-preview-output')).to_contain_text('Endpoint binding missing')
        expect(page.locator('#decision-preview-output')).to_contain_text('Observed owner state')
        page.locator('#decision-step').click()
        expect(page.locator('#decision-preview-output')).to_contain_text('Selection & outcome')
        expect(page.locator('#decision-preview-output')).to_contain_text('fallback')
        expect(page.locator('#decision-preview-output')).to_contain_text('State after this step')
        page.screenshot(path=str(output / 'decision-inspector.png'), full_page=True)
        page.locator('#decision-pause').click()
        expect(page.locator('#decision-preview-output')).to_contain_text('Controller paused')
        page.locator('#decision-preview').click()
        expect(page.locator('#decision-preview-output')).to_contain_text('Paused · manual stepping remains available')
        page.locator('#decision-resume').click()
        expect(page.locator('#decision-preview-output')).to_contain_text('Controller resumed')
        page.locator('[data-preview-command="east"]').click()
        expect(page.locator('#preview-dashboard')).to_contain_text('8, 5')
        page.locator('[data-preview-command="east"]').click()
        for _ in range(3):
            page.locator('#preview-command').fill('attack moss-wolf')
            page.locator('#preview-form button').click()
        expect(page.locator('#preview-log')).to_contain_text('Moss Wolf is defeated')
        expect(page.locator('#preview-dashboard')).to_contain_text('12')
        page.locator('#preview-start-grid [data-location-picker-cell="86"]').click()
        expect(page.locator('#preview-start-readout')).to_contain_text('6, 5')
        page.locator('#preview-reset').click()
        expect(page.locator('#preview-dashboard')).to_contain_text('6, 5')
        page.locator('#preview-player').fill('gui-other')
        page.locator('#preview-player').dispatch_event('change')
        page.locator('#preview-start-grid [data-location-picker-cell="34"]').click()
        page.locator('#preview-reset').click()
        expect(page.locator('#preview-dashboard')).to_contain_text('2, 2')
        page.locator('#preview-player').fill('builder')
        page.locator('#preview-player').dispatch_event('change')
        expect(page.locator('#preview-dashboard')).to_contain_text('6, 5')
        page.evaluate("""() => { const quest=world.quests.find(item=>item.id==='first-survey');quest.objectives=[{type:'collect',target:'Unobtainium',count:1}]; }""")
        page.locator('#build-check').click()
        expect(page.locator('#build-issues')).to_be_visible()
        expect(page.locator('#build-issues')).to_contain_text('Unobtainium')
        expect(page.locator('#build-issues')).to_contain_text('quest first-survey.objectives')
        page.locator('[data-build-issue-open="0"]').click()
        expect(page.locator('#entity-id')).to_have_value('first-survey')
        assert 'Unobtainium' in page.locator('#entity-json').input_value()
        assert page.evaluate('document.activeElement.dataset.objectiveField') == 'target'
        page.locator('[data-objective-index="0"] [data-objective-field="type"]').select_option('discover')
        page.locator('[data-objective-index="0"] [data-objective-field="count"]').fill('3')
        page.locator('#entity-save').click()
        page.locator('[data-view="playtest"]').click()
        page.locator('#build-check').click()
        expect(page.locator('#toast')).to_contain_text('Build ready:')
        expect(page.locator('#export-readiness')).to_contain_text('Ready · revision')
        expect(page.locator('#export-readiness')).to_contain_text('mud-data')
        expect(page.locator('#build-issues')).to_be_hidden()
        page.evaluate("""() => { window.verifyOriginalBiome=activeMap().tiles[2].biome;activeMap().tiles[2].biome='missing-biome'; }""")
        page.locator('#build-check').click()
        expect(page.locator('#build-issues')).to_contain_text('unknown biome')
        page.locator('[data-build-issue-open="0"]').click()
        assert page.locator('[data-cell="2"]').evaluate('(node) => node.classList.contains("cursor")')
        page.evaluate('() => { activeMap().tiles[2].biome=window.verifyOriginalBiome; }')
        page.locator('[data-view="playtest"]').click()
        page.locator('#build-check').click()
        expect(page.locator('#build-issues')).to_be_hidden()
        page.evaluate("""() => { window.verifyQuestObjectives=world.quests.slice(0,2).map(quest=>structuredClone(quest.objectives));world.quests[0].objectives=[{type:'collect',target:'Moonstone',count:1}];world.quests[1].objectives=[{type:'collect',target:'Sunsteel',count:1}]; }""")
        page.locator('#build-check').click()
        expect(page.locator('#build-issues')).to_contain_text('2 issues')
        page.evaluate("""() => world.quests.slice(0,2).forEach((quest,index)=>{quest.objectives=window.verifyQuestObjectives[index]})""")
        page.locator('#build-check').click()
        expect(page.locator('#build-issues')).to_be_hidden()
        with page.expect_download() as download_info:
            page.locator('#export-btn').click()
        archive_path = output / 'browser-game.zip'
        download_info.value.save_as(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            world = json.loads(archive.read('world/world.json'))
            assert world['decision_providers'] == [{'id':'commerce-fixture','adapter':'laya_systemone','model':'typed-decisions','endpoint_binding':'TEST_COMMERCE_ENDPOINT'}]
            assert world['building']['allow_room_editing'] is True
            assert world['economy']['player_trade']['enabled'] is True
            assert world['economy']['player_trade']['allow_in_homes'] is True
            assert world['spawn'] == {'x': 8, 'y': 5}
            assert world['tiles'][88]['name'] == 'Browser Grove'
            assert world['tiles'][88]['resources'] == [{'item':'Herbs','quantity':6}]
            assert world['tiles'][88]['station'] == 'campfire'
            assert world['tiles'][88]['rest'] is True
            assert next(n for n in world['npcs'] if n['id']=='keeper')['name'] == 'Authored Keeper'
            placed = next(n for n in world['npcs'] if n['id']=='map-scout')
            assert placed['location'] == {'x': 9, 'y': 5}
            assert placed['map_id'] == world['default_map_id']
            creature = next(e for e in world['creatures'] if e['id']=='map-wisp')
            assert creature['location'] == {'x': 14, 'y': 5}
            assert creature['map_id'] == world['default_map_id']
            assert next(item for item in world['items'] if item['id']=='map-token')['name'] == 'Map Authored Token'
            market = next(v for v in world['economy']['vendors'] if v['id']=='map-market')
            assert market['location'] == {'x': 12, 'y': 5}
            assert market['map_id'] == world['default_map_id']
            assert market['stock'][0] == {'item':'Field Notes','price':7,'sell_price':3,'quantity':8,'consumption':{'interval_seconds':600,'amount':1}}
            assert market['stock'][1] == {'item':'Torch','price':4,'sell_price':2,'quantity':5,'pricing':{'mode':'stock_based','base_price':4,'target_stock':10,'sensitivity':1,'min_price':1,'max_price':12,'spread':2,'adjustment_step':2},'restock':{'interval_seconds':60,'amount':2,'max_quantity':10},'production':{'interval_seconds':120,'amount':1,'max_quantity':10,'inputs':[{'item':'Field Notes','count':2}]}}
            assert market['decision_policy']['goals'] == [{'id':'notes-reserve','description':'Keep Field Notes available','metric':'stock_level','priority':1,'item':'Field Notes','target':4}]
            assert market['contracts'] == [{'id':'browser-notes-run','name':'Browser Notes Run','item':'Field Notes','count':2,'destination_market_id':'mira-cart','reward_currency':17,'prerequisites':[],'deadline_seconds':900}]
            mira = next(n for n in world['npcs'] if n['id']=='mira')
            assert mira['decision_policy']['goals'] == [{'id':'herb-deliveries','description':'Complete an herb supply delivery','metric':'deliveries_completed','priority':2,'target':1}]
            assert mira['decision_policy']['commerce']['markets'] == ['mira-cart']
            assert mira['decision_policy']['commerce']['items'] == ['Field Notes']
            assert mira['decision_policy']['commerce']['max_units_per_decision'] == 2
            assert mira['decision_policy']['gathering']['items'] == ['Wild Berries']
            assert mira['decision_policy']['crafting']['recipes'] == ['trail-rations']
            assert mira['decision_policy']['tasks'][0]['delivery'] == {'item':'Herbs','count':2}
            assert mira['decision_policy']['commerce']['starting_inventory'] == {'Herbs': 1}
            assert mira['decision_policy']['resources']['starting_inventory'] == {}
            assert json.loads(archive.read('world/players.json'))[0]['username'] == 'builder'
        page.screenshot(path=str(output / 'playtest.png'), full_page=True)

        page.locator('#world-switch').click()
        page.locator('#new-realm-name').fill('Second Realm')
        page.locator('#new-realm-create').click()
        expect(page.locator('#world-name-nav')).to_have_text('Second Realm')
        page.locator('#world-switch').click()
        page.locator('[data-world="starting-realm"]').click()
        expect(page.locator('#world-name-nav')).to_have_text('The Wayfarer Realm')
        legacy_import = app_module.new_world()
        legacy_import['id'] = 'legacy-import'
        legacy_import['tiles'][5 * legacy_import['width'] + 7]['name'] = 'Legacy Keepsake'
        legacy_path = output / 'legacy-import.json'
        legacy_path.write_text(json.dumps(legacy_import, indent=2))
        page.locator('#world-switch').click()
        page.locator('#import-world-file').set_input_files(str(legacy_path))
        page.wait_for_function("world.id === 'legacy-import'")
        expect(page.locator('#world-name-nav')).to_have_text('The Wayfarer Realm')
        expect(page.locator('#toast')).to_contain_text('original file kept')
        assert legacy_path.exists()
        assert page.evaluate("world.id") == 'legacy-import'
        assert page.evaluate("activeMap().tiles[87].name") == 'Legacy Keepsake'
        saved_starting = page.evaluate("async () => await fetch('/api/worlds/starting-realm').then(response=>response.json())")
        assert any(npc['id']=='keeper' for npc in saved_starting['npcs']), [npc['id'] for npc in saved_starting['npcs']]
        page.locator('#world-switch').click()
        page.locator('[data-world="starting-realm"]').click()
        page.wait_for_function("world.id === 'starting-realm'")
        expect(page.locator('#world-name-nav')).to_have_text('The Wayfarer Realm')
        assert page.evaluate("world.id") == 'starting-realm', page.evaluate("world.id")
        assert page.evaluate("world.npcs.some(npc=>npc.id==='keeper')")
        page.reload()
        expect(page.locator('#world-name-nav')).to_have_text('The Wayfarer Realm')
        page.locator('[data-view="npcs"]').click()
        expect(page.get_by_text('Authored Keeper', exact=True)).to_be_visible()
        page.locator('[data-add="npcs"]').click()
        page.locator('#entity-id').fill('recovered-guide')
        page.locator('#entity-name').fill('Recovered Guide')
        page.locator('[data-entity-field="role"]').fill('Trail keeper')
        page.reload()
        expect(page.locator('#draft-recovery')).to_contain_text('Unapplied editor draft')
        page.locator('#modal-draft-restore').click()
        expect(page.locator('#entity-name')).to_have_value('Recovered Guide')
        expect(page.locator('[data-entity-field="role"]')).to_have_value('Trail keeper')
        page.locator('#entity-save').click()
        page.locator('.entity-card').filter(has_text='Recovered Guide').locator('[data-edit]').click()
        page.locator('[data-entity-field="role"]').fill('Roadside guide')
        page.locator('#modal-ai').click()
        page.locator('#ai-concept').fill('Recovered nested guide')
        page.locator('#ai-instructions').fill('Keep the roadside role.')
        page.reload()
        if page.locator('#draft-restore').is_visible():
            page.locator('#draft-restore').click()
        expect(page.locator('#draft-recovery')).to_contain_text('nested workbench draft')
        page.locator('#modal-draft-restore').click()
        expect(page.locator('#ai-concept')).to_have_value('Recovered nested guide')
        expect(page.locator('#ai-instructions')).to_have_value('Keep the roadside role.')
        expect(page.locator('#ai-scaffold')).to_contain_text('Roadside guide')
        page.locator('#ai-generate').click()
        expect(page.locator('#ai-apply')).to_be_visible()
        page.locator('#ai-apply').click()
        expect(page.locator('#entity-name')).to_have_value('Recovered Guide')
        expect(page.locator('[data-entity-field="role"]')).to_have_value('Roadside guide')
        page.locator('#entity-save').click()
        page.set_viewport_size({'width': 390, 'height': 844})
        expect(page.locator('#world-switch')).to_be_visible()
        for name in ['map', 'npcs', 'quests', 'crafting', 'economy', 'entities', 'playtest', 'access']:
            page.locator(f'[data-view="{name}"]').click()
            expect(page.locator('h1')).to_be_visible()
        page.locator('[data-view="map"]').click()
        expect(page.locator('[data-map-panel="map"]')).to_be_visible()
        expect(page.locator('[data-map-panel="palette"]')).to_be_hidden()
        page.locator('[data-workspace-tab="palette"]').click()
        expect(page.locator('#map-select')).to_be_visible()
        page.locator('[data-place-kind="npc"]').click()
        expect(page.locator('[data-map-panel="map"]')).to_be_visible()
        page.keyboard.press('Escape')
        page.locator('[data-cell="88"]').click()
        page.locator('[data-workspace-tab="inspector"]').click()
        expect(page.locator('#tile-name')).to_have_value('Browser Grove')
        page.locator('[data-workspace-tab="map"]').click()
        expect(page.locator('[data-cell="88"]')).to_have_class(re.compile(r'\bcursor\b'))
        page.screenshot(path=str(output / 'mobile-map.png'), full_page=True)
        page.locator('[data-view="access"]').click()
        page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        # A separate MCP/editor write must not be overwritten by this stale browser.
        remote = page.request.get(base + '/api/worlds/starting-realm').json()
        remote['description'] = 'Changed by another editor'
        assert page.request.put(base + '/api/worlds/starting-realm', data=remote).ok
        page.locator('[data-view="map"]').click()
        page.locator('[data-cell="88"]').click()
        page.locator('[data-workspace-tab="inspector"]').click()
        page.locator('#tile-name').fill('Stale browser title')
        expect(page.locator('#toast')).to_contain_text('changed in another editor or MCP client')
        assert page.request.get(base + '/api/worlds/starting-realm').json()['tiles'][88]['name'] == 'Browser Grove'
        assert not errors, errors
        browser.close()
    print('Browser editing, scaffold completion, persistence, world switching, mobile navigation, playtest, and ZIP download passed')


def main():
    output = Path('artifacts/gui').resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mudmaker-gui-') as temp:
        model = ThreadingHTTPServer(('127.0.0.1', 0), ModelFixture)
        model_thread = threading.Thread(target=model.serve_forever, daemon=True)
        model_thread.start()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with patch.object(app_module, 'DB_PATH', Path(temp) / 'worlds.sqlite3'), patch.dict(os.environ, {'MUDMAKER_BIND': '127.0.0.1', 'MUDMAKER_ADMIN_PASSWORD': '', 'MUDMAKER_OPENAI_API_KEY': ''}):
            server = uvicorn.Server(uvicorn.Config(app_module.app, host='127.0.0.1', port=port, log_level='error'))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Isolated workbench did not start')
                    time.sleep(0.05)
                run_browser(f'http://127.0.0.1:{port}', f'http://127.0.0.1:{model.server_port}/v1', output)
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                model.shutdown()
                model.server_close()
                model_thread.join(timeout=5)
    print(f'Screenshots and downloaded game: {output}')


if __name__ == '__main__':
    main()
