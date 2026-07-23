# Docker Quick Start

**One-command setup for Linux:**

```bash
git clone https://github.com/lejxz/MusicBot.git
cd MusicBot
cp .env.example .env
# Edit .env and add your DISCORD_TOKEN
docker compose up -d
docker compose logs -f
```

## What's included

- ✅ System FFmpeg and Opus (fixes Linux SIGSEGV issues)
- ✅ Python 3.12 slim image (optimized size)
- ✅ Multi-stage build (smaller image, faster startup)
- ✅ Auto-restart on failure
- ✅ Persistent cache and logs outside container

## Prerequisites

```bash
# Ubuntu/Debian
sudo apt install docker.io docker-compose-plugin

# Verify
docker --version
docker compose version
```

## Usage

### Start the bot
```bash
docker compose up -d
```

### View logs
```bash
docker compose logs -f
```

### Stop the bot
```bash
docker compose stop
```

### Restart
```bash
docker compose restart
```

### Remove everything
```bash
docker compose down
```

## Files

- **Dockerfile** — Multi-stage build with FFmpeg/Opus
- **docker-compose.yml** — Orchestration with volume mounts
- **.dockerignore** — Excludes unnecessary files from image

## Image size

- Base image: `python:3.12-slim` (~150MB)
- With FFmpeg/Opus: ~450MB
- Total with layers: ~600MB

## Persistent data

```bash
# Logs stored in ./logs/ on host
tail -f logs/bot.log

# Cache stored in ./cache/ on host
du -sh cache/

# .env is read-only mounted from host
cat .env
```

## Environment variables

All variables from `.env` are loaded automatically. See `.env.example` for options.

## Troubleshooting

**Permission denied?**
```bash
sudo usermod -aG docker $USER
newgrp docker
```

**Rebuild image:**
```bash
docker compose build --no-cache
```

**Inspect container:**
```bash
docker compose exec discord-music-bot bash
```

**No audio in Discord?**
- Check logs: `docker compose logs -f | grep -i ffmpeg`
- FFmpeg is bundled in the image; this shouldn't happen
- If it does: `docker compose logs -f | head -100`

---

**For more info, see [INSTALL.md](INSTALL.md) "Docker" section.**
