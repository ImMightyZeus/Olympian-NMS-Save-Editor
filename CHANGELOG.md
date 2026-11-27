# Changelog

## 0.10.0 — 2026-11-27 — exact values, auto-sort, imported saves, Russian, Linux/macOS

### Values like the game
- **Exact value of each procedural upgrade** in its tooltip (instead of the possible range), rounded and shown the
  way the game does (supercharged, no adjacency): ships, exosuit, freighter, multi-tools and exocraft.
- **Real ship values:** Damage Potential, Shield Strength, Hyperdrive Range and Manoeuvrability as on the game's
  ship screen (base + modules + upgrades + adjacency + supercharged + class). Each value has a tooltip with what its
  upgrades add. Corvettes show shield and hyperdrive (both match the game); their damage and manoeuvrability are
  left out until they match too.
- Stat names come from the game's own language tables (English, Spanish, Russian).

### Ships
- **Auto-sort technology** (⇅ beside "Supercharged cells"): choose one or two priorities for the supercharged cells
  (damage, hyperdrive, shield, pulse engine); the rest is placed for adjacency. Table before → after, preview of the
  moves. With "damage" it recommends the weapon that gains the most. Also for corvettes, of any class.
- **Cargo "Sort by…"** (⇅ beside the cargo grid): whatever recharges the installed shield goes first (explained the
  first time).
- **Copy modules from another ship** now brings only technology the destination type accepts: sentinel and living
  ship technology becomes its equivalent (Sentinel Cannon → Photon Cannon…) or stays out when the ship already has it
  or there is none. The preview lists both.
- **Fix technology:** a ship that already holds technology its type does not accept (for example after copying
  modules from a sentinel with an earlier version) shows a warning and a button that corrects it the same way.
- **Delete selected** (click items of the open ship to select them), with the preview as confirmation.
- **Corvettes:** rename, repair, recharge, enable cells, **expand to the S maximum** (cargo 120, technology 60 once
  its 4 supercharged cells are there, identical to what the game does), auto-sort, and **delete as the game does**
  (the corvette, its inventory and its build base go; the editor index is left as the game leaves it).
- The bonus table and the class-bonus box were removed from the ship card (the compact class bonus stays in the list).

### Inventories
- **"📥 Move here all"** (right-click an item): every stack with exactly that ID (procedural ones with their seed)
  comes from all other inventories, within the game's stack limits.
- **Expand all / Collapse all** beside the search box.
- **Delete selected:** the selected items of one or several inventories in one change; core and corvette technology
  stay. Items in technology grids can be selected too.
- **Drag several items:** dragging one of several selected items moves them all to the other inventory.

### Settlements
- Class shown as a game-style shield (C green … S); the class a building will have while it is built.
- **Green check when the upgrade or construction timer has finished** (start time + the game's building time);
  the gear moves to the bottom-right corner at double size; half-built lots show as "under construction" or "next
  stage ready".

### Summary, Travel and look
- Reputation as banners (rank and the three medals); **medals and milestones** from the game's journey tables
  (read only); pending changes and history in their own full-width box; rename pencil in the header.
- Travel: portal glyphs with the game's icons; **✔ on galaxies you have visited** (deduced from discoveries,
  portals, teleporters and bases).
- Game-style tooltips everywhere (mouse and keyboard) and a look closer to the game; "I live alone" is more visible.
- **Undo / redo** per pending change (buttons and Ctrl+Z / Ctrl+Y).

### Imported saves
- Their own space for saves of other players (Steam folder or .zip, Xbox/Game Pass `wgs` folder), apart from
  yours; open, view only or allow changes, export in Steam format with install instructions, and **write back to
  Xbox / Game Pass** for its owner (backup of the `wgs` folder first, refused if the game saved that slot since).

### Platforms and languages
- **Russian** interface and game content; README and FAQ in Russian.
- **Linux** (Steam/Proton, Flatpak, Steam Deck) and **macOS** save folders, settings and launchers
  (`run_olympian_nmsse.sh`, `.command`). Not yet tested on a real machine with the game.
- `tools/gamedata_check.bat`: checks the game data tools (.NET, MBINCompiler, HGPAKtool, Pillow) against their latest
  releases, optionally downloads what is missing (MBINCompiler verified by SHA-256), and times a full rebuild of the
  game data and icons in a temporary folder. Nothing in the game or in `olympian/data` is changed.
