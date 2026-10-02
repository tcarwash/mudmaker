"""Verify creator and runtime tools with the official MCP client over real HTTP."""
import asyncio
import base64
import io
import json
import os
import secrets
import socket
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app import main
from app.creator_mcp import create_server
from app.templates.content import compile_world
from app.templates.game_engine import GameEngine
from app.templates.mcp_support import BearerAuth
from app.templates.runtime_management import RuntimeManagement, create_runtime_server


@contextmanager
def serve(mcp, token):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    http = uvicorn.Server(uvicorn.Config(BearerAuth(mcp.streamable_http_app(), token), host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=http.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not http.started:
            if time.monotonic() > deadline:
                raise RuntimeError('MCP HTTP server failed to start')
            time.sleep(0.05)
        yield f'http://127.0.0.1:{port}/mcp'
    finally:
        http.should_exit = True
        thread.join(timeout=10)


async def call(session, name, arguments=None, error=False):
    result = await session.call_tool(name, arguments or {})
    assert bool(result.isError) == error, (name, result.content)
    if error:
        return result
    return result.structuredContent if result.structuredContent is not None else json.loads(result.content[0].text)


async def verify_creator(url, token):
    async with httpx.AsyncClient() as http:
        assert (await http.post(url, json={})).status_code == 401
        assert (await http.post(url, json={}, headers={'Authorization': 'Bearer wrong'})).status_code == 401
    async with streamablehttp_client(url, headers={'Authorization': f'Bearer {token}'}) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert {'edit_map', 'complete_scaffold', 'export_game', 'upsert_entity'} <= names
            assert (await session.list_resources()).resources
            assert (await session.list_prompts()).prompts
            world = await call(session, 'get_world', {'world_id': 'starting-realm'})
            initial_revision = world['revision']
            draft = await call(session, 'complete_scaffold', {'world_id': 'starting-realm', 'kind': 'npc', 'scaffold': {'name': 'My Keeper', 'dialog': '', 'location': {'x': 7, 'y': None}}, 'generated': {'id': 'mcp-keeper', 'name': 'Generated Keeper', 'dialog': 'Welcome from MCP.', 'location': {'x': 8, 'y': 5}}})
            assert draft['name'] == 'My Keeper' and draft['location'] == {'x': 7, 'y': 5}
            changed = await call(session, 'upsert_entity', {'world_id': 'starting-realm', 'expected_revision': initial_revision, 'kind': 'npc', 'entity': draft})
            await call(session, 'edit_map', {'world_id': 'starting-realm', 'expected_revision': initial_revision, 'tiles': [{'x': 7, 'y': 5, 'name': 'Stale edit'}]}, error=True)
            current = await call(session, 'edit_map', {'world_id': 'starting-realm', 'expected_revision': changed['revision'], 'tiles': [{'x': 7, 'y': 5, 'name': 'MCP Camp'}]})
            assert current['world']['tiles'][87]['name'] == 'MCP Camp'
            assert (await call(session, 'validate_world', {'world_id': 'starting-realm'}))['ready']
            assert 'Welcome from MCP.' in (await call(session, 'playtest', {'world_id': 'starting-realm', 'command': 'talk mcp-keeper'}))['output']
            exported = await call(session, 'export_game', {'world_id': 'starting-realm'})
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(exported['base64']))) as archive:
                assert {'runtime_management.py', 'mcp_support.py', 'world/source.json'} <= set(archive.namelist())
                assert token.encode() not in b''.join(archive.read(n) for n in archive.namelist())
    print('Creator MCP discovery, resources/prompts, scaffold merge, revision conflict, map edits, playtest, export, and bearer authentication passed')


async def verify_runtime(url, token, engine, announcements):
    async with streamablehttp_client(url, headers={'Authorization': f'Bearer {token}'}) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            assert 'draft_event' in {t.name for t in (await session.list_tools()).tools}
            context = await call(session, 'world_context')
            assert 'ssh_public_keys' not in context['world']
            event = {'id': 'mcp-fair', 'name': 'MCP Fair', 'entities': [{'entity_type': 'npc', 'id': 'mcp-fair-host', 'name': 'Festival Host', 'location': {'x': 7, 'y': 5}, 'dialog': 'Come to the festival!'}]}
            draft = await call(session, 'draft_event', {'event': event})
            assert 'Festival Host' not in engine.execute('alice', 'look')
            await call(session, 'activate_event', {'event_id': 'mcp-fair', 'expected_revision': 'stale'}, error=True)
            await call(session, 'activate_event', {'event_id': 'mcp-fair', 'expected_revision': draft['revision']})
            assert 'Festival Host' in engine.execute('alice', 'look')
            await call(session, 'announce', {'message': 'Festival begins'})
            assert announcements == ['Festival begins']
            grant = {'username': 'alice', 'request_id': 'mcp-grant-1', 'reason': 'Festival reward', 'effects': [{'type': 'currency', 'amount': 5}]}
            await call(session, 'adjust_player', grant)
            await call(session, 'adjust_player', grant)
            assert engine.snapshot('alice')['currency'] == 30
            await call(session, 'end_event', {'event_id': 'mcp-fair'})
            assert 'Festival Host' not in engine.execute('alice', 'look')
            await call(session, 'audit_log')
    print('Runtime MCP draft/review/activation, announcements, idempotent grants, event ending, and audit history passed')


def main_verify():
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix='mudmaker-mcp-') as temp, patch.object(main, 'DB_PATH', Path(temp) / 'creator.sqlite3'):
        main.init_db()
        with serve(create_server(), token) as url:
            asyncio.run(verify_creator(url, token))
        engine = GameEngine(compile_world(main.new_world()), Path(temp) / 'game.sqlite3')
        engine.execute('alice', 'look')
        manager = RuntimeManagement(engine)
        announcements = []
        def broadcast(message):
            announcements.append(message)
            return ['alice']
        with serve(create_runtime_server(manager, {'alice': None}, broadcast), token) as url:
            asyncio.run(verify_runtime(url, token, engine, announcements))


if __name__ == '__main__':
    main_verify()
