"""Persistent event lifecycle and audited management, shared with the live engine."""
import copy
import hashlib
import json
import os
import re
import time

try:
    from .content import compile_world, check_effects, check_location, ENTITY_TEMPLATES, OBJECTIVE_TYPES, EFFECT_TYPES, merge_scaffold
    from .game_engine import GameError
    from .mcp_support import server
except ImportError:
    from content import compile_world, check_effects, check_location, ENTITY_TEMPLATES, OBJECTIVE_TYPES, EFFECT_TYPES, merge_scaffold
    from game_engine import GameError
    from mcp_support import server


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class RuntimeManagement:
    def __init__(self, engine):
        self.engine = engine
        self.base = copy.deepcopy(engine.world)
        with engine.connect() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS managed_events (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL,
                    revision TEXT NOT NULL, expires_at REAL);
                CREATE TABLE IF NOT EXISTS management_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                    operation TEXT NOT NULL, details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS management_requests (
                    id TEXT PRIMARY KEY, signature TEXT NOT NULL, result TEXT NOT NULL);
            ''')
        engine.before_command = self.refresh
        self.refresh()

    @staticmethod
    def audit(con, operation, details):
        con.execute('INSERT INTO management_audit(timestamp,operation,details) VALUES (?,?,?)',
                    (time.time(), operation, json.dumps(details)))

    def assemble(self, records):
        source = copy.deepcopy(self.base)
        source['entities'] = []
        for record in records:
            source['entities'].extend(json.loads(record['data'])['entities'])
        return compile_world(source)

    @staticmethod
    def records(con, status=None):
        con.row_factory = __import__('sqlite3').Row
        if status:
            return con.execute('SELECT * FROM managed_events WHERE status=? ORDER BY id', (status,)).fetchall()
        return con.execute('SELECT * FROM managed_events ORDER BY id').fetchall()

    def refresh(self):
        with self.engine.lock, self.engine.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            expired = con.execute("SELECT id FROM managed_events WHERE status='active' AND expires_at IS NOT NULL AND expires_at<=?", (time.time(),)).fetchall()
            for row in expired:
                con.execute("UPDATE managed_events SET status='ended' WHERE id=?", (row[0],))
                self.audit(con, 'expire_event', {'id': row[0]})
            active = self.records(con, 'active')
            signature = tuple((r['id'], r['revision']) for r in active)
            if signature != getattr(self, '_active_signature', None):
                world = self.assemble(active)
                self.engine.world = world
                self._active_signature = signature

    def draft(self, event):
        ident = event.get('id', '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', ident) or not event.get('name'):
            raise ValueError('Events require a slug id (maximum 64 characters) and name')
        entities = event.get('entities')
        if not isinstance(entities, list) or not entities or len(entities) > 100:
            raise ValueError('An event needs 1–100 entities')
        # A self-contained event may reference base content, but not another event.
        # Prefixes keep entity ids unique and preserve once-only rewards across restarts.
        for entity in entities:
            if not isinstance(entity, dict) or entity.get('entity_type') not in ENTITY_TEMPLATES or entity.get('entity_type') == 'world':
                raise ValueError('Events support content entities, not world replacements')
            if not isinstance(entity.get('id'), str) or not entity['id'].startswith(ident + '-'):
                raise ValueError(f'Event entity ids must begin with {ident}-')
            if entity.get('entity_type') in ('room', 'description'):
                tile_keys = set(entity) - {'id', 'entity_type', 'name', 'description', 'location', 'map_id', 'biome', 'resources', 'station', 'rest', 'exits'}
                if tile_keys:
                    raise ValueError('Room events contain unsupported location properties')
        candidate = {'data': json.dumps(event)}
        self.assemble([candidate])
        with self.engine.lock, self.engine.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            old = con.execute('SELECT status FROM managed_events WHERE id=?', (ident,)).fetchone()
            if old and old[0] != 'draft':
                raise ValueError('Active or ended events are immutable. Draft a new event id.')
            rev = digest(event)
            con.execute("INSERT INTO managed_events VALUES (?,?,'draft',?,NULL) ON CONFLICT(id) DO UPDATE SET data=excluded.data,revision=excluded.revision", (ident, json.dumps(event), rev))
            self.audit(con, 'draft_event', {'id': ident, 'revision': rev})
        return {'id': ident, 'revision': rev, 'status': 'draft', 'event': event}

    def activate(self, ident, expected_revision, expires_at=None):
        if expires_at is not None and expires_at <= time.time():
            raise ValueError('expires_at must be a future Unix timestamp')
        with self.engine.lock, self.engine.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            rows = self.records(con)
            event = next((r for r in rows if r['id'] == ident), None)
            if not event or event['revision'] != expected_revision:
                raise ValueError('Event missing or revision changed; read the draft again')
            if event['status'] == 'active':
                return {'id': ident, 'status': 'active', 'already_active': True}
            if event['status'] != 'draft':
                raise ValueError('Ended events cannot be reactivated; use a new event id')
            active = [r for r in rows if r['status'] == 'active']
            # Two location overlays cannot silently overwrite one another.
            occupied = {tuple(sorted(e['location'].items())) for r in active for e in json.loads(r['data'])['entities'] if e.get('entity_type') in ('room', 'description')}
            for entity in json.loads(event['data'])['entities']:
                if entity.get('entity_type') in ('room', 'description') and tuple(sorted(entity['location'].items())) in occupied:
                    raise ValueError('Another active event already edits this location')
            world = self.assemble([*active, event])
            con.execute("UPDATE managed_events SET status='active',expires_at=? WHERE id=?", (expires_at, ident))
            self.audit(con, 'activate_event', {'id': ident, 'revision': expected_revision, 'expires_at': expires_at})
        self.refresh()
        return {'id': ident, 'status': 'active', 'expires_at': expires_at, 'entities': len(json.loads(event['data'])['entities'])}

    def end(self, ident):
        with self.engine.lock, self.engine.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT status FROM managed_events WHERE id=?', (ident,)).fetchone()
            if not row:
                raise ValueError('Unknown event')
            if row[0] != 'ended':
                con.execute("UPDATE managed_events SET status='ended' WHERE id=?", (ident,))
                self.audit(con, 'end_event', {'id': ident})
        self.refresh()
        return {'id': ident, 'status': 'ended', 'note': 'Earned items, currency, and completion history are retained.'}

    def list_events(self):
        self.refresh()
        with self.engine.connect() as con:
            return [{**dict(row), 'data': json.loads(row['data'])} for row in self.records(con)]

    def players(self):
        with self.engine.lock, self.engine.connect() as con:
            return [{'username': row[0], 'state': json.loads(row[1])} for row in con.execute('SELECT username,state FROM players ORDER BY username')]

    def decision_traces(self, owner_id: str = '', owner_type: str = '', limit: int = 20):
        limit=max(1,min(int(limit),100))
        if owner_type and owner_type not in ('npc','market'): raise ValueError('owner_type must be npc or market')
        with self.engine.connect() as con:
            if owner_id and owner_type:
                rows=con.execute('SELECT created_at,owner_type,owner_id,trigger,observation,candidates,selected,outcome,status,latency_ms FROM decision_traces WHERE owner_type=? AND owner_id=? ORDER BY created_at DESC LIMIT ?', (owner_type,owner_id,limit))
            elif owner_id:
                rows=con.execute('SELECT created_at,owner_type,owner_id,trigger,observation,candidates,selected,outcome,status,latency_ms FROM decision_traces WHERE owner_id=? ORDER BY created_at DESC LIMIT ?', (owner_id,limit))
            elif owner_type:
                rows=con.execute('SELECT created_at,owner_type,owner_id,trigger,observation,candidates,selected,outcome,status,latency_ms FROM decision_traces WHERE owner_type=? ORDER BY created_at DESC LIMIT ?', (owner_type,limit))
            else:
                rows=con.execute('SELECT created_at,owner_type,owner_id,trigger,observation,candidates,selected,outcome,status,latency_ms FROM decision_traces ORDER BY created_at DESC LIMIT ?', (limit,))
            return [{'created_at':r[0],'owner_type':r[1],'owner_id':r[2],'trigger':r[3],'observation':json.loads(r[4]),'candidates':json.loads(r[5]),'selected':r[6],'outcome':r[7],'status':r[8],'latency_ms':r[9]} for r in rows]

    def decision_controller(self, owner_id: str, owner_type: str):
        if owner_type not in ('npc','market'): raise ValueError('owner_type must be npc or market')
        entries=self.engine.world.get('npcs',[]) if owner_type=='npc' else self.engine.world.get('economy',{}).get('vendors',[])
        owner=next((entry for entry in entries if entry.get('id')==owner_id),None)
        if not owner or not owner.get('decision_policy',{}).get('enabled'): raise ValueError('Unknown or disabled decision controller')
        with self.engine.connect() as con:
            state=self.engine._decision_state(con,owner_id) if owner_type=='npc' else self.engine._market_decision_state(con,owner_id)
        policy=owner['decision_policy']; profile=next((p for p in self.engine.world.get('decision_providers',[]) if p.get('id')==policy.get('provider_id')),{})
        result={'owner_type':owner_type,'owner_id':owner_id,'name':owner.get('name',owner_id),'enabled':True,'paused':bool(state.get('paused')),'revision':state.get('revision',0),'next_due':state.get('next_due',0),'goals':copy.deepcopy(policy.get('goals',[])),'actions':policy.get('actions',[]),'observations':policy.get('observations',[]),'triggers':policy.get('triggers',[]),'active_radius':policy.get('active_radius',8),'routes':copy.deepcopy(policy.get('routes',[])) if owner_type=='npc' else [],'fallback':policy.get('fallback','wait'),'cooldown_seconds':policy.get('cooldown_seconds',30),'timeout_seconds':policy.get('timeout_seconds',5),'memory':copy.deepcopy(state.get('memory',[])),'last_outcome':state.get('last_outcome'),'provider':{'adapter':profile.get('adapter'),'model':profile.get('model'),'endpoint_configured':bool(profile.get('endpoint_binding') and os.getenv(profile.get('endpoint_binding',''))),'credential_configured':not profile.get('credential_binding') or bool(os.getenv(profile.get('credential_binding','')))}}
        if owner_type=='npc': result.update(location=copy.deepcopy(state.get('location') or owner.get('location')),map_id=state.get('map_id') or owner.get('map_id',self.engine.default_map_id),active_route=copy.deepcopy(state.get('route_target')),route_visits=state.get('route_visits',0))
        else: result.update(base_prices=copy.deepcopy(state.get('base_prices',{})),paused_offers=copy.deepcopy(state.get('paused_offers',{})))
        result['recent_traces']=self.decision_traces(owner_id,owner_type,5)
        return result

    def set_decision_controller_paused(self, owner_id: str, owner_type: str, paused: bool):
        if type(paused) is not bool: raise ValueError('paused must be true or false')
        operation='pause_decision_controller' if paused else 'resume_decision_controller'
        def record(con,result): self.audit(con,operation,{'owner_id':owner_id,'owner_type':owner_type,'revision':result['revision']})
        try: return self.engine.set_decision_paused(owner_type,owner_id,paused,audit=record)
        except GameError as exc: raise ValueError(str(exc)) from exc

    def adjust_player(self, username, request_id, effects, location=None, reason=''):
        if not request_id or len(request_id) > 128 or not reason.strip():
            raise ValueError('A unique request_id and a reason are required')
        with self.engine.lock:
            self.refresh()
            plugin_effects = {kind for plugin in self.engine.world.get('plugins', []) if plugin.get('enabled', True) for kind in plugin.get('effect_types', [])}
            check_effects(effects, 'player grant', {q['id'] for q in self.engine.world['quests']}, plugin_effects)
            if len(effects) > 50:
                raise ValueError('At most 50 effects per request')
            if location is not None:
                state_map = location.get('map_id', self.engine.default_map_id)
                area = self.engine.map_data(state_map)
                check_location(location, area, 'teleport')
                tile = area['tiles'][location['y'] * area['width'] + location['x']]
                if not self.engine.world['biomes'][tile['biome']].get('passable', True):
                    raise ValueError('Teleport destination must be passable')
            signature = digest({'username': username, 'effects': effects, 'location': location, 'reason': reason})
            with self.engine.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                prior = con.execute('SELECT signature,result FROM management_requests WHERE id=?', (request_id,)).fetchone()
                if prior:
                    if prior[0] != signature:
                        raise ValueError('request_id was already used for a different operation')
                    return json.loads(prior[1])
                if not con.execute('SELECT 1 FROM players WHERE username=?', (username,)).fetchone():
                    raise ValueError('Unknown player; the character must have logged in first')
                state = self.engine.load(con, username)
                output = self.engine.effects(state, effects)
                notice = []
                for effect in effects:
                    if effect['type'] == 'currency':
                        notice.append(f"{self.engine.presentation.get('currency_icon', '🪙')} {effect.get('amount', 0)} {self.engine.world['economy']['currency']}")
                    elif effect['type'] == 'item':
                        notice.append(f"{self.engine.item_icon(effect['item'])} {effect.get('count', 1)} {effect['item']}")
                    elif effect['type'] == 'heal':
                        notice.append(f"💚 {effect.get('amount', 0)} health")
                    elif effect['type'] == 'quest':
                        notice.append(f"📜 quest {effect['target']}")
                    elif effect['type'] == 'flag':
                        notice.append(f"🔖 {effect['name']}")
                    else:
                        notice.append(f"✨ {effect['type']}")
                if notice:
                    state.setdefault('notifications', []).append('📣 Game update: ' + ', '.join(notice) + '.')
                if location is not None:
                    state.update(map_id=location.get('map_id', state.get('map_id', self.engine.default_map_id)), x=location['x'], y=location['y'])
                    state['dialog'] = None
                    coordinate = self.engine.location_key(state['map_id'], location['x'], location['y'])
                    if coordinate not in state['discovered']:
                        state['discovered'].append(coordinate)
                output += self.engine.advance(state)
                result = {'username': username, 'state': state, 'output': output, 'request_id': request_id}
                con.execute('UPDATE players SET state=? WHERE username=?', (json.dumps(state), username))
                con.execute('INSERT INTO management_requests VALUES (?,?,?)', (request_id, signature, json.dumps(result)))
                self.audit(con, 'adjust_player', {'username': username, 'request_id': request_id, 'effects': effects, 'location': location, 'reason': reason})
                return result

    def audit_log(self, limit=50):
        with self.engine.connect() as con:
            return [{'sequence': row[0], 'timestamp': row[1], 'operation': row[2], 'details': json.loads(row[3])} for row in con.execute('SELECT * FROM management_audit ORDER BY sequence DESC LIMIT ?', (max(1, min(limit, 200)),))]


def create_runtime_server(manager, sessions, broadcast):
    mcp = server('Mudmaker Game Master', 'Manage a running MUD. Read world_context and event_schema before creating events. Draft, review, and activate explicitly. Use unique request ids for player grants. No shell access.', 'MUD_MCP_ALLOWED_HOSTS')

    @mcp.tool()
    async def world_context() -> dict:
        """Read live content, online names, and event status to plan coherent events."""
        manager.refresh()
        world = copy.deepcopy(manager.engine.world)
        world.pop('ssh_public_keys', None)
        return {'world': world, 'online': sorted(sessions), 'events': manager.list_events()}

    @mcp.tool()
    def event_schema() -> dict:
        """Get templates for AI-authored events. All entity ids must start with the event id plus '-'. References use full ids. Events may reference base content, but not other events."""
        return {'event': {'id': 'harvest-fair', 'name': 'Harvest Fair', 'description': 'A visiting caravan', 'entities': []},
                'entity_templates': {k: v for k, v in ENTITY_TEMPLATES.items() if k != 'world'},
                'objective_types': sorted(OBJECTIVE_TYPES), 'effect_types': sorted(EFFECT_TYPES)}

    @mcp.tool()
    def list_players() -> list[dict]:
        """Inspect saved player positions, inventories, quest progress, and flags."""
        return manager.players()

    @mcp.tool()
    def complete_event_scaffold(scaffold: dict, generated: dict) -> dict:
        """Merge an AI client's generated event with a partial authored scaffold, preserving nonblank fields. Returns a draft for review; use draft_event to validate and save it."""
        return merge_scaffold(generated, scaffold)

    @mcp.tool()
    def draft_event(event: dict) -> dict:
        """Validate and persist AI-authored event content as an inactive draft. Replaces only an existing draft of the same id."""
        return manager.draft(event)

    @mcp.tool()
    def list_events() -> list[dict]:
        """Read event drafts, revisions, active events, expiry times, and ended events."""
        return manager.list_events()

    @mcp.tool()
    def activate_event(event_id: str, expected_revision: str, expires_at: float | None = None) -> dict:
        """Publish a reviewed draft immediately. Optional expiry is a Unix timestamp. Activation persists across server restarts."""
        return manager.activate(event_id, expected_revision, expires_at)

    @mcp.tool()
    def end_event(event_id: str) -> dict:
        """Remove an event's live content while retaining earned rewards and history. Event ids cannot be reused."""
        return manager.end(event_id)

    @mcp.tool()
    def adjust_player(username: str, request_id: str, reason: str, effects: list[dict], location: dict | None = None) -> dict:
        """Grant typed item/currency/heal/flag/quest effects and optionally teleport an existing player. Repeating the same request id cannot duplicate rewards."""
        return manager.adjust_player(username, request_id, effects, location, reason)

    @mcp.tool()
    async def announce(message: str) -> dict:
        """Send a plain text announcement to all currently connected players."""
        if not message.strip() or len(message) > 2000 or any(ord(c) < 32 and c != '\n' for c in message):
            raise ValueError('Use 1–2000 characters of plain text')
        recipients = broadcast(message)
        with manager.engine.connect() as con:
            manager.audit(con, 'announce', {'message': message, 'recipients': recipients})
        return {'recipients': recipients}

    @mcp.tool()
    def audit_log(limit: int = 50) -> list[dict]:
        """Read persisted management actions, grants, announcements, and event lifecycle history."""
        return manager.audit_log(limit)

    @mcp.tool()
    def decision_inspector(owner_id: str = '', owner_type: str = '', limit: int = 20) -> list[dict]:
        """Inspect recent bounded decisions, candidates, outcomes, and timing without exposing provider credentials or raw response bodies."""
        return manager.decision_traces(owner_id, owner_type, limit)

    @mcp.tool()
    def decision_controller(owner_id: str, owner_type: str) -> dict:
        """Inspect one enabled NPC or market controller, including pause state, goals, memory, runtime owner state, and recent traces."""
        return manager.decision_controller(owner_id, owner_type)

    @mcp.tool()
    def pause_decision_controller(owner_id: str, owner_type: str) -> dict:
        """Pause an NPC or market controller. In-flight results become stale; controller state and audit history are preserved."""
        return manager.set_decision_controller_paused(owner_id, owner_type, True)

    @mcp.tool()
    def resume_decision_controller(owner_id: str, owner_type: str) -> dict:
        """Resume an NPC or market controller and make it eligible for its next configured trigger."""
        return manager.set_decision_controller_paused(owner_id, owner_type, False)

    @mcp.resource('mudmaker://event-schema')
    def schema_resource() -> str:
        return json.dumps(event_schema())

    @mcp.prompt()
    def special_event(concept: str) -> str:
        return f'Read world_context and event_schema. Create this playable MUD event: {concept}. Use existing lore and valid map coordinates. Include named NPCs, dialogue, structured quest objectives, and rewards as appropriate. Call draft_event, review its validated content, then activate_event using the returned revision. Announce the event after activation.'

    return mcp
