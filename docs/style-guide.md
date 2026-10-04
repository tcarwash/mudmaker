# Mudmaker style guide

Status: design standard for future work. Existing presentation features are a foundation; this document does not imply every described control or rendering mode already exists.

## Product direction

Mudmaker is a modern MUD engine and builder. Its games should feel alive in a terminal: expressive emoji, readable icons, intentional color, attractive maps, clear status displays, and useful motion. The browser builder should make those choices easy to author and preview.

Rich presentation is the intended experience on capable terminals. Preserve the speed, command-driven interaction, scrollback, and readability that make a MUD pleasant to play. Presentation should communicate world identity and gameplay state while keeping commands responsive.

This is an engine-wide standard. Themes, symbols, and effects are saved world configuration with player preferences layered over them. Sample games provide editable examples, not hard-coded renderer exceptions.

## Shared visual language

- Give places, items, creatures, markets, connections, structures, and activities appropriate icons. Prefer one clear icon beside a label over a row of decorative emoji.
- Use a consistent icon for a concept throughout maps, room descriptions, inventory, prompts, menus, and builder controls.
- Use semantic color roles: heading, body, muted, accent, success, warning, error, currency, health, quest, player, and system. Themes assign colors to roles; gameplay code requests roles rather than embedding ANSI sequences.
- Include words or symbols alongside color. A depleted resource, inaccessible entrance, or failed trade must remain understandable in monochrome.
- Use bold for names, headings, and key totals; use dim text for supplementary details only when it remains legible. Avoid blinking text and long passages in all capitals.
- Keep world prose expressive and interface language concise. Responses should say what happened, the amount involved, and the resulting state when useful.

Suggested starter vocabulary, all editable by authors:

| Concept | Rich icon | Plain fallback |
| --- | --- | --- |
| Player position | 🧭 | `@` |
| Other player | 👤 | `&` |
| Market | 🛒 | `$` |
| Home entrance | 🏠 | `H` |
| Travel connection | 🚪 | `>` |
| Inventory | 🎒 | `[Inventory]` |
| Currency | 🪙 | Named currency |
| Crafting | 🔨 | `[Craft]` |
| Quest | 📜 | `[Quest]` |
| Warning | ⚠️ | `[Warning]` |

## Terminal layout

Default to a scrollback-friendly command interface. Use a clear hierarchy: location heading, short description, visible people/services, exits, and optional tracked objectives. Separate sections with whitespace rather than large decorative frames.

Command chaining should feel like a compact builder command palette, not shell evaluation. Accept explicit command separators and bounded repeat suffixes (for example, `go west(3), go north`). Show the expanded step count in help, cap batch size, and apply every step through ordinary game rules. Never pass chained input to a shell or evaluate it as code.

Illustrative room output; theme colors and emphasis are applied by the renderer:

```text
🌲 Willow Crossing · Riverlands
A timber bridge crosses the slow green river.

🛒 Market: Bridge Exchange   🏠 Home: Rowan's Workshop
👤 Nearby: Mira
Exits: north · east · enter workshop

📜 Timber delivery: 4/6 delivered
```

Use a compact prompt with configurable fields. A useful default shows player name, health, currency, and current area, with optional equipped icons. Provide a shorter prompt for narrow terminals and allow players to hide fields.

```text
Rowan · HP 84/100 · 🪙 240 · Willow Crossing >
```

Keep long descriptions wrapped to terminal width. Adapt tables to the available columns; on narrow screens, switch to labeled rows instead of clipping values. Never depend on one exact window size or terminal font. Wide dashboards and persistent status panels may be optional modes later; the core command interface must work independently.

Show actionable errors: `You need 20 more coins to buy this tile.` Include valid choices for ambiguous targets. Make help, exits, and available actions discoverable without requiring players to memorize identifiers.

## Maps and emoji width

Offer rich icon, Unicode symbol, and ASCII map modes. Use terrain color plus landmarks and a concise legend. Show current position, discovered players, markets, entrances, and connections without exposing undiscovered content.

Emoji can occupy different cell widths across terminals. Measure terminal display columns, not string length. Use a display-width-aware library and test grapheme clusters, variation selectors, and wide glyphs. Render icon maps with fixed-width padded cells; if a chosen glyph cannot fit reliably, use its configured symbol fallback. Keep entity names out of dense map cells and place them in the legend.

Do not require custom fonts, Nerd Fonts, image protocols, or private-use glyphs. Such features may be opt-in enhancements with portable fallbacks. Emoji should work in ordinary modern terminal fonts.

The builder should preview rich and fallback maps and warn about unsuitable map glyphs. Color alone must not distinguish the player from terrain or an occupied tile from an available tile.

## Markets, inventory, and building feedback

Make numbers easy to scan. Align quantities and currency totals using measured display width. Label prices from the player's perspective: **You pay** and **You receive**. Distinguish unit price from total, especially when stock-based pricing changes during a batch.

```text
🛒 Bridge Exchange
Item          Stock   You pay   You receive
🪵 Timber        24        12             9
🔧 Tools          8        35            26

Bought 3 timber for 🪙 36. Balance: 🪙 204.
```

Group large inventories by authored category and support concise summaries with details on request. Show counts, equipped state, and capacity when enabled. Use the same item icons across trading, crafting, storage, and rewards.

Before land purchases or construction, show the proposed tile/structure, exact total cost, required materials, and resulting area size. The first tile's preview should explicitly say its entrance is included at no extra cost. Unaffordable or blocked placements must explain the reason using text and visual state.

