# Claude Usage Tray

A lightweight Ubuntu desktop widget that shows your **Claude AI usage limits** in real time — the 5-hour rolling window and the weekly limit — right from the system tray, without opening a browser.

---

## What it does

| Interaction | Behaviour |
|---|---|
| **Left-click** tray icon | Toggle a compact popup showing both usage bars |
| **Click anywhere outside** the popup | Close the popup |
| **Right-click** tray icon | Context menu |
| → Show / Hide Floating Window | Toggle the persistent floating panel |
| → Auto Refresh: ON / OFF | Pause or resume automatic background polling |
| → Settings | Open the settings dialog |
| → Quit | Exit the app |
| **↻ Refresh** (bottom-left of floating window) | Manually fetch latest data from Claude |
| **Drag** floating window | Move it anywhere; position is remembered across restarts |
| **Drag bottom-right corner** (◢) | Resize the floating window; font scales proportionally |

Each usage row shows:
```
5-Hour Window
████████████░░░░░░░░░
35% used  ·  2h 50m              1:29 PM

Weekly
███░░░░░░░░░░░░░░░░░░
8% used  ·  1d 18h               Friday
```
- **Left under bar** — percentage used + countdown to reset
- **Right under bar** — absolute reset time (clock time for 5-hr, day name for weekly)
- Progress bar turns **amber** near the warning threshold, **red** when critical

---

## Activity Graph

The floating window shows a **7-day × 24-hour contribution heatmap** above the usage bars, similar to GitHub's contribution graph.

- **Rows** — the last 7 days (oldest at top, today at bottom), labeled Mon–Sun
- **Columns** — hours 0–23, with tick marks at 0, 6, 12, 18, 23
- **Cell color** — scales from a faint tint to full accent color based on how many tokens you used that hour relative to your peak hour in the window
- **Data source** — reads directly from `~/.claude/projects/**/*.jsonl` (local Claude Code session files), so it works offline and requires no extra API calls
- Can be hidden via **Settings → Show activity graph**

---

## Settings

Open via right-click menu → **Settings**.

| Setting | Description |
|---|---|
| Auto Refresh | Toggle automatic background polling |
| Refresh Interval | Seconds between API fetches (default 60) |
| Opacity | Floating window transparency (0.1–1.0) |
| Also apply opacity to popup | Apply the same opacity to the left-click popup |
| Keep below other windows | Pin the floating window behind all other windows (desktop widget mode) |
| Show activity graph | Show or hide the hourly contribution heatmap |
| Progress Bar Color | Accent color used for bars and the graph heatmap |
| Background Color | Card background color |
| **Reset Defaults** | Restore all settings to their default values |
| **Restart** | Save current settings and restart the app in place |

---

## Requirements

- Ubuntu 20.04 or later (or any Debian-based Linux)
- GNOME desktop on **X11** (Wayland is not supported by `Gtk.StatusIcon`)
- Chrome or Firefox — your existing logged-in browser session is used for auth
- Internet connection to reach `claude.ai`

---

## Installation

### One-command setup

```bash
git clone <repo-url> claude-tray
cd claude-tray
bash install.sh
```

`install.sh` will:
1. Check for Python 3 (`/usr/bin/python3`)
2. Install GTK3 system packages if missing (`sudo apt` — only if needed)
3. Install Python packages to your user directory (`pip3 install --user`)
4. Create an **app menu shortcut** (search "Claude Usage" in your launcher)
5. Create an **autostart entry** so the widget launches on every login
6. Start the app immediately

### Manual dependency install

```bash
# System packages (GTK3 bindings)
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0

# Python packages
pip3 install --user requests browser-cookie3 curl_cffi
```

Then run:

```bash
python3 claude_tray.py &
```

---

## How authentication works

The app reads your browser's session cookies for `claude.ai` automatically using `browser-cookie3`. **No password or API key is required.**

- Supports **Chrome**, **Chromium**, **Firefox**, and **Brave**
- You must be logged into `claude.ai` in one of those browsers before starting the app
- If the tray icon shows an error, log in via your browser and restart the app

The app only reads cookies — it never stores or transmits your credentials anywhere other than to `claude.ai` itself.

---

## How usage data is fetched

| Endpoint | What it does |
|---|---|
| `GET /api/bootstrap` | Gets your org UUID and plan info |
| `GET /api/organizations/{uuid}/usage` | Gets current `percent` used + `resets_at` for each limit |

`curl_cffi` is used to impersonate Chrome's TLS fingerprint, which is required to pass Cloudflare protection on `claude.ai`.

The **activity graph** does not use any API calls — it reads `~/.claude/projects/**/*.jsonl` directly for token counts and timestamps.

---

## Configuration

All settings are managed through the **Settings dialog** (right-click tray icon → Settings). They are persisted to `~/.config/claude-tray/config.json`.

The config file also stores the cached org UUID and floating window position/size so the app restores its exact state on each launch.

---

## Troubleshooting

**Tray icon doesn't appear**
- Make sure you are on an X11 session (not Wayland). At the login screen, click the gear icon and select "Ubuntu on Xorg".
- On stock GNOME, install the [AppIndicator and KStatusNotifierItem Support](https://extensions.gnome.org/extension/615/appindicator-support/) extension.

**Shows "No browser session found"**
- Log into [claude.ai](https://claude.ai) in Chrome or Firefox, then restart the app.

**Shows "Cannot reach claude.ai"**
- Check your internet connection. The app needs outbound HTTPS to `claude.ai`.

**Usage shows 0% / no data**
- Check `~/.config/claude-tray/app.log` for error details.
- Your plan may expose usage differently — open an issue with the log.

**Activity graph is empty**
- The graph only shows Claude Code sessions from `~/.claude/projects/`. Browser-based claude.ai usage is not reflected.

---

## File layout

```
claude-tray/
├── claude_tray.py      # Main app (single file)
├── install.sh          # One-shot setup script
├── requirements.txt    # Python package list
├── CLAUDE.md           # Architecture guide for Claude Code
└── README.md           # This file

~/.config/claude-tray/
├── calude_icon.png     # Bundled tray icon
├── config.json         # Org UUID cache + window state + settings
└── app.log             # Debug log
```
