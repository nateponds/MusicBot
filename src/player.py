"""
Core music player service
"""
import discord
import asyncio
import logging
import shutil
import os
import sys
import subprocess
import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Iterator
from .queue import Queue, Track
from .providers import YouTubeProvider, SpotifyProvider
from .cache import AudioCache
from config import Config


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FFmpegCandidate:
    executable: str
    source: str
    bundled: bool = False


@dataclass(frozen=True)
class FFmpegResolution:
    executable: str
    source: str
    version: str


def _iter_ffmpeg_candidates() -> Iterator[FFmpegCandidate]:
    candidates: list[FFmpegCandidate] = []
    if Config.FFMPEG_EXECUTABLE:
        candidates.append(FFmpegCandidate(Config.FFMPEG_EXECUTABLE, "configured"))

    path_candidate = shutil.which("ffmpeg")
    if path_candidate:
        candidates.append(FFmpegCandidate(path_candidate, "path"))

    if sys.platform == "win32":
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            winget_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
            if winget_root.exists():
                try:
                    candidates.extend(
                        FFmpegCandidate(str(path), "winget")
                        for path in winget_root.rglob("ffmpeg.exe")
                    )
                except Exception:
                    logger.debug("WinGet search failed", exc_info=True)
        candidates.extend(
            FFmpegCandidate(str(path), "program-files")
            for path in (
                Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin" / "ffmpeg.exe",
                Path(os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "ffmpeg" / "bin" / "ffmpeg.exe",
            )
            if path.exists()
        )
    elif sys.platform.startswith("linux"):
        candidates.extend(
            FFmpegCandidate(str(path), "system-path")
            for path in (Path("/usr/local/bin/ffmpeg"), Path("/usr/bin/ffmpeg"))
            if path.exists()
        )

    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).exists():
            if sys.platform.startswith("linux"):
                logger.warning(
                    "imageio-ffmpeg candidate (%s) is a last-resort fallback on Linux; system FFmpeg is strongly recommended",
                    bundled,
                )
            candidates.append(FFmpegCandidate(bundled, "imageio", bundled=True))
    except (ImportError, AttributeError, FileNotFoundError, RuntimeError, OSError):
        logger.debug("imageio-ffmpeg candidate is unavailable", exc_info=True)

    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = os.path.normcase(os.path.abspath(candidate.executable))
        except Exception:
            key = candidate.executable
        if key not in seen:
            seen.add(key)
            yield candidate


