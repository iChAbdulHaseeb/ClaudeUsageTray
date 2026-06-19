# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
# Install system deps (GTK3 bindings) — one time only
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0

# Install Python packages
pip3 install --user requests browser-cookie3 curl_cffi

# Run
python3 claude_tray.py &
```

Or run the one-shot setup which also creates autostart + app-menu entries:

```bash
bash install.sh
```

Check logs at `~/.config/claude-tray/app.log` when debugging fetch errors.

## Architecture

Single-file app (`claude_tray.py`). No build step, no tests.

### Component map

| Class | Role |
|---|---|
| `ClaudeUsageFetcher` | All network I/O: loads browser cookies, calls `/api/bootstrap` then `/api/organizations/{uuid}/usage`, parses the response into `UsageData` |
| `LeftClickPopup` | Transient popup shown on tray left-click. Uses a full-screen invisible shield window to detect outside clicks and close itself |
| `FloatingWindow` | Persistent always-on-top panel; draggable, persists its position in `config.json` |
| `ClaudeTrayApp` | Top-level orchestrator: owns `Gtk.StatusIcon`, wires up the two windows, drives two GLib timers (`REFRESH_INTERVAL` for network, `UI_TICK` for countdown label redraw) |

### Threading model

All network calls run in a daemon thread (`threading.Thread`). The thread calls `ClaudeUsageFetcher.refresh()` which writes to `self._data` under a lock, then schedules `GLib.idle_add(self._on_data_ready)` to push results back to the GTK main loop. Never touch GTK widgets from the fetch thread.

### Data flow

`ClaudeUsageFetcher.refresh()` → `UsageData` (list of `LimitEntry`) → `build_content()` (pure GTK widget builder) → slotted into `_content_slot` of either window on each update. The slot is cleared and rebuilt on every update — no incremental diffing.

### Key constants to tune

- `ClaudeTrayApp.REFRESH_INTERVAL` (default 60 s) — full API fetch cadence
- `ClaudeTrayApp.UI_TICK` (default 30 s) — countdown label redraw, no network call

### Config file (`~/.config/claude-tray/config.json`)

Stores the cached org UUID (avoids a bootstrap call) and the floating window position. Written by `ClaudeUsageFetcher._save_cfg()` and `FloatingWindow._save_pos()` — both read the file first to merge, so neither clobbers the other's key.

### Platform constraint

`Gtk.StatusIcon` is X11-only. The entry point sets `GDK_BACKEND=x11` to prevent accidental Wayland startup. Wayland support would require porting to `AppIndicator3` or a Wayland-native status-icon protocol.
