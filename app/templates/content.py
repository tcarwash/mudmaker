"""Portable content contract for the authoring app and exported games."""
from __future__ import annotations

import copy
import ipaddress
import re
from typing import Any

BIOMES = {
    "grassland": {"name": "Grassland", "description": "A quiet stretch of grassland.", "symbol": ".", "icon": "🌿", "color": "#b3cb81", "passable": True, "buildable": True},
    "forest": {"name": "Forest", "description": "A quiet stretch of forest.", "symbol": "T", "icon": "🌲", "color": "#537e5d", "passable": True, "buildable": True},
    "desert": {"name": "Desert", "description": "A quiet stretch of desert.", "symbol": "~", "icon": "🏜️", "color": "#dfc478", "passable": True, "buildable": True},
    "tundra": {"name": "Tundra", "description": "A quiet stretch of tundra.", "symbol": "*", "icon": "❄️", "color": "#d1dfe0", "passable": True, "buildable": True},
    "swamp": {"name": "Swamp", "description": "A quiet stretch of swamp.", "symbol": "%", "icon": "🐸", "color": "#859974", "passable": True, "buildable": True},
    "mountain": {"name": "Mountain", "description": "A quiet stretch of mountain terrain.", "symbol": "^", "icon": "⛰️", "color": "#92958a", "passable": True, "buildable": True},
    "water": {"name": "Water", "description": "Water stretches out before you.", "symbol": "=", "icon": "🌊", "color": "#76b5c6", "passable": False, "buildable": False},
    "road": {"name": "Road", "description": "A worn road crosses the landscape.", "symbol": "#", "icon": "🛤️", "color": "#c4a37c", "passable": True, "buildable": False},
}
OBJECTIVE_TYPES = {"discover", "visit", "talk", "collect", "craft", "deliver", "interact", "defeat"}
EFFECT_TYPES = {"item", "currency", "heal", "flag", "quest"}
CURRENT_SCHEMA_VERSION = 1
ENTITY_TEMPLATES = {
    "npc": {"id": "", "name": "", "role": "", "location": {"x": 0, "y": 0}, "dialog": "", "quest_hooks": [], "decision_policy": {"enabled": False, "provider_id": "", "goals": [], "actions": ["wait"], "observations": ["nearby"], "triggers": ["player_nearby"], "cooldown_seconds": 30, "timeout_seconds": 5, "fallback": "wait", "memory_limit": 20}},
    "quest": {"id": "", "name": "", "description": "", "giver": "", "objectives": [{"type": "discover", "count": 3}], "prerequisites": [], "reward": {"currency": 25, "items": []}},
    "crafting_recipe": {"id": "", "name": "", "description": "", "inputs": [{"item": "", "count": 1}], "output": {"item": "", "count": 1}, "station": ""},
    "vendor": {"id": "", "name": "", "location": {"x": 0, "y": 0}, "stock": [{"item": "", "price": 1, "sell_price": 0, "quantity": 20, "pricing": {"mode": "fixed", "base_price": 1, "target_stock": 20, "sensitivity": 1.0, "min_price": 0, "max_price": 1000, "spread": 1}}], "contracts": []},
    "item": {"id": "", "name": "", "description": "", "icon": "🎒", "equip_slot": "", "value": 1, "use_effects": []},
    "biome": {"id": "", "name": "", "description": "", "color": "#87a982", "symbol": "+", "icon": "🌿", "passable": True, "buildable": True, "resources": []},
    "room": {"id": "", "name": "", "description": "", "location": {"x": 0, "y": 0}, "biome": "grassland", "resources": [], "station": "", "rest": False, "exits": {}},
    "dialog": {"id": "", "name": "", "start": "greeting", "nodes": {"greeting": {"text": "", "choices": [{"text": "", "next": "greeting", "effects": []}]}}},
    "description": {"id": "", "name": "", "description": "", "location": {"x": 0, "y": 0}},
    "world": {"name": "", "description": "", "lore": "", "building": {"enabled": False, "tile_cost": 100, "tile_materials": [], "template_map_id": "", "max_tiles": 64, "max_areas_per_player": 1, "storage_capacity": 100, "allow_visitors": False, "allow_collaborators": False, "allow_room_editing": False, "allow_on_services": False}, "emotes": [{"id": "wave", "verb": "wave", "broadcast": "waves", "icon": "👋"}], "runtime_config": {"ssh_port": 2222, "mcp_bind": "127.0.0.1", "mcp_port": 2223, "mcp_allowed_hosts": ""}},
    "creature": {"id": "", "name": "", "description": "", "icon": "🐾", "location": {"x": 0, "y": 0}, "spawn_area": None, "max_hp": 20, "damage": 3, "experience": 5, "loot": [], "respawn_seconds": 0},
    "custom_entity": {"id": "", "name": "", "description": "", "location": {"x": 0, "y": 0}, "actions": [{"id": "inspect", "text": "", "effects": [], "once": True}]},
}


class ContentError(ValueError):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "entity"


def merge_scaffold(generated: Any, scaffold: Any) -> Any:
    """Keep authored values, recursively fill blank fields and partial list entries."""
    if scaffold is None or scaffold == "":
        return copy.deepcopy(generated)
    if isinstance(scaffold, dict):
        result = copy.deepcopy(generated) if isinstance(generated, dict) else {}
        for key, value in scaffold.items():
            result[key] = merge_scaffold(result.get(key), value)
        return result
    if isinstance(scaffold, list):
        if not scaffold:
            return copy.deepcopy(generated) if isinstance(generated, list) else []
        source = generated if isinstance(generated, list) else []
        return [merge_scaffold(source[i] if i < len(source) else None, scaffold[i]) if i < len(scaffold) else copy.deepcopy(source[i]) for i in range(max(len(source), len(scaffold)))]
    return copy.deepcopy(scaffold)


def objective(value: Any, path: str) -> dict:
    if isinstance(value, dict):
        result = copy.deepcopy(value)
    elif isinstance(value, str):
        numbers = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        match = re.search(r"discover\s+(\d+|" + "|".join(numbers) + r")\s+(?:distinct\s+)?locations", value, re.I)
        if not match:
            raise ContentError(f"{path}: use a structured objective, e.g. {{type: 'talk', target: 'mira', count: 1}}")
        token = match.group(1).lower()
        result = {"type": "discover", "count": int(token) if token.isdigit() else numbers[token]}
    else:
        raise ContentError(f"{path}: objective must be an object")
    if result.get("type") not in OBJECTIVE_TYPES:
        raise ContentError(f"{path}: objective type must be one of {', '.join(sorted(OBJECTIVE_TYPES))}")
    result.setdefault("count", 1)
    if type(result["count"]) is not int or result["count"] < 1:
        raise ContentError(f"{path}.count: must be a positive integer")
    if result["type"] != "discover" and not result.get("target"):
        raise ContentError(f"{path}.target: required for {result['type']}")
    return result


