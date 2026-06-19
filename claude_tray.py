#!/usr/bin/env /usr/bin/python3
"""Claude Usage Tray Widget — Ubuntu system tray showing 5-hour & weekly limits."""

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
import cairo
import math
import threading
import json
import logging
import os
import time as _time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests
    HAS_CURL_CFFI = False

try:
    import browser_cookie3
    HAS_BROWSER_COOKIES = True
except ImportError:
    HAS_BROWSER_COOKIES = False

# ── Paths ─────────────────────────────────────────────────────────────────────
CONFIG_DIR  = Path.home() / ".config" / "claude-tray"
CONFIG_FILE = CONFIG_DIR / "config.json"
ICON_FILE   = CONFIG_DIR / "icon.png"
LOG_FILE    = CONFIG_DIR / "app.log"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── Colours ───────────────────────────────────────────────────────────────────
BG      = "#1C1917"
SURFACE = "#292524"
ACCENT  = "#D97757"
WARNING = "#F59E0B"
DANGER  = "#EF4444"
TEXT    = "#F5F0E8"
SUBTEXT = "#A09080"
TROUGH  = "#3D3330"
BORDER  = "#403B38"

CSS = f"""
/* Window root is transparent — card provides the visible rounded background */
window.popup-win, window.float-win {{
    background-color: transparent;
}}
.card {{
    background-color: {BG};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 16px;
}}
label.win-title {{
    color: {TEXT};
    font-weight: bold;
    font-size: 15px;
}}
label.plan-badge {{
    color: {ACCENT};
    font-size: 11px;
    font-weight: bold;
}}
label.row-label {{
    color: {TEXT};
    font-size: 12px;
    font-weight: 600;
}}
label.pct-sub {{
    color: {SUBTEXT};
    font-size: 10px;
}}
label.reset-abs {{
    color: {SUBTEXT};
    font-size: 10px;
}}
label.updated-lbl {{
    color: {SUBTEXT};
    font-size: 10px;
}}
label.error-lbl {{
    color: {SUBTEXT};
    font-size: 11px;
}}
label.drag-hint {{
    color: {BORDER};
    font-size: 16px;
}}
button.refresh-btn {{
    background: transparent;
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {SUBTEXT};
    padding: 2px 8px;
    font-size: 10px;
    min-height: 0;
    min-width: 0;
}}
button.refresh-btn:hover {{
    background-color: {SURFACE};
    color: {TEXT};
    border-color: {SUBTEXT};
}}
progressbar {{ min-height: 7px; border-radius: 4px; }}
progressbar progress {{
    background-color: {ACCENT};
    border-radius: 4px;
    min-height: 7px;
}}
progressbar.warn progress  {{ background-color: {WARNING}; }}
progressbar.crit progress  {{ background-color: {DANGER};  }}
progressbar trough {{
    background-color: {TROUGH};
    border-radius: 4px;
    min-height: 7px;
}}
label.section-header {{
    color: {SUBTEXT};
    font-size: 10px;
    font-weight: bold;
}}
"""


# ── Data ──────────────────────────────────────────────────────────────────────
@dataclass
class LimitEntry:
    label:       str   = ""
    pct:         float = 0.0
    severity:    str   = "normal"
    resets_at:   str   = ""
    is_weekly:   bool  = False
    placeholder: bool  = False   # True when the API returned no data for this slot


@dataclass
class UsageData:
    limits:     List[LimitEntry] = field(default_factory=list)
    plan:       str = "Pro"
    updated_at: Optional[datetime] = None
    error:      Optional[str] = None
    sessions:   List["SessionEntry"] = field(default_factory=list)


