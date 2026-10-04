"""MCP tools for the same worlds edited by the browser workbench."""
import base64
import copy
import hashlib
import json
import os
import re
import time
import uuid

import uvicorn
from fastapi import HTTPException

from . import main
from .templates.content import ENTITY_TEMPLATES, BIOMES, OBJECTIVE_TYPES, EFFECT_TYPES, merge_scaffold
from .templates.mcp_support import BearerAuth, load_token, server
from .templates.game_engine import GameEngine, GameError


def revision(world):
    return main.world_revision(world)


def document(world):
    return {'revision': revision(world), 'world': world}


def mutate(world_id, expected_revision, change):
    with main.db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT data FROM worlds WHERE id=?', (world_id,)).fetchone()
        if not row:
            raise ValueError('World not found')
        world = main.normalize_world(json.loads(row['data']))
        if revision(world) != expected_revision:
            raise ValueError('World changed. Read it again before applying your edit.')
        change(world)
        try:
            main.validate_world(world)
        except HTTPException as exc:
            raise ValueError(exc.detail) from exc
        con.execute('UPDATE worlds SET data=? WHERE id=?', (json.dumps(world), world_id))
    return document(world)


def create_server():
    mcp = server('Mudmaker Creator', 'Author MUD worlds. Read get_world for a revision before edits. Complete scaffolds, validate, playtest, and export. Generated drafts require an explicit save tool call.', 'MUDMAKER_MCP_ALLOWED_HOSTS')

    @mcp.tool()
    def list_worlds() -> list[dict]:
        """List worlds available in the web workbench."""
        return [{'id': w['id'], 'name': w['name'], 'width': w['width'], 'height': w['height']} for w in main.list_worlds()]

    @mcp.tool()
    def get_world(world_id: str) -> dict:
        """Read a complete authoring world and its current revision."""
        return document(main.get_world(world_id))

    @mcp.tool()
    def create_world(name: str) -> dict:
        """Create a new editable world using the playable starter adventure."""
        return document(main.create_world({'name': name}))

    @mcp.tool()
    def content_schema() -> dict:
        """Get partial entity templates, supported biome and quest/effect contracts."""
        return {'templates': ENTITY_TEMPLATES, 'biomes': BIOMES, 'objectives': sorted(OBJECTIVE_TYPES), 'effects': sorted(EFFECT_TYPES)}

    @mcp.tool()
    def save_world(world_id: str, expected_revision: str, world: dict) -> dict:
        """Save a complete authoring document if its revision still matches. Drafts may be incomplete."""
        def replace(current):
            current.clear()
            current.update(copy.deepcopy(world))
            current['id'] = world_id
        return mutate(world_id, expected_revision, replace)

    @mcp.tool()
    def upsert_entity(world_id: str, expected_revision: str, kind: str, entity: dict) -> dict:
        """Add or replace an entity by id. Supports every content_schema template."""
        if kind not in ENTITY_TEMPLATES or not entity.get('id'):
            raise ValueError('Use a supported kind and a stable entity id')
        def change(world):
            value = {**copy.deepcopy(entity), 'entity_type': kind}
            route = {'npc': 'npcs', 'quest': 'quests', 'crafting_recipe': 'crafting'}
            entries = world['economy']['vendors'] if kind == 'vendor' else world.setdefault(route.get(kind, 'entities'), [])
            for i, existing in enumerate(entries):
                if existing.get('id') == value['id']:
                    entries[i] = value
                    break
            else:
                entries.append(value)
        return mutate(world_id, expected_revision, change)

    @mcp.tool()
    def edit_map(world_id: str, expected_revision: str, tiles: list[dict], spawn: dict | None = None, map_id: str | None = None) -> dict:
        """Edit one named map using [{x,y,biome,name,description,...}]; optionally move that map's spawn. Updates room overlays too."""
        def change(world):
            target_map = map_id or world.get('default_map_id')
            area = next((entry for entry in world.get('maps', []) if entry.get('id') == target_map), None) if world.get('maps') else None
            if world.get('maps') and area is None:
                raise ValueError('Unknown map id')
            area = area or world
            for patch in tiles:
                x, y = patch.get('x'), patch.get('y')
                if type(x) is not int or type(y) is not int or not (0 <= x < area['width'] and 0 <= y < area['height']):
                    raise ValueError('Tile coordinates are outside the map')
                values = {k: v for k, v in patch.items() if k not in ('x', 'y', 'location', 'entity_type')}
                area['tiles'][y * area['width'] + x].update(values)
                for entity in world.get('entities', []):
                    if entity.get('entity_type') in ('room', 'description') and entity.get('map_id', world.get('default_map_id')) == target_map and entity.get('location') == {'x': x, 'y': y}:
                        entity.update(values)
            if spawn is not None:
                area['spawn'] = spawn
                area.setdefault('entry_points', {})['start'] = copy.deepcopy(spawn)
                if not world.get('maps') or area['id'] == world.get('default_map_id'):
                    world['spawn'] = spawn
        return mutate(world_id, expected_revision, change)

    @mcp.tool()
    def upsert_map(world_id: str, expected_revision: str, map_id: str, name: str, width: int = 16, height: int = 12, biome: str = 'grassland', spawn: dict | None = None) -> dict:
        """Create a named map or update its name and dimensions. Resizing replaces its terrain; use edit_map for tile-level edits."""
        import re
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', map_id):
            raise ValueError('Map id must use lowercase letters, numbers, dashes, or underscores.')
        if type(width) is not int or type(height) is not int or not (4 <= width <= 64 and 4 <= height <= 64):
            raise ValueError('Map dimensions must be between 4 and 64 tiles.')
        if biome not in main.BIOMES and biome not in BIOMES:
            raise ValueError('Choose a built-in or authored biome.')
        point = copy.deepcopy(spawn) if spawn is not None else None
        def change(world):
            if not world.get('maps'):
                legacy_id = world.get('default_map_id', 'overworld')
                world['default_map_id'] = legacy_id
                world['maps'] = [{'id': legacy_id, 'name': world.get('name', 'Overworld'), 'width': world['width'], 'height': world['height'], 'tiles': copy.deepcopy(world['tiles']), 'spawn': copy.deepcopy(world['spawn']), 'entry_points': {'start': copy.deepcopy(world['spawn'])}}]
            existing = next((area for area in world['maps'] if area['id'] == map_id), None)
            if existing and (existing['width'], existing['height']) != (width, height):
                existing['tiles'] = [{'biome': biome, 'name': biome.replace('_', ' ').title()} for _ in range(width * height)]
            elif not existing:
                existing = {'id': map_id, 'tiles': [{'biome': biome, 'name': biome.replace('_', ' ').title()} for _ in range(width * height)], 'entry_points': {}}
                world['maps'].append(existing)
            existing.update(name=name.strip(), width=width, height=height)
            if not existing.get('entry_points'):
                existing['entry_points'] = {}
            if point is not None:
                if type(point.get('x')) is not int or type(point.get('y')) is not int or not (0 <= point['x'] < width and 0 <= point['y'] < height):
                    raise ValueError('Spawn coordinates must be inside the map.')
                existing['spawn'] = point
                existing['entry_points']['start'] = copy.deepcopy(point)
            else:
                current = existing.get('spawn', {'x': 0, 'y': 0})
                if type(current.get('x')) is not int or type(current.get('y')) is not int or not (0 <= current['x'] < width and 0 <= current['y'] < height): current = {'x': 0, 'y': 0}
                existing['spawn'] = current
                existing['entry_points'].setdefault('start', copy.deepcopy(current))
            if map_id == world['default_map_id']:
                world.update(width=width, height=height, tiles=copy.deepcopy(existing['tiles']), spawn=copy.deepcopy(existing['spawn']))
        return mutate(world_id, expected_revision, change)

    @mcp.tool()
    def connect_maps(world_id: str, expected_revision: str, source_map_id: str, x: int, y: int, exit_name: str, destination_map_id: str, destination_entry: str = 'start', bidirectional: bool = True, return_exit_name: str = 'return') -> dict:
        """Connect a source tile to a named destination entry point, optionally creating a reciprocal exit."""
        def change(world):
            maps = world.get('maps', [])
            source = next((area for area in maps if area['id'] == source_map_id), None)
            destination = next((area for area in maps if area['id'] == destination_map_id), None)
            if source is None or destination is None:
                raise ValueError('Both source and destination map ids must exist.')
            if type(x) is not int or type(y) is not int or not (0 <= x < source['width'] and 0 <= y < source['height']):
                raise ValueError('Connection coordinates are outside the source map.')
            entry = destination.get('entry_points', {}).get(destination_entry)
            if not isinstance(entry, dict) or type(entry.get('x')) is not int or type(entry.get('y')) is not int or not (0 <= entry['x'] < destination['width'] and 0 <= entry['y'] < destination['height']):
                raise ValueError('Destination entry point does not exist or is outside the destination map.')
            clean_exit_name, clean_return_name = exit_name.strip(), return_exit_name.strip()
            if not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', clean_exit_name) or (bidirectional and not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', clean_return_name)):
                raise ValueError('Exit names must be lowercase slugs.')
            if bidirectional and clean_exit_name == clean_return_name and source_map_id == destination_map_id and (x,y)==(entry['x'],entry['y']):
                raise ValueError('A reciprocal connection at one tile needs two different exit labels.')
            source_tile = source['tiles'][y * source['width'] + x]
            prior_source = source_tile.get('exits', {}).get(clean_exit_name)
            if isinstance(prior_source, dict) and prior_source.get('pair_id'):
                raise ValueError('This exit belongs to a reciprocal pair. Edit it in the builder to keep both endpoints together.')
            destination_tile = destination['tiles'][entry['y'] * destination['width'] + entry['x']]
            prior_return = destination_tile.get('exits', {}).get(clean_return_name)
            if bidirectional and isinstance(prior_return, dict) and prior_return.get('pair_id'):
                raise ValueError('The return exit belongs to another reciprocal pair.')
            pair_id = f'pair-{uuid.uuid4().hex}' if bidirectional else None
            forward = {'map_id': destination_map_id, 'x': entry['x'], 'y': entry['y'], 'entry_point': destination_entry}
            if pair_id:
                forward.update(pair_id=pair_id, return_exit=return_exit_name.strip())
            source_tile.setdefault('exits', {})[clean_exit_name] = forward
            if bidirectional:
                destination_tile.setdefault('exits', {})[clean_return_name] = {'map_id': source_map_id, 'x': x, 'y': y, 'pair_id': pair_id, 'return_exit': clean_exit_name}
        return mutate(world_id, expected_revision, change)

    @mcp.tool()
    def complete_scaffold(world_id: str, kind: str, scaffold: dict, instructions: str = '', generated: dict | None = None) -> dict:
        """Complete a draft without saving. Supply generated content from your model, or omit it to call the workbench's configured OpenAI-compatible provider. Authored fields are preserved recursively."""
        if kind not in ENTITY_TEMPLATES:
            raise ValueError('Unknown entity type')
        if generated is not None:
            main.get_world(world_id)
            return {**merge_scaffold(generated, scaffold), 'entity_type': kind}
        try:
            return main.generate(world_id, kind, {'scaffold': scaffold, 'instructions': instructions})
        except HTTPException as exc:
            raise ValueError(exc.detail) from exc

    @mcp.tool()
    def validate_world(world_id: str) -> dict:
        """Validate playable content and references before export."""
        try:
            return main.validate_build(world_id)
        except HTTPException as exc:
            raise ValueError(exc.detail) from exc

    @mcp.tool()
    def playtest(world_id: str, command: str = 'look', player: str = 'mcp-builder') -> dict:
        """Run a game command against isolated preview saves."""
        return main.preview(world_id, {'command': command, 'player': player})

    @mcp.tool()
    def decision_controller(world_id: str, owner_id: str, owner_type: str, player: str = 'mcp-builder') -> dict:
        """Inspect an enabled NPC or market controller in isolated playtest, including observation, legal candidates, provider readiness, and pause state."""
        try:
            world=main.compiled(main.get_world(world_id))
            engine=GameEngine(world,main.DB_PATH.parent/'previews'/f'{world_id}.sqlite3')
            return engine.decision_preview(player,owner_id,owner_type)
        except (HTTPException,GameError) as exc:
            raise ValueError(exc.detail if isinstance(exc,HTTPException) else str(exc)) from exc

    def control_playtest_decision(world_id, owner_id, owner_type, paused):
        world=main.compiled(main.get_world(world_id))
        engine=GameEngine(world,main.DB_PATH.parent/'previews'/f'{world_id}.sqlite3')
        operation='pause_decision_controller' if paused else 'resume_decision_controller'
        def record(con,result):
            con.execute('CREATE TABLE IF NOT EXISTS creator_audit(sequence INTEGER PRIMARY KEY AUTOINCREMENT,timestamp REAL NOT NULL,operation TEXT NOT NULL,details TEXT NOT NULL)')
            con.execute('INSERT INTO creator_audit(timestamp,operation,details) VALUES (?,?,?)',(time.time(),operation,json.dumps({'world_id':world_id,'owner_type':owner_type,'owner_id':owner_id,'revision':result['revision']})))
        try: result=engine.set_decision_paused(owner_type,owner_id,paused,audit=record)
        except GameError as exc: raise ValueError(str(exc)) from exc
        return result

    @mcp.tool()
    def pause_decision_controller(world_id: str, owner_id: str, owner_type: str) -> dict:
        """Pause an NPC or market controller in isolated playtest; invalidate in-flight results and record the action in creator audit history."""
        return control_playtest_decision(world_id,owner_id,owner_type,True)

    @mcp.tool()
    def resume_decision_controller(world_id: str, owner_id: str, owner_type: str) -> dict:
        """Resume an NPC or market controller in isolated playtest and record the action in creator audit history."""
        return control_playtest_decision(world_id,owner_id,owner_type,False)

    @mcp.tool()
    def decision_audit_log(world_id: str, limit: int = 50) -> list[dict]:
        """Read recent creator MCP controller pause/resume actions for isolated playtest."""
        world=main.compiled(main.get_world(world_id))
        engine=GameEngine(world,main.DB_PATH.parent/'previews'/f'{world_id}.sqlite3')
        with engine.connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS creator_audit(sequence INTEGER PRIMARY KEY AUTOINCREMENT,timestamp REAL NOT NULL,operation TEXT NOT NULL,details TEXT NOT NULL)')
            return [{'sequence':row[0],'timestamp':row[1],'operation':row[2],'details':json.loads(row[3])} for row in con.execute('SELECT * FROM creator_audit ORDER BY sequence DESC LIMIT ?',(max(1,min(int(limit),200)),))]

    @mcp.tool()
    def export_game(world_id: str) -> dict:
        """Return the standalone Compose project as a base64 ZIP for the client to save."""
        data = main.make_export(main.get_world(world_id))
        return {'filename': world_id + '.zip', 'mime_type': 'application/zip', 'base64': base64.b64encode(data).decode()}

    @mcp.resource('mudmaker://content-schema')
    def schema_resource() -> str:
        return json.dumps(content_schema())

    @mcp.prompt()
    def build_entity(world_id: str, kind: str, concept: str) -> str:
        return f'Read get_world({world_id!r}) and content_schema. Develop this {kind} scaffold: {concept}. Preserve authored fields. Review the draft, then use upsert_entity with the current revision. Validate the world and playtest the result.'

    return mcp


def main_server():
    main.init_db()
    mcp = create_server()
    token = load_token('MUDMAKER_MCP_TOKEN', main.DB_PATH.parent / 'creator-mcp-token')
    uvicorn.run(BearerAuth(mcp.streamable_http_app(), token), host='0.0.0.0', port=int(os.getenv('MUDMAKER_MCP_PORT_INTERNAL', '8001')))


if __name__ == '__main__':
    main_server()