def compile_world(source: dict) -> dict:
    """Compile authored entities into the runtime's canonical collections."""
    world = migrate_world_schema(source)
    maps = world["maps"]
    if not isinstance(maps, list) or not 1 <= len(maps) <= 256:
        raise ContentError("maps: expected a list containing 1 to 256 maps")
    map_ids = set()
    for index, map_data in enumerate(maps):
        if not isinstance(map_data, dict):
            raise ContentError(f"maps[{index}]: expected an object")
        ident = map_data.get("id", "")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", ident) or ident in map_ids:
            raise ContentError(f"maps[{index}].id: expected a unique lowercase map id")
        map_ids.add(ident)
        width, height, tiles = map_data.get("width"), map_data.get("height"), map_data.get("tiles")
        if type(width) is not int or type(height) is not int or not isinstance(tiles, list) or len(tiles) != width * height:
            raise ContentError(f"map {ident}: dimensions and tiles are missing or inconsistent")
        map_data.setdefault("name", ident.replace("-", " ").title())
        map_data.setdefault("spawn", {"x": 0, "y": 0})
        map_data.setdefault("entry_points", {"start": copy.deepcopy(map_data["spawn"])})
    world.setdefault("default_map_id", maps[0]["id"])
    default_map = next((map_data for map_data in maps if map_data["id"] == world["default_map_id"]), None)
    if default_map is None:
        raise ContentError("default_map_id: must reference an authored map")
    # Keep the original top-level map interface as a live alias for old tools and games.
    for key in ("width", "height", "tiles", "spawn"):
        world[key] = default_map[key]
    for key in ("npcs", "quests", "crafting", "items", "dialogs", "interactables", "creatures"):
        world.setdefault(key, [])
    world.setdefault("economy", {}).setdefault("vendors", [])
    world["economy"].setdefault("currency", "Coins")
    world["economy"].setdefault("player_trade", {"enabled": False, "allow_in_homes": True, "offer_timeout_seconds": 600})
    world["economy"].setdefault("market_simulation", {"offline_simulation": True, "max_catchup_cycles": 24})
    world.setdefault("starting_currency", 25)
    building = world.setdefault("building", {})
    if isinstance(building, dict):
        for key, value in {"enabled": False, "tile_cost": 100, "tile_materials": [], "tile_cost_overrides": [], "paintable_biomes": [], "paint_cost": 0, "paint_materials": [], "structures": [], "max_structures_per_area": 128, "structure_refund_percent": 0, "tile_refund_percent": 0, "template_map_id": "", "max_tiles": 64, "max_areas_per_player": 1, "storage_capacity": 100, "allow_visitors": False, "allow_collaborators": False, "allow_room_editing": False, "allow_on_services": False}.items():
            building.setdefault(key, value)
    world.setdefault("presentation", {})
    world["presentation"].setdefault("currency_icon", "🪙")
    world["presentation"].setdefault("color", "auto")
    world["presentation"].setdefault("map_style", "ascii")
    world["presentation"].setdefault("palette", {"success": "green", "info": "cyan", "warning": "yellow", "error": "red"})
    world.setdefault("plugins", [])
    world.setdefault("decision_providers", [])
    world.setdefault("emotes", [
        {"id": "wave", "verb": "wave", "broadcast": "waves", "icon": "👋"},
        {"id": "cheer", "verb": "cheer", "broadcast": "cheers", "icon": "🎉"},
        {"id": "laugh", "verb": "laugh", "broadcast": "laughs", "icon": "😂"},
        {"id": "shrug", "verb": "shrug", "broadcast": "shrugs", "icon": "🤷"},
        {"id": "dance", "verb": "dance", "broadcast": "dances", "icon": "💃"},
    ])
    runtime_defaults = {"ssh_port": 2222, "mcp_bind": "127.0.0.1", "mcp_port": 2223, "mcp_allowed_hosts": "", "player_web": {"enabled": False, "port": 8080}, "ssh_enrollment": {"enabled": False, "approval_required": True}}
    runtime_config = world.setdefault("runtime_config", {})
    if isinstance(runtime_config, dict):
        for key, value in runtime_defaults.items():
            runtime_config.setdefault(key, value)
    world["biomes"] = {
        ident: {**copy.deepcopy(BIOMES.get(ident, {})), **definition}
        for ident, definition in {**BIOMES, **world.get("biomes", {})}.items()
    }
    routes = {"npc": world["npcs"], "quest": world["quests"], "crafting_recipe": world["crafting"], "vendor": world["economy"]["vendors"], "item": world["items"], "dialog": world["dialogs"], "creature": world["creatures"]}
    for entity in world.get("entities", []):
        if not isinstance(entity, dict):
            raise ContentError("entities: every entry must be an object")
        kind = entity.get("entity_type", "custom_entity")
        if kind in routes:
            routes[kind].append(copy.deepcopy(entity))
        elif kind == "biome":
            ident = entity.get("id") or slug(entity.get("name", ""))
            world["biomes"][ident] = {"symbol": "+", "color": "#87a982", "passable": True, "buildable": True, **world["biomes"].get(ident, {}), **entity, "id": ident}
        elif kind in ("room", "description"):
            location = entity.get("location")
            map_id = entity.get("map_id", world["default_map_id"])
            map_data = map_by_id(world, map_id)
            check_location(location, map_data, f"{kind} {entity.get('name', '')}.location")
            tile = map_data["tiles"][location["y"] * map_data["width"] + location["x"]]
            tile.update({k: v for k, v in entity.items() if k not in ("entity_type", "location")})
        elif kind == "world":
            for key in ("description", "lore", "starting_currency", "starting_inventory", "building", "emotes", "runtime_config"):
                if key in entity:
                    world[key] = copy.deepcopy(entity[key])
        else:
            world["interactables"].append(copy.deepcopy(entity))
    world.pop("entities", None)
    for map_data in maps:
        for index, tile in enumerate(map_data["tiles"]):
            if not isinstance(tile, dict):
                raise ContentError(f"map {map_data['id']}.tiles[{index}]: expected an object")
    for key, entries in {**routes, "interactable": world["interactables"]}.items():
        seen = set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
                raise ContentError(f"{key}[{index}].name: required")
            entry["id"] = entry.get("id") or slug(entry["name"])
            if entry["id"] in seen:
                raise ContentError(f"{key}: duplicate id '{entry['id']}'")
            seen.add(entry["id"])
    for quest in world["quests"]:
        quest["objectives"] = [objective(o, f"quest {quest['id']}.objectives[{i}]") for i, o in enumerate(quest.get("objectives", []))]
        if not quest["objectives"]:
            raise ContentError(f"quest {quest['id']}: at least one objective is required")
    for npc in world["npcs"]:
        if isinstance(npc.get("dialog"), dict):
            dialog = npc.pop("dialog")
            dialog.setdefault("id", npc["id"] + "-dialog")
            dialog.setdefault("name", npc["name"] + " dialogue")
            world["dialogs"].append(dialog)
            npc["dialog_id"] = dialog["id"]
    validate_runtime_world(world)
    return world


def migrate_world_schema(source: dict) -> dict:
    """Apply explicit, repeatable authored-world schema migrations."""
    if not isinstance(source, dict):
        raise ContentError("world: expected an object")
    world = copy.deepcopy(source)
    version = world.get("schema_version", 0)
    if type(version) is not int or version < 0:
        raise ContentError("schema_version: expected a nonnegative integer")
    if version > CURRENT_SCHEMA_VERSION:
        raise ContentError(f"schema_version {version} is newer than supported version {CURRENT_SCHEMA_VERSION}")
    while version < CURRENT_SCHEMA_VERSION:
        if version == 0:
            world = migrate_world_v0_to_v1(world)
            version = 1
        else:
            raise ContentError(f"schema_version {version}: no migration is available")
    world["schema_version"] = version
    return world


def migrate_world_v0_to_v1(world: dict) -> dict:
    """Canonicalize legacy single-map worlds without changing their spawn."""
    if "maps" not in world:
        width, height, tiles, spawn = (world.get(key) for key in ("width", "height", "tiles", "spawn"))
        if type(width) is not int or type(height) is not int or not isinstance(tiles, list) or len(tiles) != width * height:
            raise ContentError("Map dimensions and tiles are missing or inconsistent")
        if not isinstance(spawn, dict) or any(type(spawn.get(key)) is not int for key in ("x", "y")):
            raise ContentError("spawn: legacy world requires integer x and y")
        map_id = world.get("default_map_id", "overworld")
        world["maps"] = [{"id": map_id, "name": world.get("name", "Overworld"), "width": width, "height": height, "tiles": tiles, "spawn": copy.deepcopy(spawn), "entry_points": {"start": copy.deepcopy(spawn)}}]
        world["default_map_id"] = map_id
    else:
        maps = world["maps"]
        if not isinstance(maps, list) or not maps:
            raise ContentError("maps: legacy world requires at least one map")
        default_map_id = world.get("default_map_id", maps[0].get("id") if isinstance(maps[0], dict) else None)
        default_map = next((area for area in maps if isinstance(area, dict) and area.get("id") == default_map_id), None)
        if default_map is None:
            raise ContentError("default_map_id: must reference an authored map")
        world["default_map_id"] = default_map_id
        for key in ("width", "height", "tiles"):
            if key in default_map:
                world[key] = default_map[key]
        world["spawn"] = copy.deepcopy(default_map.get("spawn", world.get("spawn", {"x": 0, "y": 0})))
    world["schema_version"] = 1
    return world


def map_by_id(world: dict, ident: str) -> dict:
    maps = world.get("maps", [])
    if maps:
        found = next((map_data for map_data in maps if map_data.get("id") == ident), None)
        if found is None:
            raise ContentError(f"unknown map '{ident}'")
        return found
    return world


def check_location(location: Any, world: dict, path: str) -> None:
    if not isinstance(location, dict) or any(type(location.get(k)) is not int for k in ("x", "y")):
        raise ContentError(f"{path}: needs integer x and y")
    if not (0 <= location["x"] < world["width"] and 0 <= location["y"] < world["height"]):
        raise ContentError(f"{path}: outside the map")


def positive(value: Any, path: str, allow_zero: bool = False) -> None:
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ContentError(f"{path}: must be a {'nonnegative' if allow_zero else 'positive'} integer")


def check_effects(effects: list, path: str, quest_ids: set, plugin_effects: set | None = None, item_names: set[str] | None = None) -> None:
    if not isinstance(effects, list):
        raise ContentError(f"{path}: effects must be a list")
    for effect in effects:
        if not isinstance(effect, dict) or effect.get("type") not in EFFECT_TYPES | (plugin_effects or set()):
            raise ContentError(f"{path}: supported effects are {', '.join(sorted(EFFECT_TYPES | (plugin_effects or set())))}")
        if effect["type"] == "item":
            if not effect.get("item"):
                raise ContentError(f"{path}: item effect needs an item")
            if item_names is not None and (not isinstance(effect["item"], str) or effect["item"].casefold() not in item_names):
                raise ContentError(f"{path}: item effect references unknown item '{effect['item']}'")
            positive(effect.get("count", 1), path)
        elif effect["type"] in ("currency", "heal"):
            positive(effect.get("amount", 0), path, True)
        elif effect["type"] == "flag" and not effect.get("name"):
            raise ContentError(f"{path}: flag effect needs a name")
        elif effect["type"] == "quest" and effect.get("target") not in quest_ids:
            raise ContentError(f"{path}: quest effect references an unknown quest")