@dataclass
class SessionEntry:
    session_id:     str
    project_name:   str    # os.path.basename(cwd)
    cwd:            str
    model:          str    # e.g. "claude-sonnet-4-6"
    context_tokens: int    # input + cache_creation + cache_read from last assistant msg
    context_limit:  int = 200_000
    status:         str = "idle"
    started_at:     int = 0


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_countdown(iso: str) -> str:
    """Remaining time until reset: '2h 55m', '45m', '1d 3h'."""
    if not iso:
        return ""
    try:
        dt  = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        sec = (dt - now).total_seconds()
        if sec <= 0:
            return "now"
        h, rem = divmod(int(sec), 3600)
        m = rem // 60
        if h >= 24:
            return f"{h // 24}d {h % 24}h"
        if h:
            return f"{h}h {m}m"
        return f"{m}m"
    except Exception:
        return ""


def fmt_reset_absolute(iso: str, is_weekly: bool = False) -> str:
    """Absolute reset time shown on the right: clock time for 5-hr, day name for weekly."""
    if not iso:
        return ""
    try:
        dt       = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        local_dt = dt.astimezone()
        now      = datetime.now(timezone.utc)
        sec      = (dt - now).total_seconds()
        if is_weekly:
            # Show day name when far away, day+time when close
            if sec > 18 * 3600:
                return local_dt.strftime("%A")          # "Friday"
            return local_dt.strftime("%a %-I:%M %p")   # "Fri 3:30 PM"
        else:
            return local_dt.strftime("%-I:%M %p")      # "3:30 PM"
    except Exception:
        return ""


def fmt_updated(dt: Optional[datetime]) -> str:
    if not dt:
        return "Never updated"
    sec = (datetime.now() - dt).total_seconds()
    if sec < 60:
        return "Updated just now"
    if sec < 3600:
        return f"Updated {int(sec // 60)}m ago"
    return f"Updated {int(sec // 3600)}h ago"


def _bar_css_class(severity: str) -> Optional[str]:
    return {"warning": "warn", "critical": "crit"}.get(severity)


def _enable_rgba(win: Gtk.Window):
    """Give the window an RGBA visual so the compositor renders rounded-corner transparency."""
    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        win.set_visual(visual)
    win.set_app_paintable(True)

    def _clear(widget, ctx):
        # Erase the window to fully transparent before GTK draws widgets.
        # Without this the X11 default black background leaks through CSS border-radius corners.
        ctx.set_source_rgba(0, 0, 0, 0)
        ctx.set_operator(cairo.OPERATOR_SOURCE)
        ctx.paint()
        ctx.set_operator(cairo.OPERATOR_OVER)
        return False   # let normal widget drawing continue

    win.connect("draw", _clear)


def create_tray_icon(path: Path):
    """Draw 22×22 Claude sun icon: 8 orange rays radiating from a central dot."""
    sz       = 22
    cx = cy  = sz / 2.0
    surf     = cairo.ImageSurface(cairo.FORMAT_ARGB32, sz, sz)
    ctx      = cairo.Context(surf)

    # Fully transparent background (panel shows through)
    ctx.set_source_rgba(0, 0, 0, 0)
    ctx.paint()

    ctx.set_source_rgb(0.851, 0.467, 0.341)   # #D97757

    # 8 radiating rays with round caps
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_width(2.0)
    num_rays = 8
    inner_r  = 3.8
    outer_r  = 9.2
    for i in range(num_rays):
        angle = math.pi * 2 * i / num_rays - math.pi / 2   # start from top
        ctx.move_to(cx + inner_r * math.cos(angle),
                    cy + inner_r * math.sin(angle))
        ctx.line_to(cx + outer_r * math.cos(angle),
                    cy + outer_r * math.sin(angle))
        ctx.stroke()

    # Central filled dot
    ctx.arc(cx, cy, 2.6, 0, 2 * math.pi)
    ctx.fill()

    surf.write_to_png(str(path))


# ── Fetcher ───────────────────────────────────────────────────────────────────
PLAN_MAP = {
    "stripe_subscription": "Pro",
    "stripe_free":         "Free",
    "free":                "Free",
}

LIMIT_LABEL = {
    "session":    ("5-Hour Window", False),
    "weekly_all": ("Weekly",        True),
    "weekly":     ("Weekly",        True),
    "daily":      ("Daily",         False),
    "monthly":    ("Monthly",       False),
}


