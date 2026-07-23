#!/usr/bin/env bash
# Local installer script for the Discord Music Bot
# Usage (from repository root): ./install.sh [--install-system-deps] [--systemd] [--run]
set -euo pipefail

REPO_DIR="$(pwd)"
VENV_DIR="$REPO_DIR/venv"

INSTALL_SYSTEM_DEPS=0
DO_SYSTEMD=0
DO_RUN=0

for arg in "$@"; do
  case "$arg" in
    --install-system-deps) INSTALL_SYSTEM_DEPS=1 ;;
    --systemd) DO_SYSTEMD=1 ;;
    --run) DO_RUN=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

echo "Installing into: $REPO_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.11+ first." >&2
  exit 1
fi

check_ffmpeg_capability() {
  local ffmpeg_bin="$1"
  if ! "$ffmpeg_bin" -hide_banner -version >/dev/null 2>&1; then
    return 1
  fi
  if ! "$ffmpeg_bin" -hide_banner -loglevel error -f lavfi -i anullsrc=r=48000:cl=stereo -t 0.1 -c:a libopus -f opus - >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

FFMPEG_BIN="$(command -v ffmpeg || true)"

if [ -z "$FFMPEG_BIN" ] || ! check_ffmpeg_capability "$FFMPEG_BIN"; then
  echo "⚠️  System FFmpeg with Opus support was not found or failed capability check."

  if [ -f /etc/debian_version ]; then
    if [ "$INSTALL_SYSTEM_DEPS" -eq 1 ]; then
      echo "Installing system FFmpeg & Opus via apt-get..."
      if [ "$(id -u)" -eq 0 ]; then
        apt-get update && apt-get install -y ffmpeg libopus0
      else
        sudo apt-get update && sudo apt-get install -y ffmpeg libopus0
      fi
      FFMPEG_BIN="$(command -v ffmpeg || true)"
    elif [ -t 0 ]; then
      read -rp "Install system FFmpeg and libopus0 via apt? [y/N] " response
      if [[ "$response" =~ ^[Yy]$ ]]; then
        if [ "$(id -u)" -eq 0 ]; then
          apt-get update && apt-get install -y ffmpeg libopus0
        else
          sudo apt-get update && sudo apt-get install -y ffmpeg libopus0
        fi
        FFMPEG_BIN="$(command -v ffmpeg || true)"
      fi
    else
      echo "Non-interactive shell detected. Run:" >&2
      echo "  sudo apt-get update && sudo apt-get install -y ffmpeg libopus0" >&2
      echo "or rerun with --install-system-deps." >&2
      exit 1
    fi
  else
    echo "Please install FFmpeg with libopus support for your distribution:" >&2
    echo "  Fedora:  sudo dnf install -y ffmpeg-free opus" >&2
    echo "  Arch:    sudo pacman -S ffmpeg opus" >&2
    echo "  macOS:   brew install ffmpeg opus" >&2
    exit 1
  fi
fi

if [ -z "$FFMPEG_BIN" ] || ! check_ffmpeg_capability "$FFMPEG_BIN"; then
  echo "❌ ERROR: FFmpeg validation failed after installation check." >&2
  exit 1
fi

echo "✅ Verified working FFmpeg executable: $FFMPEG_BIN"

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

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "Created .env from .env.example — edit .env with DISCORD_TOKEN."
fi

if [ "$DO_SYSTEMD" -eq 1 ]; then
  if [ "$(id -u)" -ne 0 ]; then
    echo "--systemd requires root. Rerun with sudo." >&2
    exit 1
  fi

  BOT_USER="${SUDO_USER:-$(logname 2>/dev/null || echo $USER)}"
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
Environment="PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$VENV_DIR/bin/python $REPO_DIR/index.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable discord-music-bot
  echo "✅ Systemd service created at $SERVICE_PATH"
fi

if [ "$DO_RUN" -eq 1 ]; then
  echo "🎵 Starting bot..."
  # shellcheck source=/dev/null
  source "$VENV_DIR/bin/activate"
  python "$REPO_DIR/index.py"
fi