def validate_decision_policy(policy: Any, path: str, world: dict) -> None:
    providers=world.get("decision_providers",[])
    if not isinstance(providers,list) or len(providers)>16: raise ContentError("decision_providers: expected at most 16 profiles")
    ids=set()
    for provider in providers:
        if not isinstance(provider,dict) or not isinstance(provider.get("id"),str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}",provider["id"]) or provider["id"] in ids:
            raise ContentError("decision_providers: each profile needs a unique lowercase id")
        ids.add(provider["id"])
        if provider.get("adapter") not in ("laya_systemone","jev_systemone") or not isinstance(provider.get("model"),str) or not provider["model"].strip():
            raise ContentError(f"decision provider {provider['id']}: choose a supported typed adapter and model")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}",provider.get("endpoint_binding","")):
            raise ContentError(f"decision provider {provider['id']}.endpoint_binding: use an environment variable name")
        credential=provider.get("credential_binding","")
        if credential and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}",credential): raise ContentError(f"decision provider {provider['id']}.credential_binding: use an environment variable name")
        if credential and credential==provider.get("endpoint_binding"): raise ContentError(f"decision provider {provider['id']}: endpoint and credential need separate environment bindings")
    if policy is None:
        return
    if not isinstance(policy,dict): raise ContentError(f"{path}: expected an object")
    if type(policy.get("enabled",False)) is not bool: raise ContentError(f"{path}.enabled: expected true or false")
    if policy.get("enabled") and policy.get("provider_id") not in ids: raise ContentError(f"{path}.provider_id: choose a configured provider profile")
    if policy.get("fallback","wait") not in ("wait","authored"): raise ContentError(f"{path}.fallback: choose wait or authored")
    actions=policy.get("actions",["wait"]); allowed={"wait","move","dialogue","emote","task","trade","market_stock","gather","craft"}
    if path.startswith("npc "): allowed.discard("market_stock")
    else: allowed.difference_update({"gather","craft"})
    if path.startswith("vendor "): allowed={"wait","market_stock","market_price"}
    if not isinstance(actions,list) or not actions or len(actions)>8 or any(not isinstance(action,str) or action not in allowed for action in actions) or len(set(actions))!=len(actions):
        raise ContentError(f"{path}.actions: choose unique supported action types")
    observations=policy.get("observations",["nearby"]); allowed_observations={"nearby","own_state","public_quests","public_markets","visible_events"}
    if not isinstance(observations,list) or any(not isinstance(item,str) or item not in allowed_observations for item in observations): raise ContentError(f"{path}.observations: unsupported observation scope")
    triggers=policy.get("triggers",["player_nearby"]); allowed_triggers={"player_nearby","scheduled"}
    if not isinstance(triggers,list) or not triggers or any(not isinstance(item,str) or item not in allowed_triggers for item in triggers): raise ContentError(f"{path}.triggers: unsupported trigger")
    positive(policy.get("cooldown_seconds",30),f"{path}.cooldown_seconds",True)
    positive(policy.get("timeout_seconds",5),f"{path}.timeout_seconds")
    if policy.get("timeout_seconds",5)>120: raise ContentError(f"{path}.timeout_seconds: maximum is 120")
    positive(policy.get("memory_limit",20),f"{path}.memory_limit",True)
    if policy.get("memory_limit",20)>100: raise ContentError(f"{path}.memory_limit: maximum is 100 entries")
    positive(policy.get("active_radius",8),f"{path}.active_radius")
    if policy.get("active_radius",8)>32: raise ContentError(f"{path}.active_radius: maximum is 32 tiles")
    routes=policy.get("routes",[])
    if path.startswith("npc "):
        if not isinstance(routes,list) or len(routes)>32: raise ContentError(f"{path}.routes: expected at most 32 authored destinations")
        route_ids=set()
        for i,route in enumerate(routes):
            if not isinstance(route,dict) or not isinstance(route.get("id"),str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}",route["id"]): raise ContentError(f"{path}.routes[{i}].id: expected a lowercase destination id")
            if route["id"] in route_ids: raise ContentError(f"{path}.routes: duplicate destination id '{route['id']}'")
            route_ids.add(route["id"])
            map_id=route.get("map_id",world.get("default_map_id")); map_data=map_by_id(world,map_id)
            check_location(route.get("location"),map_data,f"{path}.routes[{i}].location")
            tile=map_data["tiles"][route["location"]["y"]*map_data["width"]+route["location"]["x"]]
            biome=world.get("biomes",{}).get(tile.get("biome"),BIOMES.get(tile.get("biome"),{}))
            if not biome.get("passable",True): raise ContentError(f"{path}.routes[{i}].location: destination terrain must be passable")
        if routes and "move" not in actions: raise ContentError(f"{path}.routes: enable the move action to use authored destinations")
        tasks=policy.get("tasks",[])
        if not isinstance(tasks,list) or len(tasks)>32: raise ContentError(f"{path}.tasks: expected at most 32 authored tasks")
        deliverable_items={item.get("name") for item in world.get("items",[]) if isinstance(item,dict)}
        deliverable_items.update(offer.get("item") for vendor in world.get("economy",{}).get("vendors",[]) for offer in vendor.get("stock",[]) if isinstance(offer,dict))
        deliverable_items.update(resource.get("item") for biome in world.get("biomes",{}).values() for resource in biome.get("resources",[]) if isinstance(resource,dict))
        deliverable_items.update(resource.get("item") for area in world.get("maps",[]) for tile in area.get("tiles",[]) for resource in tile.get("resources",[]) if isinstance(resource,dict))
        for recipe in world.get("crafting",[]):
            deliverable_items.add(recipe.get("output",{}).get("item")); deliverable_items.update(part.get("item") for part in recipe.get("inputs",[]))
        has_delivery=False
        task_ids=set(); quest_ids={q.get("id") for q in world.get("quests",[]) if isinstance(q,dict)}
        for i,task in enumerate(tasks):
            task_path=f"{path}.tasks[{i}]"
            if not isinstance(task,dict) or not isinstance(task.get("id"),str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}",task["id"]) or task["id"] in task_ids: raise ContentError(f"{task_path}.id: expected a unique lowercase task id")
            task_ids.add(task["id"])
            for field in ("name","description"):
                if not isinstance(task.get(field),str) or not task[field].strip() or len(task[field])>500: raise ContentError(f"{task_path}.{field}: required, up to 500 characters")
            steps=task.get("steps",1)
            if type(steps) is not int or not 1<=steps<=100: raise ContentError(f"{task_path}.steps: expected an integer from 1 to 100")
            timeout=task.get("timeout_seconds",86400)
            if type(timeout) is not int or not 60<=timeout<=604800: raise ContentError(f"{task_path}.timeout_seconds: expected an integer from 60 to 604800")
            if type(task.get("once",True)) is not bool: raise ContentError(f"{task_path}.once: expected true or false")
            cooldown=task.get("cooldown_seconds",86400)
            if type(cooldown) is not int or not 0<=cooldown<=2592000: raise ContentError(f"{task_path}.cooldown_seconds: expected an integer from 0 to 2592000")
            check_effects(task.get("effects",[]),f"{task_path}.effects",quest_ids)
            delivery=task.get("delivery")
            if delivery is not None:
                if not isinstance(delivery,dict) or delivery.get("item") not in deliverable_items: raise ContentError(f"{task_path}.delivery.item: choose an authored item, resource, or recipe ingredient")
                positive(delivery.get("count"),f"{task_path}.delivery.count")
                if delivery["count"]>9999: raise ContentError(f"{task_path}.delivery.count: maximum is 9999")
                if steps!=1: raise ContentError(f"{task_path}.steps: delivery tasks use one completion step")
                has_delivery=True
        if tasks and "task" not in actions: raise ContentError(f"{path}.tasks: enable the task action to use authored tasks")
        commerce=policy.get("commerce",{})
        if "trade" in actions:
            if not isinstance(commerce,dict): raise ContentError(f"{path}.commerce: expected an object")
            markets=commerce.get("markets",[]); items=commerce.get("items",[])
            if not isinstance(markets,list) or not markets or len(markets)>32 or any(not isinstance(market,str) for market in markets) or len(set(markets))!=len(markets): raise ContentError(f"{path}.commerce.markets: choose 1 to 32 unique market ids")
            vendors={vendor.get("id"):vendor for vendor in world.get("economy",{}).get("vendors",[]) if isinstance(vendor,dict)}
            if any(market not in vendors for market in markets): raise ContentError(f"{path}.commerce.markets: every market must reference an authored vendor")
            if not isinstance(items,list) or not items or len(items)>64 or any(not isinstance(item,str) for item in items) or len(set(items))!=len(items): raise ContentError(f"{path}.commerce.items: choose 1 to 64 unique items")
            offered={offer.get("item") for market in markets for offer in vendors[market].get("stock",[]) if isinstance(offer,dict)}
            if any(item not in offered for item in items): raise ContentError(f"{path}.commerce.items: every item must be offered by an allowed market")
            for key,default,minimum,maximum in (("starting_currency",0,0,100000000),("currency_reserve",0,0,100000000),("inventory_capacity",20,1,10000),("max_units_per_decision",1,1,20),("max_spend_per_decision",100,1,100000000)):
                value=commerce.get(key,default)
                if type(value) is not int or not minimum<=value<=maximum: raise ContentError(f"{path}.commerce.{key}: expected an integer from {minimum} to {maximum}")
            if type(commerce.get("allow_buy",True)) is not bool or type(commerce.get("allow_sell",True)) is not bool or not commerce.get("allow_buy",True) and not commerce.get("allow_sell",True): raise ContentError(f"{path}.commerce: enable buying, selling, or both")
            inventory=commerce.get("starting_inventory",{})
            authored_items={item.get("name") for item in world.get("items",[]) if isinstance(item,dict)}
            authored_items.update(offer.get("item") for vendor in world.get("economy",{}).get("vendors",[]) for offer in vendor.get("stock",[]) if isinstance(offer,dict))
            authored_items.update(resource.get("item") for biome in world.get("biomes",{}).values() for resource in biome.get("resources",[]) if isinstance(resource,dict))
            authored_items.update(resource.get("item") for area in world.get("maps",[]) for tile in area.get("tiles",[]) for resource in tile.get("resources",[]) if isinstance(resource,dict))
            for recipe in world.get("crafting",[]):
                authored_items.add(recipe.get("output",{}).get("item")); authored_items.update(part.get("item") for part in recipe.get("inputs",[]))
            if not isinstance(inventory,dict) or len(inventory)>64 or any(item not in authored_items or type(count) is not int or not 0<=count<=10000 for item,count in inventory.items()): raise ContentError(f"{path}.commerce.starting_inventory: use authored item names with counts from 0 to 10000")
            if sum(inventory.values())>commerce.get("inventory_capacity",20): raise ContentError(f"{path}.commerce.starting_inventory: total items cannot exceed inventory capacity")
            if (set(actions)&{"gather","craft"} or has_delivery) and isinstance(policy.get("resources",{}),dict) and policy.get("resources",{}).get("starting_inventory"): raise ContentError(f"{path}.resources.starting_inventory: configure the shared starting inventory under commerce when trade is enabled")
            if "own_state" not in observations: raise ContentError(f"{path}.observations: enable own_state for NPC commerce decisions")
        elif isinstance(policy.get("commerce"),dict) and (policy["commerce"].get("markets") or policy["commerce"].get("items") or policy["commerce"].get("starting_inventory")):
            raise ContentError(f"{path}.commerce: enable the trade action to use NPC commerce settings")
        resource_names={r.get("item") for biome in world.get("biomes",{}).values() for r in biome.get("resources",[]) if isinstance(r,dict)}
        for map_data in world.get("maps",[]):
            resource_names.update(r.get("item") for tile in map_data.get("tiles",[]) for r in tile.get("resources",[]) if isinstance(r,dict))
        recipe_ids={recipe.get("id") for recipe in world.get("crafting",[]) if isinstance(recipe,dict)}
        if "gather" in actions:
            gathering=policy.get("gathering",{}); items=gathering.get("items",[]) if isinstance(gathering,dict) else None
            if not isinstance(items,list) or not items or len(items)>64 or any(not isinstance(item,str) for item in items) or len(set(items))!=len(items) or any(item not in resource_names for item in items): raise ContentError(f"{path}.gathering.items: choose 1 to 64 unique authored gatherable item names")
            value=gathering.get("max_units_per_decision",1)
            if type(value) is not int or not 1<=value<=20: raise ContentError(f"{path}.gathering.max_units_per_decision: expected an integer from 1 to 20")
        elif isinstance(policy.get("gathering"),dict) and policy["gathering"].get("items"):
            raise ContentError(f"{path}.gathering: enable the gather action to use gathering settings")
        if "craft" in actions:
            crafting=policy.get("crafting",{}); recipes=crafting.get("recipes",[]) if isinstance(crafting,dict) else None
            if not isinstance(recipes,list) or not recipes or len(recipes)>64 or any(not isinstance(recipe,str) for recipe in recipes) or len(set(recipes))!=len(recipes) or any(recipe not in recipe_ids for recipe in recipes): raise ContentError(f"{path}.crafting.recipes: choose 1 to 64 unique authored recipe ids")
            value=crafting.get("max_batches_per_decision",1)
            if type(value) is not int or not 1<=value<=20: raise ContentError(f"{path}.crafting.max_batches_per_decision: expected an integer from 1 to 20")
        elif isinstance(policy.get("crafting"),dict) and policy["crafting"].get("recipes"):
            raise ContentError(f"{path}.crafting: enable the craft action to use crafting settings")
        if "gather" in actions or "craft" in actions or has_delivery:
            resources=policy.get("resources",{})
            if not isinstance(resources,dict): raise ContentError(f"{path}.resources: expected an object")
            positive(resources.get("inventory_capacity",100),f"{path}.resources.inventory_capacity")
            if resources.get("inventory_capacity",100)>10000: raise ContentError(f"{path}.resources.inventory_capacity: maximum is 10000")
            item_names={item.get("name") for item in world.get("items",[]) if isinstance(item,dict)}|resource_names
            for recipe in world.get("crafting",[]):
                item_names.add(recipe.get("output",{}).get("item")); item_names.update(part.get("item") for part in recipe.get("inputs",[]))
            inventory=resources.get("starting_inventory",{})
            if not isinstance(inventory,dict) or len(inventory)>64 or any(item not in item_names or type(count) is not int or not 0<=count<=10000 for item,count in inventory.items()): raise ContentError(f"{path}.resources.starting_inventory: use authored item names with counts from 0 to 10000")
            if sum(inventory.values())>resources.get("inventory_capacity",100): raise ContentError(f"{path}.resources.starting_inventory: total items cannot exceed inventory capacity")
            if "trade" in actions and sum(policy.get("commerce",{}).get("starting_inventory",{}).values())>resources.get("inventory_capacity",100): raise ContentError(f"{path}.commerce.starting_inventory: shared starting inventory cannot exceed the NPC resource capacity")
        elif isinstance(policy.get("resources"),dict) and policy["resources"].get("starting_inventory"):
            raise ContentError(f"{path}.resources: enable gather, craft, or a delivery task to use resource settings")
    elif policy.get("tasks"):
        raise ContentError(f"{path}.tasks: authored tasks are supported for NPC policies only")
    elif routes:
        raise ContentError(f"{path}.routes: authored destinations are available for NPC policies only")
    goals=policy.get("goals",[])
    if not isinstance(goals,list) or len(goals)>16: raise ContentError(f"{path}.goals: expected at most 16 measurable goals")
    seen_goals=set()
    for i,goal in enumerate(goals):
        if not isinstance(goal,dict) or not isinstance(goal.get("id"),str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}",goal["id"]) or not isinstance(goal.get("description"),str) or not goal["description"].strip():
            raise ContentError(f"{path}.goals[{i}]: needs a stable id and description")
        if goal["id"] in seen_goals: raise ContentError(f"{path}.goals: duplicate goal id '{goal['id']}'")
        seen_goals.add(goal["id"])
        metrics={"visitors_greeted","task_progress","deliveries_completed","route_visits","inventory_level","currency_balance","market_trades"} if path.startswith("npc ") else {"stock_level","stock_target","supply_level"} if path.startswith("vendor ") else {"visitors_greeted","task_progress","deliveries_completed","stock_level","stock_target","route_visits","event_variety","supply_level","inventory_level","currency_balance","market_trades"}
        if goal.get("metric") not in metrics:
            raise ContentError(f"{path}.goals[{i}].metric: choose a supported measurable metric")
        positive(goal.get("priority",1),f"{path}.goals[{i}].priority")
        if goal.get("target") is not None and (type(goal["target"]) not in (int,float) or goal["target"]<0): raise ContentError(f"{path}.goals[{i}].target: must be a nonnegative number")
        if path.startswith("npc ") and goal.get("metric") in ("inventory_level","currency_balance","market_trades") and goal.get("target") is None: raise ContentError(f"{path}.goals[{i}].target: set a numeric target for this NPC goal")
        if path.startswith("npc ") and goal.get("metric") in ("inventory_level","currency_balance","market_trades") and "trade" not in actions: raise ContentError(f"{path}.goals[{i}].metric: enable the NPC trade action to use commerce goals")
        if path.startswith("npc ") and goal.get("metric")=="deliveries_completed":
            if goal.get("target") is None: raise ContentError(f"{path}.goals[{i}].target: set a completion target for delivery work")
            if "task" not in actions or not any(task.get("delivery") for task in policy.get("tasks",[]) if isinstance(task,dict)): raise ContentError(f"{path}.goals[{i}].metric: add an authored delivery task to measure deliveries")
        if path.startswith("npc ") and goal.get("metric")=="inventory_level":
            commerce=policy.get("commerce",{})
            if "trade" not in actions or goal.get("item") not in commerce.get("items",[]): raise ContentError(f"{path}.goals[{i}].item: inventory goals require an item in the NPC commerce allowlist")
        if path.startswith("vendor ") and goal.get("metric") in ("stock_level", "stock_target", "supply_level"):
            if not isinstance(goal.get("item"), str) or not goal["item"].strip():
                raise ContentError(f"{path}.goals[{i}].item: name the offered item this stock goal controls")
            vendor_id=path.split()[1].split(".")[0]
            vendor=next((v for v in world.get("economy",{}).get("vendors",[]) if v.get("id")==vendor_id),None)
            if vendor:
                offer=next((offer for offer in vendor.get("stock",[]) if isinstance(offer,dict) and offer.get("item")==goal["item"]),None)
                if not offer: raise ContentError(f"{path}.goals[{i}].item: choose an item offered by this market")
                if "quantity" not in offer: raise ContentError(f"{path}.goals[{i}].item: market stock decisions require finite offer quantity")
                if goal.get("target") is None: raise ContentError(f"{path}.goals[{i}].target: set a stock threshold for this market goal")
                pricing=offer.get("pricing",{})
                if "market_price" in actions and (not isinstance(pricing,dict) or pricing.get("mode")!="stock_based"): raise ContentError(f"{path}.goals[{i}].item: market price decisions require stock-based pricing")


