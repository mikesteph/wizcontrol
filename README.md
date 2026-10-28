# 💡 WiZ Control

A local desktop app for **WiZ smart lights**: on/off, brightness, color temperature, full color,
groups, all 36 built-in WiZ scenes, and **64 custom light effects** with 30 color palettes, from
gentle sunrises to max-rate rainbow strobes.

It talks to the bulbs directly over your LAN (UDP port 38899). No cloud, no account, no WiZ app needed.
Works on **Windows** (primary) and **Linux**.

---

## Quick start

### Windows
1. Install **Python 3.10+** from <https://www.python.org/downloads/>. Tick **"Add python.exe to PATH"**.
2. Double-click **`run.bat`**. The first run sets everything up (about a minute), then the app window opens.
3. If Windows Firewall asks, click **Allow** for **Private networks**. The app has to receive the bulbs' replies.

**Single `.exe` instead:** run **`build_windows.bat`**. It produces `dist\WizControl.exe`, which you can copy anywhere
(it doesn't need Python installed). Some antivirus tools flag self-built PyInstaller executables as a false positive.

### Linux
```bash
./run.sh                    # first run creates .venv and installs dependencies
./build_linux.sh            # optional: single-file binary at dist/WizControl
./install_linux_shortcut.sh # optional: add it to your application menu
```
On Linux the app opens in a Chromium/Chrome/Edge "app window" if one of them is installed, otherwise in your
browser. For a fully native window, install the GTK WebKit bindings
(`sudo apt install python3-gi gir1.2-webkit2-4.1`) before the first `./run.sh`.

---

## Adding your lights
- **Type the IPs:** click **Add lights** and paste one IP per line. You can put a name after each IP:
  ```
  192.168.1.20 Desk lamp
  192.168.1.21 Ceiling front
  192.168.1.22
  ```
- **Or discover them:** click **🔍 Discover**. The app broadcasts on your network and also probes every address on
  your /24 subnet directly, so it still works on routers that block broadcasts. If your lights are on a
  different subnet, enter it there (e.g. `192.168.50.0/24`).

The app remembers each bulb by its MAC address. If a bulb's IP changes, run Discover again and the app
updates the IP without losing your groups. For the smoothest experience, give your bulbs **fixed IPs (DHCP reservation)** in your router.

## Groups
Click **＋ New group** and pick lights, e.g. *Front lights*, *Back lights*, *Living room*. **All Lights** is always there.
Every control, scene and effect applies to whichever light or group is selected in the sidebar.
Chase, wave and scanner effects run through a group's lights in the order you added them.

You can run different effects on different groups at the same time. If you control one light that's part of a
running effect, only that light leaves the effect and the rest keep going.

## Effects

| Category | Effects |
|---|---|
| **Strobe & Flash** (12) | Rainbow Strobe ⚠, Hyper Rainbow ⚠, Color Strobe ⚠, Daylight Strobe ⚠, Chaos Strobe ⚠, Color Chaos ⚠, Palette Strobe ⚠, Strobe Bursts ⚠, Police Lights ⚠, Hazard Beacon ⚠, Paparazzi ⚠, Rave ⚠ |
| **Party** (13) | Rainbow Cycle, Rainbow Wave, Disco, Party Jump, Bass Pulse, Color Chase, Scanner, Comet, Fireworks, Neon Sign, Cyber Glitch ⚠, Alternate, Plasma |
| **Nature** (12) | Thunderstorm ⚠, Lightning ⚠, Rain, Fireplace, Candlelight, Ocean Waves, Underwater, Aurora Borealis, Forest Canopy, Lava Lamp, Starry Night, Lighthouse |
| **Ambient** (7) | Breathing, Galaxy, Sparkle, Pastel Dream, Vaporwave, Toxic Waste, Matrix |
| **Palette** (5) | Palette Fade, Palette Wave, Palette Jump, Palette Twinkle, Color Wipe |
| **Holiday** (7) | Christmas, Halloween, Valentine, Fourth of July, New Year Countdown ⚠, Festival of Lights, St. Patrick's |
| **Wellness** (5) | Sunrise, Sunset, Breathing Guide, Heartbeat, Focus Flow |
| **Utility** (3) | Fake TV, Red Alert, Siren Sweep |

⚠ = rapid flashing. The app shows a photosensitivity warning first; you can switch it off in Settings.

Every effect has live **speed** and **brightness** sliders. Some also use your chosen **color** or **palette**.
There are 30 built-in palettes and you can make your own. The 36 **WiZ Scenes** (Ocean, Party, Fireplace, Club, …)
run on the bulb's own firmware with an adjustable speed.

### How fast can the strobes go?
Each bulb gets at most **Max effect update rate** commands per second (Settings, default **20/s**). A strobe needs
2 updates per flash (on + off), so the default is ~10 flashes per second, which is the fastest WiZ bulbs
handle reliably. You can raise it to 50/s. Above ~30/s most bulbs start lagging or skipping frames, but nothing
breaks: the app always sends only the newest frame, so commands never pile up.

## Controlling from your phone
```bash
run.bat --host 0.0.0.0        # Windows
./run.sh --host 0.0.0.0       # Linux
```
Then open `http://<your-pc-ip>:8765` on your phone (same Wi-Fi). The UI is responsive. Anyone on your network can
then control the lights, so only do this on a network you trust.

Headless server mode (the base for a future Docker image): `python -m wizcontrol --server`.

## Docker (home server, NAS, OpenMediaVault)
The repo has a `Dockerfile` and `docker-compose.yml` that run the app headless, with the UI on port 8765.
```bash
git clone https://github.com/<you>/wizcontrol.git && cd wizcontrol
docker compose up -d --build
```
Then open `http://<server-ip>:8765`. The container uses **host networking**. The bulbs talk UDP on port 38899,
and discovery needs LAN broadcasts, which a bridged container can't send or receive. Your lights, groups and
palettes live in `./data`. To update: `git pull && docker compose up -d --build`. Port 8765 taken? Change `WIZ_PORT`
in `docker-compose.yml`. Anyone on your network can open the UI, so keep it on a trusted LAN.

## Troubleshooting
- **Discover finds nothing:** make sure the PC is on the same network/VLAN as the bulbs (guest Wi-Fi networks often
  isolate devices). Allow the app/Python through Windows Firewall on *Private* networks, and check the network
  is set to *Private*, not *Public*. Adding bulbs by IP always works if the IP is reachable.
- **A light shows offline:** its IP probably changed. Run Discover, or edit the light (✎) and fix the IP.
- **Local control:** some WiZ firmware versions have a "Allow local communication" toggle in the WiZ app
  (Settings → Security). It must be on.
- **Logs** are written to `%APPDATA%\WizControl\wizcontrol.log` (Windows) or `~/.config/wizcontrol/wizcontrol.log` (Linux),
  next to `config.json`, which holds your lights, groups and palettes.

## Command line
```
python -m wizcontrol [--host HOST] [--port PORT] [--window auto|native|app|browser] [--server] [--config FILE] [-v]
```

## Development
```bash
python tools/fake_bulb.py --count 6 --kinds rgb,rgb,tw   # simulated bulbs on 127.0.0.11..16 (Linux)
python -m wizcontrol -v                                  # then Discover with network 127.0.0.0/24
python -m tests.test_effects                             # runs every effect and validates its output
```
The fake bulbs print how many commands per second each one receives and what state it's in.

**Layout**
```
wizcontrol/
  protocol.py    WiZ UDP protocol: send / request-with-retry / discovery
  controller.py  lights, groups, per-bulb rate-limited command streams, effect runner, status polling
  effects.py     the effect library (each effect is a small async function)
  colors.py      color math, WiZ scene list, built-in palettes
  server.py      local HTTP + WebSocket API
  app.py         launcher: native window (pywebview) → Edge/Chrome app window → browser
  web/           the UI (plain HTML/CSS/JS, no build step, works offline)
```
Adding an effect means writing one decorated function in `effects.py`. It shows up in the UI automatically.

The UI is a web page served locally. The same code can later be shipped as a Docker image (`--server`) or
wrapped as a mobile app.
