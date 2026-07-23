"""Self-check: a failed stream/probe must not deadlock the queue.

Runnable standalone: `python tests/test_play_next_failure.py`
Asserts play_next() recovers from a from_probe crash by advancing to the next
track instead of leaving is_playing stuck True (the Issue #2 deadlock).
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.player import MusicPlayer, GuildPlayer  # noqa: E402
from src.queue import Track  # noqa: E402


def _track(title):
    return Track(title=title, url=f"http://x/{title}", duration=0, source="youtube", artist="a")


class _FakeVoice:
    """Records .play() calls without touching real audio."""
    def __init__(self):
        self.played = []
    def play(self, source, after=None):
        self.played.append(source)
    def is_playing(self): return False
    def is_paused(self): return False


async def _run():
    mp = MusicPlayer.__new__(MusicPlayer)  # skip real __init__ (network providers)
    class _Bot: loop = asyncio.get_event_loop()
    mp.bot = _Bot()

    gp = GuildPlayer(guild_id=1, music_player=mp)
    gp.voice_client = _FakeVoice()
    await gp.queue.add(_track("broken"))
    await gp.queue.add(_track("good"))

    # get_stream_url succeeds; from_probe raises (simulates segfault/binary failure)
    # for the FIRST track only, then works for the second.
    async def fake_stream(url): return "http://stream"
    mp.youtube = type("Y", (), {"get_stream_url": staticmethod(fake_stream)})()

    calls = {"n": 0}
    async def fake_probe(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated ffmpeg SIGSEGV")
        return object()  # a "source" for the good track

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=fake_probe):
        await gp.play_next()

    # First track blew up but the queue advanced and the second track started playing.
    assert calls["n"] == 2, f"expected fallback to 2nd track, probe called {calls['n']}x"
    assert len(gp.voice_client.played) == 1, "good track should be playing"
    assert gp.current_track is not None and gp.current_track.title == "good"
    print("OK: play_next recovers from probe failure and advances the queue")


if __name__ == "__main__":
    asyncio.run(_run())