def validate_runtime_world(world: dict) -> None:
    validate_decision_policy(None,"decision_policy",world)
    maps = world.get("maps") or [{"id": world.get("default_map_id", "overworld"), "width": world.get("width"), "height": world.get("height"), "tiles": world.get("tiles"), "spawn": world.get("spawn"), "entry_points": {"start": world.get("spawn")}}]
    map_ids = set()
    for index, map_data in enumerate(maps):
        path = f"maps[{index}]"
        if not isinstance(map_data, dict):
            raise ContentError(f"{path}: expected an object")
        ident = map_data.get("id", "")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", ident) or ident in map_ids:
            raise ContentError(f"{path}.id: expected a unique lowercase map id")
        map_ids.add(ident)
        width, height = map_data.get("width"), map_data.get("height")
        if type(width) is not int or type(height) is not int or not (4 <= width <= 64 and 4 <= height <= 64):
            raise ContentError(f"map {ident}: dimensions must be integers between 4 and 64")
        if not isinstance(map_data.get("tiles"), list) or len(map_data["tiles"]) != width * height:
            raise ContentError(f"map {ident}: must have exactly width × height tiles")
        check_location(map_data.get("spawn"), map_data, f"map {ident}.spawn")
        entries = map_data.get("entry_points", {})
        if not isinstance(entries, dict) or not entries:
            raise ContentError(f"map {ident}.entry_points: expected at least one named entry")
        for entry_name, location in entries.items():
            if not isinstance(entry_name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", entry_name):
                raise ContentError(f"map {ident}.entry_points: entry names must be lowercase ids")
            check_location(location, map_data, f"map {ident}.entry_points.{entry_name}")
            entry_tile = map_data["tiles"][location["y"] * map_data["width"] + location["x"]]
            biome = world.get("biomes", {}).get(entry_tile.get("biome"), BIOMES.get(entry_tile.get("biome"), {}))
            if not biome.get("passable", True):
                raise ContentError(f"map {ident}.entry_points.{entry_name}: entry terrain must be passable")
    if world.get("default_map_id") not in map_ids:
        raise ContentError("default_map_id: must reference an authored map")
    emotes = world.get("emotes", [])
    if not isinstance(emotes, list) or len(emotes) > 32:
        raise ContentError("emotes: expected a list with no more than 32 entries")
    presentation = world.get("presentation", {})
    if not isinstance(presentation, dict):
        raise ContentError("presentation: expected an object")
    if presentation.get("color", "auto") not in ("auto", "on", "off"):
        raise ContentError("presentation.color: choose auto, on, or off")
    if presentation.get("map_style", "ascii") not in ("ascii", "icons"):
        raise ContentError("presentation.map_style: choose ascii or icons")
    if not isinstance(presentation.get("currency_icon", "🪙"), str) or len(presentation.get("currency_icon", "🪙")) > 12:
        raise ContentError("presentation.currency_icon: use a short text or emoji icon")
    palette = presentation.get("palette", {})
    allowed_colors = {"black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"}
    if not isinstance(palette, dict) or any(role not in ("success", "info", "warning", "error") or color not in allowed_colors for role, color in palette.items()):
        raise ContentError("presentation.palette: use standard terminal colors for success, info, warning, and error")
    plugins = world.get("plugins", [])
    plugin_effects = set()
    if not isinstance(plugins, list) or len(plugins) > 32:
        raise ContentError("plugins: expected a list with no more than 32 plugins")
    plugin_ids = set()
    for plugin in plugins:
        if not isinstance(plugin, dict) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", plugin.get("id", "")) or plugin["id"] in plugin_ids:
            raise ContentError("plugins: each plugin needs a unique lowercase id")
        plugin_ids.add(plugin["id"])
        if not isinstance(plugin.get("source", ""), str) or len(plugin.get("source", "")) > 50000:
            raise ContentError(f"plugin {plugin['id']}.source: must be text under 50000 characters")
        try:
            compile(plugin.get("source", ""), f"{plugin['id']}.py", "exec")
        except SyntaxError as exc:
            raise ContentError(f"plugin {plugin['id']}: Python syntax error on line {exc.lineno}")
        declared = plugin.get("effect_types", [])
        if not isinstance(declared, list) or any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name) for name in declared):
            raise ContentError(f"plugin {plugin['id']}.effect_types: use lowercase effect ids")
        plugin_effects.update(declared if plugin.get("enabled", True) else [])
    emote_ids = set()
    for emote in emotes:
        if not isinstance(emote, dict) or not isinstance(emote.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,23}", emote["id"]):
            raise ContentError("emotes: each entry needs a unique lowercase id")
        if emote["id"] in emote_ids:
            raise ContentError(f"emotes: duplicate id '{emote['id']}'")
        emote_ids.add(emote["id"])
        for key in ("verb", "broadcast", "icon"):
            if not isinstance(emote.get(key), str) or not emote[key].strip() or len(emote[key]) > 48 or any(ord(char) < 32 for char in emote[key]):
                raise ContentError(f"emote {emote['id']}.{key}: required, maximum 48 characters")
    config = world.get("runtime_config", {})
    if not isinstance(config, dict):
        raise ContentError("runtime_config: expected an object")
    for key in ("ssh_port", "mcp_port"):
        if type(config.get(key)) is not int or not 1 <= config[key] <= 65535:
            raise ContentError(f"runtime_config.{key}: must be a port from 1 to 65535")
    try:
        if not isinstance(config.get("mcp_bind"), str) or ipaddress.ip_address(config["mcp_bind"]).version != 4:
            raise ValueError
    except ValueError:
        raise ContentError("runtime_config.mcp_bind: must be an IPv4 bind address")
    if not isinstance(config.get("mcp_allowed_hosts", ""), str) or not re.fullmatch(r"[A-Za-z0-9.*:, -]*", config.get("mcp_allowed_hosts", "")):
        raise ContentError("runtime_config.mcp_allowed_hosts: use comma-separated host names and optional ports")
    web = config.get("player_web", {})
    if not isinstance(web, dict) or type(web.get("enabled", False)) is not bool:
        raise ContentError("runtime_config.player_web.enabled: expected true or false")
    if type(web.get("port", 8080)) is not int or not 1 <= web.get("port", 8080) <= 65535:
        raise ContentError("runtime_config.player_web.port: must be a port from 1 to 65535")
    enrollment=config.get("ssh_enrollment", {})
    if not isinstance(enrollment,dict) or type(enrollment.get("enabled",False)) is not bool or type(enrollment.get("approval_required",True)) is not bool:
        raise ContentError("runtime_config.ssh_enrollment: enabled and approval_required must be true or false")
    for ident, biome in world["biomes"].items():
        if not isinstance(biome, dict) or not re.fullmatch(r"#[0-9a-fA-F]{6}", biome.get("color", "#87a982")) or not isinstance(biome.get("symbol", "+"), str) or len(biome.get("symbol", "+")) != 1:
            raise ContentError(f"biome {ident}: color needs #rrggbb and symbol needs one character")
        if not isinstance(biome.get("icon", ""), str) or len(biome.get("icon", "")) > 12:
            raise ContentError(f"biome {ident}.icon: use a short text or emoji icon")
        if type(biome.get("buildable", True)) is not bool:
            raise ContentError(f"biome {ident}.buildable: must be true or false")
        for resource in biome.get("resources", []):
            if not resource.get("item"):
                raise ContentError(f"biome {ident}.resources: item required")
            positive(resource.get("quantity", resource.get("count", 1)), f"biome {ident}.resources.quantity")
    building = world.get("building", {})
    if not isinstance(building, dict):
        raise ContentError("building: expected an object")
    for key in ("enabled", "allow_on_services", "allow_visitors", "allow_collaborators", "allow_room_editing"):
        if type(building.get(key)) is not bool:
            raise ContentError(f"building.{key}: must be true or false")
    positive(building.get("tile_cost"), "building.tile_cost", True)
    positive(building.get("max_tiles"), "building.max_tiles")
    positive(building.get("max_areas_per_player"), "building.max_areas_per_player")
    positive(building.get("storage_capacity",100), "building.storage_capacity", True)
    if building.get("template_map_id") and building["template_map_id"] not in map_ids:
        raise ContentError("building.template_map_id: unknown map")
    materials = building.get("tile_materials", [])
    if not isinstance(materials, list) or len(materials) > 32:
        raise ContentError("building.tile_materials: expected a list of at most 32 materials")
    for part in materials:
        if not isinstance(part, dict) or not isinstance(part.get("item"), str) or not part["item"].strip():
            raise ContentError("building.tile_materials: each entry needs an item name")
        positive(part.get("count"), "building.tile_materials.count")
    paintable=building.get("paintable_biomes",[])
    if not isinstance(paintable,list) or len(paintable)>len(world["biomes"]) or any(not isinstance(biome,str) or biome not in world["biomes"] for biome in paintable) or len(set(paintable))!=len(paintable):
        raise ContentError("building.paintable_biomes: choose unique configured terrain ids")
    positive(building.get("paint_cost",0),"building.paint_cost",True)
    paint_materials=building.get("paint_materials",[])
    if not isinstance(paint_materials,list) or len(paint_materials)>32: raise ContentError("building.paint_materials: expected at most 32 materials")
    for part in paint_materials:
        if not isinstance(part,dict) or not isinstance(part.get("item"),str) or not part["item"].strip(): raise ContentError("building.paint_materials: each entry needs an item name")
        positive(part.get("count"),"building.paint_materials.count")
    overrides=building.get("tile_cost_overrides",[])
    if not isinstance(overrides,list) or len(overrides)>len(world["biomes"]): raise ContentError("building.tile_cost_overrides: expected at most one entry per biome")
    seen_override=set()
    for i,override in enumerate(overrides):
        path=f"building.tile_cost_overrides[{i}]"
        if not isinstance(override,dict) or override.get("biome") not in world["biomes"]: raise ContentError(f"{path}.biome: choose a configured terrain type")
        if override["biome"] in seen_override: raise ContentError(f"{path}.biome: terrain cost override is duplicated")
        seen_override.add(override["biome"])
        positive(override.get("currency",building.get("tile_cost")),f"{path}.currency",True)
        override_materials=override.get("materials",[])
        if not isinstance(override_materials,list) or len(override_materials)>32: raise ContentError(f"{path}.materials: expected at most 32 materials")
        for j,part in enumerate(override_materials):
            if not isinstance(part,dict) or not isinstance(part.get("item"),str) or not part["item"].strip(): raise ContentError(f"{path}.materials[{j}]: item is required")
            positive(part.get("count"),f"{path}.materials[{j}].count")
    structures=building.get("structures",[])
    positive(building.get("max_structures_per_area",128),"building.max_structures_per_area")
    refund=building.get("structure_refund_percent",0)
    if type(refund) is not int or not 0<=refund<=100: raise ContentError("building.structure_refund_percent: must be an integer from 0 to 100")
    tile_refund=building.get("tile_refund_percent",0)
    if type(tile_refund) is not int or not 0<=tile_refund<=100: raise ContentError("building.tile_refund_percent: must be an integer from 0 to 100")
    if not isinstance(structures,list) or len(structures)>128: raise ContentError("building.structures: expected a list of at most 128 structures")
    structure_ids=set()
    for i,structure in enumerate(structures):
        path=f"building.structures[{i}]"
        if not isinstance(structure,dict) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}",structure.get("id","")) or structure["id"] in structure_ids: raise ContentError(f"{path}.id: expected a unique lowercase structure id")
        structure_ids.add(structure["id"])
        if not isinstance(structure.get("name"),str) or not structure["name"].strip(): raise ContentError(f"{path}.name: required")
        if not isinstance(structure.get("icon","🪚"),str) or len(structure.get("icon","🪚"))>12: raise ContentError(f"{path}.icon: use a short text or emoji icon")
        positive(structure.get("cost",0),f"{path}.cost",True)
        if structure.get("max_per_area") is not None: positive(structure["max_per_area"],f"{path}.max_per_area")
        footprint=structure.get("footprint",{"width":1,"height":1})
        if not isinstance(footprint,dict): raise ContentError(f"{path}.footprint: expected width and height")
        fw,fh=footprint.get("width",1),footprint.get("height",1)
        if type(fw) is not int or type(fh) is not int or not (1<=fw<=8 and 1<=fh<=8 and fw*fh<=16): raise ContentError(f"{path}.footprint: width and height must be 1–8 tiles, with at most 16 tiles total")
        parts=structure.get("materials",[])
        if not isinstance(parts,list) or len(parts)>32: raise ContentError(f"{path}.materials: expected at most 32 materials")
        for j,part in enumerate(parts):
            if not isinstance(part,dict) or not isinstance(part.get("item"),str) or not part["item"].strip(): raise ContentError(f"{path}.materials[{j}].item: required")
            positive(part.get("count"),f"{path}.materials[{j}].count")
        service=structure.get("service",{"type":"decoration"})
        if not isinstance(service,dict) or service.get("type","decoration") not in ("decoration","rest","station"): raise ContentError(f"{path}.service.type: choose decoration, rest, or station")
        if service.get("type")=="station" and (not isinstance(service.get("station"),str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}",service["station"])): raise ContentError(f"{path}.service.station: expected a lowercase station id")
    player_trade=world["economy"].get("player_trade",{})
    if not isinstance(player_trade,dict): raise ContentError("economy.player_trade: expected an object")
    for key in ("enabled","allow_in_homes"):
        if type(player_trade.get(key, key=="allow_in_homes")) is not bool: raise ContentError(f"economy.player_trade.{key}: must be true or false")
    positive(player_trade.get("offer_timeout_seconds",600),"economy.player_trade.offer_timeout_seconds")
    if player_trade.get("offer_timeout_seconds",600)>86400: raise ContentError("economy.player_trade.offer_timeout_seconds: maximum is 86400")
    market_simulation=world["economy"].get("market_simulation",{})
    if not isinstance(market_simulation,dict): raise ContentError("economy.market_simulation: expected an object")
    if type(market_simulation.get("offline_simulation",True)) is not bool: raise ContentError("economy.market_simulation.offline_simulation: must be true or false")
    cycles=market_simulation.get("max_catchup_cycles",24)
    if type(cycles) is not int or not 1<=cycles<=10000: raise ContentError("economy.market_simulation.max_catchup_cycles: must be an integer from 1 to 10000")
    paired_exits = {}
    for map_data in maps:
        ident, width = map_data["id"], map_data["width"]
        for i, tile in enumerate(map_data["tiles"]):
            if not isinstance(tile, dict) or tile.get("biome") not in world["biomes"]:
                raise ContentError(f"map {ident}.tiles[{i}].biome: unknown biome")
            if "rest" in tile and type(tile["rest"]) is not bool:
                raise ContentError(f"map {ident}.tiles[{i}].rest: must be true or false")
            if "buildable" in tile and type(tile["buildable"]) is not bool:
                raise ContentError(f"map {ident}.tiles[{i}].buildable: must be true or false")
            for resource in tile.get("resources", []):
                if not isinstance(resource, dict) or not resource.get("item"):
                    raise ContentError(f"map {ident}.tiles[{i}].resources: item required")
                positive(resource.get("count", 1), f"map {ident}.tiles[{i}].resources.count")
                positive(resource.get("quantity", resource.get("count", 1)), f"map {ident}.tiles[{i}].resources.quantity")
            for direction, location in tile.get("exits", {}).items():
                target_map = location.get("map_id", ident) if isinstance(location, dict) else ident
                target_area = map_by_id(world, target_map)
                resolved = location
                if isinstance(location, dict) and "entry_point" in location:
                    entry_id = location["entry_point"]
                    if not isinstance(entry_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", entry_id):
                        raise ContentError(f"map {ident}.tiles[{i}].exits.{direction}.entry_point: expected a lowercase entry id")
                    resolved = target_area.get("entry_points", {}).get(entry_id)
                    if not isinstance(resolved, dict):
                        raise ContentError(f"map {ident}.tiles[{i}].exits.{direction}.entry_point: unknown entry point '{entry_id}'")
                check_location(resolved, target_area, f"map {ident}.tiles[{i}].exits.{direction}")
                if isinstance(location, dict):
                    pair_id = location.get("pair_id")
                    return_exit = location.get("return_exit")
                    if pair_id is not None:
                        if not isinstance(pair_id, str) or not re.fullmatch(r"pair-[a-z0-9-]{1,64}", pair_id):
                            raise ContentError(f"map {ident}.tiles[{i}].exits.{direction}.pair_id: expected a paired-exit id")
                        if not isinstance(return_exit, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", return_exit):
                            raise ContentError(f"map {ident}.tiles[{i}].exits.{direction}.return_exit: expected a lowercase exit label")
                        paired_exits.setdefault(pair_id, []).append((ident, i, direction, location))
                    elif return_exit is not None:
                        raise ContentError(f"map {ident}.tiles[{i}].exits.{direction}.return_exit: requires pair_id")
        spawn = map_data["spawn"]
        spawn_tile = map_data["tiles"][spawn["y"] * width + spawn["x"]]
        if not world["biomes"][spawn_tile["biome"]].get("passable", True):
            raise ContentError(f"map {ident}.spawn: starting terrain must be passable")
    for pair_id, members in paired_exits.items():
        if len(members) != 2:
            raise ContentError(f"paired exit {pair_id}: expected exactly two matching exits")
        for source_id, source_index, label, exit_data in members:
            target_id = exit_data.get("map_id", source_id)
            target_map = map_by_id(world, target_id)
            arrival = target_map.get("entry_points", {}).get(exit_data.get("entry_point"), exit_data)
            target_index = arrival["y"] * target_map["width"] + arrival["x"]
            reverse = target_map["tiles"][target_index].get("exits", {}).get(exit_data["return_exit"])
            source_map = map_by_id(world, source_id)
            source_location = {"map_id": source_id, "x": source_index % source_map["width"], "y": source_index // source_map["width"]}
            if not isinstance(reverse, dict) or reverse.get("pair_id") != pair_id:
                raise ContentError(f"paired exit {pair_id}: {source_id}.{label} has no matching return exit")
            if reverse.get("map_id", target_id) != source_id or (reverse.get("x"), reverse.get("y")) != (source_location["x"], source_location["y"]):
                raise ContentError(f"paired exit {pair_id}: {source_id}.{label} does not point back to its source")
    npc_ids = {n["id"] for n in world["npcs"]}
    quest_ids = {q["id"] for q in world["quests"]}
    entity_ids = {e["id"] for e in world["interactables"]}
    creature_ids = {e["id"] for e in world["creatures"]}
    recipe_ids = {r["id"] for r in world["crafting"]}
    item_names = {item["name"].casefold() for item in world["items"]}
    item_names.update(
        stock["item"].casefold()
        for vendor in world["economy"].get("vendors", [])
        for stock in vendor.get("stock", [])
        if isinstance(stock, dict) and isinstance(stock.get("item"), str)
    )
    item_names.update(
        resource["item"].casefold()
        for map_data in maps
        for tile in map_data["tiles"]
        for resource in tile.get("resources", [])
        if isinstance(resource, dict) and isinstance(resource.get("item"), str)
    )
    item_names.update(
        resource["item"].casefold()
        for biome in world.get("biomes", {}).values()
        for resource in biome.get("resources", [])
        if isinstance(resource, dict) and isinstance(resource.get("item"), str)
    )
    item_names.update(
        drop["item"].casefold()
        for creature in world["creatures"]
        for drop in creature.get("loot", [])
        if isinstance(drop, dict) and isinstance(drop.get("item"), str)
    )
    item_names.update(name.casefold() for name in world.get("starting_inventory", {}) if isinstance(name, str))
    for part in building.get("tile_materials", []):
        if part["item"].casefold() not in item_names:
            raise ContentError(f"building.tile_materials: unknown item '{part['item']}'")
    for i,override in enumerate(building.get("tile_cost_overrides",[])):
        for part in override.get("materials",[]):
            if part["item"].casefold() not in item_names: raise ContentError(f"building.tile_cost_overrides[{i}].materials: unknown item '{part['item']}'")
    dialog_ids = {d["id"] for d in world["dialogs"]}
    if len(dialog_ids) != len(world["dialogs"]):
        raise ContentError("Duplicate dialogue ids")
    for entries in (world["npcs"], world["economy"]["vendors"], world["interactables"]):
        for entity in entries:
            map_id = entity.setdefault("map_id", world["default_map_id"])
            map_data = map_by_id(world, map_id)
            entity.setdefault("location", copy.deepcopy(map_data["spawn"]))
            check_location(entity["location"], map_data, f"{entity['id']}.location")
    for creature in world["creatures"]:
        map_id = creature.setdefault("map_id", world["default_map_id"])
        area = map_by_id(world, map_id)
        check_location(creature.get("location"), area, f"creature {creature['id']}.location")
        spawn_area = creature.get("spawn_area")
        if spawn_area is not None:
            if not isinstance(spawn_area, dict):
                raise ContentError(f"creature {creature['id']}.spawn_area: expected x, y, width, and height")
            x, y, width, height = (spawn_area.get(k) for k in ("x", "y", "width", "height"))
            if any(type(v) is not int for v in (x, y, width, height)) or width < 1 or height < 1 or x < 0 or y < 0 or x + width > area["width"] or y + height > area["height"]:
                raise ContentError(f"creature {creature['id']}.spawn_area: rectangle must fit inside map {map_id}")
            loc = creature["location"]
            if not (x <= loc["x"] < x + width and y <= loc["y"] < y + height):
                raise ContentError(f"creature {creature['id']}.location: initial spawn must be inside spawn_area")
            passable = [area["tiles"][py * area["width"] + px] for py in range(y, y + height) for px in range(x, x + width) if world["biomes"].get(area["tiles"][py * area["width"] + px]["biome"], {}).get("passable", True)]
            if not passable:
                raise ContentError(f"creature {creature['id']}.spawn_area: rectangle needs at least one passable tile")
        positive(creature.get("max_hp", 20), f"creature {creature['id']}.max_hp")
        positive(creature.get("damage", 0), f"creature {creature['id']}.damage", True)
        positive(creature.get("experience", 0), f"creature {creature['id']}.experience", True)
        positive(creature.get("respawn_seconds", 0), f"creature {creature['id']}.respawn_seconds", True)
        for drop in creature.get("loot", []):
            if not isinstance(drop, dict) or not drop.get("item"):
                raise ContentError(f"creature {creature['id']}.loot: each drop needs an item and count")
            positive(drop.get("count", 1), f"creature {creature['id']}.loot.count")
    for npc in world["npcs"]:
        if npc.get("dialog_id") and npc["dialog_id"] not in dialog_ids:
            raise ContentError(f"npc {npc['id']}.dialog_id: unknown dialogue")
        validate_decision_policy(npc.get("decision_policy"), f"npc {npc['id']}.decision_policy", world)
    for quest in world["quests"]:
        if quest.get("giver") and quest["giver"] not in npc_ids:
            raise ContentError(f"quest {quest['id']}.giver: unknown NPC")
        if any(q not in quest_ids or q == quest["id"] for q in quest.get("prerequisites", [])):
            raise ContentError(f"quest {quest['id']}.prerequisites: unknown or self-referencing quest")
        positive(quest.get("reward", {}).get("currency", 0), f"quest {quest['id']}.reward.currency", True)
        for part in quest.get("reward", {}).get("items", []):
            if isinstance(part, str):
                item_name = part
            elif isinstance(part, dict):
                item_name = part.get("item")
                if not isinstance(item_name, str) or not item_name.strip():
                    raise ContentError(f"quest {quest['id']}.reward.items: item name required")
                positive(part.get("count", 1), f"quest {quest['id']}.reward.items.count")
            else:
                raise ContentError(f"quest {quest['id']}.reward.items: expected item name or item/count object")
            if item_name.casefold() not in item_names:
                raise ContentError(f"quest {quest['id']}.reward.items: unknown item '{item_name}'")
        for objective_index, obj in enumerate(quest["objectives"]):
            kind, target = obj["type"], obj.get("target")
            if kind in ("collect", "deliver") and (not isinstance(target, str) or target.casefold() not in item_names):
                raise ContentError(f"quest {quest['id']}.objectives[{objective_index}].target: unknown item '{target}'")
            if kind in ("talk", "deliver") and (target if kind == "talk" else obj.get("npc")) not in npc_ids:
                field = "target" if kind == "talk" else "npc"
                raise ContentError(f"quest {quest['id']}.objectives[{objective_index}].{field}: objective references an unknown NPC")
            if kind == "craft" and target not in recipe_ids:
                raise ContentError(f"quest {quest['id']}.objectives[{objective_index}].target: objective references an unknown recipe")
            if kind == "defeat" and target not in creature_ids:
                raise ContentError(f"quest {quest['id']}.objectives[{objective_index}].target: objective references an unknown creature")
            if kind == "interact" and target not in entity_ids:
                raise ContentError(f"quest {quest['id']}.objectives[{objective_index}].target: objective references an unknown interaction")
            if kind == "visit":
                if obj["count"] != 1:
                    raise ContentError(f"quest {quest['id']}.objectives[{objective_index}].count: visit count must be 1")
                if isinstance(target, dict):
                    target_map = target.get("map_id", world["default_map_id"])
                    check_location(target, map_by_id(world, target_map), f"quest {quest['id']}.objectives[{objective_index}].target")
                elif target not in {t.get('id') for area in maps for t in area["tiles"]}:
                    raise ContentError(f"quest {quest['id']}.objectives[{objective_index}].target: visit target must be a room id or coordinates")
    for recipe in world["crafting"]:
        if not recipe.get("inputs") or not recipe.get("output", {}).get("item"):
            raise ContentError(f"recipe {recipe['id']}: inputs and output item are required")
        for part in [*recipe["inputs"], recipe["output"]]:
            if not part.get("item"):
                raise ContentError(f"recipe {recipe['id']}: ingredient item is required")
            if not isinstance(part["item"], str) or part["item"].casefold() not in item_names:
                raise ContentError(f"recipe {recipe['id']}: unknown item '{part['item']}'")
            positive(part.get("count", 1), f"recipe {recipe['id']}.count")
    vendors_by_id={vendor["id"]:vendor for vendor in world["economy"]["vendors"]}
    delivery_contracts={}
    for vendor in world["economy"]["vendors"]:
        validate_decision_policy(vendor.get("decision_policy"), f"vendor {vendor['id']}.decision_policy", world)
        for stock in vendor.get("stock", []):
            if not stock.get("item"):
                raise ContentError(f"vendor {vendor['id']}: stock item required")
            positive(stock.get("price", 0), f"vendor {vendor['id']}.price", True)
            positive(stock.get("sell_price", stock.get("price", 0) // 2), f"vendor {vendor['id']}.sell_price", True)
            if stock.get("sell_price", stock.get("price", 0) // 2) > stock.get("price", 0):
                raise ContentError(f"vendor {vendor['id']}: sell_price must not exceed price")
            if "quantity" in stock:
                positive(stock["quantity"], f"vendor {vendor['id']}.quantity", True)
            restock=stock.get("restock")
            if restock is not None:
                path=f"vendor {vendor['id']}.{stock['item']}.restock"
                if not isinstance(restock,dict): raise ContentError(f"{path}: expected an object")
                if "quantity" not in stock: raise ContentError(f"{path}: restocking requires a finite starting quantity")
                interval=restock.get("interval_seconds"); amount=restock.get("amount"); maximum=restock.get("max_quantity",stock["quantity"])
                if type(interval) is not int or not 1<=interval<=31536000: raise ContentError(f"{path}.interval_seconds: must be an integer from 1 to 31536000")
                if type(amount) is not int or amount<1: raise ContentError(f"{path}.amount: must be a positive integer")
                if type(maximum) is not int or maximum<stock["quantity"]: raise ContentError(f"{path}.max_quantity: must be an integer at least as large as starting quantity")
            production=stock.get("production")
            if production is not None:
                path=f"vendor {vendor['id']}.{stock['item']}.production"
                if not isinstance(production,dict): raise ContentError(f"{path}: expected an object")
                if "quantity" not in stock: raise ContentError(f"{path}: production requires finite output stock")
                interval=production.get("interval_seconds"); amount=production.get("amount"); maximum=production.get("max_quantity")
                if type(interval) is not int or not 1<=interval<=31536000: raise ContentError(f"{path}.interval_seconds: must be an integer from 1 to 31536000")
                if type(amount) is not int or amount<1: raise ContentError(f"{path}.amount: must be a positive integer")
                if type(maximum) is not int or maximum<stock["quantity"]: raise ContentError(f"{path}.max_quantity: must be an integer at least as large as starting quantity")
                inputs=production.get("inputs")
                if not isinstance(inputs,list) or not inputs: raise ContentError(f"{path}.inputs: expected a non-empty list")
                market_offers={entry.get("item"):entry for entry in vendor.get("stock",[])}
                seen=set()
                for j,part in enumerate(inputs):
                    if not isinstance(part,dict) or part.get("item") not in market_offers: raise ContentError(f"{path}.inputs[{j}]: item must be offered by this market")
                    if "quantity" not in market_offers[part["item"]]: raise ContentError(f"{path}.inputs[{j}]: input items require finite stock")
                    if part["item"]==stock["item"] or part["item"] in seen: raise ContentError(f"{path}.inputs[{j}]: duplicate inputs and self-consumption are not allowed")
                    seen.add(part["item"]); positive(part.get("count"),f"{path}.inputs[{j}].count")
            consumption=stock.get("consumption")
            if consumption is not None:
                path=f"vendor {vendor['id']}.{stock['item']}.consumption"
                if not isinstance(consumption,dict): raise ContentError(f"{path}: expected an object")
                if "quantity" not in stock: raise ContentError(f"{path}: consumption requires finite stock")
                interval=consumption.get("interval_seconds"); amount=consumption.get("amount")
                if type(interval) is not int or not 1<=interval<=31536000: raise ContentError(f"{path}.interval_seconds: must be an integer from 1 to 31536000")
                if type(amount) is not int or amount<1: raise ContentError(f"{path}.amount: must be a positive integer")
            pricing=stock.get("pricing", {"mode":"fixed"})
            if not isinstance(pricing,dict) or pricing.get("mode","fixed") not in ("fixed","stock_based"):
                raise ContentError(f"vendor {vendor['id']}: pricing.mode must be fixed or stock_based")
            if pricing.get("mode")=="stock_based":
                for field in ("base_price","target_stock","min_price","max_price","spread"):
                    positive(pricing.get(field),f"vendor {vendor['id']}.{stock['item']}.pricing.{field}",field in ("min_price","spread"))
                if type(pricing.get("sensitivity")) not in (int,float) or not 0<=pricing["sensitivity"]<=4:
                    raise ContentError(f"vendor {vendor['id']}.{stock['item']}.pricing.sensitivity: must be between 0 and 4")
                if "quantity" not in stock:
                    raise ContentError(f"vendor {vendor['id']}.{stock['item']}: stock-based pricing requires finite quantity")
                if pricing["min_price"]>pricing["max_price"] or pricing["spread"]>pricing["max_price"]:
                    raise ContentError(f"vendor {vendor['id']}.{stock['item']}.pricing: min/max/spread are inconsistent")
                if "adjustment_step" in pricing:
                    positive(pricing["adjustment_step"],f"vendor {vendor['id']}.{stock['item']}.pricing.adjustment_step")
                    if pricing["adjustment_step"]>pricing["max_price"]: raise ContentError(f"vendor {vendor['id']}.{stock['item']}.pricing.adjustment_step: cannot exceed max_price")
        contracts=vendor.get("contracts",[])
        if not isinstance(contracts,list) or len(contracts)>64: raise ContentError(f"vendor {vendor['id']}.contracts: expected a list of at most 64 contracts")
        offers={offer.get("item"):offer for offer in vendor.get("stock",[]) if isinstance(offer,dict)}
        for index,contract in enumerate(contracts):
            path=f"vendor {vendor['id']}.contracts[{index}]"
            if not isinstance(contract,dict) or not isinstance(contract.get("id"),str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}",contract["id"]): raise ContentError(f"{path}.id: expected a lowercase stable id")
            if contract["id"] in delivery_contracts: raise ContentError(f"{path}.id: contract ids must be unique across markets")
            if len(delivery_contracts)>=512: raise ContentError("Economy supports at most 512 delivery contracts")
            delivery_contracts[contract["id"]]=(vendor,contract)
            if not isinstance(contract.get("name"),str) or not contract["name"].strip(): raise ContentError(f"{path}.name: required")
            item=contract.get("item")
            if item not in offers or "quantity" not in offers[item]: raise ContentError(f"{path}.item: cargo must be a finite-stock offer at its origin market")
            positive(contract.get("count"),f"{path}.count")
            if contract["count"]>9999: raise ContentError(f"{path}.count: maximum is 9999")
            destination=contract.get("destination_market_id")
            if destination not in vendors_by_id or destination==vendor["id"]: raise ContentError(f"{path}.destination_market_id: choose a different authored market")
            positive(contract.get("reward_currency",0),f"{path}.reward_currency",True)
            if "deadline_seconds" in contract and contract["deadline_seconds"] not in (None,0):
                positive(contract["deadline_seconds"],f"{path}.deadline_seconds")
                if contract["deadline_seconds"]>2592000: raise ContentError(f"{path}.deadline_seconds: maximum is 2592000")
            prerequisites=contract.get("prerequisites",[])
            if not isinstance(prerequisites,list) or len(prerequisites)>64 or any(not isinstance(value,str) for value in prerequisites) or len(set(prerequisites))!=len(prerequisites) or contract["id"] in prerequisites:
                raise ContentError(f"{path}.prerequisites: expected unique contract ids without self-reference")
    for ident,(vendor,contract) in delivery_contracts.items():
        for prerequisite in contract.get("prerequisites",[]):
            if prerequisite not in delivery_contracts: raise ContentError(f"market contract {ident}.prerequisites: unknown contract '{prerequisite}'")
    contract_marks={ident:0 for ident in delivery_contracts}
    def visit_contract(ident):
        if contract_marks[ident]==1: raise ContentError("Market delivery contract prerequisites contain a cycle")
        if contract_marks[ident]==2: return
        contract_marks[ident]=1
        for prerequisite in delivery_contracts[ident][1].get("prerequisites",[]): visit_contract(prerequisite)
        contract_marks[ident]=2
    for ident in delivery_contracts: visit_contract(ident)
    for dialog in world["dialogs"]:
        nodes = dialog.get("nodes", {})
        if dialog.get("start") not in nodes:
            raise ContentError(f"dialog {dialog['id']}: start node must exist")
        for node in nodes.values():
            for choice in node.get("choices", []):
                if choice.get("next") and choice["next"] not in nodes:
                    raise ContentError(f"dialog {dialog['id']}: unknown next node")
                check_effects(choice.get("effects", []), f"dialog {dialog['id']}", quest_ids, plugin_effects, item_names)
    for entity in world["interactables"]:
        for action in entity.get("actions", []):
            if not action.get("id"):
                raise ContentError(f"entity {entity['id']}: action id required")
            check_effects(action.get("effects", []), f"entity {entity['id']}", quest_ids, plugin_effects, item_names)
    for item in world["items"]:
        check_effects(item.get("use_effects", []), f"item {item['id']}", quest_ids, plugin_effects, item_names)
        if not isinstance(item.get("icon", "🎒"), str) or len(item.get("icon", "🎒")) > 12 or any(ord(char) < 32 for char in item.get("icon", "🎒")):
            raise ContentError(f"item {item['id']}.icon: must be a short text or emoji string")
        if item.get("equip_slot", "") not in ("", "head", "body", "hand", "offhand", "feet", "neck", "ring"):
            raise ContentError(f"item {item['id']}.equip_slot: unsupported equipment slot")
    for creature in world["creatures"]:
        if not isinstance(creature.get("icon", "🐾"), str) or len(creature.get("icon", "🐾")) > 12 or any(ord(char) < 32 for char in creature.get("icon", "🐾")):
            raise ContentError(f"creature {creature['id']}.icon: must be a short text or emoji string")
    positive(world["starting_currency"], "starting_currency", True)
    for name, count in world.get("starting_inventory", {}).items():
        positive(count, f"starting_inventory.{name}", True)
    def walk(ident, path):
        if ident in path:
            raise ContentError("Quest prerequisites contain a cycle")
        quest = next(q for q in world["quests"] if q["id"] == ident)
        for prerequisite in quest.get("prerequisites", []):
            walk(prerequisite, path | {ident})
    for ident in quest_ids:
        walk(ident, set())