- `tools/build_gamedata.py` reports the time of each step and also reads shield recharge items, journey/medal
  tables, building times and the roll tables used for exact values.

### Fixes
- Techstats no longer needs `numpy` (the first preview failed on Windows).
- "State when loaded" showed untranslated states (aboard the fleet, Nexus, vehicle).

## 0.9.0 — 2026-11-20 — protection, backups, ships overhaul, richer summary, settlement lots

### Safety
- **Protection (optional):** PIN + time-based code (TOTP, RFC 6238) from any authenticator app, asked when the app
  opens and again before destructive actions (delete a save or a ship, copy or restore over an occupied slot, move
  a ship to another save, purge backups). Offline; the PIN is kept only as a PBKDF2 verifier, the secret is
  encrypted with a key derived from the PIN; eight one-time recovery codes; 30-second pause after five wrong
  attempts. Offered the first time the app opens, saying which features stay disabled without it; Settings can
  turn it on or off at any time.
- **"I live alone"** (Settings, with confirmation): deleting a save and copying over an occupied slot work without
  PIN or code. Without protection or this option, both stay disabled. The backup first, the preview and the
  closed-game check always apply.
- **Manual save first:** every slot has an autosave and a manual save (written when leaving the ship or at a save
  point). The views show the manual save and every edit goes to it; the autosave is edited too only when it holds
  the same data, otherwise it is left as it was and the preview says so. Cell order no longer counts as a
  difference. Applies to Inventories, Ships and Settlements.

### Start and backups
- **Delete a save** (both files of the slot; a full backup first; `accountdata.hg` never touched).
- Disk space used by backups and **purge**, which always keeps the newest 10 automatic backups and never touches
  restore points made by hand.
- Optional **extra backup folder** outside the save folder; every backup and restore point is also copied there,
  and Start lists the copies that only exist there (they can be restored too).
- **Top bar:** a button with the game's base computer icon goes back to Start, asking about pending changes first.