class ClaudeUsageFetcher:
    def __init__(self):
        self._lock    = threading.Lock()
        self._data    = UsageData(error="Loading…")
        self._org_id  = None
        self._session = self._make_session()
        self._load_cfg()

    def _make_session(self):
        if HAS_CURL_CFFI:
            return cffi_requests.Session(impersonate="chrome120")
        return cffi_requests.Session()

    def _load_cfg(self):
        if CONFIG_FILE.exists():
            try:
                cfg = json.loads(CONFIG_FILE.read_text())
                self._org_id = cfg.get("org_id")
            except Exception:
                pass

    def _save_cfg(self):
        cfg = {}
        if CONFIG_FILE.exists():
            try:
                cfg = json.loads(CONFIG_FILE.read_text())
            except Exception:
                pass
        if self._org_id:
            cfg["org_id"] = self._org_id
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

    def _load_cookies(self) -> bool:
        if not HAS_BROWSER_COOKIES:
            return False
        for fn in [browser_cookie3.chrome, browser_cookie3.firefox,
                   browser_cookie3.chromium, browser_cookie3.brave]:
            try:
                jar     = fn(domain_name=".claude.ai")
                cookies = {c.name: c.value for c in jar}
                if cookies:
                    if HAS_CURL_CFFI:
                        for name, val in cookies.items():
                            self._session.cookies.set(name, val, domain=".claude.ai")
                    else:
                        self._session.cookies.update(cookies)
                    log.info(f"Cookies from {fn.__name__}: {list(cookies.keys())[:8]}")
                    return True
            except Exception as e:
                log.debug(f"{getattr(fn,'__name__','?')}: {e}")
        return False

    def _bootstrap(self) -> Optional[dict]:
        try:
            r = self._session.get("https://claude.ai/api/bootstrap", timeout=15)
            if r.status_code != 200:
                log.warning(f"Bootstrap HTTP {r.status_code}")
                return None
            return r.json()
        except Exception as e:
            log.error(f"Bootstrap: {e}")
            return None

    def _fetch_usage(self) -> Optional[dict]:
        if not self._org_id:
            return None
        try:
            r = self._session.get(
                f"https://claude.ai/api/organizations/{self._org_id}/usage",
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
            log.warning(f"Usage HTTP {r.status_code}")
        except Exception as e:
            log.error(f"Usage: {e}")
        return None

    def _parse(self, boot: dict, raw: Optional[dict]) -> UsageData:
        d = UsageData()
        try:
            bt     = boot["account"]["memberships"][0]["organization"].get("billing_type","")
            d.plan = PLAN_MAP.get(bt, "Pro")
        except Exception:
            d.plan = "Pro"

        try:
            if not self._org_id:
                self._org_id = boot["account"]["memberships"][0]["organization"]["uuid"]
                self._save_cfg()
        except Exception:
            pass

        if raw:
            for item in raw.get("limits", []):
                kind = item.get("kind", "")
                if kind not in LIMIT_LABEL:
                    continue
                label, is_weekly = LIMIT_LABEL[kind]
                d.limits.append(LimitEntry(
                    label     = label,
                    pct       = float(item.get("percent", item.get("utilization", 0))),
                    severity  = item.get("severity", "normal"),
                    resets_at = item.get("resets_at", ""),
                    is_weekly = is_weekly,
                ))
            # De-duplicate by label (keep first occurrence)
            seen, unique = set(), []
            for lim in d.limits:
                if lim.label not in seen:
                    seen.add(lim.label)
                    unique.append(lim)
            d.limits = unique

        # Always show both expected slots; fill missing ones with a placeholder
        EXPECTED = [("5-Hour Window", False), ("Weekly", True)]
        present  = {lim.label for lim in d.limits}
        for label, is_weekly in EXPECTED:
            if label not in present:
                d.limits.append(LimitEntry(
                    label       = label,
                    pct         = 0.0,
                    is_weekly   = is_weekly,
                    placeholder = True,
                ))
        # Keep canonical order: 5-Hour Window first, then Weekly
        order = {label: i for i, (label, _) in enumerate(EXPECTED)}
        d.limits.sort(key=lambda lim: order.get(lim.label, 99))

        return d

    def refresh(self):
        try:
            if not self._session.cookies:
                if not self._load_cookies():
                    with self._lock:
                        self._data = UsageData(
                            error="No browser session found.\n"
                                  "Log into claude.ai in Chrome or\n"
                                  "Firefox, then restart."
                        )
                    return

            boot = self._bootstrap()
            if not boot:
                with self._lock:
                    self._data = UsageData(error="Cannot reach claude.ai.\nCheck your connection.")
                return

            if not self._org_id:
                try:
                    self._org_id = boot["account"]["memberships"][0]["organization"]["uuid"]
                    self._save_cfg()
                except Exception:
                    pass

            raw = self._fetch_usage()
            d   = self._parse(boot, raw)
            d.updated_at = datetime.now()
            with self._lock:
                self._data = d

        except Exception:
            log.exception("Refresh failed")
            with self._lock:
                self._data = UsageData(error="Unexpected error — check app.log")

    def get(self) -> UsageData:
        with self._lock:
            return self._data


# ── Session reader ────────────────────────────────────────────────────────────
CLAUDE_SESSIONS_DIR = Path.home() / ".claude" / "sessions"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CONTEXT_LIMIT       = 200_000
JSONL_TAIL_BYTES    = 8192

_MODEL_SHORT = {
    "claude-sonnet-4-6": "Sonnet",
    "claude-opus-4-8":   "Opus",
    "claude-haiku-4-5":  "Haiku",
}


def _model_shortname(model: str) -> str:
    if model in _MODEL_SHORT:
        return _MODEL_SHORT[model]
    parts = model.split("-")
    return parts[1].capitalize() if len(parts) > 1 else model


class ClaudeSessionReader:
    def read(self) -> List[SessionEntry]:
        entries = []
        if not CLAUDE_SESSIONS_DIR.exists():
            return entries

        for session_file in CLAUDE_SESSIONS_DIR.glob("*.json"):
            try:
                data = json.loads(session_file.read_text())
            except Exception:
                continue

            pid = data.get("pid")
            if not pid:
                continue

            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue        # stale file — process is dead
            except PermissionError:
                pass            # process alive, different user

            session_id   = data.get("sessionId", "")
            cwd          = data.get("cwd", "")
            status       = data.get("status", "idle")
            started_at   = data.get("startedAt", 0)
            project_name = os.path.basename(cwd) if cwd else session_id[:8]

            dir_key    = cwd.replace("/", "-")
            jsonl_path = CLAUDE_PROJECTS_DIR / dir_key / f"{session_id}.jsonl"

            context_tokens, model = self._read_last_usage(jsonl_path)

            entries.append(SessionEntry(
                session_id     = session_id,
                project_name   = project_name,
                cwd            = cwd,
                model          = model,
                context_tokens = context_tokens,
                context_limit  = CONTEXT_LIMIT,
                status         = status,
                started_at     = started_at,
            ))

        entries.sort(key=lambda e: e.started_at)
        return entries

    def _read_last_usage(self, path: Path):
        """Return (context_tokens, model) from the most recent assistant message."""
        if not path.exists():
            return 0, ""
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - JSONL_TAIL_BYTES), 0)
                chunk = f.read().decode("utf-8", errors="replace")

            for line in reversed(chunk.split("\n")):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue    # first line in chunk may be truncated
                if obj.get("type") != "assistant":
                    continue
                msg   = obj.get("message", {})
                usage = msg.get("usage", {})
                if not usage:
                    continue
                tokens = (
                    usage.get("input_tokens", 0) +
                    usage.get("cache_creation_input_tokens", 0) +
                    usage.get("cache_read_input_tokens", 0)
                )
                return tokens, msg.get("model", "")
        except Exception as e:
            log.debug(f"Session JSONL read error {path}: {e}")
        return 0, ""