Combat and quest updates should emphasize meaningful changes: damage, defeat, rewards, objective completion. Avoid repeating an entire dashboard after every action. Longer output should support paging or explicit detail requests without trapping the player in a modal flow.

## Animation and live updates

Use animation where it conveys activity or a meaningful transition: a travel progress indicator, an active crafting task, a brief reward accent, or a builder placement preview. Static output remains appropriate for ordinary command responses.

Terminal motion should be restrained and event-driven. Start with at most 4–8 frames per second for small progress indicators and reduce updates when nothing changes. Motion must never delay results, sleep a command handler, change game timing, or generate a new scrollback line for every frame. Avoid mandatory typewriter effects; descriptions appear immediately by default.

Animate only a renderer-owned status region when terminal capabilities and input handling permit it. Keep one output coordinator per session so animation, chat, and command responses cannot overwrite each other. Preserve the player's partially typed command and redraw the prompt after asynchronous messages. Cancel animation on completion, disconnect, or preference changes.

Provide a no-motion mode and a static equivalent for every effect. Progress must use the authoritative engine state rather than inventing a duration for decoration. Reduced-motion or plain-output clients receive concise progress/completion messages.

Browser transitions should generally take 120–200 ms. Use subtle fades and placement highlights; avoid continuous decorative motion. Honor `prefers-reduced-motion`, preserve focus, and keep critical results visible until the user can read them.

## Capabilities and player preferences

Keep capability detection conservative and allow explicit overrides. `TERM` is a hint, not proof that emoji, truecolor, or cursor controls work. Respect `NO_COLOR`, dumb terminals, terminal resize, and noninteractive output.

| Setting | World author controls | Player/session controls |
| --- | --- | --- |
| Theme | Semantic palette, map terrain colors, heading styles | Default theme or high-contrast override |
| Icons | Entity icons and symbol fallbacks | Emoji, Unicode, or ASCII |
| Color | Suggested mode and palette | Auto/on/off, with environment constraints honored |
| Motion | Approved effects and suggested intensity | Auto/on/off or reduced motion |
| Layout | Prompt fields, room sections, map defaults | Compact/comfortable, width-aware output |
| Detail | Default summaries and optional sections | Output verbosity and paging preferences |

Persist player preferences separately from authored content. Effective rendering combines world configuration, player choice, and actual session capabilities. New worlds should prefer rich icons and automatic color; terminal animation should default to automatic with conservative detection. Existing world settings remain intact.

## Browser builder

Make the workbench visually inviting and practical: generous readable text, consistent spacing, clear selection states, icons on important controls, semantic color, and short responsive transitions. Keep dense data editors legible; do not shrink labels to make a panel fit.

Use a consistent UI icon set for application controls, with text labels or accessible names. Use authored emoji for game content so authors can distinguish application actions from the world they are editing. Tooltips supplement labels; essential actions cannot rely on hover.

Extend **Style & plugins** with discoverable theme, icon, motion, prompt, and layout settings. Entity and biome editors expose local icon/color/fallback settings; the style panel owns shared defaults. Avoid duplicate controls with divergent saved values.

Provide a live terminal preview with narrow/wide widths, emoji/ASCII modes, color on/off, and motion on/off. Include room, map, inventory, market, combat, chat, and building examples. Browser playtest and SSH output must share presentation semantics and content; browser-only enhancements must not conceal a poor terminal experience.

Support keyboard navigation, visible focus, descriptive accessible names, adequate contrast, and comfortable pointer targets. Treat the map as the central build surface: authors can start any content type there, and spatial content is placed on a selected tile. The map editor has a visible tile cursor that moves with arrow keys, while Enter selects the focused tile and Escape cancels placement. Explain map placement and connection actions inline; authors should not need to infer that location changes require editing JSON. Show invalid fields beside their controls. Save/reload must preserve presentation settings, and export validation must catch missing or invalid presentation references.

## Rendering architecture and content safety

Separate gameplay results from presentation. Prefer structured messages and semantic roles that both browser and terminal renderers understand. Keep persistence and rules independent of color and animation. Migrate current string responses incrementally; do not create a second game engine for styled output.

Treat authored text, player chat, names, and plugin output as content at rendering boundaries. Strip or escape terminal control characters so they cannot move the cursor, change the terminal title, or trigger escape-sequence features. Only the renderer emits approved terminal controls. Escape browser text as text, not raw HTML.

Store theme and effect definitions with the world; validate them and include them in exports. Effects use a bounded supported vocabulary and parameters. Terminal output should never depend on an external asset download or model service.

## Acceptance checks

- Exercise room output, maps, markets, inventory, combat, travel, chat, and home building in a real SSH terminal.
- Check narrow and wide windows, live resize, light and dark terminal backgrounds, and long names.
- Verify emoji alignment and ASCII/monochrome fallbacks; plain mode must contain no ANSI controls.
- Verify that chat and motion preserve typed input and do not corrupt output under concurrent updates.
- Check no-motion settings, keyboard-only builder use, readable contrast, and browser reduced-motion behavior.
- Save, reload, playtest, and export a custom theme with entity overrides. Confirm the exported game uses the same settings.
- Add focused renderer tests for display width, control-character sanitization, semantic roles, capability fallbacks, and preference persistence. Capture browser screenshots and manually inspect real terminal output; snapshots alone do not establish a good terminal experience.

Presentation work is complete when it looks good and remains responsive through the real builder, playtest, and exported SSH paths.
