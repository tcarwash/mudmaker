"""Deterministic MUD rules shared by browser playtesting and SSH games."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import sqlite3
import threading
import types
from pathlib import Path

try:
    from .plugin_api import PluginAPI
except ImportError:
    from plugin_api import PluginAPI


class GameError(ValueError):
    pass


def find(entries, query):
    query = query.strip().casefold()
    if not query:
        raise GameError("Supply a name or id.")
    exact = [e for e in entries if query in (e.get("id", "").casefold(), e.get("name", e.get("item", "")).casefold())]
    matches = exact or [e for e in entries if query in e.get("name", e.get("item", "")).casefold()]
    if len(matches) != 1:
        raise GameError("No matching target." if not matches else "That name is ambiguous; use an exact name or id.")
    return matches[0]


def quantity_arg(argument):
    match = re.fullmatch(r"(.+?)(?:\s+(\d+))?", argument.strip())
    if not match:
        raise GameError("Supply an item name and optional quantity.")
    count = int(match.group(2) or 1)
    if not 1 <= count <= 9999:
        raise GameError("Quantity must be between 1 and 9999.")
    return match.group(1), count


class GameEngine:
    def __init__(self, world, database):
        self.lock = threading.RLock()
        self.before_command = None
        self.online_players = lambda: ()
        self.world = world
        self.presentation = world.get("presentation", {})
        self.plugin_api = PluginAPI()
        self.load_plugins()
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript("CREATE TABLE IF NOT EXISTS players (username TEXT PRIMARY KEY, state TEXT NOT NULL); CREATE TABLE IF NOT EXISTS world_state (key TEXT PRIMARY KEY, value INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS ground_items (x INTEGER NOT NULL, y INTEGER NOT NULL, item TEXT NOT NULL, count INTEGER NOT NULL CHECK(count>=0), PRIMARY KEY(x,y,item)); CREATE TABLE IF NOT EXISTS creature_contributions (creature TEXT NOT NULL, username TEXT NOT NULL, damage INTEGER NOT NULL, PRIMARY KEY(creature, username));")

    def connect(self):
        return sqlite3.connect(self.database, timeout=10)

    def load_plugins(self):
        # Python plugins execute with the game's privileges. Only install trusted code.
        plugin_dir = os.getenv("MUD_PLUGIN_DIR")
        for plugin in self.world.get("plugins", []):
            if not plugin.get("enabled", True):
                continue
            path = Path(plugin_dir) / f"{plugin['id']}.py" if plugin_dir else None
            if path and path.is_file():
                spec = importlib.util.spec_from_file_location(f"mud_plugin_{plugin['id']}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            elif plugin.get("source"):
                module = types.ModuleType(f"mud_plugin_{plugin['id']}")
                exec(compile(plugin["source"], f"{plugin['id']}.py", "exec"), module.__dict__)
            else:
                continue
            register = getattr(module, "register", None)
            if not callable(register):
                raise ValueError(f"Plugin {plugin['id']} must define register(api)")
            register(self.plugin_api)
        declared = {kind for plugin in self.world.get("plugins", []) if plugin.get("enabled", True) for kind in plugin.get("effect_types", [])}
        missing = declared - self.plugin_api.effects.keys()
        if missing:
            raise ValueError("Enabled plugins did not register declared effects: " + ", ".join(sorted(missing)))

    def initial_state(self):
        spawn = self.world["spawn"]
        return {"x": spawn["x"], "y": spawn["y"], "currency": self.world.get("starting_currency", 25), "inventory": copy.deepcopy(self.world.get("starting_inventory", {})), "equipment": {}, "active_quests": [], "completed_quests": [], "discovered": [f"{spawn['x']},{spawn['y']}"], "events": {}, "quest_baselines": {}, "flags": {}, "once": [], "hp": 100, "dialog": None, "notes": {}, "tracked_quest": None, "experience": 0, "combat": None, "notifications": []}

    def load(self, con, username):
        row = con.execute("SELECT state FROM players WHERE username=?", (username,)).fetchone()
        state = json.loads(row[0]) if row else self.initial_state()
        for key, value in self.initial_state().items():
            state.setdefault(key, copy.deepcopy(value))
        if not (0 <= state["x"] < self.world["width"] and 0 <= state["y"] < self.world["height"]):
            state.update(self.world["spawn"])
        if state.get("combat") and state["combat"].get("creature") not in {c["id"] for c in self.world.get("creatures", [])}:
            state["combat"] = None
        if state.get("dialog") and state["dialog"].get("id") not in {d["id"] for d in self.world["dialogs"]}:
            state["dialog"] = None
        return state

    def execute(self, username, line):
        with self.lock:
            if self.before_command:
                self.before_command()
            return self._execute(username, line)

    def _execute(self, username, line):
        """Apply one command and all resulting quest effects in a single transaction."""
        try:
            with self.connect() as con:
                con.execute("BEGIN IMMEDIATE")
                state = self.load(con, username)
                output = self.command(con, state, line.strip(), username)
                output += self.advance(state)
                output = [*state.pop("notifications", []), *output]
                con.execute("INSERT INTO players VALUES (?,?) ON CONFLICT(username) DO UPDATE SET state=excluded.state", (username, json.dumps(state)))
            return "\n".join(output)
        except GameError as exc:
            return str(exc)

    def snapshot(self, username):
        with self.lock:
            if self.before_command:
                self.before_command()
            with self.connect() as con:
                return self.load(con, username)

    def item_icon(self, name):
        item = next((i for i in self.world.get("items", []) if i["name"].casefold() == name.casefold()), None)
        if item and item.get("icon"):
            return item["icon"]
        key = name.casefold()
        return next((icon for token, icon in (("berry", "🍓"), ("herb", "🌿"), ("ration", "🍲"), ("fang", "🦷"), ("torch", "🔦"), ("note", "📜")) if token in key), "🎒")

    def prompt(self, username):
        state = self.snapshot(username)
        icons = []
        for slot, name in state.get("equipment", {}).items():
            icons.append(self.item_icon(name))
        gear = f" {' '.join(icons)}" if icons else ""
        return f"{username}{gear} > "

    def tile(self, state):
        return self.world["tiles"][state["y"] * self.world["width"] + state["x"]]

    def nearby(self, entries, state):
        return [e for e in entries if e.get("location", self.world["spawn"]) == {"x": state["x"], "y": state["y"]}]

    @staticmethod
    def event(state, kind, target, count=1):
        key = f"{kind}:{target}"
        state["events"][key] = state["events"].get(key, 0) + count

    def objective_progress(self, state, quest, objective):
        kind, target = objective["type"], objective.get("target")
        if kind == "discover":
            return len(state["discovered"])
        if kind == "defeat":
            return state["events"].get(f"defeat:{target}", 0) - state.get("quest_baselines", {}).get(quest["id"], {}).get(f"defeat:{target}", 0)
        if kind == "collect":
            return state["inventory"].get(target, 0)
        if kind == "visit":
            if isinstance(target, str):
                tile = next(t for t in self.world["tiles"] if t.get("id") == target)
                index = self.world["tiles"].index(tile)
                target = {"x": index % self.world["width"], "y": index // self.world["width"]}
            return int(f"{target['x']},{target['y']}" in state["discovered"])
        event_target = f"{objective['npc']}:{target}" if kind == "deliver" else target
        key = f"{kind}:{event_target}"
        baseline = state["quest_baselines"].get(quest["id"], {}).get(key, 0)
        return max(0, state["events"].get(key, 0) - baseline)

    def accept_quest(self, state, quest, check_giver=True):
        qid = quest["id"]
        if qid in state["completed_quests"]:
            raise GameError("That quest is already complete.")
        if qid in state["active_quests"]:
            return f"Quest already active: {quest['name']}."
        missing = set(quest.get("prerequisites", [])) - set(state["completed_quests"])
        if missing:
            raise GameError("Complete the prerequisite quests first: " + ", ".join(sorted(missing)))
        if check_giver and quest.get("giver") not in {n["id"] for n in self.nearby(self.world["npcs"], state)} and quest.get("giver"):
            raise GameError("Visit the quest giver to accept this quest.")
        state["active_quests"].append(qid)
        state["quest_baselines"][qid] = copy.deepcopy(state["events"])
        return f"Quest accepted: {quest['name']}. {quest.get('description', '')}"

    def advance(self, state):
        output = []
        # Prerequisites form a DAG; a completed quest may unlock another, but never auto-accept it.
        for quest in self.world["quests"]:
            if quest["id"] not in state["active_quests"] or quest["id"] in state["completed_quests"]:
                continue
            if all(self.objective_progress(state, quest, obj) >= obj.get("count", 1) for obj in quest["objectives"]):
                reward = quest.get("reward", {})
                state["currency"] += reward.get("currency", 0)
                for part in reward.get("items", []):
                    item, count = (part, 1) if isinstance(part, str) else (part["item"], part.get("count", 1))
                    state["inventory"][item] = state["inventory"].get(item, 0) + count
                state["completed_quests"].append(quest["id"])
                state["active_quests"].remove(quest["id"])
                if state.get("tracked_quest") == quest["id"]:
                    state["tracked_quest"] = None
                output.append(f"Quest complete: {quest['name']}! {quest.get('completion_dialog', '')} Reward: {self.presentation.get('currency_icon', '🪙')} {reward.get('currency', 0)} {self.world['economy']['currency']}.")
        return output

    def effects(self, state, effects):
        output = []
        for effect in effects:
            kind = effect["type"]
            if kind == "item":
                item, count = effect["item"], effect.get("count", 1)
                state["inventory"][item] = state["inventory"].get(item, 0) + count
                output.append(f"Received {self.item_icon(item)} {count} {item}.")
            elif kind == "currency":
                state["currency"] += effect.get("amount", 0)
                output.append(f"Received {self.presentation.get('currency_icon', '🪙')} {effect.get('amount', 0)} {self.world['economy']['currency']}.")
            elif kind == "heal":
                state["hp"] = min(100, state["hp"] + effect.get("amount", 0))
                output.append(f"💚 Restored {effect.get('amount', 0)} health.")
            elif kind == "flag":
                state["flags"][effect["name"]] = effect.get("value", True)
            elif kind == "quest":
                quest = find(self.world["quests"], effect["target"])
                if quest["id"] not in state["active_quests"] and quest["id"] not in state["completed_quests"]:
                    output.append(self.accept_quest(state, quest, False))
            elif kind in self.plugin_api.effects:
                result = self.plugin_api.effects[kind](self, state, effect)
                if result:
                    output.extend(result if isinstance(result, list) else [str(result)])
        return output

    @staticmethod
    def requirements(state, requirements):
        if requirements.get("quest") and requirements["quest"] not in state["completed_quests"]:
            raise GameError("This requires completing " + requirements["quest"] + ".")
        if requirements.get("flag") and not state["flags"].get(requirements["flag"]):
            raise GameError("You have not unlocked this action.")
        for part in requirements.get("items", []):
            if state["inventory"].get(part["item"], 0) < part.get("count", 1):
                raise GameError("You need " + part["item"] + ".")

    def dialogue_text(self, state):
        current = state["dialog"]
        dialog = find(self.world["dialogs"], current["id"])
        node = dialog["nodes"][current["node"]]
        return [node.get("text", ""), *[f"{i + 1}. {choice['text']}" for i, choice in enumerate(node.get("choices", []))]]

    def creature_hp(self, con, creature):
        key = f"enemy_hp:{creature['id']}"
        row = con.execute("SELECT value FROM world_state WHERE key=?", (key,)).fetchone()
        if not row or row[0] > 0:
            return row[0] if row else creature["max_hp"]
        if creature.get("respawn_seconds", 0):
            ready = con.execute("SELECT value FROM world_state WHERE key=?", (f"enemy_respawn:{creature['id']}",)).fetchone()
            if ready and __import__('time').time() >= ready[0]:
                con.execute("UPDATE world_state SET value=? WHERE key=?", (creature["max_hp"], key))
                return creature["max_hp"]
        return 0

    def exits(self, state):
        candidates = {"north": {"x": state['x'], "y": state['y'] - 1},
                      "south": {"x": state['x'], "y": state['y'] + 1},
                      "east": {"x": state['x'] + 1, "y": state['y']},
                      "west": {"x": state['x'] - 1, "y": state['y']}}
        candidates.update(self.tile(state).get('exits', {}))
        return {name: pos for name, pos in candidates.items()
                if 0 <= pos['x'] < self.world['width'] and 0 <= pos['y'] < self.world['height']
                and self.world['biomes'][self.world['tiles'][pos['y'] * self.world['width'] + pos['x']]['biome']].get('passable', True)}

    def quest_progress(self, state, quest):
        return [f"  {obj['type']} {obj.get('target', 'locations')}: {min(self.objective_progress(state, quest, obj), obj.get('count', 1))}/{obj.get('count', 1)}" for obj in quest['objectives']]

    def recipe_details(self, state, recipes):
        lines = []
        for recipe in recipes:
            required = {}
            for part in recipe['inputs']:
                required[part['item']] = required.get(part['item'], 0) + part.get('count', 1)
            possible = min(state['inventory'].get(item, 0) // count for item, count in required.items())
            station = recipe.get('station')
            here = not station or self.tile(state).get('station') == station
            parts = ', '.join(f"{item} {state['inventory'].get(item, 0)}/{count}" for item, count in required.items())
            lines.append(f"{recipe['name']} ({recipe['id']}): {parts} -> {recipe['output']['item']} x{recipe['output'].get('count', 1)}")
            lines.append(f"  Station: {station or 'anywhere'} | Can craft here: {possible if here else 0}" + (" (visit the station)" if not here else ""))
        return lines or ['No recipes in this realm.']

    def command(self, con, state, line, username):
        parts = line.split(maxsplit=1)
        command, argument = (parts[0].lower(), parts[1] if len(parts) > 1 else "") if parts else ("look", "")
        aliases = {"n": "north", "s": "south", "e": "east", "w": "west"}
        if state.get("combat") and command not in {"attack", "flee", "look", "map", "stats", "help", "?"}:
            raise GameError("You are in combat. Attack or flee before doing something else.")
        if command in aliases or command in ("north", "south", "east", "west"):
            argument, command = aliases.get(command, command), "go"
        if command in ("help", "?"):
            emote_help = "|".join(item["id"] for item in self.world.get("emotes", [])) or "none configured"
            return [f"look · map · exits · journal · note <text|clear> · track <quest|off> · rest · go <direction> · talk <NPC> · choose <number> · quests [start <name>] · gather <item> [count] · inventory · equip/unequip <item|slot> · inspect <item> · use <item> · shop · buy/sell <item> [count] · recipes [name] · craft <recipe> [count] · drop/take <item> [count] · give <item> [count] to <NPC> · interact <entity> [action] · emote <{emote_help}> · stats", "Multiplayer: who · say <message> · tell <player> <message> · quit"]
        if command == "emote":
            emotes = self.world.get("emotes", [])
            emote = next((item for item in emotes if item["id"].casefold() == argument.casefold()), None)
            if not emote:
                raise GameError("Choose an emote: " + (", ".join(item["id"] for item in emotes) or "none configured") + ".")
            return [f"You {emote['verb']} {emote['icon']}."]
        if command == "look":
            tile = self.tile(state)
            output = [f"{tile.get('name') or 'Unnamed place'} ({tile['biome']}) — {state['x']},{state['y']}", tile.get("description", "")]
            output += [f"🧑 {n['name']} ({n.get('role', 'traveler')})." for n in self.nearby(self.world["npcs"], state)]
            output += [f"🛒 Market: {v['name']} (shop)." for v in self.nearby(self.world["economy"]["vendors"], state)]
            output += [f"✨ {e['name']}: {e.get('description', '')} (interact {e['id']})." for e in self.nearby(self.world["interactables"], state)]
            enemies = self.nearby(self.world.get("creatures", []), state)
            output += [f"{creature.get('icon', '🐾')} Hostile: {creature['name']} ({self.creature_hp(con, creature)}/{creature['max_hp']} HP; attack {creature['id']})." for creature in enemies if self.creature_hp(con, creature) > 0]
            if state.get("combat"): output.append(f"In combat with {state['combat']['creature']}; use attack or flee.")
            resources = [*tile.get("resources", []), *self.world["biomes"][tile["biome"]].get("resources", [])]
            if resources:
                output.append("Gatherable: " + ", ".join(r["item"] for r in resources))
            if tile.get("station"):
                output.append("Crafting station: " + tile["station"])
            ground = con.execute('SELECT item,count FROM ground_items WHERE x=? AND y=? AND count>0 ORDER BY item', (state['x'], state['y'])).fetchall()
            if ground:
                output.append('On the ground: ' + ', '.join(f'{item} x{count} {self.item_icon(item)}' for item, count in ground) + ' (take <item>).')
            output.append('Exits: ' + (', '.join(self.exits(state)) or 'none'))
            if tile.get('rest', tile.get('station') in ('campfire', 'inn')):
                output.append('You can rest here.')
            quest = next((q for q in self.world['quests'] if q['id'] == state.get('tracked_quest')), None)
            if quest and quest['id'] in state['active_quests']:
                output += [f"Tracking: {quest['name']}", *self.quest_progress(state, quest)]
            return output
        if command == 'exits':
            return ['Exits: ' + (', '.join(self.exits(state)) or 'none')]
        if command == 'rest':
            tile = self.tile(state)
            if not tile.get('rest', tile.get('station') in ('campfire', 'inn')):
                raise GameError('Find a campfire, inn, or resting place first.')
            if state['hp'] >= 100:
                return ['You are already fully rested.']
            healed = 100 - state['hp']
            state['hp'] = 100
            return [f'You rest and recover {healed} HP.']
        if command == 'note':
            coordinate = f"{state['x']},{state['y']}"
            if not argument:
                return [state['notes'].get(coordinate, 'No note here. Use: note <text>, or note clear.')]
            if argument.lower() == 'clear':
                state['notes'].pop(coordinate, None)
                return ['Location note cleared.']
            if len(argument) > 500 or any(ord(c) < 32 for c in argument):
                raise GameError('Notes must be plain text, at most 500 characters.')
            state['notes'][coordinate] = argument
            return ['Location note saved in your journal.']
        if command == 'track':
            if argument.lower() == 'off':
                state['tracked_quest'] = None
                return ['Quest tracking cleared.']
            quest = find(self.world['quests'], argument)
            if quest['id'] not in state['active_quests']:
                raise GameError('Accept that quest before tracking it.')
            state['tracked_quest'] = quest['id']
            return [f"Tracking: {quest['name']}", *self.quest_progress(state, quest)]
        if command == 'journal':
            output = ['Exploration journal']
            for coordinate in sorted(set(state['discovered']), key=lambda c: tuple(map(int, c.split(',')))[::-1]):
                x, y = map(int, coordinate.split(','))
                if not (0 <= x < self.world['width'] and 0 <= y < self.world['height']):
                    continue
                tile = self.world['tiles'][y * self.world['width'] + x]
                output.append(f"  ({x},{y}) {tile.get('name') or tile['biome']}" + (f" — {state['notes'][coordinate]}" if coordinate in state['notes'] else ''))
            for quest in self.world['quests']:
                if quest['id'] in state['active_quests']:
                    output += [f"{'*' if state.get('tracked_quest') == quest['id'] else '-'} {quest['name']}", *self.quest_progress(state, quest)]
            output.append(f"{len(state['completed_quests'])} quests completed. Use track <quest> to pin progress to look.")
            return output
        if command in ('drop', 'take'):
            source = [{'name': name, 'count': count} for name, count in state['inventory'].items() if count > 0] if command == 'drop' else [{'name': row[0], 'count': row[1]} for row in con.execute('SELECT item,count FROM ground_items WHERE x=? AND y=? AND count>0', (state['x'], state['y']))]
            query, count = quantity_arg(argument)
            entry = find(source, query)
            item = entry['name']
            if command == 'drop' and item in state.get('equipment', {}).values():
                raise GameError(f"Unequip {item} before dropping it.")
            if entry['count'] < count:
                raise GameError(f"Only {entry['count']} {item} available.")
            delta = count if command == 'drop' else -count
            if command == 'drop':
                con.execute('INSERT INTO ground_items VALUES (?,?,?,?) ON CONFLICT(x,y,item) DO UPDATE SET count=count+excluded.count', (state['x'], state['y'], item, count))
            else:
                con.execute('UPDATE ground_items SET count=count-? WHERE x=? AND y=? AND item=?', (count, state['x'], state['y'], item))
            state['inventory'][item] = state['inventory'].get(item, 0) - delta
            return [f"{'Dropped' if command == 'drop' else 'Took'} {count} {item}."]
        if command == "map":
            discovered = set(state["discovered"])
            discovered.add(f"{state['x']},{state['y']}")
            occupants = {}
            for peer in sorted(self.online_players()):
                if peer == username:
                    continue
                other = self.load(con, peer)
                coordinate = f"{other['x']},{other['y']}"
                if coordinate in discovered:
                    occupants.setdefault((other['x'], other['y']), []).append(peer)
            output, visible_biomes = [], set()
            for y in range(self.world["height"]):
                row = []
                for x in range(self.world["width"]):
                    if f"{x},{y}" not in discovered:
                        row.append("·" if self.presentation.get("map_style") == "icons" else "?")
                        continue
                    biome_id = self.world["tiles"][y * self.world["width"] + x]["biome"]
                    visible_biomes.add(biome_id)
                    biome = self.world["biomes"][biome_id]
                    symbol = (biome.get("icon") or biome.get("symbol", ".")) if self.presentation.get("map_style") == "icons" else biome.get("symbol", ".")[:1]
                    if symbol in "@&?":
                        symbol = ":"
                    row.append(("🧭" if self.presentation.get("map_style") == "icons" else "@") if (x, y) == (state['x'], state['y']) else ("🧑" if self.presentation.get("map_style") == "icons" else "&") if (x, y) in occupants else symbol)
                output.append((" " if self.presentation.get("map_style") == "icons" else "").join(row))
            output.append("Legend: 🧭 you | 🧑 other online players | · unexplored" if self.presentation.get("map_style") == "icons" else "Legend: @ you | & other online players | ? unexplored")
            terrain = []
            for key in sorted(visible_biomes):
                biome = self.world['biomes'][key]
                symbol = (biome.get('icon') or biome.get('symbol', '.')) if self.presentation.get('map_style') == 'icons' else biome.get('symbol', '.')
                if symbol in ('@', '&', '?'):
                    symbol = ':'
                terrain.append(f"{symbol} {biome.get('name', key)}")
            output.append("Terrain: " + " | ".join(terrain))
            for (x, y), names in sorted(occupants.items()):
                marker = "@" if (x, y) == (state['x'], state['y']) else "&"
                output.append(f"{marker} ({x},{y}): " + ", ".join(names) + (" (with you)" if marker == "@" else ""))
            return output
        if command == "flee":
            if not state.get("combat"):
                raise GameError("You are not in combat.")
            creature = find(self.world["creatures"], state["combat"]["creature"])
            state["combat"] = None
            damage = min(state["hp"] - 1, max(1, creature.get("damage", 1) // 2))
            state["hp"] -= damage
            return [f"You flee from {creature['name']} and take {damage} damage. You can still attack it later."]
        if command == "attack":
            query = argument or (state.get("combat") or {}).get("creature", "")
            if not query:
                enemies = [e for e in self.nearby(self.world.get("creatures", []), state) if self.creature_hp(con, e) > 0]
                if len(enemies) != 1:
                    raise GameError("Use attack <creature id> to choose your target.")
                creature = enemies[0]
            else:
                creature = find(self.nearby(self.world.get("creatures", []), state), query)
            enemy_hp = self.creature_hp(con, creature)
            if enemy_hp <= 0:
                raise GameError(f"{creature['name']} is defeated. It will return later if this creature respawns.")
            hit = min(5, enemy_hp)
            enemy_hp -= hit
            key = f"enemy_hp:{creature['id']}"
            con.execute("INSERT INTO creature_contributions VALUES (?,?,?) ON CONFLICT(creature,username) DO UPDATE SET damage=damage+excluded.damage", (creature["id"], username, hit))
            con.execute("INSERT INTO world_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, enemy_hp))
            output = [f"You strike {creature['name']} for {hit} damage ({enemy_hp}/{creature['max_hp']} HP)."]
            if enemy_hp <= 0:
                state["combat"] = None
                contributors = con.execute("SELECT username FROM creature_contributions WHERE creature=? ORDER BY username", (creature["id"],)).fetchall()
                recipients = [row[0] for row in contributors] or [username]
                con.execute("DELETE FROM creature_contributions WHERE creature=?", (creature["id"],))
                for recipient in recipients:
                    member = state if recipient == username else self.load(con, recipient)
                    member["combat"] = None
                    member["experience"] = member.get("experience", 0) + creature.get("experience", 0)
                    self.event(member, "defeat", creature["id"])
                    for drop in creature.get("loot", []):
                        item, count = drop["item"], drop.get("count", 1)
                        member["inventory"][item] = member["inventory"].get(item, 0) + count
                    completed = self.advance(member)
                    if recipient != username:
                        con.execute("UPDATE players SET state=? WHERE username=?", (json.dumps(member), recipient))
                    if completed:
                        output.extend([f"{recipient}: {message}" for message in completed])
                reward_verb = "gains" if len(recipients) == 1 else "gain"
                output.append(f"{creature['name']} is defeated. " + ", ".join(recipients) + f" {reward_verb} {creature.get('experience', 0)} XP and their share of the loot.")
                if creature.get("loot"):
                    output.append("Loot: " + ", ".join(f"{part.get('count', 1)} {part['item']} per contributor" for part in creature["loot"]))
                respawn = creature.get("respawn_seconds", 0)
                if respawn:
                    ready_at = int(__import__('time').time()) + respawn
                    con.execute("INSERT INTO world_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"enemy_respawn:{creature['id']}", ready_at))
            else:
                state["combat"] = {"creature": creature["id"]}
                damage = creature.get("damage", 0)
                state["hp"] -= damage
                output.append(f"{creature['name']} hits back for {damage} damage ({state['hp']} HP).")
                if state["hp"] <= 0:
                    state["hp"] = 50
                    state.update(self.world["spawn"])
                    state["combat"] = None
                    output.append("You are knocked out and carried back to your starting place with 50 HP. Your items and currency are safe.")
            return output
        if command == "go":
            if state.get("combat"):
                raise GameError("You are in combat. Attack or flee before moving.")
            directions = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
            argument = aliases.get(argument.lower(), argument.lower())
            location = self.tile(state).get("exits", {}).get(argument)
            if location:
                x, y = location["x"], location["y"]
            elif argument in directions:
                dx, dy = directions[argument]; x, y = state["x"] + dx, state["y"] + dy
            else:
                raise GameError("Choose north, south, east, west, or a named room exit.")
            if not (0 <= x < self.world["width"] and 0 <= y < self.world["height"]):
                raise GameError("The edge of the known world blocks your way.")
            tile = self.world["tiles"][y*self.world["width"]+x]
            if not self.world["biomes"][tile["biome"]].get("passable", True):
                raise GameError("That terrain is impassable.")
            state.update(x=x, y=y, dialog=None)
            coord = f"{x},{y}"
            if coord not in state["discovered"]:
                state["discovered"].append(coord)
            return [f"You travel {argument}.", *self.command(con, state, "look", username)]
        if command == "talk":
            npc = find(self.nearby(self.world["npcs"], state), argument)
            self.event(state, "talk", npc["id"])
            if npc.get("dialog_id"):
                dialog = find(self.world["dialogs"], npc["dialog_id"])
                state["dialog"] = {"id": dialog["id"], "node": dialog["start"]}
                output = self.dialogue_text(state)
            else:
                output = [f"{npc['name']}: {npc.get('dialog', 'Greetings, traveler.')} "]
            hooks = [q for q in self.world["quests"] if q.get("giver") == npc["id"] and q["id"] not in state["completed_quests"]]
            return output + ["Available quests: " + ", ".join(q['name'] for q in hooks)] if hooks else output
        if command == "choose":
            if not state["dialog"]:
                raise GameError("Talk to someone first.")
            dialog = find(self.world["dialogs"], state["dialog"]["id"])
            choices = dialog["nodes"][state["dialog"]["node"]].get("choices", [])
            if not argument.isdigit() or not 1 <= int(argument) <= len(choices):
                raise GameError("Choose one of the displayed numbers.")
            choice = choices[int(argument)-1]
            self.requirements(state, choice.get("requires", {}))
            marker = f"dialog:{dialog['id']}:{state['dialog']['node']}:{argument}"
            output = []
            if not choice.get("once", True) or marker not in state["once"]:
                output += self.effects(state, choice.get("effects", []))
                state["once"].append(marker)
            if choice.get("next"):
                state["dialog"]["node"] = choice["next"]
                output += self.dialogue_text(state)
            else:
                state["dialog"] = None
                output.append(choice.get("response", "The conversation ends."))
            return output
        if command in ("quests", "quest"):
            if argument.lower().startswith("start "):
                return [self.accept_quest(state, find(self.world["quests"], argument[6:]))]
            output = []
            for quest in self.world["quests"]:
                qid = quest["id"]
                status = "complete" if qid in state["completed_quests"] else "active" if qid in state["active_quests"] else "locked" if set(quest.get("prerequisites", [])) - set(state["completed_quests"]) else "available"
                output.append(f"[{status}] {quest['name']} ({qid}): {quest.get('description', '')}")
                if status == "active":
                    output += self.quest_progress(state, quest)
            return output or ["There are no quests in this realm."]
        if command in ("equip", "unequip"):
            equipment = state.setdefault("equipment", {})
            if command == "unequip":
                if not argument:
                    return ["Equipped: " + (", ".join(f"{slot}: {self.item_icon(name)} {name}" for slot, name in equipment.items()) or "nothing")]
                match = next(((slot, name) for slot, name in equipment.items() if argument.casefold() in (slot.casefold(), name.casefold())), None)
                if not match:
                    raise GameError("That item or equipment slot is not equipped.")
                slot, name = match
                del equipment[slot]
                return [f"Unequipped {self.item_icon(name)} {name} from {slot}."]
            item = find(self.world["items"], argument)
            slot, name = item.get("equip_slot", ""), item["name"]
            if not slot:
                raise GameError(f"{name} cannot be equipped.")
            if state["inventory"].get(name, 0) < 1:
                raise GameError(f"You do not have {name}.")
            previous = equipment.get(slot)
            equipment[slot] = name
            return [f"Equipped {self.item_icon(name)} {name} in {slot}." + (f" Replaced {previous}." if previous else "")]
        if command == "inventory":
            equipment = state.get("equipment", {})
            items = ", ".join(f"{self.item_icon(name)} {name} ×{count}" + (f" [equipped: {next(slot for slot, equipped in equipment.items() if equipped == name)}]" if name in equipment.values() else "") for name, count in state["inventory"].items() if count > 0)
            gear = ", ".join(f"{slot}: {self.item_icon(name)} {name}" for slot, name in equipment.items()) or "nothing"
            return [f"Purse: {self.presentation.get('currency_icon', '🪙')} {state['currency']} {self.world['economy']['currency']}", "Inventory: " + (items or "empty"), "Equipped: " + gear]
        if command == "stats":
            return [f"{username} · {self.presentation.get('currency_icon', '🪙')} {state['currency']} {self.world['economy']['currency']} · HP {state['hp']}/100 · XP {state.get('experience', 0)} · {len(state['discovered'])} places discovered · ({state['x']},{state['y']})"]
        if command == "gather":
            query, count = quantity_arg(argument)
            tile = self.tile(state)
            resources = [*tile.get("resources", []), *self.world["biomes"][tile["biome"]].get("resources", [])]
            resource = find(resources, query)
            key = f"resource:{state['x']},{state['y']}:{resource['item']}"
            left = self.shared_count(con, key, resource.get("quantity", resource.get("count", 1)))
            if left < count:
                raise GameError(f"Only {left} {resource['item']} remain here.")
            con.execute("INSERT INTO world_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, left-count))
            item = resource["item"]
            state["inventory"][item] = state["inventory"].get(item, 0) + count
            return [f"Gathered {count} {item} {self.item_icon(item)}."]
        if command in ("inspect", "use"):
            item = find(self.world["items"], argument)
            name = item["name"]
            if command == "inspect":
                return [f"{self.item_icon(name)} {name}: {item.get('description', '')}"]
            if state["inventory"].get(name, 0) < 1:
                raise GameError("You do not have that item.")
            if command == "use" and name in state.get("equipment", {}).values():
                raise GameError(f"Unequip {name} before using it.")
            if not item.get("use_effects"):
                raise GameError("That item has no use effect.")
            state["inventory"][name] -= 1
            return [f"Used {self.item_icon(name)} {name}.", *self.effects(state, item["use_effects"])]
        if command == "shop":
            output = []
            for vendor in self.nearby(self.world["economy"]["vendors"], state):
                output.append(vendor["name"])
                for part in vendor.get("stock", []):
                    key = f"stock:{vendor['id']}:{part['item']}"
                    count = self.shared_count(con, key, part["quantity"]) if "quantity" in part else "unlimited"
                    output.append(f"  {self.item_icon(part['item'])} {part['item']}: buy {part['price']}, sell {part.get('sell_price', part['price']//2)}, stock {count}")
            return output or ["There are no vendors here."]
        if command in ("buy", "sell"):
            query, count = quantity_arg(argument)
            vendors = self.nearby(self.world["economy"]["vendors"], state)
            offers = [{**part, "vendor": vendor} for vendor in vendors for part in vendor.get("stock", [])]
            part = find(offers, query); item = part["item"]
            if command == "sell" and item in state.get("equipment", {}).values():
                raise GameError(f"Unequip {item} before selling it.")
            key = f"stock:{part['vendor']['id']}:{item}"
            remaining = self.shared_count(con, key, part["quantity"]) if "quantity" in part else None
            if command == "buy":
                total = part["price"] * count
                if state["currency"] < total:
                    raise GameError(f"You need {total} {self.world['economy']['currency']}.")
                if remaining is not None and remaining < count:
                    raise GameError(f"The vendor has only {remaining} {item}.")
                state["currency"] -= total; state["inventory"][item] = state["inventory"].get(item, 0) + count
                if remaining is not None: remaining -= count
            else:
                if state["inventory"].get(item, 0) < count:
                    raise GameError(f"You do not have {count} {item}.")
                total = part.get("sell_price", part["price"] // 2) * count
                state["currency"] += total; state["inventory"][item] -= count
                if remaining is not None: remaining += count
            if remaining is not None:
                con.execute("INSERT INTO world_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, remaining))
            return [f"{'Bought' if command == 'buy' else 'Sold'} {count} {self.item_icon(item)} {item} for {self.presentation.get('currency_icon', '🪙')} {total}."]
        if command == 'recipes' or (command == 'craft' and not argument):
            recipes = [find(self.world['crafting'], argument)] if argument else self.world['crafting']
            return self.recipe_details(state, recipes)
        if command == "craft":
            query, batches = quantity_arg(argument)
            recipe = find(self.world["crafting"], query)
            if recipe.get("station") and recipe["station"] != self.tile(state).get("station"):
                raise GameError("This requires the " + recipe["station"] + " station.")
            required = {}
            for part in recipe["inputs"]:
                required[part["item"]] = required.get(part["item"], 0) + part.get("count", 1) * batches
            for item, count in required.items():
                if item in state.get("equipment", {}).values():
                    raise GameError(f"Unequip {item} before using it as a crafting ingredient.")
                if state["inventory"].get(item, 0) < count:
                    raise GameError(f"You need {count} {item}.")
            for item, count in required.items():
                state["inventory"][item] -= count
            part = recipe["output"]
            state["inventory"][part["item"]] = state["inventory"].get(part["item"], 0) + part.get("count", 1) * batches
            self.event(state, "craft", recipe["id"], batches)
            return [f"Crafted {part.get('count', 1) * batches} {part['item']} {self.item_icon(part['item'])}."]
        if command == "give":
            match = re.fullmatch(r"(.+?)\s+to\s+(.+)", argument, re.I)
            if not match:
                raise GameError("Use: give <item> [count] to <NPC>.")
            item_query, count = quantity_arg(match.group(1))
            item = find([{"name": n} for n, qty in state['inventory'].items() if qty > 0], item_query)["name"]
            if item in state.get("equipment", {}).values():
                raise GameError(f"Unequip {item} before giving it away.")
            npc = find(self.nearby(self.world["npcs"], state), match.group(2))
            if state["inventory"].get(item, 0) < count:
                raise GameError("You do not have that many.")
            accepted = any(q['id'] in state['active_quests'] and any(o['type'] == 'deliver' and o['target'] == item and o['npc'] == npc['id'] for o in q['objectives']) for q in self.world['quests'])
            if not accepted:
                raise GameError("That NPC is not asking for that item in an active quest.")
            state["inventory"][item] -= count
            self.event(state, "deliver", f"{npc['id']}:{item}", count)
            return [f"Delivered {count} {item} {self.item_icon(item)} to {npc['name']}."]
        if command == "interact":
            entries = self.nearby(self.world["interactables"], state)
            try:
                entity = find(entries, argument); action_id = ""
            except GameError:
                query, _, action_id = argument.rpartition(" ")
                entity = find(entries, query)
            actions = entity.get("actions", [])
            if not actions:
                return [entity.get("description", entity["name"])]
            if not action_id and len(actions) > 1:
                return ["Actions: " + ", ".join(a['id'] for a in actions)]
            action = next((a for a in actions if a['id'] == action_id), None) if action_id else actions[0]
            if not action:
                raise GameError("Unknown action.")
            marker = f"interaction:{entity['id']}:{action['id']}"
            if action.get("once", True) and marker in state["once"]:
                raise GameError("You have already used this action.")
            self.requirements(state, action.get("requires", {}))
            output = [action.get("text", entity.get("description", "")), *self.effects(state, action.get("effects", []))]
            state["once"].append(marker); self.event(state, "interact", entity["id"])
            return output
        if command in self.plugin_api.commands:
            handler, _ = self.plugin_api.commands[command]
            result = handler(self, con, state, username, argument)
            return result if isinstance(result, list) else [str(result)]
        raise GameError("Unknown command. Type help.")

    @staticmethod
    def shared_count(con, key, default):
        row = con.execute("SELECT value FROM world_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
