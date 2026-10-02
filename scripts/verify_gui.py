"""Exercise the real authoring GUI against an isolated database and model fixture."""
import json
import os
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
        expect(page.locator('h1')).to_have_text('The Wayfarer Realm')
        page.locator('#settings-shortcut').click()
        page.locator('#api-base').fill(model_url)
        page.locator('#api-model').fill('fixture-model')
        page.locator('#save-settings').click()
        expect(page.locator('#toast')).to_have_text('Endpoint and model saved')

        page.locator('[data-view="map"]').click()
        tile = page.locator('[data-cell="88"]')
        original_color = tile.evaluate('(element) => element.style.background')
        tile.click()
        assert tile.evaluate('(element) => element.style.background') == original_color
        page.locator('#tile-name').fill('Browser Grove')
        page.locator('#tile-desc').fill('A clearing authored in the browser.')
        page.locator('[data-biome="forest"]').click()
        page.locator('[data-cell="87"]').click()
        page.locator('#select-tool').click()
        page.locator('[data-cell="88"]').click()
        page.locator('#set-spawn').click()
        expect(page.locator('[data-cell="88"]')).to_have_class('map-cell spawn')
        page.locator('#map-undo').click()
        expect(page.locator('[data-cell="87"]')).to_have_class('map-cell spawn')
        page.locator('#map-redo').click()
        expect(page.locator('[data-cell="88"]')).to_have_class('map-cell spawn')
        assert '♟' in page.locator('[data-cell="87"]').inner_text()
        page.screenshot(path=str(output / 'map.png'), full_page=True)

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
        expect(page.locator('#entity-match-count')).to_have_text('3 shown')
        page.locator('[data-view="access"]').click()
        page.locator('#add-key').click()
        page.locator('#player-name').fill('builder')
        key = asyncssh.generate_private_key('ssh-ed25519').export_public_key().decode()
        page.locator('#public-key').fill(key)
        page.locator('#key-save').click()
        expect(page.locator('.key-row .badge')).to_have_text('builder')

        page.locator('[data-view="playtest"]').click()
        expect(page.locator('#preview-log')).to_contain_text('Browser Grove')
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
        page.locator('[data-preview-command="east"]').click()
        for _ in range(3):
            page.locator('#preview-command').fill('attack moss-wolf')
            page.locator('#preview-form button').click()
        expect(page.locator('#preview-log')).to_contain_text('Moss Wolf is defeated')
        expect(page.locator('#preview-dashboard')).to_contain_text('12')
        page.locator('#build-check').click()
        expect(page.locator('#toast')).to_contain_text('Build ready:')
        with page.expect_download() as download_info:
            page.locator('#export-btn').click()
        archive_path = output / 'browser-game.zip'
        download_info.value.save_as(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            world = json.loads(archive.read('world/world.json'))
            assert world['spawn'] == {'x': 8, 'y': 5}
            assert world['tiles'][88]['name'] == 'Browser Grove'
            assert next(n for n in world['npcs'] if n['id']=='keeper')['name'] == 'Authored Keeper'
            assert json.loads(archive.read('world/players.json'))[0]['username'] == 'builder'
        page.screenshot(path=str(output / 'playtest.png'), full_page=True)

        page.locator('#world-switch').click()
        page.locator('#new-realm-name').fill('Second Realm')
        page.locator('#new-realm-create').click()
        expect(page.locator('h1')).to_have_text('Second Realm')
        page.locator('#world-switch').click()
        page.locator('[data-world="starting-realm"]').click()
        expect(page.locator('h1')).to_have_text('The Wayfarer Realm')
        page.reload()
        expect(page.locator('h1')).to_have_text('The Wayfarer Realm')
        page.locator('[data-view="npcs"]').click()
        expect(page.get_by_text('Authored Keeper', exact=True)).to_be_visible()
        page.set_viewport_size({'width': 390, 'height': 844})
        expect(page.locator('#world-switch')).to_be_visible()
        for name in ['map', 'npcs', 'quests', 'crafting', 'economy', 'entities', 'playtest', 'access']:
            page.locator(f'[data-view="{name}"]').click()
            expect(page.locator('h1')).to_be_visible()
        page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        # A separate MCP/editor write must not be overwritten by this stale browser.
        remote = page.request.get(base + '/api/worlds/starting-realm').json()
        remote['description'] = 'Changed by another editor'
        assert page.request.put(base + '/api/worlds/starting-realm', data=remote).ok
        page.locator('[data-view="map"]').click()
        page.locator('[data-cell="88"]').click()
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
