# Installation and Setup (Windows & Linux)

This guide covers final, tested steps to install and run the Discord Music Bot on both Linux (Debian/Ubuntu-like) and Windows (PowerShell).

---

## ⚠️ CRITICAL: FFmpeg Requirement on Linux

**Linux users MUST install system FFmpeg and Opus.** The bundled `imageio-ffmpeg` fails on modern Linux glibc systems (Ubuntu 24.04+, GLIBC 2.39+) with **FFmpeg exit code -11 (SIGSEGV)**, causing audio playback to fail.

**Install on Linux:**
```bash
sudo apt update && sudo apt install -y ffmpeg libopus0 libopus-dev
```

**Windows**: The bundled FFmpeg fallback is normally sufficient. **macOS**:
install FFmpeg and Opus with Homebrew as shown in the macOS section.

---

## Quick summary
- **Linux**: `./install.sh` (checks for system FFmpeg), then `sudo ./install.sh --systemd` for persistent service.
- **Windows**: `.\install.ps1 -Run` for quick testing, or `.\install.ps1` to set up with Task Scheduler.
- **Python**: 3.11+ required on all platforms.
- **Python audio packages**: `imageio-ffmpeg` supplies an FFmpeg fallback;
  `opuslib` is a Python binding, not a replacement for Linux's native Opus runtime.

---

## Prerequisites

