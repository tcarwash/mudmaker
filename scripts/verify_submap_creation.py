"""Verify submap authoring, connections, runtime travel and export parity in an isolated GUI."""
import os
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import uvicorn
from playwright.sync_api import expect, sync_playwright

from app import main as app_module
from app.templates.content import compile_world
from app.templates.game_engine import GameEngine
import io
import json
import zipfile


def main():
    output = Path('artifacts/gui')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mudmaker-submap-') as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with patch.object(app_module, 'DB_PATH', Path(temp) / 'worlds.sqlite3'), patch.dict(os.environ, {'MUDMAKER_BIND': '127.0.0.1', 'MUDMAKER_ADMIN_PASSWORD': ''}):
            server = uvicorn.Server(uvicorn.Config(app_module.app, host='127.0.0.1', port=port, log_level='error'))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started:
                    if time.monotonic() > deadline:
                        raise RuntimeError('Workbench did not start')
                    time.sleep(.05)
                with sync_playwright() as p:
                    browser = p.chromium.launch(executable_path=shutil.which('google-chrome') or shutil.which('chromium'), headless=True)
                    page = browser.new_page(viewport={'width': 1600, 'height': 1100})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    base = f'http://127.0.0.1:{port}'
                    page.goto(base)
                    page.wait_for_selector('.map-cell')
                    expect(page.locator('#map-create-visible')).to_be_visible()
                    page.locator('#map-create-visible').click()
                    page.locator('.modal-cancel').click()
                    assert page.evaluate('world.maps.length') == 1
                    # Connect from an authored room: exits must reach the room override too.
                    page.evaluate("world.entities.push({entity_type:'room',id:'workshop-door',name:'Workshop door',biome:'grassland',description:'A sturdy door.',map_id:'overworld',location:{x:7,y:5},exits:{}});render()")
                    page.locator('[data-cell="87"]').click()
                    expect(page.locator('#tile-create-submap')).to_be_enabled()
                    page.locator('#tile-create-submap').click()
                    page.locator('#new-map-name').fill('Workshop')
                    page.locator('#new-map-width').fill('2')
                    page.locator('#create-map-save').click()
                    expect(page.locator('#new-map-error')).to_contain_text('4 to 64')
                    assert page.evaluate('world.maps.length') == 1
                    page.locator('#new-map-width').fill('4')
                    page.locator('#new-map-height').fill('6')
                    page.screenshot(path=str(output / 'create-submap.png'), full_page=True)
                    page.locator('#create-map-save').click()
                    expect(page.locator('#map-switch-visible')).to_have_value('workshop')
                    expect(page.locator('.map-cell')).to_have_count(24)
                    expect(page.locator('#tile-name')).to_have_value('Grassland')
                    page.evaluate('flushSave()')
                    saved = page.request.get(base + '/api/worlds/starting-realm').json()
                    assert saved['entities'][-1]['exits']['enter-workshop']['map_id'] == 'workshop'
                    compiled = compile_world(saved)
                    engine = GameEngine(compiled, Path(temp) / 'game.sqlite3')
                    engine.execute('tester', 'go enter-workshop')
                    assert engine.snapshot('tester')['map_id'] == 'workshop'
                    engine.execute('tester', 'go return')
                    assert engine.snapshot('tester')['map_id'] == 'overworld'
                    exported = page.request.get(base + '/api/worlds/starting-realm/export')
                    assert exported.ok
                    with zipfile.ZipFile(io.BytesIO(exported.body())) as archive:
                        filename = next(n for n in archive.namelist() if n.endswith('world.json'))
                        data = json.loads(archive.read(filename))
                        assert next(m for m in data['maps'] if m['id']=='workshop')['height'] == 6
                    # Creation and both connections are one undoable operation.
                    page.locator('#map-undo').click()
                    assert page.evaluate('world.maps.length') == 1
                    assert page.evaluate('!world.entities.at(-1).exits["enter-workshop"]')
                    page.locator('#map-redo').click()
                    assert page.evaluate('world.maps.some(m=>m.id==="workshop")')
                    page.locator('#map-switch-visible').select_option('workshop')
                    expect(page.locator('#map-switch-visible')).to_have_value('workshop')
                    page.locator('#map-switch-visible').select_option('overworld')
                    expect(page.locator('#map-select')).to_have_value('overworld')
                    # The map action remains visible on mobile; standalone areas are optional.
                    page.set_viewport_size({'width':390, 'height':844})
                    expect(page.locator('#map-create-visible')).to_be_visible()
                    page.locator('#map-create-visible').click()
                    page.locator('#new-map-name').fill('Courtyard')
                    page.locator('#new-map-connect').uncheck()
                    page.locator('#create-map-save').click()
                    expect(page.locator('#map-switch-visible')).to_have_value('courtyard')
                    assert page.evaluate('!activeMap().tiles[0].exits')
                    page.evaluate('flushSave()')
                    assert not errors, errors
                    browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)
    print('Submap GUI: visible actions, dimensions, room connections, runtime travel, ZIP export, undo/redo and mobile standalone creation passed')


if __name__ == '__main__':
    main()
