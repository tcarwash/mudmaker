"""Portable content contract for the authoring app and exported games."""
from __future__ import annotations

import copy
import ipaddress
import re
from typing import Any

BIOMES = {
    "grassland": {"name": "Grassland", "symbol": ".", "icon": "🌿", "color": "#b3cb81", "passable": True},
    "forest": {"name": "Forest", "symbol": "T", "icon": "🌲", "color": "#537e5d", "passable": True},
    "desert": {"name": "Desert", "symbol": "~", "icon": "🏜️", "color": "#dfc478", "passable": True},
    "tundra": {"name": "Tundra", "symbol": "*", "icon": "❄️", "color": "#d1dfe0", "passable": True},
    "swamp": {"name": "Swamp", "symbol": "%", "icon": "🐸", "color": "#859974", "passable": True},
    "mountain": {"name": "Mountain", "symbol": "^", "icon": "⛰️", "color": "#92958a", "passable": True},
    "water": {"name": "Water", "symbol": "=", "icon": "🌊", "color": "#76b5c6", "passable": False},
    "road": {"name": "Road", "symbol": "#", "icon": "🛤️", "color": "#c4a37c", "passable": True},
}
OBJECTIVE_TYPES = {"discover", "visit", "talk", "collect", "craft", "deliver", "interact", "defeat"}
EFFECT_TYPES = {"item", "currency", "heal", "flag", "quest"}
ENTITY_TEMPLATES = {
    "npc": {"id": "", "name": "", "role": "", "location": {"x": 0, "y": 0}, "dialog": "", "quest_hooks": []},
    "quest": {"id": "", "name": "", "description": "", "giver": "", "objectives": [{"type": "discover", "count": 3}], "prerequisites": [], "reward": {"currency": 25, "items": []}},
    "crafting_recipe": {"id": "", "name": "", "description": "", "inputs": [{"item": "", "count": 1}], "output": {"item": "", "count": 1}, "station": ""},
    "vendor": {"id": "", "name": "", "location": {"x": 0, "y": 0}, "stock": [{"item": "", "price": 1, "sell_price": 0, "quantity": 20}]},
    "item": {"id": "", "name": "", "description": "", "icon": "🎒", "equip_slot": "", "value": 1, "use_effects": []},
    "biome": {"id": "", "name": "", "description": "", "color": "#87a982", "symbol": "+", "icon": "🌿", "passable": True, "resources": []},
    "room": {"id": "", "name": "", "description": "", "location": {"x": 0, "y": 0}, "biome": "grassland", "resources": [], "station": "", "rest": False, "exits": {}},
    "dialog": {"id": "", "name": "", "start": "greeting", "nodes": {"greeting": {"text": "", "choices": [{"text": "", "next": "greeting", "effects": []}]}}},
    "description": {"id": "", "name": "", "description": "", "location": {"x": 0, "y": 0}},
    "world": {"name": "", "description": "", "lore": "", "emotes": [{"id": "wave", "verb": "wave", "broadcast": "waves", "icon": "👋"}], "runtime_config": {"ssh_port": 2222, "mcp_bind": "127.0.0.1", "mcp_port": 2223, "mcp_allowed_hosts": ""}},
    "creature": {"id": "", "name": "", "description": "", "icon": "🐾", "location": {"x": 0, "y": 0}, "max_hp": 20, "damage": 3, "experience": 5, "loot": [], "respawn_seconds": 0},
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
    world = copy.deepcopy(source)
    if type(world.get("width")) is not int or type(world.get("height")) is not int or not isinstance(world.get("tiles"), list) or len(world["tiles"]) != world["width"] * world["height"]:
        raise ContentError("Map dimensions and tiles are missing or inconsistent")
    world["schema_version"] = 1
    for key in ("npcs", "quests", "crafting", "items", "dialogs", "interactables", "creatures"):
        world.setdefault(key, [])
    world.setdefault("economy", {}).setdefault("vendors", [])
    world["economy"].setdefault("currency", "Coins")
    world.setdefault("starting_currency", 25)
    world.setdefault("presentation", {})
    world["presentation"].setdefault("currency_icon", "🪙")
    world["presentation"].setdefault("color", "auto")
    world["presentation"].setdefault("map_style", "ascii")
    world["presentation"].setdefault("palette", {"success": "green", "info": "cyan", "warning": "yellow", "error": "red"})
    world.setdefault("plugins", [])
    world.setdefault("emotes", [
        {"id": "wave", "verb": "wave", "broadcast": "waves", "icon": "👋"},
        {"id": "cheer", "verb": "cheer", "broadcast": "cheers", "icon": "🎉"},
        {"id": "laugh", "verb": "laugh", "broadcast": "laughs", "icon": "😂"},
        {"id": "shrug", "verb": "shrug", "broadcast": "shrugs", "icon": "🤷"},
        {"id": "dance", "verb": "dance", "broadcast": "dances", "icon": "💃"},
    ])
    runtime_defaults = {"ssh_port": 2222, "mcp_bind": "127.0.0.1", "mcp_port": 2223, "mcp_allowed_hosts": ""}
    runtime_config = world.setdefault("runtime_config", {})
    if isinstance(runtime_config, dict):
        for key, value in runtime_defaults.items():
            runtime_config.setdefault(key, value)
    world["biomes"] = {**copy.deepcopy(BIOMES), **world.get("biomes", {})}
    routes = {"npc": world["npcs"], "quest": world["quests"], "crafting_recipe": world["crafting"], "vendor": world["economy"]["vendors"], "item": world["items"], "dialog": world["dialogs"], "creature": world["creatures"]}
    for entity in world.get("entities", []):
        if not isinstance(entity, dict):
            raise ContentError("entities: every entry must be an object")
        kind = entity.get("entity_type", "custom_entity")
        if kind in routes:
            routes[kind].append(copy.deepcopy(entity))
        elif kind == "biome":
            ident = entity.get("id") or slug(entity.get("name", ""))
            world["biomes"][ident] = {"symbol": "+", "color": "#87a982", "passable": True, **entity, "id": ident}
        elif kind in ("room", "description"):
            location = entity.get("location")
            check_location(location, world, f"{kind} {entity.get('name', '')}.location")
            tile = world["tiles"][location["y"] * world["width"] + location["x"]]
            tile.update({k: v for k, v in entity.items() if k not in ("entity_type", "location")})
        elif kind == "world":
            for key in ("description", "lore", "starting_currency", "starting_inventory", "emotes", "runtime_config"):
                if key in entity:
                    world[key] = copy.deepcopy(entity[key])
        else:
            world["interactables"].append(copy.deepcopy(entity))
    world.pop("entities", None)
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


def check_location(location: Any, world: dict, path: str) -> None:
    if not isinstance(location, dict) or any(type(location.get(k)) is not int for k in ("x", "y")):
        raise ContentError(f"{path}: needs integer x and y")
    if not (0 <= location["x"] < world["width"] and 0 <= location["y"] < world["height"]):
        raise ContentError(f"{path}: outside the map")


def positive(value: Any, path: str, allow_zero: bool = False) -> None:
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ContentError(f"{path}: must be a {'nonnegative' if allow_zero else 'positive'} integer")


def check_effects(effects: list, path: str, quest_ids: set, plugin_effects: set | None = None) -> None:
    if not isinstance(effects, list):
        raise ContentError(f"{path}: effects must be a list")
    for effect in effects:
        if not isinstance(effect, dict) or effect.get("type") not in EFFECT_TYPES | (plugin_effects or set()):
            raise ContentError(f"{path}: supported effects are {', '.join(sorted(EFFECT_TYPES | (plugin_effects or set())))}")
        if effect["type"] == "item":
            if not effect.get("item"):
                raise ContentError(f"{path}: item effect needs an item")
            positive(effect.get("count", 1), path)
        elif effect["type"] in ("currency", "heal"):
            positive(effect.get("amount", 0), path, True)
        elif effect["type"] == "flag" and not effect.get("name"):
            raise ContentError(f"{path}: flag effect needs a name")
        elif effect["type"] == "quest" and effect.get("target") not in quest_ids:
            raise ContentError(f"{path}: quest effect references an unknown quest")


def validate_runtime_world(world: dict) -> None:
    width, height = world.get("width"), world.get("height")
    if type(width) is not int or type(height) is not int or not (4 <= width <= 64 and 4 <= height <= 64):
        raise ContentError("Map dimensions must be integers between 4 and 64")
    if not isinstance(world.get("tiles"), list) or len(world["tiles"]) != width * height:
        raise ContentError("Map must have exactly width × height tiles")
    check_location(world.get("spawn"), world, "spawn")
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
    for ident, biome in world["biomes"].items():
        if not isinstance(biome, dict) or not re.fullmatch(r"#[0-9a-fA-F]{6}", biome.get("color", "#87a982")) or not isinstance(biome.get("symbol", "+"), str) or len(biome.get("symbol", "+")) != 1:
            raise ContentError(f"biome {ident}: color needs #rrggbb and symbol needs one character")
        if not isinstance(biome.get("icon", ""), str) or len(biome.get("icon", "")) > 12:
            raise ContentError(f"biome {ident}.icon: use a short text or emoji icon")
        for resource in biome.get("resources", []):
            if not resource.get("item"):
                raise ContentError(f"biome {ident}.resources: item required")
            positive(resource.get("quantity", resource.get("count", 1)), f"biome {ident}.resources.quantity")
    for i, tile in enumerate(world["tiles"]):
        if not isinstance(tile, dict) or tile.get("biome") not in world["biomes"]:
            raise ContentError(f"tiles[{i}].biome: unknown biome")
        if "rest" in tile and type(tile["rest"]) is not bool:
            raise ContentError(f"tiles[{i}].rest: must be true or false")
        for resource in tile.get("resources", []):
            if not resource.get("item"):
                raise ContentError(f"tiles[{i}].resources: item required")
            positive(resource.get("count", 1), f"tiles[{i}].resources.count")
            positive(resource.get("quantity", resource.get("count", 1)), f"tiles[{i}].resources.quantity")
        for direction, location in tile.get("exits", {}).items():
            check_location(location, world, f"tiles[{i}].exits.{direction}")
    spawn_tile = world["tiles"][world["spawn"]["y"] * width + world["spawn"]["x"]]
    if not world["biomes"][spawn_tile["biome"]].get("passable", True):
        raise ContentError("spawn: the starting biome must be passable")
    npc_ids = {n["id"] for n in world["npcs"]}
    quest_ids = {q["id"] for q in world["quests"]}
    entity_ids = {e["id"] for e in world["interactables"]}
    creature_ids = {e["id"] for e in world["creatures"]}
    recipe_ids = {r["id"] for r in world["crafting"]}
    dialog_ids = {d["id"] for d in world["dialogs"]}
    if len(dialog_ids) != len(world["dialogs"]):
        raise ContentError("Duplicate dialogue ids")
    for entries in (world["npcs"], world["economy"]["vendors"], world["interactables"]):
        for entity in entries:
            check_location(entity.get("location", world["spawn"]), world, f"{entity['id']}.location")
    for creature in world["creatures"]:
        check_location(creature.get("location"), world, f"creature {creature['id']}.location")
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
    for quest in world["quests"]:
        if quest.get("giver") and quest["giver"] not in npc_ids:
            raise ContentError(f"quest {quest['id']}.giver: unknown NPC")
        if any(q not in quest_ids or q == quest["id"] for q in quest.get("prerequisites", [])):
            raise ContentError(f"quest {quest['id']}.prerequisites: unknown or self-referencing quest")
        positive(quest.get("reward", {}).get("currency", 0), f"quest {quest['id']}.reward.currency", True)
        for part in quest.get("reward", {}).get("items", []):
            if not isinstance(part, str):
                if not part.get("item"):
                    raise ContentError(f"quest {quest['id']}.reward.items: item name required")
                positive(part.get("count", 1), f"quest {quest['id']}.reward.items.count")
        for obj in quest["objectives"]:
            kind, target = obj["type"], obj.get("target")
            if kind in ("talk", "deliver") and (target if kind == "talk" else obj.get("npc")) not in npc_ids:
                raise ContentError(f"quest {quest['id']}: objective references an unknown NPC")
            if kind == "craft" and target not in recipe_ids:
                raise ContentError(f"quest {quest['id']}: objective references an unknown recipe")
            if kind == "defeat" and target not in creature_ids:
                raise ContentError(f"quest {quest['id']}: objective references an unknown creature")
            if kind == "interact" and target not in entity_ids:
                raise ContentError(f"quest {quest['id']}: objective references an unknown interaction")
            if kind == "visit":
                if obj["count"] != 1:
                    raise ContentError(f"quest {quest['id']}: visit count must be 1")
                if isinstance(target, dict):
                    check_location(target, world, f"quest {quest['id']}.visit")
                elif target not in {t.get('id') for t in world['tiles']}:
                    raise ContentError(f"quest {quest['id']}: visit target must be a room id or coordinates")
    for recipe in world["crafting"]:
        if not recipe.get("inputs") or not recipe.get("output", {}).get("item"):
            raise ContentError(f"recipe {recipe['id']}: inputs and output item are required")
        for part in [*recipe["inputs"], recipe["output"]]:
            if not part.get("item"):
                raise ContentError(f"recipe {recipe['id']}: ingredient item is required")
            positive(part.get("count", 1), f"recipe {recipe['id']}.count")
    for vendor in world["economy"]["vendors"]:
        for stock in vendor.get("stock", []):
            if not stock.get("item"):
                raise ContentError(f"vendor {vendor['id']}: stock item required")
            positive(stock.get("price", 0), f"vendor {vendor['id']}.price", True)
            positive(stock.get("sell_price", stock.get("price", 0) // 2), f"vendor {vendor['id']}.sell_price", True)
            if stock.get("sell_price", stock.get("price", 0) // 2) > stock.get("price", 0):
                raise ContentError(f"vendor {vendor['id']}: sell_price must not exceed price")
            if "quantity" in stock:
                positive(stock["quantity"], f"vendor {vendor['id']}.quantity", True)
    for dialog in world["dialogs"]:
        nodes = dialog.get("nodes", {})
        if dialog.get("start") not in nodes:
            raise ContentError(f"dialog {dialog['id']}: start node must exist")
        for node in nodes.values():
            for choice in node.get("choices", []):
                if choice.get("next") and choice["next"] not in nodes:
                    raise ContentError(f"dialog {dialog['id']}: unknown next node")
                check_effects(choice.get("effects", []), f"dialog {dialog['id']}", quest_ids, plugin_effects)
    for entity in world["interactables"]:
        for action in entity.get("actions", []):
            if not action.get("id"):
                raise ContentError(f"entity {entity['id']}: action id required")
            check_effects(action.get("effects", []), f"entity {entity['id']}", quest_ids, plugin_effects)
    for item in world["items"]:
        check_effects(item.get("use_effects", []), f"item {item['id']}", quest_ids, plugin_effects)
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
