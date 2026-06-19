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
| `ClaudeSessionReader` | Reads live Claude Code sessions from `~/.claude/sessions/*.json` and the matching JSONL file to get current context token usage |
| `ActivityReader` | Scans `~/.claude/projects/**/*.jsonl` for the last 7 days and builds a `{(date_str, hour): tokens}` map for the contribution graph |
| `ContributionGraph` | GTK `DrawingArea` that renders the 7-day × 24-hour heatmap using Cairo. Colors scale with the user's accent color setting |
| `LeftClickPopup` | Transient popup shown on tray left-click. Uses a full-screen invisible shield window to detect outside clicks and close itself |
| `FloatingWindow` | Persistent panel; draggable, resizable, persists position and size in `config.json`. Z-order is user-configurable (always-on-top or always-below). Contains the contribution graph above the usage bars |
| `SettingsDialog` | Settings window opened from the right-click menu. Controls auto-refresh, refresh interval, opacity, z-order, activity graph visibility, and bar/background colors. Includes Reset Defaults and Restart buttons |
| `ClaudeTrayApp` | Top-level orchestrator: owns `Gtk.StatusIcon`, wires up all windows, drives two GLib timers (`REFRESH_INTERVAL` for network, `UI_TICK` for countdown label redraw) |

### Threading model

All network calls and file scans run in a daemon thread (`threading.Thread`). The thread calls `ClaudeUsageFetcher.refresh()` and `ActivityReader.read()` sequentially, then schedules `GLib.idle_add(self._on_data_ready)` to push results back to the GTK main loop. Never touch GTK widgets from the fetch thread.

### Data flow

```
Background thread:
  ClaudeUsageFetcher.refresh() → writes UsageData to _data under lock
  ActivityReader.read()        → writes activity dict to _activity_cache

GLib.idle_add(_on_data_ready):
  _merge_sessions(fetcher.get()) → UsageData with sessions + activity merged
  → LeftClickPopup.update(data)
  → FloatingWindow.update(data)  → build_content() for bars
                                 → ContributionGraph.set_data(data.activity)
```

The `_content_slot` in each window is cleared and rebuilt on every update — no incremental diffing.

### Key constants to tune

- `ClaudeTrayApp.REFRESH_INTERVAL` (default 60 s) — full API fetch + activity scan cadence
- `ClaudeTrayApp.UI_TICK` (default 30 s) — countdown label redraw, no network call

### Config file (`~/.config/claude-tray/config.json`)

Stores the cached org UUID, floating window position and size, visibility state, and all user settings. Written by multiple methods — each reads the file first and merges its key, so they never clobber each other.

| Key | Written by | Purpose |
|---|---|---|
| `org_id` | `ClaudeUsageFetcher._save_cfg()` | Skips bootstrap call on next start |
| `float_x`, `float_y` | `FloatingWindow._save_pos()` | Restore window position |
| `float_w`, `float_h` | `FloatingWindow._save_size()` | Restore window width |
| `float_visible` | `FloatingWindow._save_visibility()` | Re-show on next launch if it was open |
| `settings` | `ClaudeTrayApp._save_settings()` | All `AppSettings` fields |

### AppSettings fields

| Field | Default | Description |
|---|---|---|
| `auto_refresh` | `True` | Whether to poll on a timer |
| `refresh_interval` | `60` | Seconds between API fetches |
| `opacity` | `1.0` | Floating window opacity (0.1–1.0) |
| `opacity_popup` | `False` | Apply same opacity to left-click popup |
| `keep_below` | `False` | Pin floating window below all other windows |
| `show_graph` | `True` | Show the activity contribution graph |
| `bar_color` | `#D97757` | Progress bar and graph accent color |
| `bg_color` | `#1C1917` | Card background color |

### Platform constraint

`Gtk.StatusIcon` is X11-only. The entry point sets `GDK_BACKEND=x11` to prevent accidental Wayland startup. Wayland support would require porting to `AppIndicator3` or a Wayland-native status-icon protocol.
