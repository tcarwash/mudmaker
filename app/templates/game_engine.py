"""Deterministic MUD rules shared by browser playtesting and SSH games."""
from __future__ import annotations

import copy
import asyncio
from collections import deque
import hashlib
import importlib.util
import json
import os
import random
import re
import sqlite3
import threading
import time
import types
import uuid
from pathlib import Path

try:
    from .plugin_api import PluginAPI
    from .decision_runtime import DecisionProviderError, choose as choose_decision
except ImportError:
    from plugin_api import PluginAPI
    from decision_runtime import DecisionProviderError, choose as choose_decision


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


def expand_command_chain(line):
    """Expand safe command separators and trailing repeat counts into a bounded batch."""
    commands = re.split(r";|,(?=\s*(?:go|north|south|east|west|n|s|e|w|look|map|exits|talk|choose|quests|gather|inventory|equip|unequip|inspect|use|shop|buy|sell|contracts|contract|recipes|craft|drop|take|give|interact|emote|rest|note|track|stats|build|home|trade|storage|store|retrieve)\b)", line, flags=re.I)
    expanded=[]
    repeat_total=0
    for raw in commands:
        command=raw.strip()
        if not command: raise GameError("Command chains cannot contain an empty step.")
        match=re.fullmatch(r"(.+?)\((\d+)\)",command)
        count=int(match.group(2)) if match else 1
        if count<1 or count>20: raise GameError("Repeat counts must be between 1 and 20.")
        command=match.group(1).strip() if match else command
        expanded.extend([command]*count)
        repeat_total+=count
        if len(expanded)>50 or repeat_total>50: raise GameError("A command chain can contain at most 50 steps.")
    return expanded