def validate_ffmpeg_candidate(candidate: FFmpegCandidate) -> Optional[FFmpegResolution]:
    logger.debug("Validating FFmpeg candidate [%s]: %s", candidate.source, candidate.executable)
    try:
        version_result = subprocess.run(
            [candidate.executable, "-hide_banner", "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
        if version_result.returncode != 0:
            logger.warning(
                "FFmpeg candidate [%s] -version failed with returncode %s: %s",
                candidate.source,
                version_result.returncode,
                version_result.stderr.strip()[:200],
            )
            return None

        version_line = version_result.stdout.splitlines()[0] if version_result.stdout else "unknown"

        codec_result = subprocess.run(
            [
                candidate.executable,
                "-hide_banner",
                "-loglevel", "error",
                "-f", "lavfi",
                "-i", "anullsrc=r=48000:cl=stereo",
                "-t", "0.1",
                "-c:a", "libopus",
                "-f", "opus",
                "-",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        if codec_result.returncode != 0:
            logger.warning(
                "FFmpeg candidate [%s] opus probe failed with returncode %s: %s",
                candidate.source,
                codec_result.returncode,
                codec_result.stderr.strip()[:200],
            )
            return None

        return FFmpegResolution(
            executable=candidate.executable,
            source=candidate.source,
            version=version_line,
        )
    except subprocess.TimeoutExpired:
        logger.warning("FFmpeg candidate [%s] timed out during validation", candidate.source)
        return None
    except (OSError, PermissionError) as e:
        logger.warning("FFmpeg candidate [%s] validation error: %s", candidate.source, e)
        return None
    except Exception as e:
        logger.warning("FFmpeg candidate [%s] unexpected validation failure: %s", candidate.source, e, exc_info=True)
        return None


@functools.lru_cache(maxsize=1)
def resolve_ffmpeg() -> Optional[FFmpegResolution]:
    """Deterministically resolve and validate an FFmpeg executable candidate."""
    for candidate in _iter_ffmpeg_candidates():
        res = validate_ffmpeg_candidate(candidate)
        if res is not None:
            return res
    return None


def find_ffmpeg_executable() -> Optional[str]:
    """Locate ffmpeg executable using deterministic resolution."""
    res = resolve_ffmpeg()
    return res.executable if res else None


class MusicPlayer:
    """Main music player service"""

    def __init__(self, bot):
        self.bot = bot
        self.queue = Queue()
        self.youtube = YouTubeProvider()
        self.spotify = SpotifyProvider(
            Config.SPOTIFY_CLIENT_ID,
            Config.SPOTIFY_CLIENT_SECRET
        )
        self.cache = AudioCache()
        
        # Per-guild players
        self.players: Dict[int, 'GuildPlayer'] = {}
        self.current_volume = Config.DEFAULT_VOLUME
    
    def get_player(self, guild_id: int) -> 'GuildPlayer':
        """Get or create player for guild"""
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer(guild_id, self)
        return self.players[guild_id]
    
    async def search_youtube(self, query: str) -> list:
        """Search YouTube"""
        return await self.youtube.search(query, limit=5)
    
    async def search_spotify(self, query: str) -> list:
        """Search Spotify"""
        return await self.spotify.search(query, limit=5)
    
    async def play_youtube(self, url: str, ctx) -> Optional[Track]:
        """Play from YouTube URL"""
        player = self.get_player(ctx.guild.id)
        await player.queue.add(Track(
            title="Track",
            url=url,
            duration=0,
            source="youtube",
            artist="Unknown",
            added_by_id=ctx.author.id,
            added_by_name=ctx.author.name,
        ))
        return player.queue.tracks[-1] if player.queue.tracks else None
    
    async def set_volume(self, guild_id: int, volume: int) -> None:
        """Set player volume"""
        player = self.get_player(guild_id)
        player.volume = max(0, min(100, volume))
    
    async def cleanup(self, guild_id: int) -> None:
        """Cleanup player for guild"""
        if guild_id in self.players:
            del self.players[guild_id]


class GuildPlayer:
    """Per-guild music player"""
    
    def __init__(self, guild_id: int, music_player: MusicPlayer):
        self.guild_id = guild_id
        self.music_player = music_player
        self.queue = Queue()
        self.is_playing = False
        self.is_paused = False
        self.volume = Config.DEFAULT_VOLUME
        self.current_track: Optional[Track] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self._play_task: Optional[asyncio.Task] = None
    
    async def play_next(self) -> None:
        """Play next track in queue with robust error handling.
        
        Handles FFmpeg failures gracefully, preventing state corruption.
        IMPORTANT: System FFmpeg (not bundled imageio-ffmpeg) is required on Linux
        for audio playback to work reliably.
        """
        if self.is_paused:
            return
        
        next_track = await self.queue.get_next()
        if next_track:
            self.current_track = next_track
            self.is_playing = True
            logger.info("Guild %s preparing track: %s - %s", self.guild_id, next_track.artist, next_track.title)
            
            if not self.voice_client:
                logger.warning("Guild %s has no voice client while starting playback", self.guild_id)
                self.current_track = None
                self.is_playing = False
                return

            try:
                ffmpeg_path = find_ffmpeg_executable()
                if not ffmpeg_path:
                    raise RuntimeError(
                        "FFmpeg is required for voice playback but was not found. "
                        "On Linux, install: sudo apt install ffmpeg libopus0 libopus-dev"
                    )
                logger.info("Guild %s using FFmpeg at %s", self.guild_id, ffmpeg_path)

                stream_url = await self.music_player.youtube.get_stream_url(next_track.url)
                if not stream_url:
                    raise RuntimeError(f"Could not resolve stream URL for {next_track.url}")
                logger.info("Guild %s stream URL resolved", self.guild_id)

                # from_probe runs the FFmpeg binary; an incompatible binary can
                # fail or segfault before the playback callback is registered.
                source = await discord.FFmpegOpusAudio.from_probe(
                    stream_url,
                    method="fallback",
                    before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                    executable=ffmpeg_path,
                )
            except Exception as e:
                error_code = getattr(e, 'returncode', 'N/A')
                logger.error(
                    "Guild %s failed to prepare '%s': %s (exit code: %s). "
                    "If the exit code is -11 (SIGSEGV), install system FFmpeg: "
                    "sudo apt install ffmpeg libopus0 libopus-dev",
                    self.guild_id,
                    next_track.title,
                    e,
                    error_code,
                    exc_info=True,
                )
                self.current_track = None
                self.is_playing = False
                await self.play_next()
                return

            def _after_playback(error: Optional[Exception]):
                """Callback after playback ends or fails"""
                if error:
                    error_code = getattr(error, 'returncode', 'N/A')
                    logger.error(
                        "Guild %s playback error: %s (exit code: %s). "
                        "If exit code is -11, the FFmpeg binary may be incompatible with this system.",
                        self.guild_id,
                        type(error).__name__,
                        error_code,
                        exc_info=logger.isEnabledFor(logging.DEBUG)
                    )
                else:
                    logger.info("Guild %s finished track: %s", self.guild_id, next_track.title)
                
                # Always clear current track state and move to next
                self.current_track = None
                self.is_playing = False
                asyncio.run_coroutine_threadsafe(self.play_next(), self.music_player.bot.loop)

            logger.info("Guild %s starting voice playback", self.guild_id)
            try:
                self.voice_client.play(source, after=_after_playback)
            except Exception as e:
                logger.error("Guild %s: Failed to start playback: %s", self.guild_id, e, exc_info=True)
                self.current_track = None
                self.is_playing = False
                await self.play_next()
        else:
            self.is_playing = False
            self.current_track = None
            logger.info("Guild %s queue empty", self.guild_id)
    
    async def pause(self) -> bool:
        """Pause playback"""
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            self.is_paused = True
            self.is_playing = True
            return True
        return False
    
    async def resume(self) -> bool:
        """Resume playback"""
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            self.is_paused = False
            self.is_playing = True
            return True
        return False
    
    async def stop(self) -> None:
        """Stop playback and clear queue"""
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.voice_client.stop()
        self.is_playing = False
        self.is_paused = False
        self.current_track = None
        await self.queue.clear()
    
    async def skip(self) -> Optional[Track]:
        """Skip currently playing track"""
        next_track = await self.queue.peek_next()
        self.is_paused = False

        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            # Stopping triggers the after-callback, which calls play_next().
            self.voice_client.stop()
        elif next_track:
            await self.play_next()

        return next_track
    
    async def previous(self) -> Optional[Track]:
        """Play previous track"""
        prev_track = await self.queue.prepare_previous()
        if not prev_track:
            return None

        self.is_paused = False
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            # Stopping triggers the after-callback, which will play the prepared previous track.
            self.voice_client.stop()
        else:
            await self.play_next()

        return prev_track
    
    async def disconnect(self) -> None:
        """Disconnect from voice"""
        if self.voice_client:
            logger.info("Guild %s disconnecting from voice", self.guild_id)
            await self.voice_client.disconnect()
            self.voice_client = None
        self.is_playing = False
        self.current_track = None
