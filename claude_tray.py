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
import sys
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
CACHE_FILE  = CONFIG_DIR / "usage_cache.json"
ICON_FILE   = CONFIG_DIR / "calude_icon.png"
LOG_FILE    = CONFIG_DIR / "app.log"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── Default colours ───────────────────────────────────────────────────────────
BG      = "#1C1917"
SURFACE = "#292524"
ACCENT  = "#D97757"
WARNING = "#F59E0B"
DANGER  = "#EF4444"
TEXT    = "#F5F0E8"
SUBTEXT = "#A09080"
TROUGH  = "#3D3330"
BORDER  = "#403B38"

BASE_FLOAT_WIDTH = 320   # reference width at scale 1.0


def build_css(bg_color: str = BG, bar_color: str = ACCENT, float_scale: float = 1.0) -> str:
    s  = max(0.6, float_scale)
    ph = max(4, round(7 * s))    # progress bar height
    pr = max(2, round(4 * s))    # progress bar radius
    cp = max(8, round(16 * s))   # card padding
    cr = max(6, round(10 * s))   # card corner radius

    return f"""
window.popup-win, window.float-win {{
    background-color: transparent;
}}
.card {{
    background-color: {bg_color};
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
    color: {bar_color};
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
label.resize-grip {{
    color: {BORDER};
    font-size: 14px;
}}
label.resize-grip:hover {{
    color: {SUBTEXT};
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
progressbar {{
    min-height: 7px;
    border-radius: 4px;
    border: none;
    box-shadow: none;
    outline: none;
}}
progressbar progress {{
    background-color: {bar_color};
    border-radius: 4px;
    min-height: 7px;
    border: none;
    box-shadow: none;
    outline: none;
}}
progressbar.warn progress  {{ background-color: {WARNING}; }}
progressbar.crit progress  {{ background-color: {DANGER};  }}
progressbar trough {{
    background-color: {TROUGH};
    border-radius: 4px;
    min-height: 7px;
    border: none;
    box-shadow: none;
    outline: none;
}}
label.section-header {{
    color: {SUBTEXT};
    font-size: 10px;
    font-weight: bold;
}}

/* ── Floating window: scales with resize ─────────────────────────────────── */
.float-win .card {{
    background-color: {bg_color};
    border-radius: {cr}px;
    padding: {cp}px;
}}
.float-win label.win-title      {{ font-size: {round(15 * s)}px; }}
.float-win label.plan-badge     {{ font-size: {round(11 * s)}px; color: {bar_color}; }}
.float-win label.row-label      {{ font-size: {round(12 * s)}px; }}
.float-win label.pct-sub        {{ font-size: {round(10 * s)}px; }}
.float-win label.reset-abs      {{ font-size: {round(10 * s)}px; }}
.float-win label.updated-lbl    {{ font-size: {round(10 * s)}px; }}
.float-win label.error-lbl      {{ font-size: {round(11 * s)}px; }}
.float-win label.drag-hint      {{ font-size: {round(16 * s)}px; }}
.float-win label.resize-grip    {{ font-size: {round(14 * s)}px; }}
.float-win label.section-header {{ font-size: {round(10 * s)}px; }}
.float-win button.refresh-btn   {{ font-size: {round(10 * s)}px; }}
.float-win progressbar {{
    min-height: {ph}px;
    border-radius: {pr}px;
}}
.float-win progressbar progress {{
    background-color: {bar_color};
    min-height: {ph}px;
    border-radius: {pr}px;
    border: none; box-shadow: none; outline: none;
}}
.float-win progressbar.warn progress {{ background-color: {WARNING}; }}
.float-win progressbar.crit progress {{ background-color: {DANGER};  }}
.float-win progressbar trough {{
    background-color: {TROUGH};
    min-height: {ph}px;
    border-radius: {pr}px;
    border: none; box-shadow: none; outline: none;
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
    placeholder: bool  = False


@dataclass
class UsageData:
    limits:     List[LimitEntry] = field(default_factory=list)
    plan:       str = "Pro"
    name:       str = ""
    updated_at: Optional[datetime] = None
    error:      Optional[str] = None
    sessions:   List["SessionEntry"] = field(default_factory=list)
    activity:   dict = field(default_factory=dict)  # {(date_str, hour): tokens}


@dataclass
class SessionEntry:
    session_id:     str
    project_name:   str
    cwd:            str
    model:          str
    context_tokens: int
    context_limit:  int = 200_000
    status:         str = "idle"
    started_at:     int = 0


@dataclass
class AppSettings:
    auto_refresh:     bool  = True
    refresh_interval: int   = 60
    opacity:          float = 1.0
    opacity_popup:    bool  = False
    keep_below:       bool  = False
    show_graph:       bool  = True
    bar_color:        str   = ACCENT
    bg_color:         str   = BG


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_countdown(iso: str) -> str:
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
    if not iso:
        return ""
    try:
        dt       = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        local_dt = dt.astimezone()
        now      = datetime.now(timezone.utc)
        sec      = (dt - now).total_seconds()
        if is_weekly:
            if sec > 18 * 3600:
                return local_dt.strftime("%A")
            return local_dt.strftime("%a %-I:%M %p")
        else:
            return local_dt.strftime("%-I:%M %p")
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


def _hex_to_rgba(hex_color: str) -> Gdk.RGBA:
    rgba = Gdk.RGBA()
    rgba.parse(hex_color)
    return rgba


def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        int(rgba.red * 255),
        int(rgba.green * 255),
        int(rgba.blue * 255),
    )


def _enable_rgba(win: Gtk.Window):
    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        win.set_visual(visual)
    win.set_app_paintable(True)

    def _clear(widget, ctx):
        ctx.set_source_rgba(0, 0, 0, 0)
        ctx.set_operator(cairo.OPERATOR_SOURCE)
        ctx.paint()
        ctx.set_operator(cairo.OPERATOR_OVER)
        return False

    win.connect("draw", _clear)


def create_tray_icon(path: Path):
    sz       = 22
    cx = cy  = sz / 2.0
    surf     = cairo.ImageSurface(cairo.FORMAT_ARGB32, sz, sz)
    ctx      = cairo.Context(surf)
    ctx.set_source_rgba(0, 0, 0, 0)
    ctx.paint()
    ctx.set_source_rgb(0.851, 0.467, 0.341)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_width(2.0)
    num_rays = 8
    inner_r  = 3.8
    outer_r  = 9.2
    for i in range(num_rays):
        angle = math.pi * 2 * i / num_rays - math.pi / 2
        ctx.move_to(cx + inner_r * math.cos(angle), cy + inner_r * math.sin(angle))
        ctx.line_to(cx + outer_r * math.cos(angle), cy + outer_r * math.sin(angle))
        ctx.stroke()
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
        self._data    = self._load_cache()
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

    def _load_cache(self) -> UsageData:
        if not CACHE_FILE.exists():
            return UsageData(error="Loading…")
        try:
            raw    = json.loads(CACHE_FILE.read_text())
            limits = [LimitEntry(**lim) for lim in raw.get("limits", [])]
            ts     = raw.get("updated_at")
            return UsageData(
                limits     = limits,
                plan       = raw.get("plan", "Pro"),
                updated_at = datetime.fromisoformat(ts) if ts else None,
            )
        except Exception:
            return UsageData(error="Loading…")

    def _save_cache(self):
        d = self._data
        if d.error and not d.limits:
            return
        try:
            raw = {
                "limits": [
                    {
                        "label":       lim.label,
                        "pct":         lim.pct,
                        "severity":    lim.severity,
                        "resets_at":   lim.resets_at,
                        "is_weekly":   lim.is_weekly,
                        "placeholder": lim.placeholder,
                    }
                    for lim in d.limits
                ],
                "plan":       d.plan,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            CACHE_FILE.write_text(json.dumps(raw, indent=2))
        except Exception as e:
            log.debug(f"Cache save: {e}")

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
            acct   = boot.get("account", {})
            d.name = (acct.get("full_name") or
                      acct.get("display_name") or
                      acct.get("name") or
                      acct.get("email_address", "").split("@")[0] or "")
        except Exception:
            d.name = ""

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
            seen, unique = set(), []
            for lim in d.limits:
                if lim.label not in seen:
                    seen.add(lim.label)
                    unique.append(lim)
            d.limits = unique

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
            self._save_cache()

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
                continue
            except PermissionError:
                pass

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
                    continue
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


# ── Activity reader ───────────────────────────────────────────────────────────
class ActivityReader:
    """Scans local JSONL session files to build a (date, hour) → tokens map."""

    LOOKBACK_DAYS = 7

    def read(self) -> dict:
        result = {}
        if not CLAUDE_PROJECTS_DIR.exists():
            return result
        cutoff_ts = _time.time() - self.LOOKBACK_DAYS * 86400
        cutoff_dt = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc)
        for jsonl_path in CLAUDE_PROJECTS_DIR.glob("*/*.jsonl"):
            try:
                if jsonl_path.stat().st_mtime < cutoff_ts:
                    continue
                self._scan_file(jsonl_path, cutoff_dt, result)
            except Exception as e:
                log.debug(f"ActivityReader skip {jsonl_path}: {e}")
        return result

    def _scan_file(self, path: Path, cutoff_dt: datetime, result: dict):
        with open(path, "r", errors="replace") as f:
            for line in f:
                if '"type":"assistant"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if obj.get("type") != "assistant":
                    continue
                ts = obj.get("timestamp", "")
                if not ts:
                    continue
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    continue
                if dt < cutoff_dt:
                    continue
                usage = obj.get("message", {}).get("usage", {})
                tokens = (
                    usage.get("input_tokens", 0) +
                    usage.get("cache_creation_input_tokens", 0) +
                    usage.get("cache_read_input_tokens", 0) +
                    usage.get("output_tokens", 0)
                )
                if tokens <= 0:
                    continue
                local_dt = dt.astimezone()
                key = (local_dt.strftime("%Y-%m-%d"), local_dt.hour)
                result[key] = result.get(key, 0) + tokens


# ── Progress row ──────────────────────────────────────────────────────────────
def make_progress_row(entry: LimitEntry) -> Gtk.Box:
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    root.set_margin_bottom(4)

    lbl = Gtk.Label(label=entry.label)
    lbl.get_style_context().add_class("row-label")
    lbl.set_halign(Gtk.Align.START)
    root.pack_start(lbl, False, False, 0)

    bar = Gtk.ProgressBar()
    bar.set_fraction(min(entry.pct / 100.0, 1.0))
    extra = _bar_css_class(entry.severity)
    if extra:
        bar.get_style_context().add_class(extra)
    root.pack_start(bar, False, False, 0)

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
    def __init__(self, on_refresh):
        self._on_refresh  = on_refresh
        self._last_close  = 0.0

        self._shield = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._shield.set_decorated(False)
        _enable_rgba(self._shield)
        self._shield.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._shield.connect("button-press-event", lambda *_: self._close())

        self._win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._win.set_decorated(False)
        self._win.get_style_context().add_class("popup-win")
        _enable_rgba(self._win)

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
        self._plan_lbl.set_text(f"{data.name}  ·  {data.plan}" if data.name else data.plan)
        for child in self._content_slot.get_children():
            self._content_slot.remove(child)
        self._content_slot.pack_start(build_content(data), False, False, 0)
        self._upd_lbl.set_text(fmt_updated(data.updated_at))
        self._content_slot.show_all()
        self._win.resize(1, 1)

    def toggle(self, cx: int, cy: int):
        if self._win.get_visible():
            self._close()
            return
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


# ── Contribution graph ────────────────────────────────────────────────────────
class ContributionGraph(Gtk.DrawingArea):
    DAYS  = 7
    HOURS = 24

    def __init__(self):
        super().__init__()
        self._data      = {}
        self._bar_r = self._bar_g = self._bar_b = 0.85
        self._bg_r  = self._bg_g  = self._bg_b  = 0.11
        self.set_size_request(-1, 84)
        self.connect("draw", self._on_draw)

    def set_data(self, activity: dict):
        self._data = activity
        self.queue_draw()

    def set_colors(self, bar_hex: str, bg_hex: str):
        self._bar_r, self._bar_g, self._bar_b = self._hex_to_rgb(bar_hex)
        self._bg_r,  self._bg_g,  self._bg_b  = self._hex_to_rgb(bg_hex)
        self.queue_draw()

    def set_scale(self, scale: float):
        h = max(56, int(84 * scale))
        self.set_size_request(-1, h)
        self.queue_draw()

    @staticmethod
    def _hex_to_rgb(hex_color: str):
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = h[0]*2 + h[1]*2 + h[2]*2
        return int(h[0:2],16)/255, int(h[2:4],16)/255, int(h[4:6],16)/255

    def _on_draw(self, widget, cr):
        from datetime import date, timedelta

        alloc  = self.get_allocation()
        W, H   = alloc.width, alloc.height

        LABEL_W = 26
        LABEL_H = 13
        GAP     = 2

        gw = W - LABEL_W
        gh = H - LABEL_H

        cw = (gw - (self.HOURS - 1) * GAP) / self.HOURS
        ch = (gh - (self.DAYS  - 1) * GAP) / self.DAYS

        today = date.today()
        days  = [today - timedelta(days=self.DAYS - 1 - i) for i in range(self.DAYS)]

        max_tok = max(self._data.values(), default=1) or 1

        ar, ag, ab = self._bar_r, self._bar_g, self._bar_b
        bgr, bgg, bgb = self._bg_r, self._bg_g, self._bg_b
        # empty cell: slightly lighter than bg
        er = min(1.0, bgr + 0.10)
        eg = min(1.0, bgg + 0.09)
        eb = min(1.0, bgb + 0.08)

        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)

        for row, d in enumerate(days):
            date_str = d.strftime("%Y-%m-%d")
            day_name = d.strftime("%a")

            # day label
            cr.set_source_rgba(0.63, 0.56, 0.50, 0.9)
            cr.set_font_size(8)
            ext = cr.text_extents(day_name)
            y_cell_top = row * (ch + GAP)
            cr.move_to(max(0, LABEL_W - ext[2] - 3), y_cell_top + ch/2 + ext[3]/2)
            cr.show_text(day_name)

            for col in range(self.HOURS):
                tokens    = self._data.get((date_str, col), 0)
                intensity = tokens / max_tok if tokens > 0 else 0

                x = LABEL_W + col * (cw + GAP)
                y = y_cell_top
                rr = max(1.5, min(cw, ch) * 0.28)

                if intensity > 0:
                    t = 0.18 + 0.82 * intensity
                    cr.set_source_rgb(
                        bgr + (ar - bgr) * t,
                        bgg + (ag - bgg) * t,
                        bgb + (ab - bgb) * t,
                    )
                else:
                    cr.set_source_rgb(er, eg, eb)

                self._rrect(cr, x, y, cw, ch, rr)
                cr.fill()

        # hour tick labels
        cr.set_source_rgba(0.63, 0.56, 0.50, 0.7)
        cr.set_font_size(7)
        for h_tick in [0, 6, 12, 18, 23]:
            x = LABEL_W + h_tick * (cw + GAP)
            cr.move_to(x, H - 1)
            cr.show_text(str(h_tick))

    @staticmethod
    def _rrect(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r,     r, -math.pi/2, 0)
        cr.arc(x + w - r, y + h - r, r, 0,           math.pi/2)
        cr.arc(x + r,     y + h - r, r, math.pi/2,   math.pi)
        cr.arc(x + r,     y + r,     r, math.pi,     3*math.pi/2)
        cr.close_path()


# ── Floating window ───────────────────────────────────────────────────────────
class FloatingWindow:
    def __init__(self, on_refresh, on_scale_change):
        self._on_scale_change = on_scale_change
        self._scale           = 1.0
        self._resize_timer    = None

        self._win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self._win.set_decorated(False)
        self._win.set_keep_above(True)
        self._win.set_keep_below(False)
        self._win.set_resizable(True)
        self._win.set_size_request(200, -1)
        self._win.get_style_context().add_class("float-win")
        self._win.connect("delete-event",    lambda w, e: w.hide() or True)
        self._win.connect("configure-event", self._on_configure)
        _enable_rgba(self._win)

        drag_eb = Gtk.EventBox()
        drag_eb.connect("button-press-event", self._on_drag_or_resize)
        self._win.add(drag_eb)

        self._card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._card.get_style_context().add_class("card")
        self._card.set_size_request(200, -1)
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
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 6
        )

        # Activity graph (hidden by default until settings are applied)
        self._graph       = ContributionGraph()
        self._graph_wrap  = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._graph_wrap.pack_start(self._graph, False, False, 0)
        self._graph_sep   = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self._graph_wrap.pack_start(self._graph_sep, False, False, 6)
        self._card.pack_start(self._graph_wrap, False, False, 0)

        self._content_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._card.pack_start(self._content_slot, False, False, 0)

        self._card.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 8
        )

        # Bottom row: refresh  ·  updated  ·  resize grip
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        ref_btn = Gtk.Button(label="↻  Refresh")
        ref_btn.get_style_context().add_class("refresh-btn")
        ref_btn.connect("clicked", lambda *_: on_refresh())
        bottom.pack_start(ref_btn, False, False, 0)

        self._upd_lbl = Gtk.Label(label="")
        self._upd_lbl.get_style_context().add_class("updated-lbl")
        self._upd_lbl.set_halign(Gtk.Align.CENTER)
        bottom.pack_start(self._upd_lbl, True, True, 0)

        # Resize grip — bottom-right corner drag handle
        grip_lbl = Gtk.Label(label="◢")
        grip_lbl.get_style_context().add_class("resize-grip")
        grip_eb = Gtk.EventBox()
        grip_eb.add(grip_lbl)
        grip_eb.connect("button-press-event", self._on_resize_grip)
        grip_eb.connect("realize", self._set_resize_cursor)
        bottom.pack_end(grip_eb, False, False, 0)

        self._card.pack_start(bottom, False, False, 0)

    # ── Drag / resize ─────────────────────────────────────────────────────────
    def _on_drag_or_resize(self, widget, event):
        if event.button == 1:
            self._win.begin_move_drag(
                event.button, int(event.x_root), int(event.y_root), event.time
            )
            GLib.timeout_add(600, self._save_pos)

    def _on_resize_grip(self, widget, event):
        if event.button == 1:
            self._win.begin_resize_drag(
                Gdk.WindowEdge.SOUTH_EAST,
                event.button, int(event.x_root), int(event.y_root), event.time
            )

    def _set_resize_cursor(self, widget):
        widget.get_window().set_cursor(
            Gdk.Cursor.new_from_name(Gdk.Display.get_default(), "se-resize")
        )

    # ── Configure / scale ─────────────────────────────────────────────────────
    def _on_configure(self, win, event):
        if self._resize_timer:
            GLib.source_remove(self._resize_timer)
        self._resize_timer = GLib.timeout_add(150, self._on_resize_settled)

    def _on_resize_settled(self):
        self._resize_timer = None
        w, _ = self._win.get_size()
        new_scale = max(0.6, min(3.0, w / BASE_FLOAT_WIDTH))
        if abs(new_scale - self._scale) > 0.02:
            self._scale = new_scale
            self._on_scale_change(new_scale)
        self._save_size()
        return False   # one-shot

    # ── Persistence ───────────────────────────────────────────────────────────
    def _read_cfg(self) -> dict:
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text())
            except Exception:
                pass
        return {}

    def _write_cfg(self, cfg: dict):
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

    def _load_pos(self):
        return self._read_cfg().get("float_pos")

    def _save_pos(self):
        x, y = self._win.get_position()
        cfg  = self._read_cfg()
        cfg["float_pos"] = [x, y]
        self._write_cfg(cfg)
        return False

    def _load_size(self):
        return self._read_cfg().get("float_size")

    def _save_size(self):
        w, h = self._win.get_size()
        cfg  = self._read_cfg()
        cfg["float_size"] = [w, h]
        self._write_cfg(cfg)

    def _save_visibility(self, visible: bool):
        cfg = self._read_cfg()
        cfg["float_visible"] = visible
        self._write_cfg(cfg)

    def was_visible(self) -> bool:
        return self._read_cfg().get("float_visible", False)

    # ── Content ───────────────────────────────────────────────────────────────
    def update(self, data: UsageData):
        self._plan_lbl.set_text(f"{data.name}  ·  {data.plan}" if data.name else data.plan)
        for child in self._content_slot.get_children():
            self._content_slot.remove(child)
        self._content_slot.pack_start(build_content(data), False, False, 0)
        self._upd_lbl.set_text(fmt_updated(data.updated_at))
        self._content_slot.show_all()
        if data.activity:
            self._graph.set_data(data.activity)
        if self._win.get_visible():
            w, _ = self._win.get_size()
            self._win.resize(w, 1)

    def set_graph_visible(self, show: bool):
        self._graph_wrap.set_visible(show)
        if self._win.get_visible():
            w, _ = self._win.get_size()
            self._win.resize(w, 1)

    def apply_graph_colors(self, bar_hex: str, bg_hex: str):
        self._graph.set_colors(bar_hex, bg_hex)

    def show(self):
        self._win.show_all()
        size = self._load_size()
        if size:
            self._win.resize(size[0], 1)   # restore width only; height is dynamic
            # Recompute scale immediately so CSS matches saved size
            saved_scale = max(0.6, min(3.0, size[0] / BASE_FLOAT_WIDTH))
            if abs(saved_scale - self._scale) > 0.02:
                self._scale = saved_scale
                self._on_scale_change(saved_scale)
        pos = self._load_pos()
        if pos:
            self._win.move(pos[0], pos[1])
        else:
            GLib.idle_add(self._default_pos)
        self._save_visibility(True)

    def _default_pos(self):
        sc   = self._win.get_screen()
        w, h = self._win.get_size()
        self._win.move(sc.get_width() - w - 24, sc.get_height() - h - 60)

    def hide(self):
        self._save_pos()
        self._save_visibility(False)
        self._win.hide()

    def is_visible(self) -> bool:
        return self._win.get_visible()

    def get_scale(self) -> float:
        return self._scale

    def apply_z_order(self, keep_below: bool):
        if keep_below:
            self._win.set_keep_above(False)
            self._win.set_keep_below(True)
        else:
            self._win.set_keep_below(False)
            self._win.set_keep_above(True)


# ── Settings dialog ───────────────────────────────────────────────────────────
class SettingsDialog:
    def __init__(self, settings: AppSettings, on_apply):
        self._on_apply = on_apply

        self._win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self._win.set_title("Settings")
        self._win.set_decorated(True)
        self._win.set_resizable(False)
        self._win.set_keep_above(True)
        self._win.set_border_width(20)
        self._win.connect("delete-event", lambda w, e: w.hide() or True)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self._win.add(outer)

        grid = Gtk.Grid()
        grid.set_row_spacing(14)
        grid.set_column_spacing(20)
        outer.pack_start(grid, False, False, 0)

        row = 0

        self._ar_check = Gtk.CheckButton(label="Auto Refresh")
        self._ar_check.set_active(settings.auto_refresh)
        grid.attach(self._ar_check, 0, row, 2, 1)
        row += 1

        ri_lbl = Gtk.Label(label="Refresh Interval (seconds)")
        ri_lbl.set_halign(Gtk.Align.START)
        adj = Gtk.Adjustment(
            value=settings.refresh_interval,
            lower=15, upper=3600,
            step_increment=15, page_increment=60,
        )
        self._ri_spin = Gtk.SpinButton(adjustment=adj, climb_rate=1, digits=0)
        self._ri_spin.set_size_request(100, -1)
        grid.attach(ri_lbl,        0, row, 1, 1)
        grid.attach(self._ri_spin, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, row, 2, 1)
        row += 1

        op_lbl = Gtk.Label(label="Opacity (floating window)")
        op_lbl.set_halign(Gtk.Align.START)
        op_adj = Gtk.Adjustment(
            value=settings.opacity,
            lower=0.1, upper=1.0,
            step_increment=0.05, page_increment=0.1,
        )
        self._op_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=op_adj)
        self._op_scale.set_size_request(200, -1)
        self._op_scale.set_digits(2)
        self._op_scale.set_draw_value(True)
        grid.attach(op_lbl,         0, row, 1, 1)
        grid.attach(self._op_scale, 1, row, 1, 1)
        row += 1

        self._pop_check = Gtk.CheckButton(label="Also apply opacity to left-click popup")
        self._pop_check.set_active(settings.opacity_popup)
        grid.attach(self._pop_check, 0, row, 2, 1)
        row += 1

        self._kb_check = Gtk.CheckButton(label="Keep floating window below other windows")
        self._kb_check.set_active(settings.keep_below)
        grid.attach(self._kb_check, 0, row, 2, 1)
        row += 1

        self._sg_check = Gtk.CheckButton(label="Show activity graph")
        self._sg_check.set_active(settings.show_graph)
        grid.attach(self._sg_check, 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, row, 2, 1)
        row += 1

        bc_lbl = Gtk.Label(label="Progress Bar Color")
        bc_lbl.set_halign(Gtk.Align.START)
        self._bc_btn = Gtk.ColorButton()
        self._bc_btn.set_rgba(_hex_to_rgba(settings.bar_color))
        self._bc_btn.set_title("Choose Progress Bar Color")
        grid.attach(bc_lbl,       0, row, 1, 1)
        grid.attach(self._bc_btn, 1, row, 1, 1)
        row += 1

        bg_lbl = Gtk.Label(label="Background Color")
        bg_lbl.set_halign(Gtk.Align.START)
        self._bg_btn = Gtk.ColorButton()
        self._bg_btn.set_rgba(_hex_to_rgba(settings.bg_color))
        self._bg_btn.set_title("Choose Background Color")
        grid.attach(bg_lbl,       0, row, 1, 1)
        grid.attach(self._bg_btn, 1, row, 1, 1)
        row += 1

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_margin_top(4)

        restart_btn = Gtk.Button(label="Restart")
        restart_btn.connect("clicked", self._restart)
        btn_box.pack_start(restart_btn, False, False, 0)

        spacer = Gtk.Box()
        btn_box.pack_start(spacer, True, True, 0)

        reset_btn = Gtk.Button(label="Reset Defaults")
        reset_btn.connect("clicked", self._reset_defaults)
        btn_box.pack_start(reset_btn, False, False, 0)

        apply_btn = Gtk.Button(label="Apply")
        apply_btn.connect("clicked", self._apply)
        btn_box.pack_start(apply_btn, False, False, 0)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _: self._win.hide())
        btn_box.pack_start(close_btn, False, False, 0)

        outer.pack_start(btn_box, False, False, 0)

    def _collect(self) -> AppSettings:
        return AppSettings(
            auto_refresh     = self._ar_check.get_active(),
            refresh_interval = int(self._ri_spin.get_value()),
            opacity          = round(self._op_scale.get_value(), 2),
            opacity_popup    = self._pop_check.get_active(),
            keep_below       = self._kb_check.get_active(),
            show_graph       = self._sg_check.get_active(),
            bar_color        = _rgba_to_hex(self._bc_btn.get_rgba()),
            bg_color         = _rgba_to_hex(self._bg_btn.get_rgba()),
        )

    def _apply(self, _):
        self._on_apply(self._collect())

    def _restart(self, _):
        self._on_apply(self._collect())
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _reset_defaults(self, _):
        defaults = AppSettings()
        self._ar_check.set_active(defaults.auto_refresh)
        self._ri_spin.set_value(defaults.refresh_interval)
        self._op_scale.set_value(defaults.opacity)
        self._pop_check.set_active(defaults.opacity_popup)
        self._kb_check.set_active(defaults.keep_below)
        self._sg_check.set_active(defaults.show_graph)
        self._bc_btn.set_rgba(_hex_to_rgba(defaults.bar_color))
        self._bg_btn.set_rgba(_hex_to_rgba(defaults.bg_color))
        self._on_apply(defaults)

    def sync(self, settings: AppSettings):
        self._ar_check.set_active(settings.auto_refresh)
        self._ri_spin.set_value(settings.refresh_interval)
        self._op_scale.set_value(settings.opacity)
        self._pop_check.set_active(settings.opacity_popup)
        self._kb_check.set_active(settings.keep_below)
        self._sg_check.set_active(settings.show_graph)
        self._bc_btn.set_rgba(_hex_to_rgba(settings.bar_color))
        self._bg_btn.set_rgba(_hex_to_rgba(settings.bg_color))

    def show(self):
        self._win.show_all()
        self._win.present()


# ── Main app ──────────────────────────────────────────────────────────────────
class ClaudeTrayApp:
    def __init__(self):
        self._settings = self._load_settings()

        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_data(
            build_css(self._settings.bg_color, self._settings.bar_color).encode()
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            self._css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        if not ICON_FILE.exists():
            create_tray_icon(ICON_FILE)

        self._fetcher         = ClaudeUsageFetcher()
        self._session_reader  = ClaudeSessionReader()
        self._activity_reader = ActivityReader()
        self._popup          = LeftClickPopup(on_refresh=self._do_refresh)
        self._float          = FloatingWindow(
            on_refresh      = self._do_refresh,
            on_scale_change = self._on_float_scale,
        )
        self._float.apply_z_order(self._settings.keep_below)
        self._float.set_graph_visible(self._settings.show_graph)
        self._float.apply_graph_colors(self._settings.bar_color, self._settings.bg_color)
        self._settings_dlg = SettingsDialog(self._settings, on_apply=self._apply_settings)

        self._apply_opacity()

        self._fetcher._load_cookies()

        self._tray = Gtk.StatusIcon()
        self._tray.set_from_file(str(ICON_FILE))
        self._tray.set_tooltip_text("Claude Usage")
        self._tray.set_visible(True)
        self._tray.connect("activate",   self._on_left_click)
        self._tray.connect("popup-menu", self._on_right_click)

        if self._float.was_visible():
            self._float.show()

        self._do_refresh()
        self._refresh_timer_id = GLib.timeout_add_seconds(
            self._settings.refresh_interval, self._refresh_timer
        )
        GLib.timeout_add_seconds(30, self._ui_tick)

    # ── Scale callback ────────────────────────────────────────────────────────
    def _on_float_scale(self, scale: float):
        self._float._graph.set_scale(scale)
        self._css_provider.load_from_data(
            build_css(self._settings.bg_color, self._settings.bar_color, scale).encode()
        )

    # ── Settings persistence ──────────────────────────────────────────────────
    def _load_settings(self) -> AppSettings:
        if CONFIG_FILE.exists():
            try:
                s = json.loads(CONFIG_FILE.read_text()).get("settings", {})
                return AppSettings(
                    auto_refresh     = s.get("auto_refresh",     True),
                    refresh_interval = s.get("refresh_interval", 60),
                    opacity          = s.get("opacity",          1.0),
                    opacity_popup    = s.get("opacity_popup",    False),
                    keep_below       = s.get("keep_below",       False),
                    show_graph       = s.get("show_graph",       True),
                    bar_color        = s.get("bar_color",        ACCENT),
                    bg_color         = s.get("bg_color",         BG),
                )
            except Exception:
                pass
        return AppSettings()

    def _save_settings(self):
        cfg = {}
        if CONFIG_FILE.exists():
            try:
                cfg = json.loads(CONFIG_FILE.read_text())
            except Exception:
                pass
        cfg["settings"] = {
            "auto_refresh":     self._settings.auto_refresh,
            "refresh_interval": self._settings.refresh_interval,
            "opacity":          self._settings.opacity,
            "opacity_popup":    self._settings.opacity_popup,
            "keep_below":       self._settings.keep_below,
            "show_graph":       self._settings.show_graph,
            "bar_color":        self._settings.bar_color,
            "bg_color":         self._settings.bg_color,
        }
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

    def _apply_settings(self, settings: AppSettings):
        interval_changed = settings.refresh_interval != self._settings.refresh_interval
        self._settings = settings

        self._css_provider.load_from_data(
            build_css(settings.bg_color, settings.bar_color,
                      self._float.get_scale()).encode()
        )
        self._apply_opacity()
        self._float.apply_z_order(settings.keep_below)
        self._float.set_graph_visible(settings.show_graph)
        self._float.apply_graph_colors(settings.bar_color, settings.bg_color)

        if interval_changed:
            GLib.source_remove(self._refresh_timer_id)
            self._refresh_timer_id = GLib.timeout_add_seconds(
                settings.refresh_interval, self._refresh_timer
            )

        self._save_settings()
        GLib.idle_add(self._on_data_ready)

    def _apply_opacity(self):
        self._float._win.set_opacity(self._settings.opacity)
        popup_opacity = self._settings.opacity if self._settings.opacity_popup else 1.0
        self._popup._win.set_opacity(popup_opacity)

    # ── Data flow ─────────────────────────────────────────────────────────────
    def _do_refresh(self):
        threading.Thread(target=self._fetch_thread, daemon=True).start()

    def _fetch_thread(self):
        self._fetcher.refresh()
        self._activity_cache = self._activity_reader.read()
        GLib.idle_add(self._on_data_ready)

    def _merge_sessions(self, data: UsageData) -> UsageData:
        return replace(
            data,
            sessions = self._session_reader.read(),
            activity = getattr(self, "_activity_cache", {}),
        )

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
        if self._settings.auto_refresh:
            self._do_refresh()
        return True

    def _ui_tick(self) -> bool:
        data = self._merge_sessions(self._fetcher.get())
        self._popup.update(data)
        if self._float.is_visible():
            self._float.update(data)
        return True

    # ── Tray events ───────────────────────────────────────────────────────────
    def _on_left_click(self, icon):
        display = Gdk.Display.get_default()
        _, cx, cy = display.get_default_seat().get_pointer().get_position()
        self._popup.update(self._merge_sessions(self._fetcher.get()))
        self._popup.toggle(cx, cy)

    def _on_right_click(self, icon, button, time):
        menu = Gtk.Menu()

        lbl    = "Hide Floating Window" if self._float.is_visible() else "Show Floating Window"
        toggle = Gtk.MenuItem(label=lbl)
        toggle.connect("activate", self._toggle_float)
        menu.append(toggle)

        settings_item = Gtk.MenuItem(label="Settings…")
        settings_item.connect("activate", lambda _: self._settings_dlg.show())
        menu.append(settings_item)

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

    def run(self):
        Gtk.main()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    os.environ.setdefault("GDK_BACKEND", "x11")
    ClaudeTrayApp().run()