### Summary
- The save card lists its details with icons (the game's own where it has them): location, state, ships, play
  time, race chosen at the Appearance Modifier, units, nanites, quicksilver and reputation (races, guilds,
  outlaws, Autophage).
- A "Ships and gear" row: primary ship, freighter, primary exocraft, corvettes and the multi-tool in hand; ships,
  freighter and exocraft open their view.

### Ships
- **One header** for the open ship: emblem, name, type and class, a pencil to rename and the actions as icons with
  tooltips (repair, enable cells, recharge, expand), greyed out when they do not apply.
- **Repair** one module (right-click, also in exosuit, freighter and multi-tool grids) or all of them, setting what
  the game's own repair sets (no damage, fully installed) and nothing else. **Damaged cells** of salvaged or
  crashed ships are repaired as in the game: the damaged component goes away and the cell is left free. Damaged
  technology is marked with a red frame and "!"; damaged cells are no longer drawn as supercharged.
- **Enable closed cells** is a separate action. **Expand** grows cargo to the class maximum, and technology to 60
  cells when the ship already has its 4 supercharged cells (they stay where the game put them).
- **Charge** of each technology in its cell (percentage and bar); partly charged ones are marked and named, and the
  recharge preview lists every cell it fills.
- **Adjacency:** technologies of the same family that touch are framed in one colour, as in the game; two
  different groups that touch never share a colour. On a supercharged cell both the frame and the gold ring show.
  Warp drive modules (cadmium, emerald, indium) are not linked to the hyperdrive, as in the game.
- **Technology tooltips:** subtitle, class, the game's description and, for upgrades, the stats they can improve
  with the game's range (the exact value of each module is not stored in the save).
- **Copy or move a ship to other saves** (pick the destination, then Copy or Move), with everything in its
  inventories, into the first free ship slot of each destination; byte for byte and verified, with a backup first.
  Corvettes and ships with a custom look kept apart are not copied; the primary ship cannot be moved.
- **Seed:** copy the seed of another ship of the same model; **seed history** per ship (original first, in
  `%APPDATA%\OlympianNMS\seeds.json`): one click goes back to any of them, ◀ ▶ rotate between them.
- Corvette technology cannot be uninstalled (it comes from its parts). Right-click on ship cells works like in
  Inventories.

### Inventories
- "Move here all the…" also into the freighter and exocraft.

### Layout
- Ship and inventory grids are proportional: cells grow and shrink with the window. The technology text sits under
  its grid and the cargo grid carries its own cell count; settlement lots fill their column.

### Settlements
- A grid of the 48 lots: buildings with their class, a gold cog on upgrades in progress, a green check on upgrades
  whose timer ran out and wait to be completed in the game, new buildings under construction with their start
  time, "not activated yet" for lots the game has not granted. States deduced from real saves.
- The type of each lot is learned when the game names it in its next upgrade, also reading the older copies in
  backups and restore points once (kept in `%APPDATA%\OlympianNMS\lots.json`). Read only.

### Missions
- Missions that share a title are grouped; the stage the save keeps is shown as "saved stage N", since a finished
  mission can stay at any stage. Settlement routes count as finished only with the game's mark.

### Languages and game data
- The game's sentences (advisor, judgements, objectives) follow the interface language; names of items,
  buildings, perks and missions keep following the game-terms language.
- Game data regenerated with technology subtitles, descriptions and upgrade ranges.

### Package and docs
- Credits, AI disclosure and "What Olympian NMS Save Editor does and doesn't do" follow the MightyZeus standard 1.3
  (README in English and Spanish, Settings → About, source header). README lists the other MightyZeus projects for
  No Man's Sky.
- The zip's folder carries the version in its name. README: what's new, what Olympian does and does not do, how
  to update; FAQ: sync conflicts, protection, recovery, updating.

## 0.8.0 — first public release
- The program is now `olympian` everywhere (it started as "Zeus"). Folders and files use English names:
  `_OlympianBackups`, `_OlympianRestorePoints`, `history.json`, `%APPDATA%\OlympianNMS`. Folders and settings of
  earlier versions (`_ZeusRespaldos`, `_ZeusPuntos`, `historial.json`, `%APPDATA%\ZeusNMS`) are migrated
  automatically; no backup is lost.
- README, FAQ (English and Spanish), screenshots, GPL-3.0 license.

## 0.7.0 — settlements, exocraft, technology grids, JSON expert mode
- **Settlements**: only the player's own (by owner ID). Rename, remove existing perks, dismiss the pending
  decision. Stats and constructions are never edited. Production is shown, not collected (the save keeps no
  accumulated amount).
- **Advisor** from the game's settlement data: debt with the game's own message and tip, stats at a poor level by
  the game's thresholds and which perks cause them, negative perks, pending decision, construction in progress.
  Settlement mission routes (A Trace of Metal, The Harmonic Seal…) with the player's progress.
- **Inventories**: exocraft cargo; technology of the exosuit, freighter and every multi-tool (install,
  uninstall, move). The multi-tool in hand is written to both of its copies in the save. Core technology cannot
  be removed, as in the game.
- Upgrade modules use their template technology's icon, as the game does (corvette modules included).
- **JSON viewer expert mode**: change one text / number / yes-no value, same type, one file, verified.

## 0.6.x — missions
- Every mission with its in-game name, group and state; reset (with the rest of its series), delete, change stage
  (experimental). Resetting the active mission switches the game to a neutral one first.
- "What to do now / next" from the game's own objective texts.
- 0.6.3: application icon, desktop shortcut created by the launcher.

## 0.5.0 — JSON viewer
- The whole save as a tree with readable key names, item names, search, notes, dependencies and export.

## 0.4.x — game data and inventories
- Game data read from the player's own game files: item names, icons, rechargeable technology, stack limits.
- Unified inventories view: drag between inventories, merge stacks, sort by name / rarity / value, "move here all
  the…", search with totals, rename containers, split / fill / duplicate / delete stacks, add items with the
  game's rules (ship technology included). Stored upgrade modules with binary IDs are decoded.

## 0.1 – 0.3 — foundation and ships
- Safe read/write of the PC save format, automatic backups, restore into any slot, copy slots, editing session
  with pending changes and Save, galaxy travel, ship order and primary ship, interface and game-term languages.
- Ship options: rename, repair, recharge, expand cargo, seed, copy modules, duplicate, delete; supercharged slots
  map; base stats. Corvettes and living ships protected.