class GameEngine:
    def __init__(self, world, database):
        self.lock = threading.RLock()
        self.clock = time.time
        self._economy_started_at = self.clock()
        self.before_command = None
        self.online_players = lambda: ()
        self.world = world
        self.creature_locations = {}
        self.default_map_id = world.get("default_map_id", "overworld")
        self.presentation = world.get("presentation", {})
        self.plugin_api = PluginAPI()
        self.load_plugins()
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript("CREATE TABLE IF NOT EXISTS players (username TEXT PRIMARY KEY, state TEXT NOT NULL); CREATE TABLE IF NOT EXISTS world_state (key TEXT PRIMARY KEY, value INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS creature_contributions (creature TEXT NOT NULL, username TEXT NOT NULL, damage INTEGER NOT NULL, PRIMARY KEY(creature, username)); CREATE TABLE IF NOT EXISTS decision_owner_state(owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(owner_type,owner_id)); CREATE TABLE IF NOT EXISTS decision_traces(trace_id TEXT PRIMARY KEY, created_at REAL NOT NULL, owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, trigger TEXT NOT NULL, observation TEXT NOT NULL, candidates TEXT NOT NULL, selected TEXT NOT NULL, outcome TEXT NOT NULL, status TEXT NOT NULL, latency_ms INTEGER NOT NULL DEFAULT 0); CREATE TABLE IF NOT EXISTS player_trades(trade_id TEXT PRIMARY KEY, created_at REAL NOT NULL, expires_at REAL NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL, map_id TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, give_type TEXT NOT NULL, give_asset TEXT NOT NULL, give_count INTEGER NOT NULL, want_type TEXT NOT NULL, want_asset TEXT NOT NULL, want_count INTEGER NOT NULL, status TEXT NOT NULL);")
            columns = {row[1] for row in con.execute("PRAGMA table_info(ground_items)")}
            if columns and "map_id" not in columns:
                con.execute("ALTER TABLE ground_items RENAME TO ground_items_legacy")
            con.execute("CREATE TABLE IF NOT EXISTS ground_items (map_id TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, item TEXT NOT NULL, count INTEGER NOT NULL CHECK(count>=0), PRIMARY KEY(map_id,x,y,item))")
            if columns and "map_id" not in columns:
                con.execute("INSERT INTO ground_items(map_id,x,y,item,count) SELECT ?,x,y,item,count FROM ground_items_legacy", (self.default_map_id,))
                con.execute("DROP TABLE ground_items_legacy")
            migration = "migration:map-qualified-resources:v1"
            if not con.execute("SELECT 1 FROM world_state WHERE key=?", (migration,)).fetchone():
                for key, value in con.execute("SELECT key,value FROM world_state WHERE key GLOB 'resource:[0-9]*,[0-9]*:*'").fetchall():
                    old = key.removeprefix("resource:")
                    if re.fullmatch(r"\d+,\d+:.+", old):
                        con.execute("INSERT OR IGNORE INTO world_state(key,value) VALUES (?,?)", (f"resource:{self.default_map_id}:{old}", value))
                con.execute("INSERT INTO world_state(key,value) VALUES (?,1)", (migration,))
            con.executescript("CREATE TABLE IF NOT EXISTS player_maps (map_id TEXT PRIMARY KEY, owner TEXT NOT NULL, data TEXT NOT NULL); CREATE TABLE IF NOT EXISTS area_claims (source_map TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL, owner TEXT NOT NULL, area_map TEXT NOT NULL UNIQUE, exit_name TEXT NOT NULL, PRIMARY KEY(source_map,x,y));")
            for creature in self.world.get("creatures", []):
                if not creature.get("spawn_area"):
                    continue
                x = con.execute("SELECT value FROM world_state WHERE key=?", (f"enemy_location:{creature['id']}:x",)).fetchone()
                y = con.execute("SELECT value FROM world_state WHERE key=?", (f"enemy_location:{creature['id']}:y",)).fetchone()
                spawn_area = creature["spawn_area"]
                map_id = creature.get("map_id", self.default_map_id)
                map_data = next((entry for entry in self.world.get("maps", []) if entry["id"] == map_id), self.world)
                if x and y and spawn_area["x"] <= x[0] < spawn_area["x"] + spawn_area["width"] and spawn_area["y"] <= y[0] < spawn_area["y"] + spawn_area["height"] and self.world["biomes"].get(map_data["tiles"][y[0] * map_data["width"] + x[0]]["biome"], {}).get("passable", True):
                    self.creature_locations[creature["id"]] = {"x": x[0], "y": y[0]}
        self.dynamic_maps = {}
        self.entrance_links = {}
        self._decision_inflight = set()
        self._decision_semaphore = asyncio.Semaphore(4)
        self.npc_runtime_state = {}
        self.refresh_area_cache()
        with self.connect() as con:
            self.npc_runtime_state={row[0]:json.loads(row[1]) for row in con.execute("SELECT owner_id,state FROM decision_owner_state WHERE owner_type='npc'")}

    def connect(self):
        return sqlite3.connect(self.database, timeout=10)

    def refresh_area_cache(self):
        with self.connect() as con:
            self.dynamic_maps = {row[0]: {**json.loads(row[2]), "id": row[0], "owner": row[1]} for row in con.execute("SELECT map_id,owner,data FROM player_maps")}
            self.entrance_links = {(row[0], row[1], row[2]): {"owner": row[3], "map_id": row[4], "exit_name": row[5]} for row in con.execute("SELECT source_map,x,y,owner,area_map,exit_name FROM area_claims")}

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
        map_id=self.default_map_id;spawn=self.world["spawn"]
        start=getattr(self,"preview_start",None)
        if start:
            map_id=start["map_id"];spawn={"x":start["x"],"y":start["y"]}
        return {"map_id": map_id, "x": spawn["x"], "y": spawn["y"], "currency": self.world.get("starting_currency", 25), "inventory": copy.deepcopy(self.world.get("starting_inventory", {})), "equipment": {}, "active_quests": [], "completed_quests": [], "completed_contracts": [], "active_contracts": {}, "discovered": [self.location_key(map_id, spawn["x"], spawn["y"])], "events": {}, "quest_baselines": {}, "flags": {}, "once": [], "hp": 100, "dialog": None, "notes": {}, "tracked_quest": None, "experience": 0, "combat": None, "notifications": []}

    @staticmethod
    def location_key(map_id, x, y):
        return f"{map_id}:{x},{y}"

    def map_data(self, map_id):
        if map_id in self.dynamic_maps:
            return self.dynamic_maps[map_id]
        maps = self.world.get("maps", [])
        if not maps:
            return self.world
        return next((entry for entry in maps if entry["id"] == map_id), next(entry for entry in maps if entry["id"] == self.default_map_id))

    def home_role(self, area, username):
        if not area or not username: return None
        if area.get("owner")==username: return "owner"
        access=area.get("access",{})
        role=access.get(username) if isinstance(access,dict) else None
        policy=self.world.get("building",{})
        if role=="visitor" and policy.get("allow_visitors"): return role
        if role=="collaborator" and policy.get("allow_collaborators"): return role
        return None

    def can_modify_home(self, area, username):
        return self.home_role(area,username) in ("owner","collaborator")

    def home_tile_removal_target(self, con, area, state, username, direction):
        delta={"north":(0,-1),"south":(0,1),"east":(1,0),"west":(-1,0)}.get(direction)
        if not delta: raise GameError("Choose north, south, east, or west.")
        x,y=state["x"]+delta[0],state["y"]+delta[1]
        if not (0<=x<area["width"] and 0<=y<area["height"]): raise GameError("That is beyond the area's authored bounds.")
        index=y*area["width"]+x; owned=set(area.get("purchased_tiles",[]))
        if index not in owned: raise GameError("That tile is not part of your home.")
        if index==0: raise GameError("The entrance tile cannot be removed.")
        if area["tiles"][index].get("structures") or "structure_occupant" in area["tiles"][index]: raise GameError("Remove the structure before removing this tile.")
        adjacent=[]
        for dx,dy in ((0,-1),(0,1),(-1,0),(1,0)):
            nx,ny=x+dx,y+dy
            if 0<=nx<area["width"] and 0<=ny<area["height"]:
                neighbor=ny*area["width"]+nx
                if neighbor in owned: adjacent.append((neighbor,nx,ny))
        if len(adjacent)!=1: raise GameError("Only an outer tile with one home connection can be removed; this keeps every remaining tile connected.")
        cardinal={"north":(0,-1),"south":(0,1),"east":(1,0),"west":(-1,0)}
        for exit_name,exit_target in area["tiles"][index].get("exits",{}).items():
            if exit_name not in cardinal or not isinstance(exit_target,dict):
                raise GameError("Remove custom connections from this tile before removing it.")
            ex,ey=x+cardinal[exit_name][0],y+cardinal[exit_name][1]
            if not (0<=ex<area["width"] and 0<=ey<area["height"] and ey*area["width"]+ex in owned and exit_target.get("x")==ex and exit_target.get("y")==ey):
                raise GameError("Remove custom connections from this tile before removing it.")
        for row in con.execute("SELECT username,state FROM players").fetchall():
            try: occupant=json.loads(row[1])
            except (TypeError,ValueError): continue
            if row[0]!=username and occupant.get("map_id")==area["id"] and occupant.get("x")==x and occupant.get("y")==y:
                raise GameError("A player is standing on this tile; ask them to move first.")
        return index,adjacent[0]

    def trade_asset(self,query,*states):
        if query.casefold() in (self.world["economy"].get("currency","Coins").casefold(),"coin","coins"):
            return "currency",self.world["economy"].get("currency","Coins")
        entries=[{"id":item.get("id",""),"name":item["name"]} for item in self.world.get("items",[]) if item.get("name")]
        known={entry["name"].casefold() for entry in entries}
        for offer in (offer for vendor in self.world.get("economy",{}).get("vendors",[]) for offer in vendor.get("stock",[])):
            name=offer.get("item","")
            if name and name.casefold() not in known: entries.append({"id":"","name":name}); known.add(name.casefold())
        for name in self.world.get("starting_inventory",{}):
            if name.casefold() not in known: entries.append({"id":"","name":name}); known.add(name.casefold())
        for state in states:
            for name in state.get("inventory",{}):
                if name.casefold() not in known: entries.append({"id":"","name":name}); known.add(name.casefold())
        item=find(entries,query)
        return "item",item["name"]

    @staticmethod
    def trade_asset_count(state,asset_type,asset):
        return state.get("currency",0) if asset_type=="currency" else state.get("inventory",{}).get(asset,0)

    @staticmethod
    def move_trade_asset(source,destination,asset_type,asset,count):
        if asset_type=="currency":
            source["currency"]-=count; destination["currency"]+=count
        else:
            source["inventory"][asset]=source["inventory"].get(asset,0)-count
            if source["inventory"][asset]<=0: source["inventory"].pop(asset,None)
            destination["inventory"][asset]=destination["inventory"].get(asset,0)+count

    def load(self, con, username):
        row = con.execute("SELECT state FROM players WHERE username=?", (username,)).fetchone()
        state = json.loads(row[0]) if row else self.initial_state()
        for key, value in self.initial_state().items():
            state.setdefault(key, copy.deepcopy(value))
        state.setdefault("map_id", self.default_map_id)
        map_data = self.map_data(state["map_id"])
        state["map_id"] = map_data.get("id", self.default_map_id)
        state["discovered"] = [self.location_key(self.default_map_id, *map(int, key.split(","))) if isinstance(key, str) and ":" not in key else key for key in state.get("discovered", [])]
        state["notes"] = {self.location_key(self.default_map_id, *map(int, key.split(","))) if ":" not in key else key: value for key, value in state.get("notes", {}).items()}
        if not (0 <= state["x"] < map_data["width"] and 0 <= state["y"] < map_data["height"]):
            state.update(self.world["spawn"])
            state["map_id"] = self.default_map_id
        if state.get("combat") and state["combat"].get("creature") not in {c["id"] for c in self.world.get("creatures", [])}:
            state["combat"] = None
        if state.get("dialog") and state["dialog"].get("id") not in {d["id"] for d in self.world["dialogs"]}:
            state["dialog"] = None
        return state

    def execute(self, username, line):
        try:
            steps=expand_command_chain(line.strip() or "look")
        except GameError as exc:
            return str(exc)
        results=[]
        for step in steps:
            with self.lock:
                self._last_command_failed=False
                if self.before_command: self.before_command()
                result=self._execute(username,step)
                failed=self._last_command_failed
            results.append(result)
            if failed: break
        return "\n".join(results)

    async def trigger_decisions_for(self, username, notify, scheduled=False):
        """Queue enabled, active-area NPC policies after player input; inference never runs in the command transaction."""
        try:
            player=self.snapshot(username)
        except Exception:
            return
        for npc in self.world.get("npcs",[]):
            policy=npc.get("decision_policy",{})
            trigger="scheduled" if scheduled else "player_nearby"
            if not policy.get("enabled") or trigger not in policy.get("triggers",[]): continue
            runtime=self.npc_runtime_state.get(npc["id"],{})
            npc_map=runtime.get("map_id") or npc.get("map_id",self.default_map_id)
            npc_loc=runtime.get("location") or npc.get("location",self.world["spawn"])
            if not self._npc_player_active(player,npc_map,npc_loc,trigger,policy): continue
            if npc["id"] not in self._decision_inflight:
                self._decision_inflight.add("npc:"+npc["id"])
                asyncio.create_task(self._run_npc_decision(username,npc,player,notify,trigger=trigger))

        for vendor in self.world.get("economy",{}).get("vendors",[]):
            policy=vendor.get("decision_policy",{}); trigger="scheduled" if scheduled else "player_nearby"
            if not policy.get("enabled") or trigger not in policy.get("triggers",[]): continue
            map_id=vendor.get("map_id",self.default_map_id); location=vendor.get("location",self.world["spawn"])
            if (map_id,location.get("x"),location.get("y"))!=(player.get("map_id"),player["x"],player["y"]): continue
            inflight="market:"+vendor["id"]
            if inflight not in self._decision_inflight:
                self._decision_inflight.add(inflight)
                asyncio.create_task(self._run_market_decision(username,vendor,player,notify,trigger=trigger))

    def _decision_state(self, con, npc_id):
        row=con.execute("SELECT state FROM decision_owner_state WHERE owner_type='npc' AND owner_id=?",(npc_id,)).fetchone()
        return json.loads(row[0]) if row else {"revision":0,"memory":[],"next_due":0,"location":None,"map_id":None,"paused":False}

    @staticmethod
    def _npc_resources(npc, owner_state):
        policy=npc.get("decision_policy",{}); resources=policy.get("resources",{}); commerce=policy.get("commerce",{})
        if "inventory" not in owner_state:
            initial=commerce.get("starting_inventory",{}) if "trade" in policy.get("actions",[]) and commerce else resources.get("starting_inventory",{})
            owner_state["inventory"]=copy.deepcopy(initial)
        if "currency" not in owner_state: owner_state["currency"]=commerce.get("starting_currency",0) if "trade" in policy.get("actions",[]) and commerce else 0
        return owner_state["inventory"]

    @staticmethod
    def _npc_inventory_capacity(npc):
        policy=npc.get("decision_policy",{}); limits=[]
        if "trade" in policy.get("actions",[]) and policy.get("commerce"): limits.append(policy["commerce"].get("inventory_capacity",20))
        has_delivery=any(isinstance(task,dict) and task.get("delivery") for task in policy.get("tasks",[]))
        if set(policy.get("actions",[]))&{"gather","craft"} or has_delivery: limits.append(policy.get("resources",{}).get("inventory_capacity",100))
        return min(limits) if limits else 10000

    def _npc_gather_candidates(self,con,npc,owner_state,limit=254):
        policy=npc.get("decision_policy",{}); gathering=policy.get("gathering",{})
        if "gather" not in policy.get("actions",[]) or not gathering or limit<=0: return []
        self._npc_resources(npc,owner_state)
        map_id=owner_state.get("map_id") or npc.get("map_id",self.default_map_id)
        if map_id in self.dynamic_maps: return []
        location=owner_state.get("location") or npc.get("location",self.world["spawn"]); area=self.map_data(map_id)
        if not (0<=location["x"]<area["width"] and 0<=location["y"]<area["height"]): return []
        tile=area["tiles"][location["y"]*area["width"]+location["x"]]
        resources=[*tile.get("resources",[]),*self.world["biomes"].get(tile.get("biome"),{}).get("resources",[])]
        capacity=self._npc_inventory_capacity(npc); current=sum(owner_state["inventory"].values()); maximum=gathering.get("max_units_per_decision",1); candidates=[]; seen=set()
        for resource in resources:
            item=resource.get("item")
            if item not in gathering.get("items",[]) or item in seen: continue
            seen.add(item); key=f"resource:{map_id}:{location['x']},{location['y']}:{item}"
            left=self.shared_count(con,key,resource.get("quantity",resource.get("count",1)))
            for amount in range(1,min(maximum,left,capacity-current)+1):
                slug=re.sub(r"[^a-z0-9]+","-",item.casefold()).strip("-")[:32]
                candidates.append({"id":f"gather-{slug}-{amount}-{left}","type":"npc_gather","item":item,"amount":amount,"remaining":left,"description":f"Gather {amount} {item} here ({left} available)."})
                if len(candidates)>=limit: return candidates
        return candidates

    def _npc_craft_candidates(self,con,npc,owner_state,limit=254):
        policy=npc.get("decision_policy",{}); crafting=policy.get("crafting",{})
        if "craft" not in policy.get("actions",[]) or not crafting or limit<=0: return []
        inventory=self._npc_resources(npc,owner_state); map_id=owner_state.get("map_id") or npc.get("map_id",self.default_map_id)
        if map_id in self.dynamic_maps: return []
        location=owner_state.get("location") or npc.get("location",self.world["spawn"]); area=self.map_data(map_id)
        if not (0<=location["x"]<area["width"] and 0<=location["y"]<area["height"]): return []
        station=area["tiles"][location["y"]*area["width"]+location["x"]].get("station")
        capacity=self._npc_inventory_capacity(npc); maximum=crafting.get("max_batches_per_decision",1); candidates=[]
        for recipe in self.world.get("crafting",[]):
            if recipe["id"] not in crafting.get("recipes",[]) or recipe.get("station") and recipe["station"]!=station: continue
            required={}
            for part in recipe["inputs"]: required[part["item"]]=required.get(part["item"],0)+part.get("count",1)
            output=recipe["output"]
            for batches in range(1,maximum+1):
                if any(inventory.get(item,0)<count*batches for item,count in required.items()): break
                after=sum(inventory.values())-sum(required.values())*batches+output.get("count",1)*batches
                if after>capacity: break
                candidates.append({"id":f"craft-{recipe['id']}-{batches}","type":"npc_craft","recipe_id":recipe["id"],"batches":batches,"description":f"Craft {output.get('count',1)*batches} {output['item']} using {recipe['name']} ({batches} batch{'es' if batches!=1 else ''})."})
                if len(candidates)>=limit: return candidates
        return candidates

    def _apply_npc_gather(self,con,npc,owner_state,selected_id):
        action=next((c for c in self._npc_gather_candidates(con,npc,owner_state) if c["id"]==selected_id),None)
        if not action: return None
        map_id=owner_state.get("map_id") or npc.get("map_id",self.default_map_id); loc=owner_state.get("location") or npc.get("location",self.world["spawn"]); item=action["item"]
        tile=self.map_data(map_id)["tiles"][loc["y"]*self.map_data(map_id)["width"]+loc["x"]]; resources=[*tile.get("resources",[]),*self.world["biomes"].get(tile.get("biome"),{}).get("resources",[])]; resource=next(r for r in resources if r.get("item")==item)
        key=f"resource:{map_id}:{loc['x']},{loc['y']}:{item}"; left=self.shared_count(con,key,resource.get("quantity",resource.get("count",1)))
        if left<action["amount"]: return None
        con.execute("INSERT INTO world_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,left-action["amount"]))
        inventory=self._npc_resources(npc,owner_state); inventory[item]=inventory.get(item,0)+action["amount"]
        owner_state["gathered_items"]=owner_state.get("gathered_items",0)+action["amount"]; owner_state["revision"]=owner_state.get("revision",0)+1
        return f"🧺 {npc['name']} gathered {action['amount']} {item}"

    def _apply_npc_craft(self,con,npc,owner_state,selected_id):
        action=next((c for c in self._npc_craft_candidates(con,npc,owner_state) if c["id"]==selected_id),None)
        if not action: return None
        recipe=next(r for r in self.world["crafting"] if r["id"]==action["recipe_id"]); batches=action["batches"]; inventory=self._npc_resources(npc,owner_state); required={}
        for part in recipe["inputs"]: required[part["item"]]=required.get(part["item"],0)+part.get("count",1)*batches
        if any(inventory.get(item,0)<count for item,count in required.items()): return None
        for item,count in required.items():
            inventory[item]-=count
            if inventory[item]<=0: inventory.pop(item,None)
        output=recipe["output"]; inventory[output["item"]]=inventory.get(output["item"],0)+output.get("count",1)*batches
        owner_state["crafted_batches"]=owner_state.get("crafted_batches",0)+batches; owner_state["revision"]=owner_state.get("revision",0)+1
        return f"🛠️ {npc['name']} crafted {output.get('count',1)*batches} {output['item']}"

    @staticmethod
    def _safe_decision_text(value, limit=500):
        return re.sub(r"[\x00-\x1f\x7f]", "", str(value))[:limit]

    @staticmethod
    def _npc_player_active(player,map_id,location,trigger,policy):
        if player.get("map_id")!=map_id: return False
        radius=policy.get("active_radius",8) if trigger=="scheduled" else 1
        return abs(player["x"]-location["x"])+abs(player["y"]-location["y"])<=radius

    def _npc_reachable(self,map_id,location):
        """Bounded BFS over passable authored tiles and explicit exits; private homes are never traversed."""
        maps={area["id"]:area for area in self.world.get("maps",[])}
        start=(map_id,location["x"],location["y"])
        if map_id not in maps or map_id in self.dynamic_maps: return {}
        start_area=maps[map_id]
        if type(location.get("x")) is not int or type(location.get("y")) is not int or not (0<=location["x"]<start_area["width"] and 0<=location["y"]<start_area["height"]): return {}
        if not self.world["biomes"].get(start_area["tiles"][location["y"]*start_area["width"]+location["x"]]["biome"],{}).get("passable",True): return {}
        reached={start:(None,0)}; queue=deque([start]); directions=(("north",0,-1),("south",0,1),("east",1,0),("west",-1,0))
        while queue and len(reached)<16384:
            current=queue.popleft(); current_map,x,y=current; area=maps[current_map]; tile=area["tiles"][y*area["width"]+x]; first,distance=reached[current]
            neighbours=[]
            for direction,dx,dy in directions:
                nx,ny=x+dx,y+dy
                if 0<=nx<area["width"] and 0<=ny<area["height"]:
                    target_tile=area["tiles"][ny*area["width"]+nx]
                    if self.world["biomes"].get(target_tile["biome"],{}).get("passable",True):
                        neighbours.append(((current_map,nx,ny),{"kind":"walk","direction":direction,"destination":{"map_id":current_map,"x":nx,"y":ny}}))
            for exit_name,destination in tile.get("exits",{}).items():
                target_map=destination.get("map_id",current_map)
                if target_map not in maps or target_map in self.dynamic_maps: continue
                target_area=maps[target_map]; arrival=target_area.get("entry_points",{}).get(destination.get("entry_point"),destination); nx,ny=arrival.get("x"),arrival.get("y")
                if type(nx) is int and type(ny) is int and 0<=nx<target_area["width"] and 0<=ny<target_area["height"]:
                    target_tile=target_area["tiles"][ny*target_area["width"]+nx]
                    if self.world["biomes"].get(target_tile["biome"],{}).get("passable",True):
                        neighbours.append(((target_map,nx,ny),{"kind":"exit","name":exit_name,"destination":{"map_id":target_map,"x":nx,"y":ny}}))
            for node,step in neighbours:
                if node not in reached:
                    reached[node]=(first or step,distance+1); queue.append(node)
        return reached

    def _npc_decision_candidates(self,npc,owner_state,map_id,player_state=None,username=None):
        policy=npc["decision_policy"]; loc=owner_state.get("location") or npc.get("location",self.world["spawn"])
        if any(isinstance(task,dict) and task.get("delivery") for task in policy.get("tasks",[])): self._npc_resources(npc,owner_state)
        candidates=[{"id":"wait","type":"wait","description":"Wait and take no action."}]
        actions=set(policy.get("actions",["wait"]))
        if "move" in actions and map_id not in self.dynamic_maps:
            area=self.map_data(map_id); directions={"north":(0,-1),"south":(0,1),"east":(1,0),"west":(-1,0)}
            for name,(dx,dy) in directions.items():
                x,y=loc["x"]+dx,loc["y"]+dy
                if 0<=x<area["width"] and 0<=y<area["height"]:
                    tile=area["tiles"][y*area["width"]+x]
                    if self.world["biomes"].get(tile["biome"],{}).get("passable",True):
                        candidates.append({"id":"move-"+name,"type":"move","direction":name,"destination":{"map_id":map_id,"x":x,"y":y},"description":f"Move one legal step {name}."})
            routes=policy.get("routes",[]); active=owner_state.get("route_target"); reachable=self._npc_reachable(map_id,loc) if routes or active else {}
            if active:
                route=next((r for r in routes if r["id"]==active.get("id")),None)
                target=(active.get("map_id"),active.get("x"),active.get("y"))
                current=(map_id,loc["x"],loc["y"])
                if route and current==target:
                    candidates.append({"id":"arrive-"+route["id"],"type":"move","route_id":route["id"],"route_target":{"map_id":target[0],"x":target[1],"y":target[2]},"description":f"Complete the route to {route.get('name',route['id'])}."})
                elif route and target in reachable:
                    first,distance=reachable[target]
                    candidates.append({"id":"continue-"+route["id"],"type":"move","route_id":route["id"],"step":first,"route_target":{"map_id":target[0],"x":target[1],"y":target[2]},"description":f"Continue the active route to {route.get('name',route['id'])} ({distance} steps remain)."})
                else:
                    candidates.append({"id":"abandon-route","type":"route_cancel","description":"Clear the active route because its destination is no longer available."})
            else:
                for route in routes:
                    target_map=route.get("map_id",self.default_map_id); destination=route["location"]; target=(target_map,destination["x"],destination["y"])
                    if target not in reachable: continue
                    first,distance=reachable[target]
                    if distance==0:
                        candidates.append({"id":"arrive-"+route["id"],"type":"move","route_id":route["id"],"route_target":{"map_id":target_map,"x":destination["x"],"y":destination["y"]},"description":f"Mark arrival at {route.get('name',route['id'])}."})
                    else:
                        candidates.append({"id":"route-"+route["id"],"type":"move","route_id":route["id"],"step":first,"route_target":{"map_id":target_map,"x":destination["x"],"y":destination["y"]},"description":f"Travel to {route.get('name',route['id'])} ({distance} steps by a legal route)."})
        if "dialogue" in actions:
            text=npc.get("dialog","")
            if not text and npc.get("dialog_id"):
                dialog=next((d for d in self.world.get("dialogs",[]) if d["id"]==npc["dialog_id"]),None)
                if dialog:
                    node=dialog.get("nodes",{}).get(dialog.get("start"),{})
                    text=node.get("text","")
            if text:
                candidates.append({"id":"speak","type":"dialogue","text":self._safe_decision_text(text),"description":"Speak the NPC's authored greeting."})
        if "emote" in actions:
            for emote in self.world.get("emotes",[]):
                candidates.append({"id":"emote-"+emote["id"],"type":"emote","text":self._safe_decision_text(emote.get("broadcast",emote.get("verb",emote["id"])),80),"icon":self._safe_decision_text(emote.get("icon",""),16),"description":"Perform authored emote: "+self._safe_decision_text(emote["id"],80)+"."})
        if "task" in actions and player_state is not None and username:
            active=owner_state.get("active_task"); tasks=policy.get("tasks",[])
            if active:
                task=next((entry for entry in tasks if entry.get("id")==active.get("id")),None)
                if not task:
                    candidates.append({"id":"cancel-task-obsolete","type":"task","stage":"cancel","task_id":active.get("id"),"description":"Clear a task that no longer exists in this author's policy."})
                elif int(time.time())-active.get("started_at",int(time.time()))>=task.get("timeout_seconds",86400):
                    candidates.append({"id":"cancel-task-"+task["id"],"type":"task","stage":"cancel","task_id":task["id"],"description":f"Clear expired task {task['name']} so it can be restarted."})
                elif active.get("assigned_to")==username:
                    if task and not task.get("delivery"):
                        progress=active.get("progress",0); steps=task.get("steps",1)
                        candidates.append({"id":"advance-task-"+task["id"],"type":"task","stage":"advance","task_id":task["id"],"description":f"Advance {task['name']} ({min(progress+1,steps)}/{steps} steps): {task['description']}"})
            else:
                flags=player_state.get("flags",{})
                for task in tasks:
                    flag=f"npc-task:{npc['id']}:{task['id']}"
                    completed_at=flags.get(flag,0)
                    if task.get("once",True) and completed_at: continue
                    if not task.get("once",True) and isinstance(completed_at,int) and completed_at and time.time()-completed_at<task.get("cooldown_seconds",86400): continue
                    delivery=task.get("delivery")
                    instruction=f" Ask {delivery['count']} {delivery['item']} using give {delivery['item']} {delivery['count']} to {npc['name']}." if delivery else ""
                    candidates.append({"id":"start-task-"+task["id"],"type":"task","stage":"start","task_id":task["id"],"description":f"Start {task['name']}: {task['description']}{instruction}"})
        return candidates

    def _npc_commerce_candidates(self,con,npc,owner_state,limit=254):
        policy=npc.get("decision_policy",{}); commerce=policy.get("commerce",{})
        if "trade" not in policy.get("actions",[]) or not commerce: return []
        owner_state.setdefault("currency",commerce.get("starting_currency",0))
        owner_state.setdefault("inventory",copy.deepcopy(commerce.get("starting_inventory",{})))
        map_id=owner_state.get("map_id") or npc.get("map_id",self.default_map_id)
        location=owner_state.get("location") or npc.get("location",self.world["spawn"])
        candidates=[]
        if limit<=0: return candidates
        for vendor in self.world.get("economy",{}).get("vendors",[]):
            if vendor.get("id") not in commerce.get("markets",[]): continue
            vendor_location=vendor.get("location",self.world["spawn"]); vendor_map=vendor.get("map_id",self.default_map_id)
            if vendor_map!=map_id or vendor_location!=location: continue
            self.refresh_market_stock(con,vendor); self.refresh_market_production(con,vendor); self.refresh_market_consumption(con,vendor)
            market_owner=self._market_decision_state(con,vendor["id"]); paused=market_owner.get("paused_offers",{})
            for offer in vendor.get("stock",[]):
                item=offer.get("item")
                if item not in commerce.get("items",[]): continue
                key=f"stock:{vendor['id']}:{item}"
                remaining=self.shared_count(con,key,offer["quantity"]) if "quantity" in offer else None
                base=(market_owner.get("base_prices") or {}).get(item)
                max_units=commerce.get("max_units_per_decision",1)
                if commerce.get("allow_buy",True) and not paused.get(item,False) and not (offer.get("pricing",{}).get("mode")=="stock_based" and remaining is None):
                    level=remaining if remaining is not None else offer.get("quantity",0)
                    total=0
                    for amount in range(1,max_units+1):
                        if remaining is not None and amount>remaining: break
                        if sum(owner_state["inventory"].values())+amount>self._npc_inventory_capacity(npc): break
                        unit=self.market_price(offer,level-(amount-1),base)[0]; total+=unit
                        if total>commerce.get("max_spend_per_decision",100) or owner_state["currency"]-total<commerce.get("currency_reserve",0): break
                        slug=re.sub(r"[^a-z0-9]+","-",item.casefold()).strip("-")[:32]
                        candidate_id=f"buy-{vendor['id']}-{slug}-{amount}-{remaining if remaining is not None else 'u'}-{total}"
                        candidates.append({"id":candidate_id,"type":"npc_trade","action":"buy","market_id":vendor["id"],"item":item,"amount":amount,"stock":remaining,"total":total,"description":f"Buy {amount} {item} from {vendor['name']} for {total} {self.world['economy']['currency']} (stock {remaining if remaining is not None else 'unlimited'})."})
                if commerce.get("allow_sell",True):
                    held=owner_state["inventory"].get(item,0); level=remaining if remaining is not None else offer.get("quantity",0); total=0
                    for amount in range(1,min(max_units,held)+1):
                        total+=self.market_price(offer,level+(amount-1),base)[1]
                        if total<=0: continue
                        slug=re.sub(r"[^a-z0-9]+","-",item.casefold()).strip("-")[:32]
                        candidate_id=f"sell-{vendor['id']}-{slug}-{amount}-{remaining if remaining is not None else 'u'}-{total}"
                        candidates.append({"id":candidate_id,"type":"npc_trade","action":"sell","market_id":vendor["id"],"item":item,"amount":amount,"stock":remaining,"total":total,"description":f"Sell {amount} {item} to {vendor['name']} for {total} {self.world['economy']['currency']} (stock {remaining if remaining is not None else 'unlimited'})."})
                if len(candidates)>=limit: return candidates[:limit]
        return candidates

    def _apply_npc_trade(self,con,npc,owner_state,selected_id):
        candidates=self._npc_commerce_candidates(con,npc,owner_state)
        action=next((candidate for candidate in candidates if candidate["id"]==selected_id),None)
        if not action: return None
        commerce=npc["decision_policy"]["commerce"]; amount=action["amount"]; item=action["item"]
        vendor=next(vendor for vendor in self.world["economy"]["vendors"] if vendor["id"]==action["market_id"])
        offer=next(offer for offer in vendor["stock"] if offer["item"]==item)
        key=f"stock:{vendor['id']}:{item}"
        remaining=self.shared_count(con,key,offer["quantity"]) if "quantity" in offer else None
        inventory=owner_state.setdefault("inventory",{})
        if action["action"]=="buy":
            owner_state["currency"]-=action["total"]
            inventory[item]=inventory.get(item,0)+amount
            if remaining is not None:
                remaining-=amount
        else:
            inventory[item]-=amount
            if inventory[item]<=0: inventory.pop(item,None)
            owner_state["currency"]+=action["total"]
            if remaining is not None:
                remaining+=amount
        if remaining is not None:
            con.execute("INSERT INTO world_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,remaining))
        owner_state["revision"]=owner_state.get("revision",0)+1; owner_state["market_trades"]=owner_state.get("market_trades",0)+1
        verb="bought" if action["action"]=="buy" else "sold"
        return f"🤝 {npc['name']} {verb} {amount} {item} at {vendor['name']} for {action['total']} {self.world['economy']['currency']}"

    def _market_decision_state(self,con,vendor_id):
        row=con.execute("SELECT state FROM decision_owner_state WHERE owner_type='market' AND owner_id=?",(vendor_id,)).fetchone()
        return json.loads(row[0]) if row else {"revision":0,"memory":[],"next_due":0,"paused_offers":{},"base_prices":{},"paused":False}

    def refresh_market_stock(self,con,vendor,now=None):
        """Apply bounded elapsed-time restocks once to shared market stock."""
        if not any(offer.get("restock") for offer in vendor.get("stock",[])): return {}
        now=self.clock() if now is None else now
        simulation=self.world.get("economy",{}).get("market_simulation",{})
        maximum_cycles=simulation.get("max_catchup_cycles",24)
        offline=simulation.get("offline_simulation",True)
        own_transaction=not con.in_transaction
        if own_transaction: con.execute("BEGIN IMMEDIATE")
        changed={}
        try:
            for offer in vendor.get("stock",[]):
                policy=offer.get("restock")
                if not policy: continue
                item=offer["item"]; stock_key=f"stock:{vendor['id']}:{item}"; time_key=f"restock-time:{vendor['id']}:{item}"
                stock_row=con.execute("SELECT value FROM world_state WHERE key=?",(stock_key,)).fetchone()
                current=stock_row[0] if stock_row else offer["quantity"]
                time_row=con.execute("SELECT value FROM world_state WHERE key=?",(time_key,)).fetchone()
                if not time_row:
                    con.execute("INSERT INTO world_state(key,value) VALUES (?,?)",(time_key,int(now)))
                    continue
                last=float(time_row[0]); effective_last=last if offline else max(last,self._economy_started_at)
                if now<effective_last:
                    continue
                interval=policy["interval_seconds"]; elapsed=now-effective_last; cycles=int(elapsed//interval)
                if cycles<=0:
                    if effective_last!=last: con.execute("UPDATE world_state SET value=? WHERE key=?",(int(effective_last),time_key))
                    continue
                applied=min(cycles,maximum_cycles); cap=policy.get("max_quantity",offer["quantity"])
                updated=min(cap,current+applied*policy["amount"])
                con.execute("INSERT INTO world_state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(stock_key,updated))
                # Drop excess downtime after the authored catch-up budget, but preserve sub-interval remainder otherwise.
                next_time=now if cycles>maximum_cycles else effective_last+cycles*interval
                con.execute("UPDATE world_state SET value=? WHERE key=?",(int(next_time),time_key))
                changed[item]=updated
            if own_transaction: con.commit()
            return changed
        except Exception:
            if own_transaction: con.rollback()
            raise

    def refresh_market_production(self,con,vendor,now=None,report=None):
        """Run authored stock recipes with bounded cycles and no phantom inputs."""
        outputs=[offer for offer in vendor.get("stock",[]) if offer.get("production")]
        if not outputs: return {}
        now=self.clock() if now is None else now
        simulation=self.world.get("economy",{}).get("market_simulation",{}); maximum_cycles=simulation.get("max_catchup_cycles",24)
        offline=simulation.get("offline_simulation",True); own_transaction=not con.in_transaction
        if own_transaction: con.execute("BEGIN IMMEDIATE")
        changed={}
        try:
            for offer in outputs:
                policy=offer["production"]; item=offer["item"]
                time_key=f"production-time:{vendor['id']}:{item}"
                time_row=con.execute("SELECT value FROM world_state WHERE key=?",(time_key,)).fetchone()
                if not time_row:
                    con.execute("INSERT INTO world_state(key,value) VALUES (?,?)",(time_key,int(now))); continue
                last=float(time_row[0]); effective_last=last if offline else max(last,self._economy_started_at)
                if now<effective_last: continue
                cycles=int((now-effective_last)//policy["interval_seconds"])
                if cycles<=0:
                    if effective_last!=last: con.execute("UPDATE world_state SET value=? WHERE key=?",(int(effective_last),time_key))
                    continue
                applied=min(cycles,maximum_cycles)
                offer_key=f"stock:{vendor['id']}:{item}"
                offer_row=con.execute("SELECT value FROM world_state WHERE key=?",(offer_key,)).fetchone()
                quantities={entry["item"]:(con.execute("SELECT value FROM world_state WHERE key=?",(f"stock:{vendor['id']}:{entry['item']}",)).fetchone() or [entry["quantity"]])[0] for entry in vendor.get("stock",[]) if "quantity" in entry}
                quantities[item]=offer_row[0] if offer_row else offer["quantity"]
                produced=0
                for _ in range(applied):
                    if quantities[item]+policy["amount"]>policy["max_quantity"]: break
                    if any(quantities.get(part["item"],0)<part["count"] for part in policy["inputs"]): break
                    for part in policy["inputs"]: quantities[part["item"]]-=part["count"]
                    quantities[item]+=policy["amount"]; produced+=policy["amount"]
                if produced:
                    for changed_item,count in quantities.items():
                        key=f"stock:{vendor['id']}:{changed_item}"
                        con.execute("INSERT INTO world_state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,count))
                    changed[item]=quantities[item]
                    if report is not None: report[item]=report.get(item,0)+produced
                next_time=now if cycles>maximum_cycles else effective_last+cycles*policy["interval_seconds"]
                con.execute("UPDATE world_state SET value=? WHERE key=?",(int(next_time),time_key))
            if own_transaction: con.commit()
            return changed
        except Exception:
            if own_transaction: con.rollback()
            raise

    def refresh_market_consumption(self,con,vendor,now=None):
        """Apply bounded authored demand to shared finite offer stock."""
        offers=[offer for offer in vendor.get("stock",[]) if offer.get("consumption")]
        if not offers: return {}
        now=self.clock() if now is None else now
        simulation=self.world.get("economy",{}).get("market_simulation",{}); maximum_cycles=simulation.get("max_catchup_cycles",24)
        offline=simulation.get("offline_simulation",True); own_transaction=not con.in_transaction
        if own_transaction: con.execute("BEGIN IMMEDIATE")
        changed={}
        try:
            for offer in offers:
                policy=offer["consumption"]; item=offer["item"]
                time_key=f"consumption-time:{vendor['id']}:{item}"
                time_row=con.execute("SELECT value FROM world_state WHERE key=?",(time_key,)).fetchone()
                if not time_row:
                    con.execute("INSERT INTO world_state(key,value) VALUES (?,?)",(time_key,int(now))); continue
                last=float(time_row[0]); effective_last=last if offline else max(last,self._economy_started_at)
                if now<effective_last: continue
                cycles=int((now-effective_last)//policy["interval_seconds"])
                if cycles<=0:
                    if effective_last!=last: con.execute("UPDATE world_state SET value=? WHERE key=?",(int(effective_last),time_key))
                    continue
                applied=min(cycles,maximum_cycles); key=f"stock:{vendor['id']}:{item}"
                stock_row=con.execute("SELECT value FROM world_state WHERE key=?",(key,)).fetchone()
                current=stock_row[0] if stock_row else offer["quantity"]
                updated=max(0,current-applied*policy["amount"])
                con.execute("INSERT INTO world_state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,updated))
                changed[item]=updated
                next_time=now if cycles>maximum_cycles else effective_last+cycles*policy["interval_seconds"]
                con.execute("UPDATE world_state SET value=? WHERE key=?",(int(next_time),time_key))
            if own_transaction: con.commit()
            return changed
        except Exception:
            if own_transaction: con.rollback()
            raise

    def _market_decision_candidates(self,con,vendor,owner):
        self.refresh_market_stock(con,vendor)
        self.refresh_market_production(con,vendor)
        self.refresh_market_consumption(con,vendor)
        policy=vendor.get("decision_policy",{}); candidates=[{"id":"wait","type":"wait","description":"Wait and leave offers unchanged."}]
        goals={g.get("item"):g for g in policy.get("goals",[]) if g.get("item") and g.get("metric") in ("stock_level","stock_target","supply_level")}
        allow_stock="market_stock" in policy.get("actions",[])
        allow_price="market_price" in policy.get("actions",[])
        if not allow_stock and not allow_price: return candidates
        for offer in vendor.get("stock",[]):
            item=offer.get("item"); goal=goals.get(item)
            if not item or not goal: continue
            row=con.execute("SELECT value FROM world_state WHERE key=?",(f"stock:{vendor['id']}:{item}",)).fetchone()
            stock=row[0] if row else offer.get("quantity",0)
            threshold=goal.get("target",offer.get("pricing",{}).get("target_stock",offer.get("quantity",0)))
            paused=bool((owner.get("paused_offers") or {}).get(item,False))
            item_slug=re.sub(r"[^a-z0-9]+","-",item.casefold()).strip("-")[:40] or "offer"
            action="pause" if not paused and stock<=threshold else "resume" if paused and stock>threshold else None
            if allow_stock and action:
                slug=item_slug
                candidates.append({"id":f"{action}-{slug}","type":"market_stock","action":action,"item":item,"stock":stock,"target":threshold,"description":f"{action.title()} sales of {item} (stock {stock}, target {threshold})."})
            pricing=offer.get("pricing",{})
            if allow_price and pricing.get("mode")=="stock_based":
                base=(owner.get("base_prices") or {}).get(item,pricing["base_price"])
                step=pricing.get("adjustment_step",max(1,round(pricing["base_price"]*.1)))
                for action,direction in (("lower",-1),("raise",1)):
                    value=max(pricing["min_price"],min(pricing["max_price"],base+direction*step))
                    if value!=base:
                        candidates.append({"id":f"price-{action}-{item_slug}-{base}","type":"market_price","action":action,"item":item,"base_price":base,"new_base_price":value,"step":step,"minimum":pricing["min_price"],"maximum":pricing["max_price"],"description":f"{action.title()} the base price of {item} from {base} to {value} (range {pricing['min_price']}–{pricing['max_price']})."})
        return candidates[:255]

    def _market_decision_observation(self,vendor,owner,player,trigger,con):
        offers=[]
        for offer in vendor.get("stock",[]):
            item=offer["item"]; row=con.execute("SELECT value FROM world_state WHERE key=?",(f"stock:{vendor['id']}:{item}",)).fetchone()
            pricing=offer.get("pricing",{}); base=(owner.get("base_prices") or {}).get(item,pricing.get("base_price"))
            offers.append({"item":item,"stock":row[0] if row else offer.get("quantity","unlimited"),"paused":bool((owner.get("paused_offers") or {}).get(item,False)),"base_price":base,"price_range":{"min":pricing.get("min_price"),"max":pricing.get("max_price")} if pricing.get("mode")=="stock_based" else None})
        policy=vendor.get("decision_policy",{}); location=vendor.get("location",self.world["spawn"])
        return {"owner":{"type":"market","id":vendor["id"],"name":self._safe_decision_text(vendor["name"],100),"location":{"map_id":vendor.get("map_id",self.default_map_id),**location}},"trigger":trigger,"player":{"location":{"map_id":player.get("map_id"),"x":player["x"],"y":player["y"]}},"offers":offers,"goals":policy.get("goals",[])[:16],"memory":owner.get("memory",[])[:policy.get("memory_limit",20)]}

    async def _run_market_decision(self,username,vendor,player,notify,force=False,trigger="player_nearby"):
        started=time.monotonic(); trace_id=str(uuid.uuid4()); status="fallback"; selected="wait"; outcome="wait"; observation={}; candidates=[]; owner=None
        try:
            async with self._decision_semaphore:
                with self.connect() as con:
                    owner=self._market_decision_state(con,vendor["id"])
                    candidates=self._market_decision_candidates(con,vendor,owner)
                    observation=self._market_decision_observation(vendor,owner,player,trigger,con)
                policy=vendor["decision_policy"]
                if owner.get("paused") and not force: return
                decision_revision=owner.get("revision",0)
                if not force and owner.get("next_due",0)>time.time(): return
                location=vendor.get("location",self.world["spawn"]); map_id=vendor.get("map_id",self.default_map_id)
                if (map_id,location.get("x"),location.get("y"))!=(player.get("map_id"),player["x"],player["y"]): return
                profile=next((p for p in self.world.get("decision_providers",[]) if p["id"]==policy.get("provider_id")),None)
                if not profile: raise DecisionProviderError("Provider profile is unavailable")
                criteria={c["id"]:self._safe_decision_text(c["description"],240) for c in candidates}
                with self.connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    live_owner=self._market_decision_state(con,vendor["id"])
                    if live_owner.get("revision",0)!=decision_revision or (live_owner.get("paused") and not force): return
                    owner=live_owner; owner["next_due"]=time.time()+policy.get("cooldown_seconds",30)
                    con.execute("INSERT INTO decision_owner_state VALUES ('market',?,?) ON CONFLICT(owner_type,owner_id) DO UPDATE SET state=excluded.state",(vendor["id"],json.dumps(owner)))
                if len(criteria)<2: status="skipped"; outcome="no eligible stock action beyond wait"
                else:
                    selected,metadata=await asyncio.wait_for(asyncio.to_thread(choose_decision,profile,observation,criteria,policy.get("timeout_seconds",5)),timeout=policy.get("timeout_seconds",5)+1); status="selected"
                current=self.snapshot(username)
                announced=None
                with self.connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    live_owner=self._market_decision_state(con,vendor["id"]); live_candidates=self._market_decision_candidates(con,vendor,live_owner); owner=live_owner
                    if live_owner.get("revision",0)!=decision_revision or (live_owner.get("paused") and not force):
                        status="stale"; selected="wait"; outcome="discarded because the controller was paused or changed"
                    elif (current.get("map_id"),current["x"],current["y"])!=(player.get("map_id"),player["x"],player["y"]):
                        status="stale"; selected="wait"; outcome="discarded because player location changed"
                    elif not any(c["id"]==selected for c in live_candidates):
                        status="stale"; selected="wait"; outcome="discarded because market stock or offer state changed"
                    else:
                        owner=live_owner; action=next(c for c in live_candidates if c["id"]==selected)
                        if action["type"]=="market_stock":
                            owner.setdefault("paused_offers",{})[action["item"]]=action["action"]=="pause"; owner["revision"]+=1
                            outcome=f"{action['action']}d sales of {action['item']} at stock {action['stock']} (target {action['target']})"
                            announced="🛒 "+self._safe_decision_text(vendor["name"],100)+": "+outcome
                        elif action["type"]=="market_price":
                            owner.setdefault("base_prices",{})[action["item"]]=action["new_base_price"]; owner["revision"]+=1
                            outcome=f"{action['action']}d {action['item']} base price from {action['base_price']} to {action['new_base_price']} (range {action['minimum']}–{action['maximum']})"
                            announced="🪙 "+self._safe_decision_text(vendor["name"],100)+": "+outcome
                        else: outcome="left offers unchanged"
                        memory=owner.setdefault("memory",[]); memory.append({"at":int(time.time()),"action":selected,"outcome":self._safe_decision_text(outcome,180)})
                        owner["memory"]=memory[-policy.get("memory_limit",20):] if policy.get("memory_limit",20) else []
                    con.execute("INSERT INTO decision_owner_state VALUES ('market',?,?) ON CONFLICT(owner_type,owner_id) DO UPDATE SET state=excluded.state",(vendor["id"],json.dumps(owner)))
                    con.execute("INSERT INTO decision_traces(trace_id,created_at,owner_type,owner_id,trigger,observation,candidates,selected,outcome,status,latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(trace_id,time.time(),"market",vendor["id"],trigger,json.dumps(observation,ensure_ascii=False)[:65536],json.dumps(candidates,ensure_ascii=False)[:65536],selected,outcome,status,int((time.monotonic()-started)*1000)))
                if announced: notify(announced)
        except (DecisionProviderError,asyncio.TimeoutError,OSError,ValueError,KeyError,StopIteration) as exc:
            outcome="fallback: "+(str(exc)[:180] if isinstance(exc,DecisionProviderError) else type(exc).__name__)
            try:
                owner=owner or {"revision":0,"memory":[],"next_due":0,"paused_offers":{},"paused":False}; owner["next_due"]=time.time()+vendor.get("decision_policy",{}).get("cooldown_seconds",30)
                with self.connect() as con:
                    con.execute("INSERT INTO decision_owner_state VALUES ('market',?,?) ON CONFLICT(owner_type,owner_id) DO UPDATE SET state=excluded.state",(vendor["id"],json.dumps(owner)))
                    con.execute("INSERT INTO decision_traces(trace_id,created_at,owner_type,owner_id,trigger,observation,candidates,selected,outcome,status,latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(trace_id,time.time(),"market",vendor["id"],trigger,json.dumps(observation,ensure_ascii=False)[:65536],json.dumps(candidates,ensure_ascii=False)[:65536],"wait",outcome,"fallback",int((time.monotonic()-started)*1000)))
            except Exception: pass
        finally:
            self._decision_inflight.discard("market:"+vendor["id"])

    def set_decision_paused(self,owner_type,owner_id,paused,audit=None):
        if owner_type=="market":
            owner_def=next((v for v in self.world.get("economy",{}).get("vendors",[]) if v["id"]==owner_id),None)
            state_reader=self._market_decision_state
        elif owner_type=="npc":
            owner_def=next((n for n in self.world.get("npcs",[]) if n["id"]==owner_id),None)
            state_reader=self._decision_state
        else: raise GameError("Unsupported decision owner type.")
        if not owner_def or not owner_def.get("decision_policy",{}).get("enabled"): raise GameError("Choose an enabled decision controller.")
        with self.lock, self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            state=state_reader(con,owner_id)
            state["paused"]=bool(paused); state["revision"]=state.get("revision",0)+1
            if not paused: state["next_due"]=0
            con.execute("INSERT INTO decision_owner_state VALUES (?,?,?) ON CONFLICT(owner_type,owner_id) DO UPDATE SET state=excluded.state",(owner_type,owner_id,json.dumps(state)))
            result={"owner_type":owner_type,"owner_id":owner_id,"paused":state["paused"],"revision":state["revision"]}
            if audit: audit(con,result)
        return result

    def decision_preview(self,username,npc_id,owner_type="npc"):
        if owner_type=="market":
            vendor=next((v for v in self.world.get("economy",{}).get("vendors",[]) if v["id"]==npc_id),None)
            if not vendor or not vendor.get("decision_policy",{}).get("enabled"): raise GameError("Choose a market with an enabled decision policy.")
            player=self.snapshot(username); policy=vendor["decision_policy"]
            with self.connect() as con:
                owner=self._market_decision_state(con,npc_id); candidates=self._market_decision_candidates(con,vendor,owner); observation=self._market_decision_observation(vendor,owner,player,"player_nearby",con)
            location=vendor.get("location",self.world["spawn"]); map_id=vendor.get("map_id",self.default_map_id)
            profile=next((p for p in self.world.get("decision_providers",[]) if p["id"]==policy.get("provider_id")),{})
            return {"owner_type":"market","owner_id":npc_id,"paused":bool(owner.get("paused")),"player_here":(player.get("map_id"),player["x"],player["y"])==(map_id,location["x"],location["y"]),"observation":observation,"candidates":candidates,"question":{"id":"action","type":"choice","criteria":{c["id"]:c["description"] for c in candidates}},"provider":{"adapter":profile.get("adapter"),"model":profile.get("model"),"endpoint_configured":bool(profile.get("endpoint_binding") and os.getenv(profile.get("endpoint_binding",""))),"credential_configured":not profile.get("credential_binding") or bool(os.getenv(profile.get("credential_binding","")))},"cooldown_until":owner.get("next_due",0)}
        npc=next((n for n in self.world.get("npcs",[]) if n["id"]==npc_id),None)
        if not npc or not npc.get("decision_policy",{}).get("enabled"): raise GameError("Choose an NPC with an enabled decision policy.")
        player=self.snapshot(username); policy=npc["decision_policy"]
        with self.connect() as con:
            owner=self._decision_state(con,npc_id)
            candidates=self._npc_decision_candidates(npc,owner,owner.get("map_id") or npc.get("map_id",self.default_map_id),player,username)
            if "trade" in policy.get("actions",[]): candidates.extend(self._npc_commerce_candidates(con,npc,owner,max(0,254-len(candidates))))
            if "gather" in policy.get("actions",[]): candidates.extend(self._npc_gather_candidates(con,npc,owner,max(0,254-len(candidates))))
            if "craft" in policy.get("actions",[]): candidates.extend(self._npc_craft_candidates(con,npc,owner,max(0,254-len(candidates))))
        map_id=owner.get("map_id") or npc.get("map_id",self.default_map_id)
        location=owner.get("location") or npc.get("location",self.world["spawn"])
        candidates=[c for c in candidates if c["type"] in ("wait","npc_trade","npc_gather","npc_craft") or c["type"] in policy.get("actions",[])]
        criteria={c["id"]:c["description"] for c in candidates}
        preview_owner={"type":"npc","id":npc_id,"name":npc["name"],"role":npc.get("role","traveler"),"location":{"map_id":map_id,**location},"active_route":copy.deepcopy(owner.get("route_target")),"route_visits":owner.get("route_visits",0),"active_task":copy.deepcopy(owner.get("active_task"))}
        has_delivery=any(isinstance(task,dict) and task.get("delivery") for task in policy.get("tasks",[]))
        if "own_state" in policy.get("observations",[]) and (set(policy.get("actions",[]))&{"trade","gather","craft"} or has_delivery):
            preview_owner["resources"]={"currency":owner.get("currency",0),"inventory":copy.deepcopy(owner.get("inventory",{})),"market_trades":owner.get("market_trades",0),"gathered_items":owner.get("gathered_items",0),"crafted_batches":owner.get("crafted_batches",0),"task_completions":owner.get("task_completions",0),"deliveries_completed":owner.get("deliveries_completed",0)}
            if "trade" in policy.get("actions",[]): preview_owner["commerce"]={"currency":owner.get("currency",0),"inventory":copy.deepcopy(owner.get("inventory",{})),"market_trades":owner.get("market_trades",0)}
        observation={"owner":preview_owner,"trigger":"player_nearby","player":{"location":{"map_id":player.get("map_id"),"x":player["x"],"y":player["y"]}},"goals":policy.get("goals",[])[:16],"memory":owner.get("memory",[])[:policy.get("memory_limit",20)]}
        if "nearby" in policy.get("observations",[]):
            observation["nearby"]={"npcs":[n["name"] for n in self.nearby(self.world.get("npcs",[]),player) if n["id"]!=npc_id],"vendors":[v["name"] for v in self.nearby(self.world.get("economy",{}).get("vendors",[]),player)]}
        if "public_quests" in policy.get("observations",[]): observation["public_quests"]=[q["name"] for q in self.world.get("quests",[])[:64]]
        profile=next((p for p in self.world.get("decision_providers",[]) if p["id"]==policy.get("provider_id")),{})
        player_here=self._npc_player_active(player,map_id,location,"player_nearby",policy)
        endpoint=bool(profile.get("endpoint_binding") and os.getenv(profile["endpoint_binding"]))
        credential=not profile.get("credential_binding") or bool(os.getenv(profile.get("credential_binding","")))
        return {"owner_type":"npc","owner_id":npc_id,"paused":bool(owner.get("paused")),"player_here":player_here,"observation":observation,"candidates":candidates,"question":{"id":"action","type":"choice","criteria":criteria},"provider":{"adapter":profile.get("adapter"),"model":profile.get("model"),"endpoint_configured":endpoint,"credential_configured":credential},"cooldown_until":owner.get("next_due",0)}

    async def _run_npc_decision(self,username,npc,player,notify,force=False,trigger="player_nearby"):
        started=time.monotonic(); trace_id=str(uuid.uuid4()); status="fallback"; selected="wait"; outcome="wait"; metadata={}; observation={}; candidates=[]
        try:
            async with self._decision_semaphore:
                with self.connect() as con:
                    owner=self._decision_state(con,npc["id"])
                    candidates=self._npc_decision_candidates(npc,owner,owner.get("map_id") or npc.get("map_id",self.default_map_id),player,username)
                    if "trade" in npc["decision_policy"].get("actions",[]): candidates.extend(self._npc_commerce_candidates(con,npc,owner,max(0,254-len(candidates))))
                    if "gather" in npc["decision_policy"].get("actions",[]): candidates.extend(self._npc_gather_candidates(con,npc,owner,max(0,254-len(candidates))))
                    if "craft" in npc["decision_policy"].get("actions",[]): candidates.extend(self._npc_craft_candidates(con,npc,owner,max(0,254-len(candidates))))
                if owner.get("paused") and not force: return
                decision_revision=owner.get("revision",0)
                if not force and owner.get("next_due",0)>time.time(): return
                map_id=owner.get("map_id") or npc.get("map_id",self.default_map_id)
                location=owner.get("location") or npc.get("location",self.world["spawn"])
                policy=npc["decision_policy"]
                if not self._npc_player_active(player,map_id,location,trigger,policy): return
                allowed=set(policy.get("actions",[])); candidates=[c for c in candidates if c["type"] in ("wait","route_cancel","npc_trade","npc_gather","npc_craft") or c["type"] in allowed]
                owner_observation={"type":"npc","id":npc["id"],"name":self._safe_decision_text(npc["name"],100),"role":self._safe_decision_text(npc.get("role","traveler"),80),"location":{"map_id":map_id,**location},"active_route":copy.deepcopy(owner.get("route_target")),"route_visits":owner.get("route_visits",0),"active_task":copy.deepcopy(owner.get("active_task"))}
                has_delivery=any(isinstance(task,dict) and task.get("delivery") for task in policy.get("tasks",[]))
                if "own_state" in policy.get("observations",[]) and (set(policy.get("actions",[]))&{"trade","gather","craft"} or has_delivery):
                    owner_observation["resources"]={"currency":owner.get("currency",0),"inventory":copy.deepcopy(owner.get("inventory",{})),"market_trades":owner.get("market_trades",0),"gathered_items":owner.get("gathered_items",0),"crafted_batches":owner.get("crafted_batches",0),"task_completions":owner.get("task_completions",0),"deliveries_completed":owner.get("deliveries_completed",0)}
                    if "trade" in policy.get("actions",[]): owner_observation["commerce"]={"currency":owner.get("currency",0),"inventory":copy.deepcopy(owner.get("inventory",{})),"market_trades":owner.get("market_trades",0)}
                observation={"owner":owner_observation,"trigger":trigger,"player":{"location":{"map_id":player.get("map_id"),"x":player["x"],"y":player["y"]}},"goals":policy.get("goals",[])[:16],"memory":owner.get("memory",[])[:policy.get("memory_limit",20)]}
                if "nearby" in policy.get("observations",[]):
                    observation["nearby"]={"npcs":[self._safe_decision_text(n["name"],100) for n in self.nearby(self.world.get("npcs",[]),player) if n["id"]!=npc["id"]],"vendors":[self._safe_decision_text(v["name"],100) for v in self.nearby(self.world.get("economy",{}).get("vendors",[]),player)]}
                if "public_quests" in policy.get("observations",[]): observation["public_quests"]=[self._safe_decision_text(q["name"],100) for q in self.world.get("quests",[])[:64]]
                profile=next((p for p in self.world.get("decision_providers",[]) if p["id"]==policy["provider_id"]),None)
                if not profile: raise DecisionProviderError("Provider profile is unavailable")
                criteria={c["id"]:self._safe_decision_text(c["description"],240) for c in candidates}
                with self.connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    live_controller=self._decision_state(con,npc["id"])
                    if live_controller.get("revision",0)!=decision_revision or (live_controller.get("paused") and not force): return
                    owner=live_controller; owner["next_due"]=time.time()+policy.get("cooldown_seconds",30)
                    con.execute("INSERT INTO decision_owner_state VALUES ('npc',?,?) ON CONFLICT(owner_type,owner_id) DO UPDATE SET state=excluded.state",(npc["id"],json.dumps(owner)))
                if len(criteria)<2:
                    selected="wait"; status="skipped"; outcome="no eligible action beyond wait"
                else:
                    selected,metadata=await asyncio.wait_for(asyncio.to_thread(choose_decision,profile,observation,criteria,policy.get("timeout_seconds",5)),timeout=policy.get("timeout_seconds",5)+1)
                    status="selected"
                current=self.snapshot(username)
                live=self.npc_runtime_state.get(npc["id"],{})
                live_map=live.get("map_id") or npc.get("map_id",self.default_map_id); live_loc=live.get("location") or npc.get("location",self.world["spawn"])
                with self.lock, self.connect() as con:
                    con.execute("BEGIN IMMEDIATE")
                    live_controller=self._decision_state(con,npc["id"]); owner=live_controller
                    if live_controller.get("revision",0)!=decision_revision or (live_controller.get("paused") and not force):
                        status="stale"; outcome="discarded because the controller was paused or changed"; selected="wait"
                    elif not self._npc_player_active(current,map_id,location,trigger,policy) or (live_map,live_loc["x"],live_loc["y"])!=(map_id,location["x"],location["y"]):
                        status="stale"; outcome="discarded because owner or player state changed"; selected="wait"
                    else:
                        action=next(c for c in candidates if c["id"]==selected)
                        if action["type"]=="move":
                            if not owner.get("map_id"):
                                owner["map_id"]=map_id
                            if not isinstance(owner.get("location"),dict):
                                owner["location"]={"x":location["x"],"y":location["y"]}
                            if action.get("route_target"):
                                owner["route_target"]={"id":action.get("route_id"),**action["route_target"]}
                            if action.get("step"):
                                step=action["step"]; destination=step["destination"]
                                owner["location"]={"x":destination["x"],"y":destination["y"]}; owner["map_id"]=destination["map_id"]; owner["revision"]+=1
                                outcome=f"traveled through {step['name']}" if step["kind"]=="exit" else f"moved {step['direction']}"
                            elif action.get("destination"):
                                destination=action["destination"]; owner["location"]={"x":destination["x"],"y":destination["y"]}; owner["map_id"]=destination.get("map_id",map_id); owner["revision"]+=1
                                outcome=f"moved {action.get('direction','one step')}"
                            if owner.get("route_target") and (owner.get("map_id"),owner["location"]["x"],owner["location"]["y"])==(owner["route_target"].get("map_id"),owner["route_target"]["x"],owner["route_target"]["y"]):
                                route_id=owner["route_target"].get("id"); owner["route_target"]=None; owner["route_visits"]=owner.get("route_visits",0)+1
                                route=next((r for r in policy.get("routes",[]) if r["id"]==route_id),None)
                                outcome+=f"; arrived at {route.get('name',route_id) if route else route_id}"
                            elif not action.get("step") and not action.get("destination"):
                                owner["route_target"]=None; owner["route_visits"]=owner.get("route_visits",0)+1; outcome="arrived at authored destination"
                            self.npc_runtime_state[npc["id"]]=owner
                        elif action["type"]=="route_cancel":
                            owner["route_target"]=None; owner["revision"]+=1; self.npc_runtime_state[npc["id"]]=owner; outcome="cleared an unavailable route"
                        elif action["type"]=="task":
                            task=next((entry for entry in policy.get("tasks",[]) if entry.get("id")==action.get("task_id")),None)
                            if action.get("stage")=="cancel" and not task:
                                active=owner.get("active_task")
                                if not active or active.get("id")!=action.get("task_id"): raise GameError("That obsolete task is no longer active.")
                                owner["active_task"]=None; owner["revision"]+=1; outcome="cleared a task removed from the authoring policy"
                            elif not task: raise GameError("That authored NPC task is no longer available.")
                            elif action.get("stage")=="start":
                                if owner.get("active_task"): raise GameError("This NPC already has an active task.")
                                flag=f"npc-task:{npc['id']}:{task['id']}"
                                task_player=self.load(con,username)
                                if task.get("once",True) and task_player.get("flags",{}).get(flag): raise GameError("You have already completed this one-time task.")
                                owner["active_task"]={"id":task["id"],"assigned_to":username,"progress":0,"started_at":int(time.time()),"delivery":copy.deepcopy(task.get("delivery"))}
                                owner["revision"]+=1
                                delivery=task.get("delivery")
                                outcome=f"📦 {npc['name']} needs {delivery['count']} {delivery['item']}. Return with the item and use give {delivery['item']} {delivery['count']} to {npc['name']}." if delivery else f"📋 Started task {task['name']} for {username}"
                            elif action.get("stage")=="cancel":
                                active=owner.get("active_task")
                                if not active or active.get("id")!=task["id"] or int(time.time())-active.get("started_at",int(time.time()))<task.get("timeout_seconds",86400): raise GameError("That task has not expired.")
                                owner["active_task"]=None; owner["revision"]+=1; outcome=f"cleared expired task {task['name']}"
                            else:
                                active=owner.get("active_task")
                                if not active or active.get("id")!=task["id"] or active.get("assigned_to")!=username: raise GameError("This task is no longer assigned to you.")
                                active["progress"]=min(task.get("steps",1),active.get("progress",0)+1)
                                owner["revision"]+=1
                                if active["progress"]>=task.get("steps",1):
                                    task_player=self.load(con,username)
                                    if not self._npc_player_active(task_player,map_id,location,trigger,policy): raise GameError("Stay near this NPC to complete the task.")
                                    effect_output=self.effects(task_player,task.get("effects",[]))
                                    task_player.setdefault("flags",{})[f"npc-task:{npc['id']}:{task['id']}"]=int(time.time())
                                    con.execute("UPDATE players SET state=? WHERE username=?",(json.dumps(task_player),username))
                                    owner["active_task"]=None
                                    owner["task_completions"]=owner.get("task_completions",0)+1
                                    outcome=f"📋 Completed task {task['name']}"+("; "+" ".join(effect_output) if effect_output else "")
                                else:
                                    outcome=f"📋 Advanced task {task['name']} ({active['progress']}/{task.get('steps',1)})"
                            self.npc_runtime_state[npc["id"]]=owner
                        elif action["type"]=="npc_trade":
                            outcome=self._apply_npc_trade(con,npc,owner,selected)
                            if outcome is None:
                                status="stale"; selected="wait"; outcome="discarded a trade offer because funds, stock, or price changed"
                            else:
                                self.npc_runtime_state[npc["id"]]=owner
                        elif action["type"]=="npc_gather":
                            outcome=self._apply_npc_gather(con,npc,owner,selected)
                            if outcome is None: status="stale"; selected="wait"; outcome="discarded gathering because the resource changed"
                            else: self.npc_runtime_state[npc["id"]]=owner
                        elif action["type"]=="npc_craft":
                            outcome=self._apply_npc_craft(con,npc,owner,selected)
                            if outcome is None: status="stale"; selected="wait"; outcome="discarded crafting because ingredients or station changed"
                            else: self.npc_runtime_state[npc["id"]]=owner
                        elif action["type"]=="dialogue": outcome="🧑 "+self._safe_decision_text(npc["name"],100)+": "+action["text"]
                        elif action["type"]=="emote": outcome=self._safe_decision_text(npc["name"],100)+" "+action["text"]+" "+action["icon"]
                        else: outcome="waited"
                        memory=owner.setdefault("memory",[])
                        memory.append({"at":int(time.time()),"action":selected,"outcome":self._safe_decision_text(outcome,180)})
                        owner["memory"]=memory[-policy.get("memory_limit",20):] if policy.get("memory_limit",20) else []
                        owner["last_outcome"]=status
                    con.execute("INSERT INTO decision_owner_state VALUES ('npc',?,?) ON CONFLICT(owner_type,owner_id) DO UPDATE SET state=excluded.state",(npc["id"],json.dumps(owner)))
                    con.execute("INSERT INTO decision_traces(trace_id,created_at,owner_type,owner_id,trigger,observation,candidates,selected,outcome,status,latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(trace_id,time.time(),"npc",npc["id"],trigger,json.dumps(observation,ensure_ascii=False)[:65536],json.dumps(candidates,ensure_ascii=False)[:65536],selected,outcome,status,int((time.monotonic()-started)*1000)))
            if status=="fallback" and npc["decision_policy"].get("fallback")=="authored":
                text=next((c.get("text") for c in candidates if c["type"]=="dialogue"),"")
                if text: notify("🧑 "+self._safe_decision_text(npc["name"],100)+": "+text)
            elif status=="selected" and outcome not in ("waited",) and (outcome.startswith(("🧑 ","📋 ","📦 ","🤝 ","🧺 ","🛠️ ")) or outcome.startswith("moved ") or outcome.startswith("traveled ") or any(outcome.endswith(" "+e.get("icon","")) for e in self.world.get("emotes",[]) if e.get("icon"))):
                notify(("🧭 "+self._safe_decision_text(npc["name"],100)+" heads "+outcome[6:] if outcome.startswith("moved ") else outcome))
        except (DecisionProviderError, asyncio.TimeoutError, OSError, ValueError, KeyError, StopIteration, GameError) as exc:
            outcome="fallback: "+(str(exc)[:180] if isinstance(exc,DecisionProviderError) else type(exc).__name__)
            try:
                owner["next_due"]=time.time()+npc.get("decision_policy",{}).get("cooldown_seconds",30)
                with self.connect() as con:
                    con.execute("INSERT INTO decision_owner_state VALUES ('npc',?,?) ON CONFLICT(owner_type,owner_id) DO UPDATE SET state=excluded.state",(npc["id"],json.dumps(owner)))
                    con.execute("INSERT INTO decision_traces(trace_id,created_at,owner_type,owner_id,trigger,observation,candidates,selected,outcome,status,latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(trace_id,time.time(),"npc",npc["id"],trigger,json.dumps(observation,ensure_ascii=False)[:65536],json.dumps(candidates,ensure_ascii=False)[:65536],"wait",outcome,"fallback",int((time.monotonic()-started)*1000)))
                if npc.get("decision_policy",{}).get("fallback")=="authored":
                    text=next((c.get("text") for c in candidates if c.get("type")=="dialogue"),"")
                    if text: notify("🧑 "+self._safe_decision_text(npc["name"],100)+": "+text)
            except Exception:
                pass
        finally:
            self._decision_inflight.discard("npc:"+npc["id"])

    def _execute(self, username, line):
        """Apply one command and all resulting quest effects in a single transaction."""
        self._last_command_failed=False
        try:
            with self.connect() as con:
                con.execute("BEGIN IMMEDIATE")
                state = self.load(con, username)
                output=[]
                for step in expand_command_chain(line.strip() or "look"):
                    output.extend(self.command(con,state,step,username))
                    output.extend(self.advance(state))
                output = [*state.pop("notifications", []), *output]
                con.execute("INSERT INTO players VALUES (?,?) ON CONFLICT(username) DO UPDATE SET state=excluded.state", (username, json.dumps(state)))
            self.refresh_area_cache()
            return "\n".join(output)
        except GameError as exc:
            self._last_command_failed=True
            self.refresh_area_cache()
            return str(exc)

    def snapshot(self, username):
        with self.lock:
            if self.before_command:
                self.before_command()
            with self.connect() as con:
                return self.load(con, username)

    def queue_player_message(self,username,message):
        """Defer asynchronous activity until the next command response so typed input is never redrawn or overwritten."""
        message=self._safe_decision_text(message,500)
        if not message: return
        with self.lock,self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            state=self.load(con,username)
            queue=state.setdefault("notifications",[])
            queue.append(message)
            state["notifications"]=queue[-20:]
            con.execute("INSERT INTO players VALUES (?,?) ON CONFLICT(username) DO UPDATE SET state=excluded.state",(username,json.dumps(state)))

    def take_notifications(self, username):
        """Atomically fetch queued messages for polling transports."""
        with self.lock, self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            state = self.load(con, username)
            messages = state.pop("notifications", [])
            state["notifications"] = []
            con.execute("INSERT INTO players VALUES (?,?) ON CONFLICT(username) DO UPDATE SET state=excluded.state", (username, json.dumps(state)))
            con.commit()
            return messages

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
        map_data = self.map_data(state.get("map_id", self.default_map_id))
        return map_data["tiles"][state["y"] * map_data["width"] + state["x"]]

    @staticmethod
    def building_cost(policy,tile):
        override=next((entry for entry in policy.get("tile_cost_overrides",[]) if entry.get("biome")==tile.get("biome")),None)
        return (override.get("currency",policy.get("tile_cost",100)),override.get("materials",policy.get("tile_materials",[]))) if override else (policy.get("tile_cost",100),policy.get("tile_materials",[]))

    @staticmethod
    def structure_footprint(area, anchor, structure):
        shape=structure.get("footprint",{})
        width,height=shape.get("width",1),shape.get("height",1)
        x,y=anchor%area["width"],anchor//area["width"]
        if x+width>area["width"] or y+height>area["height"]: raise GameError("This structure footprint extends beyond the area's bounds.")
        tiles=[(y+dy)*area["width"]+(x+dx) for dy in range(height) for dx in range(width)]
        purchased=set(area.get("purchased_tiles",[]))
        if any(index not in purchased for index in tiles): raise GameError("Every tile under this structure must be purchased first.")
        if any(area["tiles"][index].get("structures") or "structure_occupant" in area["tiles"][index] for index in tiles): raise GameError("This structure footprint overlaps an existing structure.")
        return tiles

    def nearby(self, entries, state):
        found=[]
        for entry in entries:
            runtime=self.npc_runtime_state.get(entry.get("id"),{}) if entry in self.world.get("npcs",[]) else {}
            map_id=runtime.get("map_id") or entry.get("map_id",self.default_map_id)
            location=runtime.get("location") or self.creature_locations.get(entry.get("id")) or entry.get("location",self.world["spawn"])
            if map_id==state.get("map_id",self.default_map_id) and location=={"x":state["x"],"y":state["y"]}:
                found.append(entry)
        return found

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
                found = next(((map_data, tile) for map_data in self.world.get("maps", [self.world]) for tile in map_data["tiles"] if tile.get("id") == target), None)
                if found:
                    map_data, tile = found; index = map_data["tiles"].index(tile)
                    target = {"map_id": map_data.get("id", self.default_map_id), "x": index % map_data["width"], "y": index // map_data["width"]}
            target_map = target.get("map_id", self.default_map_id)
            return int(self.location_key(target_map, target["x"], target["y"]) in state["discovered"])
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
                spawn_area = creature.get("spawn_area")
                if spawn_area:
                    map_data = self.map_data(creature.get("map_id", self.default_map_id))
                    choices = [{"x": x, "y": y} for y in range(spawn_area["y"], spawn_area["y"] + spawn_area["height"]) for x in range(spawn_area["x"], spawn_area["x"] + spawn_area["width"]) if self.world["biomes"].get(map_data["tiles"][y * map_data["width"] + x]["biome"], {}).get("passable", True)]
                    location = random.choice(choices)
                    self.creature_locations[creature["id"]] = location
                    con.execute("INSERT INTO world_state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"enemy_location:{creature['id']}:x", location["x"]))
                    con.execute("INSERT INTO world_state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"enemy_location:{creature['id']}:y", location["y"]))
                return creature["max_hp"]
        return 0

    def exits(self, state, username=None):
        map_data = self.map_data(state.get("map_id", self.default_map_id))
        candidates = {"north": {"x": state['x'], "y": state['y'] - 1},
                      "south": {"x": state['x'], "y": state['y'] + 1},
                      "east": {"x": state['x'] + 1, "y": state['y']},
                      "west": {"x": state['x'] - 1, "y": state['y']}}
        candidates.update(self.tile(state).get('exits', {}))
        link = self.entrance_links.get((state.get("map_id", self.default_map_id), state["x"], state["y"]))
        if link and self.home_role(self.dynamic_maps.get(link["map_id"]),username):
            candidates[link["exit_name"]] = {"map_id": link["map_id"], "x": 0, "y": 0}
        available = {}
        for name, raw_pos in candidates.items():
            target_map = raw_pos.get("map_id", state.get("map_id", self.default_map_id))
            destination = self.map_data(target_map)
            arrival = destination.get("entry_points", {}).get(raw_pos.get("entry_point"), raw_pos)
            x, y = arrival["x"], arrival["y"]
            if target_map in self.dynamic_maps and not self.home_role(self.dynamic_maps[target_map],username):
                continue
            if target_map in self.dynamic_maps and y * destination["width"] + x not in destination.get("purchased_tiles", []):
                continue
            if 0 <= x < destination["width"] and 0 <= y < destination["height"] and self.world["biomes"][destination["tiles"][y * destination["width"] + x]["biome"]].get("passable", True):
                available[name] = {"map_id": target_map, "x": x, "y": y}
        return available

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
        for creature in self.world.get("creatures", []):
            self.creature_hp(con, creature)
        parts = line.split(maxsplit=1)
        command, argument = (parts[0].lower(), parts[1] if len(parts) > 1 else "") if parts else ("look", "")
        aliases = {"n": "north", "s": "south", "e": "east", "w": "west"}
        if state.get("combat") and command not in {"attack", "flee", "look", "map", "stats", "help", "?"}:
            raise GameError("You are in combat. Attack or flee before doing something else.")
        if command in aliases or command in ("north", "south", "east", "west"):
            argument, command = aliases.get(command, command), "go"
        if command=="trade":
            words=argument.split(maxsplit=1); action=words[0].lower() if words else "offers"; tail=words[1] if len(words)>1 else ""
            if action in ("offer","accept") and not self.world.get("economy",{}).get("player_trade",{}).get("enabled",False): raise GameError("Player-to-player trading is disabled in this world.")
            if action=="offer":
                recipient,sep,payload=tail.partition(" ")
                if not sep or not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}",recipient) or recipient==username: raise GameError("Use: trade offer <player> <item> [count] for <item> [count].")
                sides=re.split(r"\s+for\s+",payload,maxsplit=1,flags=re.I)
                if len(sides)!=2: raise GameError("Use: trade offer <player> <item> [count] for <item> [count].")
                give_query,give_count=quantity_arg(sides[0]); want_query,want_count=quantity_arg(sides[1])
                row=con.execute("SELECT state FROM players WHERE username=?",(recipient,)).fetchone()
                if not row: raise GameError("That player must enter the world before you can trade with them.")
                target=self.load(con,recipient)
                give_type,give_asset=self.trade_asset(give_query,state,target); want_type,want_asset=self.trade_asset(want_query,state,target)
                if self.trade_asset_count(state,give_type,give_asset)<give_count: raise GameError(f"You do not have {give_count} {give_asset} to offer.")
                if give_type=="item" and give_asset in state.get("equipment",{}).values(): raise GameError(f"Unequip {give_asset} before offering it.")
                location=(state.get("map_id",self.default_map_id),state["x"],state["y"])
                if (target.get("map_id",self.default_map_id),target["x"],target["y"])!=location: raise GameError("You and that player must be in the same map tile to trade.")
                area=self.dynamic_maps.get(location[0])
                if area and not self.world.get("economy",{}).get("player_trade",{}).get("allow_in_homes",True): raise GameError("Player trading is disabled inside private homes.")
                if area and (not self.home_role(area,username) or not self.home_role(area,recipient)): raise GameError("Both players need current access to trade inside this private home.")
                trade_id=uuid.uuid4().hex[:10]; now=time.time(); expires=now+self.world.get("economy",{}).get("player_trade",{}).get("offer_timeout_seconds",600)
                con.execute("INSERT INTO player_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(trade_id,now,expires,username,recipient,location[0],location[1],location[2],give_type,give_asset,give_count,want_type,want_asset,want_count,"pending"))
                timeout=self.world.get("economy",{}).get("player_trade",{}).get("offer_timeout_seconds",600)
                notice=f"🔁 {username} offers {give_count} {give_asset} for {want_count} {want_asset} (trade {trade_id}). Accept with trade accept {trade_id} within {timeout//60}m {timeout%60}s."
                target_state=self.load(con,recipient); queue=target_state.setdefault("notifications",[]); queue.append(notice); target_state["notifications"]=queue[-20:]
                con.execute("INSERT INTO players VALUES (?,?) ON CONFLICT(username) DO UPDATE SET state=excluded.state",(recipient,json.dumps(target_state)))
                return [f"🔁 Trade offer {trade_id} sent to {recipient}: {give_count} {give_asset} for {want_count} {want_asset}. Nothing moves until they accept."]
            if action=="offers":
                now=time.time(); con.execute("UPDATE player_trades SET status='expired' WHERE status='pending' AND expires_at<=?",(now,))
                rows=con.execute("SELECT trade_id,sender,recipient,give_type,give_asset,give_count,want_type,want_asset,want_count,expires_at FROM player_trades WHERE status='pending' AND (recipient=? OR sender=?) ORDER BY created_at DESC LIMIT 20",(username,username)).fetchall()
                if not rows: return ["🔁 No pending trade offers."]
                return [f"{trade_id} · {sender} offers {gc} {ga} for {wc} {wa} · {'incoming' if recipient==username else 'outgoing'} · {max(0,int(exp-now))}s left" for trade_id,sender,recipient,gt,ga,gc,wt,wa,wc,exp in rows]
            if action in ("accept","decline","cancel"):
                trade_id=tail.strip()
                if not re.fullmatch(r"[a-f0-9]{10}",trade_id): raise GameError(f"Use: trade {action} <trade-id>.")
                row=con.execute("SELECT trade_id,expires_at,sender,recipient,map_id,x,y,give_type,give_asset,give_count,want_type,want_asset,want_count,status FROM player_trades WHERE trade_id=?",(trade_id,)).fetchone()
                if not row: raise GameError("That trade offer no longer exists.")
                _,expires,sender,recipient,map_id,x,y,give_type,give_asset,give_count,want_type,want_asset,want_count,status=row
                if status!="pending": raise GameError(f"That trade is {status}.")
                if action=="cancel" and username!=sender: raise GameError("Only the player who made this offer can cancel it.")
                if action in ("accept","decline") and username!=recipient: raise GameError("Only the invited player can accept or decline this offer.")
                if expires<=time.time():
                    con.execute("UPDATE player_trades SET status='expired' WHERE trade_id=?",(trade_id,)); return ["That trade offer has expired."]
                if action!="accept":
                    status="cancelled" if action=="cancel" else "declined"
                    con.execute("UPDATE player_trades SET status=? WHERE trade_id=?",(status,trade_id))
                    other=recipient if username==sender else sender
                    other_state=self.load(con,other); queue=other_state.setdefault("notifications",[]); queue.append(f"🔁 Trade {trade_id} was {status} by {username}."); other_state["notifications"]=queue[-20:]
                    con.execute("INSERT INTO players VALUES (?,?) ON CONFLICT(username) DO UPDATE SET state=excluded.state",(other,json.dumps(other_state)))
                    return [f"🔁 Trade {trade_id} {status}."]
                sender_row=con.execute("SELECT state FROM players WHERE username=?",(sender,)).fetchone()
                if not sender_row: raise GameError("The player who made this offer is no longer available.")
                sender_state=self.load(con,sender); receiver_state=state
                target=(map_id,x,y)
                for participant,participant_state in ((sender,sender_state),(recipient,receiver_state)):
                    if (participant_state.get("map_id",self.default_map_id),participant_state["x"],participant_state["y"])!=target: raise GameError("Both players must still be together at the offer location to accept.")
                    area=self.dynamic_maps.get(map_id)
                    if area and not self.home_role(area,participant): raise GameError("A player no longer has access to this private home.")
                if self.trade_asset_count(sender_state,give_type,give_asset)<give_count: raise GameError(f"{sender} no longer has {give_count} {give_asset}.")
                if self.trade_asset_count(receiver_state,want_type,want_asset)<want_count: raise GameError(f"You do not have {want_count} {want_asset} for this trade.")
                if give_type=="item" and give_asset in sender_state.get("equipment",{}).values(): raise GameError(f"{sender} must unequip {give_asset} before trading it.")
                if want_type=="item" and want_asset in receiver_state.get("equipment",{}).values(): raise GameError(f"Unequip {want_asset} before trading it.")
                self.move_trade_asset(sender_state,receiver_state,give_type,give_asset,give_count)
                self.move_trade_asset(receiver_state,sender_state,want_type,want_asset,want_count)
                con.execute("UPDATE player_trades SET status='completed' WHERE trade_id=?",(trade_id,))
                queue=sender_state.setdefault("notifications",[]); queue.append(f"🔁 {recipient} accepted trade {trade_id}."); sender_state["notifications"]=queue[-20:]
                con.execute("UPDATE players SET state=? WHERE username=?",(json.dumps(sender_state),sender))
                return [f"🔁 Trade complete: you gave {want_count} {want_asset} and received {give_count} {give_asset} from {sender}."]
            raise GameError("Use trade offers, offer, accept <id>, decline <id>, or cancel <id>.")
        if command == "contracts":
            if argument: raise GameError("Use contracts to see nearby offers and active deliveries.")
            now=self.clock(); active=state.setdefault("active_contracts",{}); output=[]
            for ident,contract in list(active.items()):
                expires=contract.get("expires_at")
                if expires is not None and now>=expires:
                    active.pop(ident,None); output.append(f"⌛ {contract['name']} expired. The cargo remains in your inventory.")
            nearby_vendors=self.nearby(self.world.get("economy",{}).get("vendors",[]),state)
            available=[]; all_contracts=[(vendor,contract) for vendor in nearby_vendors for contract in vendor.get("contracts",[])]
            if all_contracts:
                output.append("🚚 Delivery contracts at this market:")
                completed=set(state.setdefault("completed_contracts",[]))
                for vendor,contract in all_contracts:
                    if contract["id"] in completed: status="complete"
                    elif contract["id"] in active: status="active"
                    elif set(contract.get("prerequisites",[]))-completed: status="locked: " + ", ".join(sorted(set(contract["prerequisites"])-completed))
                    else:
                        status="available"; available.append(contract["id"])
                    destination=next(market for market in self.world["economy"]["vendors"] if market["id"]==contract["destination_market_id"])
                    reward=contract.get("reward_currency",0); deadline=contract.get("deadline_seconds") or 0
                    suffix=f" · {deadline}s limit" if deadline else " · no deadline"
                    output.append(f"  {contract['id']} · {contract['name']} [{status}]: carry {contract['count']} {contract['item']} to {destination['name']} for {reward} {self.world['economy']['currency']}{suffix}")
            active=state.get("active_contracts",{})
            if active:
                output.append("Active deliveries:")
                for ident,contract in active.items():
                    left="no deadline" if contract.get("expires_at") is None else f"{max(0,int(contract['expires_at']-now))}s remaining"
                    output.append(f"  {ident} · {contract['count']} {contract['item']} → {contract['destination_name']} · {left}")
            return output or ["No delivery contracts are available at this market, and you have no active deliveries."]
        if command == "contract":
            words=argument.split(maxsplit=1); action=words[0].lower() if words else ""; ident=words[1].strip() if len(words)>1 else ""
            if action not in ("accept","deliver") or not ident: raise GameError("Use contract accept <id> at its origin market or contract deliver <id> at its destination market.")
            active=state.setdefault("active_contracts",{}); completed=set(state.setdefault("completed_contracts",[])); now=self.clock()
            if action=="accept":
                located=[(vendor,contract) for vendor in self.world.get("economy",{}).get("vendors",[]) for contract in vendor.get("contracts",[]) if contract["id"].casefold()==ident.casefold()]
                if not located: raise GameError("No authored delivery contract has that id.")
                origin,contract=located[0]
                ident=contract["id"]
                if ident in completed: raise GameError("You have already completed that contract.")
                if ident in active: raise GameError("That contract is already active.")
                if origin not in self.nearby([origin],state): raise GameError(f"Accept {contract['id']} at {origin['name']}.")
                if len(active)>=5: raise GameError("You can carry at most five active delivery contracts.")
                missing=set(contract.get("prerequisites",[]))-completed
                if missing: raise GameError("Complete these contracts first: "+", ".join(sorted(missing)))
                offer=next(part for part in origin.get("stock",[]) if part["item"]==contract["item"])
                self.refresh_market_stock(con,origin); self.refresh_market_production(con,origin); self.refresh_market_consumption(con,origin)
                market_state=self._market_decision_state(con,origin["id"])
                if (market_state.get("paused_offers") or {}).get(contract["item"],False): raise GameError("This market has paused cargo sales while its stock is low.")
                stock_key=f"stock:{origin['id']}:{contract['item']}"; remaining=self.shared_count(con,stock_key,offer["quantity"])
                if remaining<contract["count"]: raise GameError(f"This contract needs {contract['count']} {contract['item']}; only {remaining} are in stock.")
                remaining-=contract["count"]
                con.execute("INSERT INTO world_state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(stock_key,remaining))
                item=contract["item"]; state["inventory"][item]=state["inventory"].get(item,0)+contract["count"]
                destination=next(market for market in self.world["economy"]["vendors"] if market["id"]==contract["destination_market_id"])
                duration=contract.get("deadline_seconds") or 0
                active[ident]={"id":ident,"name":contract["name"],"origin_market_id":origin["id"],"destination_market_id":destination["id"],"destination_name":destination["name"],"item":item,"count":contract["count"],"reward_currency":contract.get("reward_currency",0),"accepted_at":now,"expires_at":now+duration if duration else None}
                return [f"📦 Accepted {contract['name']}: {contract['count']} {item} loaded from {origin['name']}. Deliver it at {destination['name']} with contract deliver {ident}."+(f" You have {duration} seconds." if duration else "")]
            if ident not in active: raise GameError("That delivery is not active. Use contracts to review your current work.")
            contract=active[ident]
            if contract.get("expires_at") is not None and now>=contract["expires_at"]:
                active.pop(ident,None); return [f"⌛ {contract['name']} expired. The cargo remains in your inventory."]
            destination=next((market for market in self.world["economy"]["vendors"] if market["id"]==contract["destination_market_id"]),None)
            if destination is None: raise GameError("The destination market for this active contract no longer exists.")
            if destination not in self.nearby([destination],state): raise GameError(f"Deliver this cargo at {destination['name']}.")
            if state["inventory"].get(contract["item"],0)<contract["count"]: raise GameError(f"You need {contract['count']} {contract['item']} to complete this delivery.")
            state["inventory"][contract["item"]]-=contract["count"]
            if state["inventory"][contract["item"]]<=0: state["inventory"].pop(contract["item"],None)
            reward=contract.get("reward_currency",0); state["currency"]+=reward
            active.pop(ident,None); state["completed_contracts"].append(ident); self.event(state,"contract",ident)
            return [f"✅ Delivered {contract['count']} {contract['item']} to {destination['name']} for {self.presentation.get('currency_icon','🪙')} {reward} {self.world['economy']['currency']}. Contract complete: {contract['name']}."]
        if command=="home":
            area=self.dynamic_maps.get(state.get("map_id")); words=argument.split()
            if not area or area.get("owner")!=username: raise GameError("Manage home access from inside your own area.")
            if len(words)<2 or words[0].lower()!="access": raise GameError("Use home access list, grant <player> <role>, or revoke <player>.")
            action=words[1].lower(); access=area.setdefault("access",{})
            if action=="list" and len(words)==2:
                entries=", ".join(f"{name} ({role})" for name,role in sorted(access.items())) or "none"
                return ["🔑 Home access: owner (you) · "+entries]
            if action=="grant" and len(words)==4:
                target,role=words[2],words[3].lower()
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}",target) or target==username: raise GameError("Choose another player using a valid character name.")
                if role not in ("visitor","collaborator"): raise GameError("Choose visitor or collaborator.")
                policy=self.world.get("building",{})
                if role=="visitor" and not policy.get("allow_visitors"): raise GameError("The world author has disabled visitor access grants.")
                if role=="collaborator" and not policy.get("allow_collaborators"): raise GameError("The world author has disabled collaborator grants.")
                if target not in access and len(access)>=128: raise GameError("This home has reached its 128-player access limit.")
                access[target]=role
                result=f"{target} may now enter as a {role}."
            elif action=="revoke" and len(words)==3:
                target=words[2]
                if target not in access: raise GameError("That player has no home access grant.")
                del access[target]; result=f"Home access revoked for {target}."
            else: raise GameError("Use home access list, grant <player> <role>, or revoke <player>.")
            data=json.dumps({k:v for k,v in area.items() if k not in ("id","owner")})
            con.execute("UPDATE player_maps SET data=? WHERE map_id=?",(data,area["id"]))
            return ["🔑 "+result]
        if command == "build":
            policy = self.world.get("building", {})
            if not policy.get("enabled"):
                raise GameError("Player building is not enabled in this world.")
            words = argument.lower().split()
            action = words[0] if words else "status"
            if action == "status":
                homes = [m for m in self.dynamic_maps.values() if m.get("owner") == username]
                return ["🏠 Your areas: " + (", ".join(f"{m['name']} ({len(m.get('purchased_tiles', []))} tiles)" for m in homes) if homes else "none")]
            if action in ("rename","describe"):
                if not policy.get("allow_room_editing",False): raise GameError("The world author has disabled home room editing.")
                area=self.dynamic_maps.get(state.get("map_id"))
                if not area or not self.can_modify_home(area,username): raise GameError("Stand inside a home where you have owner or collaborator access to edit rooms.")
                index=state["y"]*area["width"]+state["x"]
                if index not in area.get("purchased_tiles",[]): raise GameError("You can edit only purchased home rooms.")
                _,_,raw_value=argument.partition(" ")
                value=re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]","",raw_value)
                value=re.sub(r"[\x00-\x1f\x7f]","",value).strip()
                maximum=80 if action=="rename" else 500
                if not value or len(value)>maximum: raise GameError(f"Enter a room {'name' if action=='rename' else 'description'} between 1 and {maximum} characters.")
                key="name" if action=="rename" else "description"
                area["tiles"][index][key]=value
                data=json.dumps({k:v for k,v in area.items() if k not in ("id","owner")})
                con.execute("UPDATE player_maps SET data=? WHERE map_id=?",(data,area["id"]))
                self.dynamic_maps[area["id"]]=area
                return [f"✍️ Room {key} updated: {value}"]
            if action == "preview":
                if map_id:=state.get("map_id",self.default_map_id):
                    if map_id in self.dynamic_maps:
                        area=self.dynamic_maps[map_id]
                        if not self.can_modify_home(area,username): raise GameError("You need owner or collaborator access to preview construction.")
                        if len(words)>=4 and words[1:3]==["remove","tile"]:
                            index,(neighbor,nx,ny)=self.home_tile_removal_target(con,area,state,username,words[3])
                            tile=area["tiles"][index]; cost,materials=self.building_cost(policy,tile); percent=policy.get("tile_refund_percent",0)
                            currency=(cost*percent)//100
                            material_text=", ".join(f"{p['item']} ×{(p['count']*percent)//100}" for p in materials if (p['count']*percent)//100) or "none"
                            return [f"🧹 Remove {self.world['biomes'][tile['biome']]['name']} tile: refund {currency} {self.world['economy']['currency']} + materials: {material_text} ({percent}%).", "The entrance stays in place; only a connected outer tile can be removed."]
                        if len(words)>=3 and words[1]=="paint":
                            biome=find([{"id":ident,**data} for ident,data in self.world["biomes"].items()]," ".join(words[2:]))
                            if biome["id"] not in policy.get("paintable_biomes",[]): raise GameError(f"The world author has not allowed painting with {biome['name']}.")
                            index=state["y"]*area["width"]+state["x"]
                            if index not in area.get("purchased_tiles",[]): raise GameError("You can paint only purchased home tiles.")
                            tile=area["tiles"][index]
                            if tile.get("structures") or "structure_occupant" in tile: raise GameError("Remove the structure before repainting this tile.")
                            cost=policy.get("paint_cost",0); materials=policy.get("paint_materials",[])
                            missing=[f"{p['item']} ×{max(0,p['count']-state['inventory'].get(p['item'],0))}" for p in materials if state["inventory"].get(p["item"],0)<p["count"]]
                            affordable=state["currency"]>=cost and not missing
                            material_text=", ".join(f"{p['item']} ×{p['count']}" for p in materials) or "none"
                            return [f"🎨 Paint this tile as {biome['name']}: {cost} {self.world['economy']['currency']} + materials: {material_text}.","✅ You can afford this edit." if affordable else "⚠️ You are currently short of the listed currency or materials."+(" Missing: "+", ".join(missing)+"." if missing else "")]
                        if len(words)>=3 and words[1] in ("place","structure"):
                            current_index=state["y"]*area["width"]+state["x"]
                            structure=find(policy.get("structures",[])," ".join(words[2:]))
                            footprint=self.structure_footprint(area,current_index,structure)
                            if sum(len(t.get("structures",[])) for t in area["tiles"])>=policy.get("max_structures_per_area",128): raise GameError("This home has reached its structure limit.")
                            per_area=structure.get("max_per_area")
                            if per_area is not None and sum(1 for t in area["tiles"] for placed in t.get("structures",[]) if placed.get("id")==structure["id"])>=per_area: raise GameError(f"This home can contain at most {per_area} {structure['name']} structure(s).")
                            materials=structure.get("materials",[]); cost=structure.get("cost",0)
                            missing=[f"{p['item']} ×{max(0,p['count']-state['inventory'].get(p['item'],0))}" for p in materials if state["inventory"].get(p["item"],0)<p["count"]]
                            affordable=state["currency"]>=cost and not missing
                            material_text=", ".join(f"{p['item']} ×{p['count']}" for p in materials) or "none"
                            shape=structure.get("footprint",{}); size=f"{shape.get('width',1)}×{shape.get('height',1)} tile(s), extending east and south from here"
                            return [f"🧾 {structure.get('icon','🪚')} {structure['name']} · footprint {size}: {cost} {self.world['economy']['currency']} + materials: {material_text}.","✅ You can afford this structure." if affordable else "⚠️ You are currently short of the listed currency or materials."+(" Missing: "+", ".join(missing)+"." if missing else "")]
                        direction=words[1] if len(words)>1 else ""
                        delta={"north":(0,-1),"south":(0,1),"east":(1,0),"west":(-1,0)}.get(direction)
                        if not delta: raise GameError("Inside your area, use build preview <direction>.")
                        x,y=state["x"]+delta[0],state["y"]+delta[1]
                        if not (0<=x<area["width"] and 0<=y<area["height"]): raise GameError("That is beyond the area's authored bounds.")
                        index=y*area["width"]+x
                        if index in area.get("purchased_tiles",[]): raise GameError("That tile is already part of your area.")
                        if len(area.get("purchased_tiles",[]))>=policy.get("max_tiles",64): raise GameError("You have reached the area's tile limit.")
                        owned=set(area.get("purchased_tiles",[]))
                        if not any((x+dx,y+dy) in [(i%area["width"],i//area["width"]) for i in owned] for dx,dy in ((0,-1),(0,1),(-1,0),(1,0))): raise GameError("Expansion must connect to an owned tile.")
                        target=area["tiles"][index]
                    else:
                        if len(words)>1: raise GameError("At a world entrance, use build preview to inspect its claim cost.")
                        target=self.tile(state)
                        biome=self.world["biomes"][target["biome"]]
                        if not target.get("buildable",biome.get("buildable",False)): raise GameError("This terrain is not buildable.")
                    cost,materials=self.building_cost(policy,target)
                    material_text=", ".join(f"{part['item']} ×{part['count']}" for part in materials) or "none"
                    return [f"🧾 {self.world['biomes'][target['biome']]['name']} tile: {cost} {self.world['economy']['currency']} + materials: {material_text}.","The entrance is included with the first home tile at no extra fee." if map_id not in self.dynamic_maps else "This expansion adds one adjacent interior tile."]
            if action == "paint" and len(words)>=2:
                area=self.dynamic_maps.get(state.get("map_id"))
                if not area or not self.can_modify_home(area,username): raise GameError("Stand inside a home tile where you have owner or collaborator access to paint.")
                index=state["y"]*area["width"]+state["x"]
                if index not in area.get("purchased_tiles",[]): raise GameError("You can paint only purchased home tiles.")
                tile=area["tiles"][index]
                if tile.get("structures") or "structure_occupant" in tile: raise GameError("Remove the structure before repainting this tile.")
                biome=find([{"id":ident,**data} for ident,data in self.world["biomes"].items()]," ".join(words[1:]))
                if biome["id"] not in policy.get("paintable_biomes",[]): raise GameError(f"The world author has not allowed painting with {biome['name']}.")
                if not biome.get("passable",True): raise GameError("Home terrain must remain passable so every purchased tile stays reachable.")
                cost=policy.get("paint_cost",0); materials=policy.get("paint_materials",[])
                if state["currency"]<cost: raise GameError(f"You need {cost} {self.world['economy']['currency']} to paint this tile.")
                for part in materials:
                    if state["inventory"].get(part["item"],0)<part["count"]: raise GameError(f"You need {part['count']} {part['item']} to paint this tile.")
                state["currency"]-=cost
                for part in materials:
                    state["inventory"][part["item"]]-=part["count"]
                    if not state["inventory"][part["item"]]: del state["inventory"][part["item"]]
                previous_biome=self.world["biomes"].get(tile.get("biome"),{})
                previous_description=previous_biome.get("description") or f"A quiet stretch of {previous_biome.get('name',tile.get('biome','terrain')).lower()}."
                if tile.get("description")==previous_description:
                    tile.pop("description",None)
                tile["biome"]=biome["id"]
                data=json.dumps({k:v for k,v in area.items() if k not in ("id","owner")})
                con.execute("UPDATE player_maps SET data=? WHERE map_id=?",(data,area["id"]))
                self.dynamic_maps[area["id"]]=area
                return [f"🎨 Painted this tile as {biome.get('icon','🏞️')} {biome['name']} for {cost} {self.world['economy']['currency']}. "+("Materials: "+", ".join(f"{p['item']} ×{p['count']}" for p in materials)+"." if materials else "")]
            if action == "claim" and len(words) == 1:
                map_id = state.get("map_id", self.default_map_id)
                if map_id in self.dynamic_maps: raise GameError("Claim an entrance from a shared world map.")
                source = self.map_data(map_id); tile = self.tile(state); biome = self.world["biomes"][tile["biome"]]
                if not tile.get("buildable", biome.get("buildable", False)): raise GameError("This terrain is not buildable.")
                services = self.nearby(self.world.get("npcs", []) + self.world.get("economy", {}).get("vendors", []) + self.world.get("interactables", []), state)
                if not policy.get("allow_on_services") and (tile.get("exits") or tile.get("station") or services):
                    raise GameError("Choose an unoccupied tile without a service or map connection.")
                if (map_id,state["x"],state["y"]) in self.entrance_links: raise GameError("Someone has already claimed this entrance tile.")
                homes=[m for m in self.dynamic_maps.values() if m.get("owner")==username]
                if len(homes)>=policy.get("max_areas_per_player",1): raise GameError("You have reached the area limit for this world.")
                cost,materials=self.building_cost(policy,tile)
                if state["currency"]<cost: raise GameError(f"You need {cost} {self.world['economy']['currency']} to claim this area.")
                for p in materials:
                    if state["inventory"].get(p["item"],0)<p["count"]: raise GameError(f"You need {p['count']} {p['item']} to claim this area.")
                suffix=hashlib.sha256(f"{username}:{len(homes)}".encode()).hexdigest()
                area_id="home-"+suffix[:12]
                template_id=policy.get("template_map_id")
                if template_id:
                    area=copy.deepcopy(self.map_data(template_id))
                    area["tiles"]=[{k:v for k,v in t.items() if k!="exits"} for t in area["tiles"]]
                else: area={"width":4,"height":4,"tiles":[{"biome":"grassland"} for _ in range(16)]}
                area.update(id=area_id,name=f"{username}'s Home",owner=username,purchased_tiles=[0],spawn={"x":0,"y":0},storage={},access={})
                area["tiles"][0]["exits"]={"return":{"map_id":map_id,"x":state["x"],"y":state["y"]}}
                state["currency"]-=cost
                for p in materials:
                    state["inventory"][p["item"]]-=p["count"]
                    if not state["inventory"][p["item"]]: del state["inventory"][p["item"]]
                exit_name="enter-home-"+suffix[:8]
                data=json.dumps({k:v for k,v in area.items() if k not in ("id","owner")})
                con.execute("INSERT INTO player_maps VALUES (?,?,?)",(area_id,username,data))
                con.execute("INSERT INTO area_claims VALUES (?,?,?,?,?,?)",(map_id,state["x"],state["y"],username,area_id,exit_name))
                self.refresh_area_cache()
                return [f"🏠 Home claimed for {cost} {self.world['economy']['currency']}. The entrance is included at no extra cost.",f"Use go {exit_name} to enter; expand with build expand <direction>."]
            if action == "expand" and len(words)==2:
                area=self.dynamic_maps.get(state.get("map_id"))
                if not area or not self.can_modify_home(area,username): raise GameError("Stand inside a home where you have collaborator access to expand it.")
                delta={"north":(0,-1),"south":(0,1),"east":(1,0),"west":(-1,0)}.get(words[1])
                if not delta: raise GameError("Choose north, south, east, or west.")
                x,y=state["x"]+delta[0],state["y"]+delta[1]
                if not (0<=x<area["width"] and 0<=y<area["height"]): raise GameError("That is beyond the area's authored bounds.")
                idx=y*area["width"]+x; owned=set(area.get("purchased_tiles",[]))
                if idx in owned: raise GameError("That tile is already part of your area.")
                if not any((x+dx,y+dy) in [(i%area["width"],i//area["width"]) for i in owned] for dx,dy in ((0,-1),(0,1),(-1,0),(1,0))): raise GameError("Expansion must connect to an owned tile.")
                if len(owned)>=policy.get("max_tiles",64): raise GameError("You have reached the area's tile limit.")
                target_tile=area["tiles"][idx]; cost,materials=self.building_cost(policy,target_tile)
                if state["currency"]<cost: raise GameError(f"You need {cost} {self.world['economy']['currency']} to expand.")
                for p in materials:
                    if state["inventory"].get(p["item"],0)<p["count"]: raise GameError(f"You need {p['count']} {p['item']} to expand.")
                state["currency"]-=cost
                for p in materials:
                    state["inventory"][p["item"]]-=p["count"]
                    if not state["inventory"][p["item"]]: del state["inventory"][p["item"]]
                area["purchased_tiles"].append(idx)
                area["tiles"][idx].setdefault("exits",{})
                for name,(dx,dy) in {"north":(0,-1),"south":(0,1),"east":(1,0),"west":(-1,0)}.items():
                    nx,ny=x+dx,y+dy
                    if 0<=nx<area["width"] and 0<=ny<area["height"] and ny*area["width"]+nx in area["purchased_tiles"]:
                        area["tiles"][idx]["exits"][name]={"x":nx,"y":ny}
                        area["tiles"][ny*area["width"]+nx].setdefault("exits",{})[{"north":"south","south":"north","east":"west","west":"east"}[name]]={"x":x,"y":y}
                data=json.dumps({k:v for k,v in area.items() if k not in ("id","owner")})
                con.execute("UPDATE player_maps SET data=? WHERE map_id=?",(data,area["id"]))
                self.dynamic_maps[area["id"]]=area
                return [f"🧱 Tile added for {cost} {self.world['economy']['currency']}."]
            if action == "structures":
                catalog=policy.get("structures",[])
                if not catalog: return ["🪚 No player-buildable structures are configured."]
                return [f"{s.get('icon','🪚')} {s['name']} · id: {s['id']} · {s.get('cost',0)} {self.world['economy']['currency']} + "+(", ".join(f"{p['item']} ×{p['count']}" for p in s.get('materials',[])) or "no materials") for s in catalog]
            if action == "place" and len(words)>=2:
                area=self.dynamic_maps.get(state.get("map_id"))
                if not area or not self.can_modify_home(area,username): raise GameError("Stand inside a home tile where you have owner or collaborator access to build.")
                index=state["y"]*area["width"]+state["x"]
                structure=find(policy.get("structures",[])," ".join(words[1:]))
                footprint=self.structure_footprint(area,index,structure)
                tile=area["tiles"][index]
                if sum(len(t.get("structures",[])) for t in area["tiles"])>=policy.get("max_structures_per_area",128): raise GameError("This home has reached its structure limit.")
                per_area=structure.get("max_per_area")
                if per_area is not None and sum(1 for t in area["tiles"] for placed in t.get("structures",[]) if placed.get("id")==structure["id"])>=per_area: raise GameError(f"This home can contain at most {per_area} {structure['name']} structure(s).")
                cost=structure.get("cost",0); materials=structure.get("materials",[])
                if state["currency"]<cost: raise GameError(f"You need {cost} {self.world['economy']['currency']} to build {structure['name']}.")
                for part in materials:
                    if state["inventory"].get(part["item"],0)<part["count"]: raise GameError(f"You need {part['count']} {part['item']} to build {structure['name']}.")
                state["currency"]-=cost
                for part in materials:
                    state["inventory"][part["item"]]-=part["count"]
                    if not state["inventory"][part["item"]]: del state["inventory"][part["item"]]
                tile.setdefault("structures",[]).append({"id":structure["id"],"name":structure["name"],"icon":structure.get("icon","🪚"),"footprint_tiles":footprint,"service_before":{"rest_present":"rest" in tile,"rest":tile.get("rest"),"station_present":"station" in tile,"station":tile.get("station")}})
                for occupied in footprint[1:]: area["tiles"][occupied]["structure_occupant"]=index
                service=structure.get("service",{"type":"decoration"})
                if service.get("type")=="rest": tile["rest"]=True
                elif service.get("type")=="station": tile["station"]=service["station"]
                data=json.dumps({k:v for k,v in area.items() if k not in ("id","owner")})
                con.execute("UPDATE player_maps SET data=? WHERE map_id=?",(data,area["id"]))
                self.dynamic_maps[area["id"]]=area
                shape=structure.get("footprint",{}); size=f" across {shape.get('width',1)}×{shape.get('height',1)} tiles" if shape.get("width",1)*shape.get("height",1)>1 else ""
                return [f"{structure.get('icon','🪚')} Built {structure['name']}{size} for {cost} {self.world['economy']['currency']}." ]
            if action == "remove" and len(words)>=2:
                area=self.dynamic_maps.get(state.get("map_id"))
                if not area or not self.can_modify_home(area,username): raise GameError("Stand inside a home tile where you have owner or collaborator access to remove structures.")
                if words[1]=="tile" and len(words)==3:
                    index,(neighbor,nx,ny)=self.home_tile_removal_target(con,area,state,username,words[2])
                    tile=area["tiles"][index]; cost,materials=self.building_cost(policy,tile); percent=policy.get("tile_refund_percent",0)
                    currency=(cost*percent)//100; state["currency"]+=currency
                    refunded=[]
                    for part in materials:
                        count=(part["count"]*percent)//100
                        if count:
                            state["inventory"][part["item"]]=state["inventory"].get(part["item"],0)+count; refunded.append(f"{part['item']} ×{count}")
                    direction=words[2]; reverse={"north":"south","south":"north","east":"west","west":"east"}[direction]
                    tile.setdefault("exits",{}).pop(reverse,None)
                    area["tiles"][neighbor].setdefault("exits",{}).pop(direction,None)
                    tile["exits"]={}; area["purchased_tiles"].remove(index)
                    data=json.dumps({k:v for k,v in area.items() if k not in ("id","owner")})
                    con.execute("UPDATE player_maps SET data=? WHERE map_id=?",(data,area["id"])); self.dynamic_maps[area["id"]]=area
                    details=f" Refunded {currency} {self.world['economy']['currency']}"+(" and "+", ".join(refunded) if refunded else "")+"." if percent else " No resources were refunded."
                    return [f"🧹 Removed the {self.world['biomes'][tile['biome']]['name']} tile.{details}"]
                index=state["y"]*area["width"]+state["x"]
                if index not in area.get("purchased_tiles",[]): raise GameError("You can remove structures only from purchased home tiles.")
                tile=area["tiles"][index]
                placed=find(tile.get("structures",[])," ".join(words[1:]))
                definition=next((s for s in policy.get("structures",[]) if s.get("id")==placed.get("id")),None)
                refund_percent=policy.get("structure_refund_percent",0)
                currency_refund=(definition.get("cost",0)*refund_percent)//100 if definition else 0
                state["currency"]+=currency_refund
                material_refunds=[]
                if definition:
                    for part in definition.get("materials",[]):
                        count=(part["count"]*refund_percent)//100
                        if count:
                            state["inventory"][part["item"]]=state["inventory"].get(part["item"],0)+count
                            material_refunds.append(f"{part['item']} ×{count}")
                tile["structures"].remove(placed)
                for occupied in placed.get("footprint_tiles",[index])[1:]:
                    if area["tiles"][occupied].get("structure_occupant")==index: area["tiles"][occupied].pop("structure_occupant",None)
                previous=placed.get("service_before",{})
                if previous.get("rest_present"): tile["rest"]=previous.get("rest")
                else: tile.pop("rest",None)
                if previous.get("station_present"): tile["station"]=previous.get("station")
                else: tile.pop("station",None)
                data=json.dumps({k:v for k,v in area.items() if k not in ("id","owner")})
                con.execute("UPDATE player_maps SET data=? WHERE map_id=?",(data,area["id"]))
                self.dynamic_maps[area["id"]]=area
                refund_text=f" Refunded {currency_refund} {self.world['economy']['currency']}"+(" and "+", ".join(material_refunds) if material_refunds else "")+"." if refund_percent else " No resources were refunded."
                return [f"🧹 Removed {placed.get('name','structure')}.{refund_text}"]
            raise GameError("Use build claim, expand <direction>, rename <name>, describe <text>, preview [direction|paint <terrain>|place <structure>|remove tile <direction>], paint <terrain>, structures, place <id>, remove <id|tile direction>, or status.")
        if command in ("storage","store","retrieve"):
            area=self.dynamic_maps.get(state.get("map_id"))
            if not area or not self.can_modify_home(area,username): raise GameError("Home storage is available only to its owner and collaborators.")
            storage=area.setdefault("storage",{}); capacity=self.world.get("building",{}).get("storage_capacity",100)
            used=sum(storage.values())
            if command=="storage":
                contents=", ".join(f"{self.item_icon(item)} {item} ×{count}" for item,count in sorted(storage.items()) if count>0) or "empty"
                return [f"📦 Home storage ({used}/{capacity} items): {contents}","Use store <item> [count] or retrieve <item> [count]."]
            query,count=quantity_arg(argument)
            if command=="store":
                item=find([{"name":name} for name,qty in state["inventory"].items() if qty>0],query)["name"]
                if item in state.get("equipment",{}).values(): raise GameError(f"Unequip {item} before storing it.")
                if state["inventory"].get(item,0)<count: raise GameError(f"You do not have {count} {item}.")
                if used+count>capacity: raise GameError(f"Home storage has room for only {max(0,capacity-used)} more items.")
                state["inventory"][item]-=count
                if not state["inventory"][item]: del state["inventory"][item]
                storage[item]=storage.get(item,0)+count
            else:
                item=find([{"name":name} for name,qty in storage.items() if qty>0],query)["name"]
                if storage.get(item,0)<count: raise GameError(f"Home storage has only {storage.get(item,0)} {item}.")
                storage[item]-=count
                if not storage[item]: del storage[item]
                state["inventory"][item]=state["inventory"].get(item,0)+count
            data=json.dumps({k:v for k,v in area.items() if k not in ("id","owner")})
            con.execute("UPDATE player_maps SET data=? WHERE map_id=?",(data,area["id"]))
            new_used=used+(count if command=="store" else -count)
            return [f"{'Stored' if command=='store' else 'Retrieved'} {count} {self.item_icon(item)} {item}. Home storage: {new_used}/{capacity}."]
        if command in ("help", "?"):
            emote_help = "|".join(item["id"] for item in self.world.get("emotes", [])) or "none configured"
            return [f"look · map · exits · journal · note <text|clear> · track <quest|off> · rest · go <direction> · build claim|expand <direction>|rename <name>|describe <text>|preview [direction|paint <terrain>|place <structure>|remove tile <direction>]|paint <terrain>|place <structure>|remove <structure|tile direction>|structures|status · home access list|grant <player> <visitor|collaborator>|revoke <player> · trade offers|offer <player> <item> [count] for <item> [count]|accept|decline|cancel <id> · contracts · contract accept|deliver <id> · storage · store/retrieve <item> [count] · talk <NPC> · choose <number> · quests [start <name>] · gather <item> [count] · inventory · equip/unequip <item|slot> · inspect <item> · use <item> · shop · buy/sell <item> [at <market-id>] [count] · recipes [name] · craft <recipe> [count] · drop/take <item> [count] · give <item> [count] to <NPC> · interact <entity> [action] · emote <{emote_help}> · stats", "Chain steps with commas or semicolons, e.g. contracts, go east; repeat a step with parentheses. Maximum 50 commands and 20 repeats per step.", "Multiplayer: who · say <message> · tell <player> <message> · quit"]
        if command == "emote":
            emotes = self.world.get("emotes", [])
            emote = next((item for item in emotes if item["id"].casefold() == argument.casefold()), None)
            if not emote:
                raise GameError("Choose an emote: " + (", ".join(item["id"] for item in emotes) or "none configured") + ".")
            return [f"You {emote['verb']} {emote['icon']}."]
        if command == "look":
            tile = self.tile(state)
            area = self.map_data(state.get("map_id", self.default_map_id))
            title = tile.get("name") or self.world["biomes"].get(tile["biome"], {}).get("name") or tile["biome"]
            map_label = f" · {area['name']}" if len(self.world.get("maps", [])) > 1 else ""
            biome=self.world["biomes"].get(tile["biome"],{})
            terrain_description=biome.get("description") or f"A quiet stretch of {biome.get('name',tile['biome']).lower()}."
            room_description=tile.get("description","")
            output = [f"{title}{map_label} ({tile['biome']}) — {state['x']},{state['y']}", terrain_description]
            if room_description and room_description!=terrain_description:
                # Older generated tiles stored the biome's default prose as room text.
                # Suppress that stale default after terrain changes; authored room prose stays visible.
                old_default=re.fullmatch(r"A quiet stretch of .+\.",room_description)
                if not old_default: output.append(room_description)
            output += [f"{structure.get('icon','🪚')} {structure.get('name','Structure')}" for structure in tile.get("structures",[])]
            if "structure_occupant" in tile:
                anchor=area["tiles"][tile["structure_occupant"]]
                structure=next(iter(anchor.get("structures",[])),None)
                if structure: output.append(f"↳ Part of {structure.get('name','a structure')}")
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
            ground = con.execute('SELECT item,count FROM ground_items WHERE map_id=? AND x=? AND y=? AND count>0 ORDER BY item', (state.get("map_id", self.default_map_id), state['x'], state['y'])).fetchall()
            if ground:
                output.append('On the ground: ' + ', '.join(f'{item} x{count} {self.item_icon(item)}' for item, count in ground) + ' (take <item>).')
            output.append('Exits: ' + (', '.join(self.exits(state,username)) or 'none'))
            if tile.get('rest', tile.get('station') in ('campfire', 'inn')):
                output.append('You can rest here.')
            quest = next((q for q in self.world['quests'] if q['id'] == state.get('tracked_quest')), None)
            if quest and quest['id'] in state['active_quests']:
                output += [f"Tracking: {quest['name']}", *self.quest_progress(state, quest)]
            return output
        if command == 'exits':
            return ['Exits: ' + (', '.join(self.exits(state,username)) or 'none')]
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
            coordinate = self.location_key(state.get("map_id", self.default_map_id), state['x'], state['y'])
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
            for coordinate in sorted(set(state['discovered'])):
                map_id, _, coords = coordinate.partition(":")
                if not coords:
                    map_id, coords = self.default_map_id, map_id
                x, y = map(int, coords.split(','))
                area = self.map_data(map_id)
                if not (0 <= x < area['width'] and 0 <= y < area['height']):
                    continue
                tile = area['tiles'][y * area['width'] + x]
                prefix = f"{area.get('name', map_id)} " if len(self.world.get("maps", [])) > 1 else ""
                output.append(f"  {prefix}({x},{y}) {tile.get('name') or tile['biome']}" + (f" — {state['notes'][coordinate]}" if coordinate in state['notes'] else ''))
            for quest in self.world['quests']:
                if quest['id'] in state['active_quests']:
                    output += [f"{'*' if state.get('tracked_quest') == quest['id'] else '-'} {quest['name']}", *self.quest_progress(state, quest)]
            output.append(f"{len(state['completed_quests'])} quests completed. Use track <quest> to pin progress to look.")
            return output
        if command in ('drop', 'take'):
            map_id = state.get("map_id", self.default_map_id)
            source = [{'name': name, 'count': count} for name, count in state['inventory'].items() if count > 0] if command == 'drop' else [{'name': row[0], 'count': row[1]} for row in con.execute('SELECT item,count FROM ground_items WHERE map_id=? AND x=? AND y=? AND count>0', (map_id, state['x'], state['y']))]
            query, count = quantity_arg(argument)
            entry = find(source, query)
            item = entry['name']
            if command == 'drop' and item in state.get('equipment', {}).values():
                raise GameError(f"Unequip {item} before dropping it.")
            if entry['count'] < count:
                raise GameError(f"Only {entry['count']} {item} available.")
            delta = count if command == 'drop' else -count
            if command == 'drop':
                con.execute('INSERT INTO ground_items VALUES (?,?,?,?,?) ON CONFLICT(map_id,x,y,item) DO UPDATE SET count=count+excluded.count', (map_id, state['x'], state['y'], item, count))
            else:
                con.execute('UPDATE ground_items SET count=count-? WHERE map_id=? AND x=? AND y=? AND item=?', (count, map_id, state['x'], state['y'], item))
            state['inventory'][item] = state['inventory'].get(item, 0) - delta
            return [f"{'Dropped' if command == 'drop' else 'Took'} {count} {item}."]
        if command == "map":
            area = self.map_data(state.get("map_id", self.default_map_id))
            map_id = area.get("id", self.default_map_id)
            discovered = set(state["discovered"])
            discovered.add(self.location_key(map_id, state['x'], state['y']))
            occupants = {}
            for peer in sorted(self.online_players()):
                if peer == username:
                    continue
                other = self.load(con, peer)
                if other.get("map_id", self.default_map_id) != map_id:
                    continue
                coordinate = self.location_key(map_id, other['x'], other['y'])
                if coordinate in discovered:
                    occupants.setdefault((other['x'], other['y']), []).append(peer)
            output, visible_biomes = [], set()
            for y in range(area["height"]):
                row = []
                for x in range(area["width"]):
                    if self.location_key(map_id, x, y) not in discovered:
                        row.append("·" if self.presentation.get("map_style") == "icons" else "?")
                        continue
                    biome_id = area["tiles"][y * area["width"] + x]["biome"]
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
                    state["map_id"] = self.default_map_id
                    state["combat"] = None
                    output.append("You are knocked out and carried back to your starting place with 50 HP. Your items and currency are safe.")
            return output
        if command == "go":
            if state.get("combat"):
                raise GameError("You are in combat. Attack or flee before moving.")
            directions = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
            argument = aliases.get(argument.lower(), argument.lower())
            map_id = state.get("map_id", self.default_map_id)
            source_map_id = map_id
            location = self.tile(state).get("exits", {}).get(argument)
            entrance = self.entrance_links.get((map_id,state["x"],state["y"]))
            if not location and entrance and argument == entrance["exit_name"]:
                if not self.home_role(self.dynamic_maps.get(entrance["map_id"]),username): raise GameError("You do not have access to that private home.")
                location={"map_id":entrance["map_id"],"x":0,"y":0}
            if location:
                map_id = location.get("map_id", map_id)
                destination = self.map_data(map_id)
                arrival = destination.get("entry_points", {}).get(location.get("entry_point"), location)
                x, y = arrival["x"], arrival["y"]
            elif argument in directions:
                dx, dy = directions[argument]; x, y = state["x"] + dx, state["y"] + dy
            else:
                raise GameError("Choose north, south, east, west, or a named room exit.")
            destination = self.map_data(map_id)
            if map_id in self.dynamic_maps and not self.home_role(self.dynamic_maps[map_id],username): raise GameError("You do not have access to that private home.")
            if not (0 <= x < destination["width"] and 0 <= y < destination["height"]):
                raise GameError("The edge of the known world blocks your way.")
            tile = destination["tiles"][y*destination["width"]+x]
            if map_id in self.dynamic_maps and y*destination["width"]+x not in destination.get("purchased_tiles",[]): raise GameError("That tile has not been purchased yet.")
            if not self.world["biomes"][tile["biome"]].get("passable", True):
                raise GameError("That terrain is impassable.")
            state.update(map_id=map_id, x=x, y=y, dialog=None)
            coord = self.location_key(map_id, x, y)
            if coord not in state["discovered"]:
                state["discovered"].append(coord)
            travel = f"🚪 You enter {destination.get('name', 'the next area')}." if map_id != source_map_id else f"You travel {argument}."
            return [travel, *self.command(con, state, "look", username)]
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
            area = self.map_data(state.get("map_id", self.default_map_id))
            location = f"{area.get('name', 'World')} · " if len(self.world.get("maps", [])) > 1 else ""
            return [f"{username} · {self.presentation.get('currency_icon', '🪙')} {state['currency']} {self.world['economy']['currency']} · HP {state['hp']}/100 · XP {state.get('experience', 0)} · {len(state['discovered'])} places discovered · {location}({state['x']},{state['y']})"]
        if command == "gather":
            query, count = quantity_arg(argument)
            tile = self.tile(state)
            resources = [*tile.get("resources", []), *self.world["biomes"][tile["biome"]].get("resources", [])]
            resource = find(resources, query)
            key = f"resource:{state.get('map_id', self.default_map_id)}:{state['x']},{state['y']}:{resource['item']}"
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
                self.refresh_market_stock(con,vendor)
                self.refresh_market_production(con,vendor)
                self.refresh_market_consumption(con,vendor)
                output.append(f"{vendor['name']} [{vendor['id']}]")
                for part in vendor.get("stock", []):
                    key = f"stock:{vendor['id']}:{part['item']}"
                    count = self.shared_count(con, key, part["quantity"]) if "quantity" in part else "unlimited"
                    market_state=self._market_decision_state(con,vendor["id"])
                    paused=bool((market_state.get("paused_offers") or {}).get(part["item"],False))
                    base_price=(market_state.get("base_prices") or {}).get(part["item"])
                    buy,sell=self.market_price(part, count if isinstance(count,int) else part.get("pricing",{}).get("target_stock",0),base_price)
                    mode=" · stock-priced" if part.get("pricing",{}).get("mode")=="stock_based" else ""
                    output.append(f"  {self.item_icon(part['item'])} {part['item']}: {'sales paused · ' if paused else ''}buy {buy}, sell {sell}, stock {count}{mode}")
            return output or ["There are no vendors here."]
        if command in ("buy", "sell"):
            query, count = quantity_arg(argument)
            vendors = self.nearby(self.world["economy"]["vendors"], state)
            selector=re.fullmatch(r"(.+?)\s+at\s+([a-z0-9_-]+)",query,re.I)
            if selector:
                query,market_id=selector.groups(); vendor=find(vendors,market_id)
                offers=[{**offer,"vendor":vendor} for offer in vendor.get("stock",[])]
                part=find(offers,query)
            else:
                offers=[{**offer,"vendor":vendor} for vendor in vendors for offer in vendor.get("stock",[])]
                exact=[offer for offer in offers if query.casefold() in (offer.get("id","").casefold(),offer["item"].casefold())]
                partial=[offer for offer in offers if query.casefold() in offer["item"].casefold()]
                if len(exact or partial)>1: raise GameError(f"That item is available at multiple markets; use {command} <item> at <market-id> [count].")
                part=find(offers,query)
            item = part["item"]
            if command == "sell" and item in state.get("equipment", {}).values():
                raise GameError(f"Unequip {item} before selling it.")
            self.refresh_market_stock(con,part["vendor"])
            self.refresh_market_production(con,part["vendor"])
            self.refresh_market_consumption(con,part["vendor"])
            key = f"stock:{part['vendor']['id']}:{item}"
            remaining = self.shared_count(con, key, part["quantity"]) if "quantity" in part else None
            market_state=self._market_decision_state(con,part["vendor"]["id"])
            base_price=(market_state.get("base_prices") or {}).get(item)
            if command == "buy":
                if (market_state.get("paused_offers") or {}).get(item,False): raise GameError(f"The market is holding {item} sales while stock is below its reserve. You may sell some to replenish it.")
                if part.get("pricing",{}).get("mode")=="stock_based" and remaining is None: raise GameError("Stock-based prices require a finite stock limit.")
                level=remaining if remaining is not None else part.get("quantity",0)
                total=sum(self.market_price(part,level-step,base_price)[0] for step in range(count))
                if state["currency"] < total:
                    raise GameError(f"You need {total} {self.world['economy']['currency']}.")
                if remaining is not None and remaining < count:
                    raise GameError(f"The vendor has only {remaining} {item}.")
                state["currency"] -= total; state["inventory"][item] = state["inventory"].get(item, 0) + count
                if remaining is not None: remaining -= count
            else:
                if state["inventory"].get(item, 0) < count:
                    raise GameError(f"You do not have {count} {item}.")
                level=remaining if remaining is not None else part.get("quantity",0)
                total=sum(self.market_price(part,level+step,base_price)[1] for step in range(count))
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
            controller=self._decision_state(con,npc["id"]); active=controller.get("active_task")
            if active:
                task=next((entry for entry in npc.get("decision_policy",{}).get("tasks",[]) if entry.get("id")==active.get("id")),None)
                delivery=task.get("delivery") if task else None
                if delivery:
                    if active.get("assigned_to")!=username:
                        if delivery.get("item")==item: raise GameError("That delivery is assigned to another player.")
                    elif int(time.time())-active.get("started_at",int(time.time()))>=task.get("timeout_seconds",86400):
                        raise GameError("That delivery has expired. Ask the NPC for a new task.")
                    elif item==delivery["item"]:
                        required=delivery["count"]
                        if count!=required: raise GameError(f"This delivery needs exactly {required} {item}.")
                        inventory=self._npc_resources(npc,controller)
                        if sum(inventory.values())+count>self._npc_inventory_capacity(npc): raise GameError(f"{npc['name']} cannot carry that delivery right now.")
                        state["inventory"][item]-=count
                        inventory[item]=inventory.get(item,0)+count
                        effect_output=self.effects(state,task.get("effects",[]))
                        state.setdefault("flags",{})[f"npc-task:{npc['id']}:{task['id']}"]=int(time.time())
                        self.event(state,"deliver",f"{npc['id']}:{item}",count)
                        controller["active_task"]=None; controller["revision"]=controller.get("revision",0)+1
                        controller["task_completions"]=controller.get("task_completions",0)+1
                        controller["deliveries_completed"]=controller.get("deliveries_completed",0)+1
                        con.execute("INSERT INTO decision_owner_state VALUES ('npc',?,?) ON CONFLICT(owner_type,owner_id) DO UPDATE SET state=excluded.state",(npc["id"],json.dumps(controller)))
                        self.npc_runtime_state[npc["id"]]=controller
                        return [f"📦 Delivered {count} {item} to {npc['name']}.",f"✅ Completed {task['name']}.",*effect_output]
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

    @staticmethod
    def market_price(part, stock, base_price=None):
        rule = dict(part.get("pricing", {}))
        if base_price is not None and rule.get("mode")=="stock_based": rule["base_price"]=base_price
        if rule.get("mode") != "stock_based":
            buy = part["price"]
            return buy, part.get("sell_price", buy // 2)
        target = rule["target_stock"]
        factor = 1 + rule["sensitivity"] * (target - stock) / target
        buy = max(rule["min_price"], min(rule["max_price"], round(rule["base_price"] * factor)))
        return buy, max(0, buy - rule["spread"])

    def market_configuration_preview(self, vendor_id, units=5, cycles=3):
        """Quote edited market batches and simulate finite-stock production with runtime rules."""
        vendor = next((entry for entry in self.world.get("economy", {}).get("vendors", []) if entry.get("id") == vendor_id), None)
        if vendor is None:
            raise GameError(f"Unknown market '{vendor_id}'.")
        quotes = []
        for offer in vendor.get("stock", []):
            finite = "quantity" in offer
            starting = offer.get("quantity")
            level = starting if finite else offer.get("pricing", {}).get("target_stock", 0)
            buy = []
            sell = []
            for amount in range(1, units + 1):
                if finite and amount > starting:
                    break
                buy_total = sum(self.market_price(offer, level - step)[0] for step in range(amount))
                sell_total = sum(self.market_price(offer, level + step)[1] for step in range(amount))
                buy.append({"quantity": amount, "total": buy_total})
                sell.append({"quantity": amount, "total": sell_total})
            quotes.append({"item": offer["item"], "stock": starting if finite else None, "unlimited": not finite, "buy": buy, "sell": sell})

        production_offers = [offer for offer in vendor.get("stock", []) if offer.get("production")]
        production = []
        if production_offers:
            horizon = max(offer["production"]["interval_seconds"] for offer in production_offers) * cycles
            maximum_cycles = self.world.get("economy", {}).get("market_simulation", {}).get("max_catchup_cycles", 24)
            with self.connect() as con:
                for offer in vendor.get("stock", []):
                    if "quantity" in offer:
                        con.execute("INSERT OR REPLACE INTO world_state(key,value) VALUES (?,?)", (f"stock:{vendor_id}:{offer['item']}", offer["quantity"]))
                for offer in production_offers:
                    con.execute("INSERT OR REPLACE INTO world_state(key,value) VALUES (?,0)", (f"production-time:{vendor_id}:{offer['item']}",))
                produced_by_item = {}
                self.refresh_market_production(con, vendor, now=horizon, report=produced_by_item)
                for offer in production_offers:
                    item = offer["item"]
                    policy = offer["production"]
                    initial = offer["quantity"]
                    ending = self.shared_count(con, f"stock:{vendor_id}:{item}", initial)
                    produced = produced_by_item.get(item, 0)
                    expected_cycles = min(horizon // policy["interval_seconds"], maximum_cycles)
                    blocked_by = []
                    if produced < expected_cycles * policy["amount"]:
                        if ending + policy["amount"] > policy["max_quantity"]:
                            blocked_by.append("output stock cap")
                        for part in policy["inputs"]:
                            remaining = self.shared_count(con, f"stock:{vendor_id}:{part['item']}", 0)
                            if remaining < part["count"]:
                                blocked_by.append(f"{part['item']} ({remaining} left; {part['count']} needed per cycle)")
                    production.append({"item": item, "starting_stock": initial, "ending_stock": ending, "produced": produced, "cycles": expected_cycles, "inputs": [{"item": part["item"], "per_cycle": part["count"], "remaining_stock": self.shared_count(con, f"stock:{vendor_id}:{part['item']}", 0)} for part in policy["inputs"]], "blocked_by": blocked_by})
        return {"vendor_id": vendor_id, "currency": self.world["economy"]["currency"], "units": units, "production_horizon_seconds": max((offer["production"]["interval_seconds"] for offer in production_offers), default=0) * cycles, "quotes": quotes, "production": production}
