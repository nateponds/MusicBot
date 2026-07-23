"""
Unit tests for GuildPlayer playback recovery, failure handling, concurrency, and thread safe callbacks.
"""
import pytest
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

from src.player import (
    GuildPlayer,
    MusicPlayer,
    PlaybackState,
    FFmpegResolution,
)
from src.queue import Track


class CapturingVoiceClient:
    def __init__(self):
        self.played_sources = []
        self.after_callbacks = []
        self.is_playing_flag = False
        self.is_paused_flag = False
        self.stopped = False

    def is_playing(self):
        return self.is_playing_flag

    def is_paused(self):
        return self.is_paused_flag

    def play(self, source, after=None):
        self.played_sources.append(source)
        self.after_callbacks.append(after)
        self.is_playing_flag = True

    def stop(self):
        self.stopped = True
        self.is_playing_flag = False
        self.is_paused_flag = False


class FakeAudioSource:
    def __init__(self):
        self.cleaned_up = False

    def cleanup(self):
        self.cleaned_up = True


def make_track(title: str) -> Track:
    return Track(title=title, url=f"http://example.com/{title}", duration=180, source="youtube", artist="TestArtist")


import pytest_asyncio

@pytest_asyncio.fixture
async def mock_music_player():
    mp = MagicMock(spec=MusicPlayer)
    mp.bot = MagicMock()
    mp.bot.loop = asyncio.get_running_loop()
    mp.youtube = MagicMock()
    return mp


@pytest.mark.asyncio
async def test_play_next_no_ffmpeg_resolution(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    await player.queue.add(make_track("t1"))

    with patch("src.player.find_ffmpeg_executable", return_value=None):
        await player.play_next()

    assert player._state == PlaybackState.IDLE
    assert player.current_track is None


@pytest.mark.asyncio
async def test_play_next_stream_url_none(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    await player.queue.add(make_track("t1"))
    mock_music_player.youtube.get_stream_url = AsyncMock(return_value=None)

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"):
        await player.play_next()

    assert player._state == PlaybackState.IDLE
    assert player.current_track is None


@pytest.mark.asyncio
async def test_play_next_stream_url_raises(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    await player.queue.add(make_track("t1"))
    mock_music_player.youtube.get_stream_url = AsyncMock(side_effect=TimeoutError("Stream timeout"))

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"):
        await player.play_next()

    assert player._state == PlaybackState.IDLE
    assert player.current_track is None


@pytest.mark.asyncio
async def test_play_next_probe_sigsegv_exit_code_11(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    await player.queue.add(make_track("t1"))
    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    err = RuntimeError("FFmpeg segfault")
    setattr(err, "returncode", -11)

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=err):
        await player.play_next()

    assert player._state == PlaybackState.IDLE
    assert player.current_track is None


@pytest.mark.asyncio
async def test_play_next_voice_play_raises_cleans_source(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    vc = CapturingVoiceClient()
    vc.play = MagicMock(side_effect=RuntimeError("Voice client error"))
    player.voice_client = vc

    await player.queue.add(make_track("t1"))
    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")
    source = FakeAudioSource()

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=source):
        await player.play_next()

    assert source.cleaned_up
    assert player._state == PlaybackState.IDLE
    assert player.current_track is None


@pytest.mark.asyncio
async def test_play_next_good_second_track_after_broken_first(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    await player.queue.add(make_track("broken"))
    await player.queue.add(make_track("good"))

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    source_good = FakeAudioSource()
    calls = []

    async def fake_probe(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("broken track probe error")
        return source_good

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=fake_probe):
        await player.play_next()

    assert len(calls) == 2
    assert player._state == PlaybackState.PLAYING
    assert player.current_track is not None
    assert player.current_track.title == "good"


@pytest.mark.asyncio
async def test_play_next_completely_broken_queue_terminates(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()

    for i in range(5):
        await player.queue.add(make_track(f"broken_{i}"))

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=RuntimeError("all broken")):
        await player.play_next()

    assert player._state == PlaybackState.IDLE
    assert player.current_track is None


@pytest.mark.asyncio
async def test_play_next_track_repeat_and_queue_repeat_bounded(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    await player.queue.add(make_track("broken_1"))
    await player.queue.add(make_track("broken_2"))
    
    # Enable track repeat (loop_mode = 1)
    player.queue.loop_mode = 1

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=RuntimeError("broken")):
        await player.play_next()

    assert player._state == PlaybackState.IDLE

    # Enable queue repeat (loop_mode = 2)
    player.queue.loop_mode = 2
    await player.queue.add(make_track("broken_1"))
    await player.queue.add(make_track("broken_2"))

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=RuntimeError("broken")):
        await player.play_next()

    assert player._state == PlaybackState.IDLE


@pytest.mark.asyncio
async def test_cancelled_error_propagates(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    await player.queue.add(make_track("t1"))

    mock_music_player.youtube.get_stream_url = AsyncMock(side_effect=asyncio.CancelledError())

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"):
        with pytest.raises(asyncio.CancelledError):
            await player.play_next()


@pytest.mark.asyncio
async def test_concurrent_play_next_serialized(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    await player.queue.add(make_track("t1"))

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")
    source = FakeAudioSource()

    probe_started = asyncio.Event()
    probe_continue = asyncio.Event()

    async def slow_probe(*args, **kwargs):
        probe_started.set()
        await probe_continue.wait()
        return source

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=slow_probe):
        
        t1 = asyncio.create_task(player.play_next())
        await probe_started.wait()
        
        # Second play_next call while first is preparing
        t2 = asyncio.create_task(player.play_next())

        probe_continue.set()
        await asyncio.gather(t1, t2)

    # Only 1 play call to voice client
    assert len(player.voice_client.played_sources) == 1


@pytest.mark.asyncio
async def test_audio_callback_from_thread(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    vc = CapturingVoiceClient()
    player.voice_client = vc

    await player.queue.add(make_track("track1"))
    await player.queue.add(make_track("track2"))

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")
    source = FakeAudioSource()

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=source):
        await player.play_next()

    assert player._state == PlaybackState.PLAYING
    cb = vc.after_callbacks[0]
    assert cb is not None

    # Invoke callback from thread
    event = threading.Event()
    def call_cb():
        cb(None)
        event.set()

    thread = threading.Thread(target=call_cb)
    thread.start()
    thread.join()

    # Wait for scheduled event loop task to process
    await asyncio.sleep(0.1)

    assert player.current_track is not None
    assert player.current_track.title == "track2"
    assert player.is_playing


@pytest.mark.asyncio
async def test_stale_and_duplicate_callback_ignored(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    vc = CapturingVoiceClient()
    player.voice_client = vc

    await player.queue.add(make_track("track1"))
    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")
    source = FakeAudioSource()

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=source):
        await player.play_next()

    cb = vc.after_callbacks[0]

    # Invalidate generation
    player._generation += 1

    # Call stale callback
    cb(None)
    await asyncio.sleep(0.05)

    # State should remain untouched by stale callback
    assert player.current_track is not None
    assert player.current_track.title == "track1"