# ── Progress row ──────────────────────────────────────────────────────────────
def make_progress_row(entry: LimitEntry) -> Gtk.Box:
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    root.set_margin_bottom(4)

    # Row label at top
    lbl = Gtk.Label(label=entry.label)
    lbl.get_style_context().add_class("row-label")
    lbl.set_halign(Gtk.Align.START)
    root.pack_start(lbl, False, False, 0)

    # Progress bar
    bar = Gtk.ProgressBar()
    bar.set_fraction(min(entry.pct / 100.0, 1.0))
    extra = _bar_css_class(entry.severity)
    if extra:
        bar.get_style_context().add_class(extra)
    root.pack_start(bar, False, False, 0)

    # Under bar: LEFT = "33% used · 2h 55m"   RIGHT = "3:30 PM" / "Friday"
    under = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

    if entry.placeholder:
        left_text = "Not started"
    else:
        countdown = fmt_countdown(entry.resets_at)
        left_text = f"{int(entry.pct)}% used"
        if countdown:
            left_text += f"  ·  {countdown}"
    left_lbl = Gtk.Label(label=left_text)
    left_lbl.get_style_context().add_class("pct-sub")
    left_lbl.set_halign(Gtk.Align.START)
    under.pack_start(left_lbl, True, True, 0)

    if not entry.placeholder:
        abs_time = fmt_reset_absolute(entry.resets_at, entry.is_weekly)
        if abs_time:
            right_lbl = Gtk.Label(label=abs_time)
            right_lbl.get_style_context().add_class("reset-abs")
            right_lbl.set_halign(Gtk.Align.END)
            under.pack_end(right_lbl, False, False, 0)

    root.pack_start(under, False, False, 0)
    return root


