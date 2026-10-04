"""Verify terrain authoring, cancellation, undo and runtime parity in an isolated GUI."""
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


def main():
    output = Path('artifacts/gui')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mudmaker-terrain-') as temp:
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
                    # Keep the sample recipe's gatherable inputs available when
                    # the later scenario deliberately clears the crossroads tile.
                    page.evaluate("() => { activeMap().tiles[86].resources = [{item:'Wild Berries',quantity:20},{item:'Herbs',quantity:10}]; return flushSave(); }")
                    # Empty/default tiles need no prompt and use schema-backed terrain prose.
                    page.locator('[data-biome="water"]').click()
                    page.locator('[data-cell="0"]').click()
                    expect(page.locator('#modal-backdrop')).to_be_hidden()
                    expect(page.locator('#tile-name')).to_have_value('Water')
                    expect(page.locator('#tile-desc')).to_have_value('Water stretches out before you.')
                    page.locator('[data-cell="0"]').click()
                    expect(page.locator('#modal-backdrop')).to_be_hidden()
                    # Authored text and services trigger a prompt; cancel changes nothing.
                    page.locator('[data-biome="forest"]').click()
                    page.locator('[data-cell="87"]').click()
                    expect(page.locator('#terrain-change-apply')).to_be_visible()
                    expect(page.locator('[data-terrain-keep="resources"]')).to_be_checked()
                    page.screenshot(path=str(output / 'terrain-change.png'), full_page=True)
                    page.locator('.modal-cancel').click()
                    assert page.evaluate('activeMap().tiles[87].biome') == 'grassland'
                    # Keeping content changes only terrain; saved runtime gets the same data.
                    page.locator('[data-cell="87"]').click()
                    page.locator('#terrain-change-apply').click()
                    assert page.evaluate('activeMap().tiles[87].biome') == 'forest'
                    expect(page.locator('#tile-name')).to_have_value('Wayfarer Crossroads')
                    assert 'campfire' in page.locator('#tile-desc').input_value()
                    page.evaluate('flushSave()')
                    saved = page.request.get(base + '/api/worlds/starting-realm').json()
                    built = compile_world(saved)
                    assert built['tiles'][87]['name'] == 'Wayfarer Crossroads'
                    assert built['tiles'][87]['resources']
                    page.locator('#map-undo').click()
                    assert page.evaluate('activeMap().tiles[87].biome') == 'grassland'
                    # Explicitly cleared content is removed, with defaults persisted.
                    page.locator('[data-cell="87"]').click()
                    for box in page.locator('[data-terrain-keep]:not([data-terrain-keep="objects"])').all():
                        box.uncheck()
                    page.locator('#terrain-change-apply').click()
                    expect(page.locator('#tile-name')).to_have_value('Forest')
                    expect(page.locator('#tile-desc')).to_have_value('A quiet stretch of forest.')
                    assert page.evaluate('!activeMap().tiles[87].resources && !activeMap().tiles[87].station')
                    page.evaluate('flushSave()')
                    saved = page.request.get(base + '/api/worlds/starting-realm').json()
                    built = compile_world(saved)
                    assert built['tiles'][87]['name'] == 'Forest'
                    assert built['tiles'][87]['description'] == 'A quiet stretch of forest.'
                    page.locator('#map-undo').click()
                    assert page.evaluate('activeMap().tiles[87].resources.length') == 2
                    # Drag strokes retain a single undo boundary.
                    page.locator('[data-biome="desert"]').click()
                    a = page.locator('[data-cell="1"]').bounding_box()
                    b = page.locator('[data-cell="2"]').bounding_box()
                    page.mouse.move(a['x']+a['width']/2, a['y']+a['height']/2)
                    page.mouse.down()
                    page.mouse.move(b['x']+b['width']/2, b['y']+b['height']/2)
                    page.mouse.up()
                    assert page.evaluate('activeMap().tiles[1].biome') == 'desert'
                    assert page.evaluate('activeMap().tiles[2].biome') == 'desert'
                    page.locator('#map-undo').click()
                    assert page.evaluate('activeMap().tiles[1].biome') == 'mountain'
                    assert page.evaluate('activeMap().tiles[2].biome') == 'mountain'
                    assert not errors, errors
                    browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)
    print('Terrain GUI: defaults, no-op, cancellation, keep/clear, save/runtime parity, undo and drag passed')


if __name__ == '__main__':
    main()
