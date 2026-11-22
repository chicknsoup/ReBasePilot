# BaseLooter

**Autopilot for your Clash of Clans base.** BaseLooter farms, upgrades, opens chests, and knows when to idle — it runs unattended until your village genuinely has nothing left to start, then waits for a builder to free up and gets back to work.

Windows desktop app for **Clash of Clans** on **MuMuPlayer 12 / 15 Global** and **Google Play Games (PC)**. It plays the game the way a person does: screen capture, computer vision, and touch/click input. No memory reading, no packet manipulation, no modified client.

**[Download the latest release](../../releases/latest)** — a single standalone `BaseLooter-v*.exe` with templates, assets, and Tesseract OCR bundled in, so there is nothing to install. See [Running & building from source](#running--building-from-source) to run or build it yourself.

![BaseLooter running beside Clash of Clans](docs/basepilot-screenshot.png)

---

## Supported Emulators

| Emulator | Input & Capture Mode | Background / Minimized | Recommended Settings |
|---|---|---|---|
| **MuMuPlayer 12 / 15 Global** *(Recommended)* | Native MuMu IPC (`external_renderer_ipc.dll`) — direct frame capture & multi-touch/pinch | **Yes** — continues capturing and clicking even when minimized or behind other windows | **16:9** (`1600×900` or `1920×1080`), **240 DPI** or **320 DPI**, Graphics Renderer: **Vulkan** *(recommended on AMD GPUs to avoid OpenGL driver crashes)* |
| **Google Play Games on PC** | Windows GDI/BitBlt capture & mouse input | Window must stay visible (not minimized) | **16:9** or **16:10** aspect ratio |

### Emulator Setup Tips

- **MuMuPlayer (12 / 15 Global):**
  1. Set display to **16:9** (e.g. `1600×900`, `240 DPI` or `320 DPI`). BaseLooter automatically calibrates UI DPI scaling (`1.0×` / `1.22×`) on first frame.
  2. In MuMu *Settings → Graphics*, choose **Vulkan** (more stable than OpenGL on AMD Radeon drivers).
  3. Open *Settings → Game window* in BaseLooter and click **Auto-detect** (or pick `Android Device` / `nemuwin`) and press **Test** to verify capture.
- **Google Play Games on PC (Ultrawide / 21:9 monitors):**
  - Google Play Games locks the game's aspect ratio to your display resolution at launch. Use *Settings → Switch display to 16:9*, fully close and reopen Clash of Clans, then click *Restore my display* — the running game keeps 16:9.

---

## What it does

- **Smart Farming (Home Village & Builder Base).** Finds matches, skips low-loot bases, deploys your chosen army (**Edrags**, **Valkyries**, **Sneaky Goblins**, or **Super Minions**), collects loot, returns home, and automatically recovers from popups, `"Unable to attack Village!"` cloud errors, disconnects, and `Tap!` treasure/event chests.
- **Smart 4-Angle Edrag Strategy.** Inspects Gold and Elixir storage clusters inside the 40×40 base diamond and attacks from the **2 closest adjacent edges** (`NW+NE`, `NE+SE`, `SE+SW`, or `SW+NW`), rotating angles on balanced bases. Aligns Edrags, Heroes, and Spells to the chosen front with deploy-bar/HUD-safe clipping and an automatic upper-edge sweep so no troops or heroes are left behind.
- **Fast Loot Filter.** Reads enemy Gold / Elixir / Dark Elixir in `< 50 ms` using the bundled Clash digit glyph bank (falling back to Tesseract OCR when needed, with anti-truncation and anti-row-shift guards so high-loot bases aren't skipped by mistake).
- **Smart Battle Wait & 4× Fast-Forward.** Ends raids early as soon as Gold & Elixir reach `0 / 0`, when troops & heroes are defeated (6s stall), or after 12s with no loot gain; automatically clicks the `1x` speed button once to enable `4x` fast-forward.
- **Auto Upgrade (beta).** Reads the builder menu via OCR and spends loot:
  - **Maxer** — upgrades affordable buildings/heroes without touching your Town Hall (priciest first by default, or cheapest first in *Settings → Upgrade order*; Dark Elixir heroes always get priority).
  - **Rusher** — upgrades the Town Hall as soon as affordable.
  - **Dry run** — logs what it *would* start without clicking.
- **Run Until Maxed & Wall Upgrades.** Farms and upgrades continuously; batch-buys walls once loot exceeds your threshold (keeping a gold buffer for matchmaking fees), and idles cleanly when storages are full and all builders are busy.
- **Live Telemetry & Account Badge.** Tracks session Gold / Elixir / Dark Elixir totals, loot-per-hour rate, free builders, lab status, skipped bases, and displays the active Home Village account name in the top command bar (with scenery-proof black-outline OCR).
- **Multi-Account Rotation.** Optional account switching via the Accounts page.

Works at **any Town Hall level** — reads the game's live UI signals rather than hardcoded per-TH layouts. *(Lab research and Pet House upgrades are tracked but not started automatically yet.)*

---

## Safety rails

- Never confirms an upgrade whose cost reads red (prevents accidental gem spending).
- Verifies the selected building's name before confirming any upgrade click, and checks the builder count afterward.
- Excludes Clan Castle troop/spell cards from deployment even over bright animated sceneries.
- Escapes unknown dialogs via their close (`X`) button or empty ground — never blind "OK" clicks.
- Saves diagnostic frames to `%LOCALAPPDATA%\BaseLooter\debug\` when verification fails instead of retrying blindly.

---

## Getting Started

1. Download **`BaseLooter-v*.exe`** from [Releases](../../releases/latest) and run it (no installer required; settings and logs are stored in `%LOCALAPPDATA%\BaseLooter`). Because the executable is unsigned, Windows SmartScreen may prompt on first launch — choose *More info → Run anyway*.
2. Launch Clash of Clans in **MuMuPlayer** or **Google Play Games (PC)**.
3. In BaseLooter, open **Settings → Game window**, click **Auto-detect** (or pick the window manually), and press **Test** to confirm capture works.
4. On the **Dashboard**, select your strategy ([Army setup](#army-setup)), optionally set minimum loot thresholds in **Settings** (e.g. `800k–1,000k` Gold/Elixir), set **Auto upgrade → Dry run** for your first test run, and click **Start Autopilot**.
5. Once satisfied, switch **Auto upgrade** to **Maxer** (or **Off** for pure farming), enable **Upgrade walls** / **Run until maxed** as desired.

### Command-line autostart (scheduled / overnight runs)

```bat
BaseLooter.exe --autostart --minutes 0 --walls --upgrades maxer
```

- `--minutes 0` runs until maxed (unlimited).
- `--upgrades off|dry|maxer|rusher` selects the auto-upgrade mode.

---

## Army Setup

![Saved Recipes showing Army 1: 42 Valkyries, 11 Earthquake spells, and 1 Log Launcher](docs/army-valkyrie.png)

- **Edrags (recommended for fast loot farming):** Bring **12+ Electro Dragons**, Rage / Freeze / Invis spells (or 11 Earthquakes), and your Heroes. BaseLooter automatically chooses the best 2-edge attack front based on storage locations.
- **Valkyries:** **42 Valkyries · 11 Earthquake spells · 1 Log Launcher** plus heroes/pets (336/352 housing, 11/11 spells).
- **Sneaky Goblins / Super Minions:** Supported with perimeter spam deployment.

Before pressing **Start**:
- **Start on the Home Village** (or let Home screen recovery bring you there).
- **Use the default deployment bar layout** (single or two-row bar is supported).
- **Keep your Valkyrie army at the top of Saved Recipes** if using Valkyrie mode so recipe loading finds it without scrolling out of range.

Full deployment mechanics are documented in [docs/armies.md](docs/armies.md).

---

## Running & Building from Source

Requires **Python 3.11+** on Windows:

```powershell
pip install -r requirements.txt
python main.py
```

Run the unit test suite:

```powershell
python -m unittest discover -s tests -v
```

Build a standalone single-file `BaseLooter.exe` (bundles `templates/`, `assets/`, and `tesseract/`):

```powershell
$env:BASEPILOT_ONEFILE = "1"
pyinstaller BasePilot.spec --distpath build_dist --workpath build_work --noconfirm
```

The output executable is written to `build_dist\BaseLooter.exe`.

---

## License

MIT — see [LICENSE](LICENSE). Bundled Tesseract OCR ships under the Apache 2.0 license.

## Disclaimer

Automating Clash of Clans **violates Supercell's Terms of Service and can get your account banned.** BaseLooter is published for educational purposes as a computer-vision, OCR, and UI-automation project. Use at your own risk; no warranty is provided.

Not affiliated with, endorsed by, or associated with Supercell. Clash of Clans is a trademark of Supercell Oy.
