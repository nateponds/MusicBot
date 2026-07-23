#!/usr/bin/env bash
# Local installer script for the Discord Music Bot
# Usage (from repository root): ./install.sh
#
# IMPORTANT: FFmpeg and Opus MUST be installed as system packages on Linux
# for reliable audio playback. The bundled imageio-ffmpeg fails on modern
# glibc systems (Ubuntu 24.04+, GLIBC 2.39+).
set -euo pipefail

REPO_DIR="$(pwd)"
VENV_DIR="$REPO_DIR/venv"

echo "Installing into: $REPO_DIR"

# Ensure Python is available
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.11+ first." >&2
  exit 1
fi

# Check for system FFmpeg and Opus (REQUIRED for audio playback on Linux)
echo "Checking for system dependencies..."
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "❌ ERROR: ffmpeg not found in PATH. Audio playback will fail." >&2
  echo "" >&2
  echo "Install system FFmpeg and Opus with one of these commands:" >&2
  echo "  Debian/Ubuntu:  sudo apt update && sudo apt install -y ffmpeg libopus0 libopus-dev" >&2
  echo "  Fedora/RHEL:    sudo dnf install -y ffmpeg opus-devel" >&2
  echo "  Alpine:         sudo apk add ffmpeg opus-dev" >&2
  echo "  macOS:          brew install ffmpeg opus" >&2
  echo "" >&2
  exit 1
fi

if ! ldconfig -p 2>/dev/null | grep -q libopus >/dev/null 2>&1; then
  echo "⚠️  WARNING: libopus not found. Audio encoding may fail." >&2
  echo "Install with: sudo apt install -y libopus0 libopus-dev (Debian/Ubuntu)" >&2
  echo "           or: sudo dnf install -y opus-devel (Fedora/RHEL)" >&2
fi

echo "✅ System dependencies found."
echo ""

echo "Creating virtual environment in $VENV_DIR (if missing)"
python3 -m venv "$VENV_DIR"

echo "Activating virtualenv and installing Python dependencies"
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
else
  echo "requirements.txt not found; please ensure it exists." >&2
fi

# Copy example .env if missing
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "Created .env from .env.example — please edit .env with your DISCORD_TOKEN and other secrets."
fi

echo ""
echo "✅ Installation complete. To test-run the bot now:"
echo "  source venv/bin/activate"
echo "  python index.py"

echo ""
echo "To create a systemd service (runs on boot, auto-restart on failure):"
echo "  sudo ./install.sh --systemd"

if [ "${1-}" = "--systemd" ]; then
  if [ "$(id -u)" -ne 0 ]; then
    echo "--systemd requires root. Rerun with sudo." >&2
    exit 1
  fi

  BOT_USER="${SUDO_USER:-$(logname || echo $USER)}"
  SERVICE_PATH="/etc/systemd/system/discord-music-bot.service"

  echo "Creating systemd service for user: $BOT_USER"
  cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Discord Music Bot
After=network.target

[Service]
Type=simple
User=$BOT_USER
WorkingDirectory=$REPO_DIR
Environment="PATH=$VENV_DIR/bin"
ExecStart=$VENV_DIR/bin/python $REPO_DIR/index.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable discord-music-bot
  echo ""
  echo "✅ Systemd service created at $SERVICE_PATH"
  echo "Start with: sudo systemctl start discord-music-bot"
  echo "View logs: sudo journalctl -u discord-music-bot -f"
fi

# Optionally run the bot after install
if [ "${1-}" = "--run" ]; then
  echo ""
  echo "🎵 Starting bot in foreground... (use Ctrl+C to stop)"
  # Activate venv and run
  # shellcheck source=/dev/null
  source "$VENV_DIR/bin/activate"
  python "$REPO_DIR/index.py"
fi
