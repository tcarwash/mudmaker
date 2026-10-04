# Map builder guide

Use the **World map** view to edit a location or put content directly where it belongs. Changes save to the world and are included in playtests and game exports.

## Move around the map

- Click a tile to select it. The dark outline is the map cursor; the Location inspector shows the selected tile.
- For keyboard navigation, press **Tab** until the starting tile is focused, then use **← ↑ → ↓**. Press **Enter** to select a tile. Only the current tile is in the normal tab order; the arrow keys move the focus across the map.
- Choose **Inspect** to select tiles without painting. Choose **Paint**, then choose a terrain button and click or drag over tiles to paint them.
- Set a tile's name, room-specific description, rest service, and home-entrance eligibility in the Location inspector. Leave its room description blank to use the current terrain's description.

## Create and place world content

1. Open **World map → ＋ Create / place content**. Choose any authored type. Spatial content—characters, markets, creatures, interactables, rooms, and descriptions—asks you to select a tile with the pointer or map cursor. Nonspatial content opens its structured editor directly from the map page.
2. You can also create or edit a character or market from its content list. Its editor shows a terrain-colored mini-map: click a tile, or focus the grid and use arrow keys followed by Enter. Switch maps with the selector when the world has multiple maps. The selected tile, map, and coordinates stay synchronized.
3. Numeric coordinates are available under **Enter coordinates directly (advanced)**. NPC name, role, and dialogue have direct fields. Market offers and their inventory/pricing rules are edited in the market editor.
4. To move an existing spatial entity, open its content list, edit it, and choose another tile in the mini-map. Numeric coordinates remain available in the advanced section. The map tile marker and hover text show placed content.

Quests, crafting recipes, items, dialogue, biomes, and world settings can also be started from the map page. Advanced fields remain available in the clearly labeled JSON scaffold; routine placement and core NPC details do not require it.

## Connect maps and add exits

1. Choose **＋ Submap** to create an interior, town, or other map. If a tile is selected, the builder connects that tile to the new map and adds a return exit.
2. Select the tile that should contain an exit. In the Location inspector, choose **🔗 Connect map / add exit**.
3. Add an exit label, destination map, and arrival preset or coordinates. **Save connections** attaches the exits to that tile. Use **Edit resources & station** only for tile-specific resources, exits in the advanced scaffold, and crafting stations.
4. Use the map selector to switch maps. The same coordinates on different maps are separate locations.

For a one-way route, create the exit only at its source. To make travel reciprocal, add an exit at the destination back to the original map and tile. Validate and playtest both directions before exporting.

## Save, undo, and resize safeguards

Editing or saving a submap preserves the runtime default map and its spawn. Undo/redo retains history when you switch maps and returns to the map/tile where the edit happened; entity placement and editing are included. Entity editor Cancel discards its draft.

Resize reviews the selected map, its named arrivals and spawn, placements, decision/quest locations, and incoming connections. Cropped authored text or services also block shrinking. Move or remove listed content first; the builder does not silently move spawns or discard it. Ordinary terrain on a new submap inherits its biome prose until you write a room description.

Run `node tests/browser/map-state.test.cjs` for map-state regression checks and `.venv/bin/python scripts/verify_gui.py` for the browser authoring/export walkthrough.

## Market inventory forms

Market editors expose Add item offer, item-name suggestions, starting stock (blank for unlimited), fixed purchase/sale prices, stock-based bounds and sensitivity, and optional scheduled restocking, production, and demand. Production ingredient rows suggest this market’s finite-stock offers and configure quantities per cycle; intervals and output caps are explicit. Stock-based pricing and restocking require finite starting stock. Validation errors stay visible beside the inventory editor. Expand Advanced inventory JSON for extra fields; valid JSON updates the rows, and subsequent form edits preserve extra fields.

## Placement palette and tile content

Use the persistent Place on map palette for characters, markets, creatures, interactables, rooms, and descriptions. Choose a type, then a tile. Escape cancels; switching to Inspect or Paint also cancels placement. The full Create / place content menu remains available for other authored types.

Select a tile to see every authored object in Content here, including objects sharing the same location. Edit / move opens that object’s editor and minimap picker; Duplicate opens a separate draft. Cancel leaves the original object unchanged.

## Resource and station editor

Select a tile and choose Edit resources & station. Add resource rows with an item name and positive available-stock quantity, select or enter a recipe station ID, and configure resting. Save applies the complete service draft; Cancel discards it. Resource and station suggestions come from authored content. Extra fields on existing resources are preserved. Advanced location JSON remains available below this control.

## Find content and simplify the map

Find on this map searches authored object/place names, stable IDs, descriptions, resource items, station IDs, and exit labels. Results belong only to the active map; choose a result to focus its tile and open the tile inspector. Search also works for legacy objects whose omitted map ID means the default map. Large result sets show the first 30 matches.

Map layers can show/hide object and connection markers or reveal resources and services. Layers affect the view only. Hidden objects remain listed in the selected-tile inspector and remain saved/exported. Search and layer settings are session UI state.

Run `node tests/browser/map-search.test.cjs` for search isolation and reference coverage.