### All Platforms
- **Python 3.11+** installed and on PATH
- A Discord application bot token (from [Discord Developer Portal](https://discord.com/developers/applications))
- Git (optional, for cloning; you can also download ZIP)

### Linux Only
- **FFmpeg and Opus system libraries** (REQUIRED for audio playback):
  - Debian/Ubuntu: `sudo apt install ffmpeg libopus0 libopus-dev`
  - Fedora/RHEL: `sudo dnf install ffmpeg opus-devel`
  - Alpine: `sudo apk add ffmpeg opus-dev`

---

## Files to know
- `index.py` — main bot launcher
- `requirements.txt` — Python dependencies
- `install.sh` — Linux installer (creates `venv`, checks for system FFmpeg)
- `install.ps1` — Windows installer (creates `venv`)
- `INSTALL.md` — this guide
- `.env.example` — sample environment file

---

## 1) Linux (Debian / Ubuntu / similar)

### A. Install system dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git ffmpeg libopus0 libopus-dev
```

**Verify installation:**
```bash
ffmpeg -version
ldconfig -p | grep libopus
```

### B. Clone/extract and run installer

```bash
cd /path/to/discord-music-bot
./install.sh --run
```

The script will:
1. ✅ Check for system FFmpeg and Opus
2. Create a `venv` in the repo
3. Install `requirements.txt` dependencies
4. Copy `.env.example` → `.env` if missing

If FFmpeg/Opus is not found, the installer will **fail with clear instructions** on how to install them.

### C. Edit `.env`

Open `.env` and set your credentials:
```env
DISCORD_TOKEN=your_token_here
# Optional:
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
GENIUS_ACCESS_TOKEN=...
```

For restricted YouTube videos/playlists:
```env
YTDLP_COOKIES_FROM_BROWSER=edge
```

### D. Test run

```bash
source venv/bin/activate
python index.py
```

Press `Ctrl+C` to stop.

### E. Make it persistent with systemd (recommended for 24/7)

```bash
sudo ./install.sh --systemd
sudo systemctl start discord-music-bot
sudo systemctl enable discord-music-bot
```

**View logs:**
```bash
sudo journalctl -u discord-music-bot -f
```

**Restart/Stop:**
```bash
sudo systemctl restart discord-music-bot
sudo systemctl stop discord-music-bot
sudo systemctl status discord-music-bot
```

### F. Troubleshooting on Linux

**Audio not playing / FFmpeg exit code -11?**
- System FFmpeg installed? `ffmpeg -version`
- Bundled imageio-ffmpeg is NOT compatible. Use system FFmpeg.
- Fix: `sudo apt install ffmpeg libopus0 libopus-dev`

**FFmpeg or Opus not found?**
```bash
# Check system FFmpeg:
which ffmpeg

# Check Opus library:
ldconfig -p | grep libopus
```

**"FFmpeg is required" error at startup?**
- Reinstall system packages: `sudo apt install ffmpeg libopus0 libopus-dev`

**Logs and monitoring:**
```bash
# View live logs
sudo journalctl -u discord-music-bot -f

# View bot application logs
tail -f logs/bot.log

# Check service status
sudo systemctl status discord-music-bot

# View recent errors
sudo journalctl -u discord-music-bot -n 50 --no-pager
```

---

## 2) Windows (PowerShell)

### A. Install Python

Install Python 3.11+ from [python.org](https://python.org). **Ensure "Add Python to PATH" is checked during installation.**

The Python requirements provide a bundled FFmpeg fallback, so a separate
FFmpeg installation is normally unnecessary on Windows.

**Verify:**
```powershell
python --version
```

### B. Extract and run installer

```powershell
cd C:\Users\YourUser\discord-music-bot
PowerShell -ExecutionPolicy Bypass -File install.ps1 -Run
```

Or step-by-step:
```powershell
cd C:\Users\YourUser\discord-music-bot
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### C. Edit `.env`

```powershell
copy .env.example .env
notepad .env
```

Add your Discord token and optional Spotify/Genius credentials.

### D. Run the bot

```powershell
venv\Scripts\Activate.ps1
python index.py
```

Press `Ctrl+C` to stop.

### E. Run on Windows startup (optional)

#### Option 1: Task Scheduler (simple)

1. Open Task Scheduler (`taskschd.msc`)
2. Create Basic Task → "Discord Music Bot"
3. Trigger: "At log on"
4. Action: Start program
5. Program: `C:\path\to\venv\Scripts\python.exe`
6. Arguments: `C:\path\to\index.py`
7. Check "Run with highest privileges"

#### Option 2: NSSM (recommended for services)

Install [NSSM](https://nssm.cc/download):
```powershell
nssm install DiscordMusicBot C:\path\to\venv\Scripts\python.exe "C:\path\to\index.py"
nssm start DiscordMusicBot
```

### F. Troubleshooting on Windows

**Python not found?**
- Reinstall Python, checking "Add Python to PATH"
- Verify: `python --version`

**Permission denied running .ps1?**
```powershell
PowerShell -ExecutionPolicy Bypass -File install.ps1
```

**View logs:**
```powershell
Get-Content .\logs\bot.log -Wait
```

---

## 3) macOS

### A. Install system dependencies

```bash
brew install python@3.12 ffmpeg opus
```

### B. Clone and install

```bash
cd /path/to/discord-music-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### C. Edit `.env` and run

```bash
cp .env.example .env
# Edit .env with your token
python index.py
```

### D. Keep running 24/7 with launchd

Create `~/Library/LaunchAgents/com.discord-music-bot.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.discord-music-bot</string>
    <key>Program</key>
    <string>/path/to/venv/bin/python</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/index.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Then:
```bash
launchctl load ~/Library/LaunchAgents/com.discord-music-bot.plist
launchctl start com.discord-music-bot
```

---

## 4) Common Issues

### Audio stops after first track / "No track is currently playing"

**Cause**: FFmpeg crash (exit code -11 on Linux).

**Fix on Linux**:
```bash
sudo apt install ffmpeg libopus0 libopus-dev
```

**Fix on Windows/macOS**: Reinstall FFmpeg via Homebrew or direct download.

### "FFmpeg is required for voice playback but was not found"

**Linux**:
```bash
sudo apt install ffmpeg
```

**Windows**: Ensure Python is on PATH and FFmpeg is in the bundled package.

**macOS**:
```bash
brew install ffmpeg
```

### Bot crashes with "exit code -11"

This is a segmentation fault from the bundled FFmpeg on Linux.

**Linux fix:**
```bash
# Remove bundled FFmpeg package (if any)
pip uninstall imageio-ffmpeg
# Install system FFmpeg
sudo apt install ffmpeg libopus0 libopus-dev
```

### Slash commands not appearing in Discord

1. Bot must be in the server with `/applications.commands` scope
2. Ensure bot has "Use Slash Commands" permission
3. Restart the Discord client (`Ctrl+R`)
4. Check bot logs: `journalctl -u discord-music-bot -f` (Linux) or `Get-Content .\logs\bot.log` (Windows)

---

## 5) Helpful commands

**Check system FFmpeg/Opus (Linux):**
```bash
ffmpeg -version
ldconfig -p | grep libopus
```

**View bot logs:**
```bash
# Linux (systemd)
sudo journalctl -u discord-music-bot -f

# Linux (terminal)
tail -f logs/bot.log

# Windows PowerShell
Get-Content .\logs\bot.log -Wait
```

**Restart bot (Linux systemd):**
```bash
sudo systemctl restart discord-music-bot
```

**Stop bot:**
```bash
# Linux systemd
sudo systemctl stop discord-music-bot

# Any: Ctrl+C in terminal
```

**Run tests:**
```bash
source venv/bin/activate  # Linux/macOS
python -m pytest tests/ -v
```

---

## 6) Support

- **Issue**: Check `logs/bot.log` for error messages
- **FFmpeg problems on Linux**: Verify system installation with `ffmpeg -version`
- **Discord token issues**: Ensure token is valid at [Discord Developer Portal](https://discord.com/developers/applications)

---

✨ **Happy music streaming!**