def make_session_row(entry: SessionEntry) -> Gtk.Box:
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    root.set_margin_bottom(4)

    lbl = Gtk.Label(label=entry.project_name)
    lbl.get_style_context().add_class("row-label")
    lbl.set_halign(Gtk.Align.START)
    root.pack_start(lbl, False, False, 0)

    fraction = min(entry.context_tokens / entry.context_limit, 1.0) if entry.context_limit else 0.0
    bar = Gtk.ProgressBar()
    bar.set_fraction(fraction)
    if fraction >= 0.9:
        bar.get_style_context().add_class("crit")
    elif fraction >= 0.75:
        bar.get_style_context().add_class("warn")
    root.pack_start(bar, False, False, 0)

    under    = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    pct_int  = int(fraction * 100)
    tokens_k = entry.context_tokens // 1000
    left_lbl = Gtk.Label(label=f"{pct_int}%  ·  {tokens_k}K tokens")
    left_lbl.get_style_context().add_class("pct-sub")
    left_lbl.set_halign(Gtk.Align.START)
    under.pack_start(left_lbl, True, True, 0)

    if entry.model:
        right_lbl = Gtk.Label(label=_model_shortname(entry.model))
        right_lbl.get_style_context().add_class("reset-abs")
        right_lbl.set_halign(Gtk.Align.END)
        under.pack_end(right_lbl, False, False, 0)

    root.pack_start(under, False, False, 0)
    return root


def build_content(data: UsageData) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    if data.error and not data.limits:
        err = Gtk.Label(label=data.error)
        err.get_style_context().add_class("error-lbl")
        err.set_line_wrap(True)
        err.set_halign(Gtk.Align.START)
        box.pack_start(err, False, False, 0)
    else:
        for i, entry in enumerate(data.limits):
            box.pack_start(make_progress_row(entry), False, False, 0)
            if i < len(data.limits) - 1:
                box.pack_start(
                    Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                    False, False, 4,
                )

    if data.sessions:
        box.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
            False, False, 4,
        )
        hdr = Gtk.Label(label="Claude Code")
        hdr.get_style_context().add_class("section-header")
        hdr.set_halign(Gtk.Align.START)
        box.pack_start(hdr, False, False, 0)
        for i, session in enumerate(data.sessions):
            box.pack_start(make_session_row(session), False, False, 0)
            if i < len(data.sessions) - 1:
                box.pack_start(
                    Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                    False, False, 4,
                )

    return box


