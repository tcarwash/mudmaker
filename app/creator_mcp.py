"""MCP tools for the same worlds edited by the browser workbench."""
import base64
import copy
import hashlib
import json
import os

import uvicorn
from fastapi import HTTPException

from . import main
from .templates.content import ENTITY_TEMPLATES, BIOMES, OBJECTIVE_TYPES, EFFECT_TYPES, merge_scaffold
from .templates.mcp_support import BearerAuth, load_token, server


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
    def edit_map(world_id: str, expected_revision: str, tiles: list[dict], spawn: dict | None = None) -> dict:
        """Edit tiles using [{x,y,biome,name,description,...}]; optionally move spawn. Updates room overlays too."""
        def change(world):
            for patch in tiles:
                x, y = patch.get('x'), patch.get('y')
                if type(x) is not int or type(y) is not int or not (0 <= x < world['width'] and 0 <= y < world['height']):
                    raise ValueError('Tile coordinates are outside the map')
                values = {k: v for k, v in patch.items() if k not in ('x', 'y', 'location', 'entity_type')}
                world['tiles'][y * world['width'] + x].update(values)
                for entity in world.get('entities', []):
                    if entity.get('entity_type') in ('room', 'description') and entity.get('location') == {'x': x, 'y': y}:
                        entity.update(values)
            if spawn is not None:
                world['spawn'] = spawn
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
