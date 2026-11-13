# Changelog

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