# ── Left-click compact popup ──────────────────────────────────────────────────
class LeftClickPopup:
    """
    Shield + popup pattern:
    • _shield — invisible full-screen TYPE_POPUP; any click outside the popup hits
                the shield and closes both (left OR right click).
    • _win    — the visible card, TYPE_POPUP stacked above the shield.
                An EventBox with above_child=True ensures clicks on any child
                widget (labels, bars) are intercepted and close the popup.
    • Debounce — 300 ms cooldown prevents the shield close + tray-activate
                 signal from re-opening the popup immediately.
    """

    def __init__(self, on_refresh):
        self._on_refresh  = on_refresh
        self._last_close  = 0.0   # monotonic timestamp of last close

        # ── Shield ────────────────────────────────────────────────────────────
        self._shield = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._shield.set_decorated(False)
        _enable_rgba(self._shield)
        self._shield.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._shield.connect("button-press-event", lambda *_: self._close())

        # ── Popup ─────────────────────────────────────────────────────────────
        self._win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._win.set_decorated(False)
        self._win.get_style_context().add_class("popup-win")
        _enable_rgba(self._win)

        # No inside-click handler — shield closes on outside clicks,
        # inside clicks work normally so the refresh button is usable.

        # ── Card ──────────────────────────────────────────────────────────────
        self._card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._card.get_style_context().add_class("card")
        self._card.set_size_request(290, -1)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_row.set_margin_bottom(10)
        title = Gtk.Label(label="Claude Usage")
        title.get_style_context().add_class("win-title")
        title.set_halign(Gtk.Align.START)
        title_row.pack_start(title, True, True, 0)
        self._plan_lbl = Gtk.Label(label="Pro")
        self._plan_lbl.get_style_context().add_class("plan-badge")
        title_row.pack_end(self._plan_lbl, False, False, 0)
        self._card.pack_start(title_row, False, False, 0)
        self._card.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4
        )

        self._content_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._content_slot.set_margin_top(10)
        self._card.pack_start(self._content_slot, False, False, 0)

        # Bottom row: refresh button (left) + updated label (right)
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom.set_margin_top(10)
        ref_btn = Gtk.Button(label="↻  Refresh")
        ref_btn.get_style_context().add_class("refresh-btn")
        ref_btn.connect("clicked", lambda *_: on_refresh())
        bottom.pack_start(ref_btn, False, False, 0)
        self._upd_lbl = Gtk.Label(label="")
        self._upd_lbl.get_style_context().add_class("updated-lbl")
        self._upd_lbl.set_halign(Gtk.Align.END)
        bottom.pack_end(self._upd_lbl, False, False, 0)
        self._card.pack_start(bottom, False, False, 0)

        self._win.add(self._card)

    def _close(self):
        self._last_close = _time.monotonic()
        self._win.hide()
        self._shield.hide()

    def update(self, data: UsageData):
        self._plan_lbl.set_text(data.plan)
        for child in self._content_slot.get_children():
            self._content_slot.remove(child)
        self._content_slot.pack_start(build_content(data), False, False, 0)
        self._upd_lbl.set_text(fmt_updated(data.updated_at))
        self._content_slot.show_all()

    def toggle(self, cx: int, cy: int):
        if self._win.get_visible():
            self._close()
            return
        # Debounce: shield may close popup and then the tray 'activate' fires
        # milliseconds later — don't reopen if we just closed.
        if _time.monotonic() - self._last_close < 0.35:
            return

        screen = Gdk.Screen.get_default()
        self._shield.resize(screen.get_width(), screen.get_height())
        self._shield.move(0, 0)
        self._shield.show()

        self._win.show_all()
        GLib.idle_add(self._position, cx, cy)

    def _position(self, cx: int, cy: int):
        PAD = 12
        w, h = self._win.get_size()

        display = Gdk.Display.get_default()
        monitor = display.get_monitor_at_point(cx, cy)
        geo     = monitor.get_geometry()

        px = cx - w // 2
        px = max(geo.x + PAD, min(px, geo.x + geo.width - w - PAD))

        mid_y = geo.y + geo.height // 2
        if cy >= mid_y:
            py = max(geo.y + PAD, cy - h - PAD)
        else:
            py = min(cy + PAD + 8, geo.y + geo.height - h - PAD)

        self._win.move(px, py)
        gdk_win = self._win.get_window()
        if gdk_win:
            gdk_win.raise_()


