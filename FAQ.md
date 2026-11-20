# Olympian NMS Save Editor — FAQ

[Leer en español](FAQ.es.md) · [Back to README](README.md)

### Is it safe to use on my main save?
Olympian is built around not losing your progress: nothing is written until you press **Save**, a backup of the slot is made before every write, and every change is verified before and after writing. Still, editing saves is never risk-free — for big changes, try them first on a copy (*Start → Copy to another slot*).

### How do I undo a change?
- Before saving: **Discard changes** at the top.
- After saving: **Summary → History (last 10 changes)** lists the last changes with "go back to before this change", or restore any backup from the **Start** screen (*Restore to…*). Restoring also brings back the state of the game at that moment, so anything played after it is lost.

### Why does it ask me to close the game?
The game keeps the save in memory and overwrites it when it saves again, so changes made while it is running would be lost or mixed. Olympian checks whether `NMS.exe` is running and refuses to write if it is.

### I use Steam Cloud. Anything special?
After saving with Olympian, start the game on **this** PC first, so Steam uploads the new version. If you play on another PC first, Steam may bring back the old save.

If Steam shows a **sync conflict** after editing on this PC, **always choose the local copy** (this PC's): the cloud one is from before the edit and would replace it. This holds when the last thing you did was edit here; if you played on another PC afterwards, the newer one is in the cloud. Hello Games cross-save works much the same: start the game first on the PC where you edited. Either way, Olympian made a backup before the change.

### Where are my backups?
In your save folder: `_OlympianBackups` (automatic, one before every save, plus `history.json`) and `_OlympianRestorePoints` (the ones you make). Start and Summary show how much space they use. Olympian never deletes them on its own: the **Purge backups** button deletes old automatic backups only when you ask, and always keeps the newest 10 and every backup you made.

In Settings you can choose an **extra folder** (another drive, a USB stick, OneDrive…): every backup is also copied there, so it survives even if the save folder is deleted.

### What does the PIN and code (2FA) protection protect?
It is optional (Settings). Opening Olympian asks for your PIN and the 6-digit code of an authenticator app (Google Authenticator, Microsoft Authenticator, Authy…), and the code is asked again to delete saves or ships, copy or restore over an occupied slot and purge backups. It works offline and without an account: the app and Olympian share a secret once through a QR code. The PIN is stored only as a verifier and the secret is encrypted with your PIN.

Without protection, deleting saves and copying one over another stay disabled, unless you tick **“I live alone”** in Settings (for people who do not share the PC). A full backup is always made first.

Its limit, plainly: it stops people from using Olympian on your PC, but it does not stop anyone from deleting or changing the saves in File Explorer or with another editor. Lock Windows (Win+L) when you step away and use the extra backup folder.

### I forgot the PIN or lost my phone.
Use one of your **recovery codes** (the ones Olympian gave you when you turned protection on) on the lock screen. Each one works once; then set up protection again or turn it off in Settings. If you do not have the codes either, close Olympian and delete `%APPDATA%\OlympianNMS\lock.json`: protection is turned off (your saves and backups are not touched).

### How do I update Olympian without losing anything?
Extract the new version into its own folder (its name carries the version), open it and, once it works, delete the old one. Settings, protection, learned lots and backups live outside the app folder. The first time, the new version prepares its environment (1–3 minutes).

### I used an older version and had `_ZeusRespaldos` / `_ZeusPuntos` folders.
They are moved to the new names automatically the first time you open that save folder. Nothing is lost; if a folder is in use the move is retried the next time.

### Windows SmartScreen or my antivirus warns about the `.bat`.
The launcher is not code-signed. *More info → Run anyway*. You can read `run_olympian_nmsse.bat` — it only finds or installs Python, prepares a local environment in `.venv` and starts the app. The app listens only on `127.0.0.1` and does not use the internet.

### Do I need to install Python?
No. If no suitable Python is found, the launcher installs the official Python 3.13 for your user with `winget` (Windows may ask for permission). If an existing Python 3.13 is broken, it tells you how to repair it.

### The game was updated. Will Olympian break?
Olympian reads the save format generically, so most updates do not affect it. The game data (item names, stack limits, missions…) can become outdated: **Settings** shows it. New items then appear by their ID until the data is rebuilt with `tools/build_gamedata.py`.

### Why can't I change a ship's class, settlement stats or finish a construction?
These were left out on purpose. Class changes don't recalculate the ship's stats the way the game does, and editing settlement stats or skipping constructions removes the part of the game those systems are about. Olympian sticks to changes that keep your save consistent.

### Why doesn't Olympian collect settlement production?
The save does not store an accumulated amount (it reads 0 in real saves); the game works it out from its own timers when you talk to the overseer. Collecting it in the editor would mean inventing amounts, so Olympian shows what each settlement produces and leaves collecting to the game.

### What does "dismiss decision" do in Settlements?
It removes the pending decision without applying any option or reward. The game then brings the next one after its usual wait (15 to 120 minutes, per the game's data). Resolving it in the game is what gives its rewards.

### Some technology can't be uninstalled.
Core technology (life support, hazard protection, jetpack, hyperdrive…) cannot be dismantled in the game either, so Olympian doesn't allow it.

### What is the JSON viewer's expert mode?
A way to change a single text, number or yes/no value by hand. It keeps the value's type, never adds or removes fields, changes only the file you are viewing, and goes through the same verification as every other change. Use it only if you know what the value means.

### Why do some items show an ID instead of a name?
They are items the current game data doesn't know (usually from a newer game version) or procedural items whose name the game generates at run time. They are still moved and kept correctly.

### Does it work with Xbox / Game Pass, PlayStation or Switch saves?
No. Only the PC (Steam / GOG) save format is supported.

### Can I get banned for editing my save?
Olympian only edits local save files. No Man's Sky has no known bans for save editing, but in multiplayer other players see what you bring, so use common sense.

### Something went wrong. What should I send?
Open an [issue](../../issues) with the content of `olympian_log.txt` (next to the `.bat`) and what you were doing. If a save doesn't load, restore its backup from the Start screen first.

### How can I support Olympian?
It is free and always will be. Donations are welcome at [paypal.me/ImMightyZeus](https://paypal.me/ImMightyZeus); reporting bugs and sharing the project also help a lot.

### Who made Olympian?
MightyZeus, from Nicaragua 🇳🇮. See the credits in the [README](README.md#credits).
