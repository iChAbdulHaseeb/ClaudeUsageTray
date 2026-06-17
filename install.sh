#!/usr/bin/env bash
# Claude Usage Tray — one-time setup script
# Works on Ubuntu 20.04+ / Debian-based systems with GNOME on X11
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$SCRIPT_DIR/claude_tray.py"
PYTHON=/usr/bin/python3
PIP=/usr/bin/pip3

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "  Claude Usage Tray — Setup"
echo "  ─────────────────────────"
echo ""

# ── 1. Python ─────────────────────────────────────────────────────────────────
if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: $PYTHON not found."
    echo "       Install it with:  sudo apt install python3"
    exit 1
fi
echo "✓  Python  $($PYTHON --version 2>&1)"

# ── 2. GTK3 system packages ───────────────────────────────────────────────────
GTK_PKGS=(python3-gi python3-gi-cairo gir1.2-gtk-3.0)
MISSING=()
for pkg in "${GTK_PKGS[@]}"; do
    dpkg -s "$pkg" &>/dev/null || MISSING+=("$pkg")
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "→  Installing system packages: ${MISSING[*]}"
    echo "   (this requires sudo)"
    sudo apt-get install -y "${MISSING[@]}"
fi
echo "✓  GTK3 bindings"

# ── 3. Python packages ────────────────────────────────────────────────────────
echo "→  Installing Python packages…"
# curl_cffi lets us impersonate Chrome's TLS fingerprint (needed for claude.ai)
"$PIP" install --user --quiet --upgrade requests browser-cookie3 curl_cffi
echo "✓  Python packages"

# ── 4. Make app executable ────────────────────────────────────────────────────
chmod +x "$APP"

# ── 5. Application menu shortcut ─────────────────────────────────────────────
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
cat > "$APPS_DIR/claude-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Claude Usage
GenericName=AI Usage Monitor
Comment=Shows Claude 5-hour and weekly usage limits in the system tray
Exec=$PYTHON $APP
Icon=$HOME/.config/claude-tray/icon.png
Terminal=false
Categories=Utility;Monitor;
Keywords=claude;ai;usage;limit;tray;
StartupNotify=false
EOF
update-desktop-database "$APPS_DIR" 2>/dev/null || true
echo "✓  App menu entry  (search 'Claude Usage')"

# ── 6. Autostart on login ─────────────────────────────────────────────────────
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/claude-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Claude Usage Tray
Exec=$PYTHON $APP
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
echo "✓  Autostart on login"

# ── 7. Start the app now ──────────────────────────────────────────────────────
# Kill any existing instance first
pkill -f "python3.*claude_tray" 2>/dev/null || true
sleep 1

DISP="${DISPLAY:-:0}"
nohup "$PYTHON" "$APP" > /tmp/claude-tray.log 2>&1 &
echo "✓  Started  (PID $!,  DISPLAY=$DISP)"

echo ""
echo "  Done! The tray icon should appear shortly."
echo "  ┌─────────────────────────────────────────────────────┐"
echo "  │  First-time setup:                                  │"
echo "  │  1. Make sure you're logged into claude.ai          │"
echo "  │     in Chrome or Firefox first.                     │"
echo "  │  2. Left-click the tray icon to see usage.          │"
echo "  │  3. Right-click for options / floating window.      │"
echo "  │                                                     │"
echo "  │  If the icon doesn't appear: log out and back in,  │"
echo "  │  or install GNOME Shell's AppIndicator extension.  │"
echo "  └─────────────────────────────────────────────────────┘"
echo ""