# ── Floating window ───────────────────────────────────────────────────────────
class FloatingWindow:
    def __init__(self, on_refresh):
        self._win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self._win.set_decorated(False)
        self._win.set_keep_above(True)
        self._win.set_resizable(False)
        self._win.get_style_context().add_class("float-win")
        self._win.connect("delete-event", lambda w, e: w.hide() or True)
        _enable_rgba(self._win)

        # EventBox wraps everything so the whole surface is draggable
        drag_eb = Gtk.EventBox()
        drag_eb.connect("button-press-event", self._on_drag)
        self._win.add(drag_eb)

        self._card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._card.get_style_context().add_class("card")
        self._card.set_size_request(320, -1)
        drag_eb.add(self._card)

        # Header
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hdr.set_margin_bottom(8)
        title_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="Claude Usage")
        title.get_style_context().add_class("win-title")
        title.set_halign(Gtk.Align.START)
        self._plan_lbl = Gtk.Label(label="Pro")
        self._plan_lbl.get_style_context().add_class("plan-badge")
        self._plan_lbl.set_halign(Gtk.Align.START)
        title_col.pack_start(title, False, False, 0)
        title_col.pack_start(self._plan_lbl, False, False, 0)
        hdr.pack_start(title_col, True, True, 0)
        drag_hint = Gtk.Label(label="⠿")
        drag_hint.get_style_context().add_class("drag-hint")
        hdr.pack_end(drag_hint, False, False, 0)
        self._card.pack_start(hdr, False, False, 0)

        self._card.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 8
        )

        self._content_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._card.pack_start(self._content_slot, False, False, 0)

        self._card.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 8
        )

        # Bottom row: refresh button (left) + updated label (right)
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        ref_btn = Gtk.Button(label="↻  Refresh")
        ref_btn.get_style_context().add_class("refresh-btn")
        ref_btn.connect("clicked", lambda *_: on_refresh())
        bottom.pack_start(ref_btn, False, False, 0)
        self._upd_lbl = Gtk.Label(label="")
        self._upd_lbl.get_style_context().add_class("updated-lbl")
        self._upd_lbl.set_halign(Gtk.Align.END)
        bottom.pack_end(self._upd_lbl, False, False, 0)
        self._card.pack_start(bottom, False, False, 0)

    def _on_drag(self, widget, event):
        if event.button == 1:
            self._win.begin_move_drag(
                event.button, int(event.x_root), int(event.y_root), event.time
            )
            GLib.timeout_add(600, self._save_pos)

    def _load_pos(self):
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text()).get("float_pos")
            except Exception:
                pass
        return None

    def _save_pos(self):
        x, y = self._win.get_position()
        cfg  = {}
        if CONFIG_FILE.exists():
            try:
                cfg = json.loads(CONFIG_FILE.read_text())
            except Exception:
                pass
        cfg["float_pos"] = [x, y]
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        return False  # one-shot

    def update(self, data: UsageData):
        self._plan_lbl.set_text(data.plan)
        for child in self._content_slot.get_children():
            self._content_slot.remove(child)
        self._content_slot.pack_start(build_content(data), False, False, 0)
        self._upd_lbl.set_text(fmt_updated(data.updated_at))
        self._content_slot.show_all()

    def show(self):
        self._win.show_all()
        pos = self._load_pos()
        if pos:
            self._win.move(pos[0], pos[1])
        else:
            self._win.resize(1, 1)
            GLib.idle_add(self._default_pos)

    def _default_pos(self):
        sc   = self._win.get_screen()
        w, h = self._win.get_size()
        self._win.move(sc.get_width() - w - 24, sc.get_height() - h - 60)

    def hide(self):
        self._save_pos()
        self._win.hide()

    def is_visible(self) -> bool:
        return self._win.get_visible()


# ── Main app ──────────────────────────────────────────────────────────────────
class ClaudeTrayApp:
    REFRESH_INTERVAL = 60   # seconds between full API fetches
    UI_TICK          = 30   # seconds between countdown label refreshes (no network call)

    def __init__(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        create_tray_icon(ICON_FILE)

        self._auto_refresh   = True
        self._fetcher        = ClaudeUsageFetcher()
        self._session_reader = ClaudeSessionReader()
        self._popup          = LeftClickPopup(on_refresh=self._do_refresh)
        self._float          = FloatingWindow(on_refresh=self._do_refresh)

        self._fetcher._load_cookies()

        self._tray = Gtk.StatusIcon()
        self._tray.set_from_file(str(ICON_FILE))
        self._tray.set_tooltip_text("Claude Usage")
        self._tray.set_visible(True)
        self._tray.connect("activate",   self._on_left_click)
        self._tray.connect("popup-menu", self._on_right_click)

        self._do_refresh()
        GLib.timeout_add_seconds(self.REFRESH_INTERVAL, self._refresh_timer)
        GLib.timeout_add_seconds(self.UI_TICK,          self._ui_tick)

    def _do_refresh(self):
        threading.Thread(target=self._fetch_thread, daemon=True).start()

    def _fetch_thread(self):
        self._fetcher.refresh()
        GLib.idle_add(self._on_data_ready)

    def _merge_sessions(self, data: UsageData) -> UsageData:
        return replace(data, sessions=self._session_reader.read())

    def _on_data_ready(self):
        data = self._merge_sessions(self._fetcher.get())
        self._popup.update(data)
        self._float.update(data)
        self._tray.set_tooltip_text(self._tooltip_text(data))
        return False

    def _tooltip_text(self, d: UsageData) -> str:
        if d.error and not d.limits:
            return f"Claude — {d.error.split(chr(10))[0]}"
        parts = [f"Plan: {d.plan}"]
        for lim in d.limits:
            parts.append(f"{lim.label}: {int(lim.pct)}% used")
        return "Claude — " + " | ".join(parts)

    def _refresh_timer(self) -> bool:
        if self._auto_refresh:
            self._do_refresh()
        return True  # keep timer alive regardless

    def _ui_tick(self) -> bool:
        data = self._merge_sessions(self._fetcher.get())
        self._popup.update(data)
        if self._float.is_visible():
            self._float.update(data)
        return True

    def _on_left_click(self, icon):
        display = Gdk.Display.get_default()
        _, cx, cy = display.get_default_seat().get_pointer().get_position()
        self._popup.update(self._merge_sessions(self._fetcher.get()))
        self._popup.toggle(cx, cy)

    def _on_right_click(self, icon, button, time):
        menu = Gtk.Menu()

        # Show / Hide floating window
        lbl    = "Hide Floating Window" if self._float.is_visible() else "Show Floating Window"
        toggle = Gtk.MenuItem(label=lbl)
        toggle.connect("activate", self._toggle_float)
        menu.append(toggle)

        # Auto-refresh toggle
        ar_lbl  = "Auto Refresh: ON  ✓" if self._auto_refresh else "Auto Refresh: OFF"
        ar_item = Gtk.MenuItem(label=ar_lbl)
        ar_item.connect("activate", self._toggle_auto_refresh)
        menu.append(ar_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        menu.append(quit_item)

        menu.show_all()
        menu.popup(None, None, Gtk.StatusIcon.position_menu, icon, button, time)

    def _toggle_float(self, _):
        if self._float.is_visible():
            self._float.hide()
        else:
            self._float.update(self._merge_sessions(self._fetcher.get()))
            self._float.show()

    def _toggle_auto_refresh(self, _):
        self._auto_refresh = not self._auto_refresh
        log.info(f"Auto-refresh {'enabled' if self._auto_refresh else 'disabled'}")

    def run(self):
        Gtk.main()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    os.environ.setdefault("GDK_BACKEND", "x11")
    ClaudeTrayApp().run()
