"""
Unit tests for GuildPlayer playback recovery, failure handling, concurrency, and thread safe callbacks.
"""
import os
import sys
import pytest
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import discord
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
        self.disconnected = False

    def is_playing(self):
        return self.is_playing_flag

    def is_paused(self):
        return self.is_paused_flag

    def play(self, source, after=None):
        if self.is_playing_flag:
            raise RuntimeError("Already playing audio.")
        self.played_sources.append(source)
        self.after_callbacks.append(after)
        self.is_playing_flag = True

    def stop(self):
        self.stopped = True
        self.is_playing_flag = False
        self.is_paused_flag = False

    async def disconnect(self):
        self.disconnected = True
        self.stop()

    def finish_track(self, index=-1):
        self.is_playing_flag = False
        if self.after_callbacks:
            return self.after_callbacks[index]
        return None


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
    await player.queue.add(make_track("t2"))

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

        t1_task = asyncio.create_task(player.play_next())
        await probe_started.wait()

        gen_first_call = player._generation

        # Second play_next call while first is preparing
        t2_task = asyncio.create_task(player.play_next())

        probe_continue.set()
        await asyncio.gather(t1_task, t2_task)

    # Exactly 1 voice play call
    assert len(player.voice_client.played_sources) == 1
    assert player.current_track is not None
    assert player.current_track.title == "t1"
    assert player.queue.current_index == 0
    assert player._state == PlaybackState.PLAYING
    assert player._generation == gen_first_call


@pytest.mark.asyncio
async def test_repeat_one_unstarted_playback(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    await player.queue.add(make_track("t1"))
    player.queue.loop_mode = 1  # repeat-one unstarted

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player.play_next()

    assert player._state == PlaybackState.PLAYING
    assert player.current_track is not None
    assert player.current_track.title == "t1"
    assert player.queue.current_index == 0


@pytest.mark.asyncio
async def test_repeat_one_preparation_failure_preserves_queue(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    t_broken = make_track("broken")
    t_good = make_track("good")
    await player.queue.add(t_broken)
    await player.queue.add(t_good)
    player.queue.loop_mode = 1

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    async def fake_probe(url, **kwargs):
        if player.queue.current_index == 0:
            raise RuntimeError("broken track probe error")
        return FakeAudioSource()

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=fake_probe):
        await player.play_next()

    assert player._state == PlaybackState.PLAYING
    assert player.current_track is not None
    assert player.current_track.title == "good"
    assert player.queue.current_index == 1
    # Check queue contents were preserved (not popped via remove(0))
    all_tracks = await player.queue.get_all()
    assert len(all_tracks) == 2
    assert all_tracks[0].title == "broken"
    assert all_tracks[1].title == "good"


@pytest.mark.asyncio
async def test_repeat_one_failure_non_zero_index(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    t0 = make_track("t0")
    t1_broken = make_track("t1_broken")
    t2_good = make_track("t2_good")
    await player.queue.add(t0)
    await player.queue.add(t1_broken)
    await player.queue.add(t2_good)

    player.queue.current_index = 0
    player.queue.loop_mode = 1

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    async def fake_probe(url, **kwargs):
        if player.queue.current_index == 1:
            raise RuntimeError("t1 broken")
        return FakeAudioSource()

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=fake_probe):
        await player.play_next(skip_track_repeat=True)

    assert player._state == PlaybackState.PLAYING
    assert player.current_track is not None
    assert player.current_track.title == "t2_good"
    assert player.queue.current_index == 2
    all_tracks = await player.queue.get_all()
    assert len(all_tracks) == 3
    assert all_tracks[0].title == "t0"


@pytest.mark.asyncio
async def test_runtime_error_repeat_one_no_infinite_loop(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    vc = CapturingVoiceClient()
    player.voice_client = vc
    t1 = make_track("t1_broken_runtime")
    t2 = make_track("t2_good")
    await player.queue.add(t1)
    await player.queue.add(t2)
    player.queue.loop_mode = 1

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player.play_next()

    assert player.current_track == t1
    gen = player._generation
    vc.is_playing_flag = False

    # Simulate runtime error during playback under repeat-one
    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player._finish_playback(gen, RuntimeError("FFmpeg exit -11"))

    # Must NOT retry t1; must advance to t2
    assert player.current_track is not None
    assert player.current_track.title == "t2_good"
    assert player._state == PlaybackState.PLAYING


@pytest.mark.asyncio
async def test_runtime_error_notifies_channel(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    vc = CapturingVoiceClient()
    player.voice_client = vc

    channel = AsyncMock()
    player.set_notification_channel(channel)

    t1 = make_track("t1")
    await player.queue.add(t1)

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player.play_next()

    gen = player._generation
    vc.is_playing_flag = False
    await player._finish_playback(gen, RuntimeError("Runtime stream error"))

    assert channel.send.called


@pytest.mark.asyncio
async def test_notification_failure_does_not_block_advancement(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    vc = CapturingVoiceClient()
    player.voice_client = vc

    channel = AsyncMock()
    channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "Forbidden"))
    player.set_notification_channel(channel)

    await player.queue.add(make_track("t1"))
    await player.queue.add(make_track("t2"))

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player.play_next()

    gen = player._generation
    vc.is_playing_flag = False
    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player._finish_playback(gen, RuntimeError("Decoder failure"))

    assert player.current_track is not None
    assert player.current_track.title == "t2"


@pytest.mark.asyncio
async def test_runtime_failure_advances_queue_before_notification_io(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    vc = CapturingVoiceClient()
    player.voice_client = vc

    send_started = asyncio.Event()
    send_block = asyncio.Event()

    async def blocking_send(*args, **kwargs):
        send_started.set()
        await send_block.wait()

    channel = AsyncMock()
    channel.send = AsyncMock(side_effect=blocking_send)
    player.set_notification_channel(channel)

    t1 = make_track("t1_failed")
    t2 = make_track("t2_playable")
    await player.queue.add(t1)
    await player.queue.add(t2)

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player.play_next()

    assert player.current_track == t1
    gen = player._generation
    vc.is_playing_flag = False

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        finish_task = asyncio.create_task(player._finish_playback(gen, RuntimeError("runtime error")))
        try:
            await asyncio.wait_for(send_started.wait(), timeout=5)

            # Prove second track reaches PLAYING while channel.send() is still blocked
            assert player._state == PlaybackState.PLAYING
            assert player.current_track == t2

            send_block.set()
            await asyncio.wait_for(finish_task, timeout=5)
        finally:
            send_block.set()
            if not finish_task.done():
                finish_task.cancel()
                try:
                    await finish_task
                except (asyncio.CancelledError, Exception):
                    pass

    assert channel.send.called


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
        cb = vc.finish_track(0)
        assert cb is not None

        advance_done = asyncio.Event()

        # Wrap play_next to signal when track2 starts
        orig_play_next = player.play_next
        async def signaling_play_next(*args, **kwargs):
            res = await orig_play_next(*args, **kwargs)
            advance_done.set()
            return res

        player.play_next = signaling_play_next

        # Invoke callback from thread
        def call_cb():
            cb(None)

        thread = threading.Thread(target=call_cb)
        thread.start()
        thread.join()

        await asyncio.wait_for(advance_done.wait(), timeout=2.0)

    assert player.current_track is not None
    assert player.current_track.title == "track2"
    assert player.is_playing


@pytest.mark.asyncio
async def test_stale_and_duplicate_callback_ignored(mock_music_player):
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

    cb = vc.finish_track(0)
    gen = player._generation

    # Invalidate generation
    player._generation += 1

    # Call stale callback
    await player._finish_playback(gen, None)

    # State should remain untouched by stale callback
    assert player.current_track is not None
    assert player.current_track.title == "track1"

    # Reset generation to gen to test duplicate callback
    player._generation = gen
    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=source):
        # First call finishes track1 and starts track2 (which bumps generation to gen+1)
        await player._finish_playback(gen, None)
        # Second call with old gen (now stale)
        await player._finish_playback(gen, None)

    # Only advanced to track2, not skipped past track2
    assert player.current_track is not None
    assert player.current_track.title == "track2"


@pytest.mark.asyncio
async def test_stop_invalidates_pending_callback(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    vc = CapturingVoiceClient()
    player.voice_client = vc

    await player.queue.add(make_track("track1"))
    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player.play_next()

    gen = player._generation
    await player.stop()

    assert player._state == PlaybackState.IDLE
    assert player.current_track is None

    # Stale callback after stop
    await player._finish_playback(gen, None)
    assert player._state == PlaybackState.IDLE


@pytest.mark.asyncio
async def test_skip_and_previous_invalidation(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    vc = CapturingVoiceClient()
    player.voice_client = vc

    await player.queue.add(make_track("t1"))
    await player.queue.add(make_track("t2"))
    await player.queue.add(make_track("t3"))

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player.play_next()

    gen1 = player._generation

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player.skip()

    # Skipped to t2
    assert player.current_track.title == "t2"

    # Stale callback from t1 arrives after skip
    await player._finish_playback(gen1, None)
    assert player.current_track.title == "t2"


@pytest.mark.asyncio
async def test_disconnect_during_preparation(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    vc = CapturingVoiceClient()
    player.voice_client = vc

    await player.queue.add(make_track("t1"))
    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    probe_started = asyncio.Event()
    probe_continue = asyncio.Event()

    async def slow_probe(*args, **kwargs):
        probe_started.set()
        await probe_continue.wait()
        return FakeAudioSource()

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=slow_probe):

        play_task = asyncio.create_task(player.play_next())
        await probe_started.wait()

        # Disconnect while probe is running
        await player.disconnect()
        probe_continue.set()
        await play_task

    assert player._state == PlaybackState.IDLE
    assert player.current_track is None
    assert player.voice_client is None


@pytest.mark.asyncio
async def test_queue_repeat_broken_last_recovers_to_good_first(mock_music_player):
    """Regression test 1: Queue [good-first, broken-last], current index 0, queue-repeat enabled: broken-last fails and recovery plays good-first."""
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    t_good = make_track("good-first")
    t_broken = make_track("broken-last")
    await player.queue.add(t_good)
    await player.queue.add(t_broken)

    player.queue.current_index = 0
    player.queue.loop_mode = 2  # Queue repeat enabled

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    async def fake_probe(url, **kwargs):
        if player.queue.current_index == 1:
            raise RuntimeError("broken-last probe error")
        return FakeAudioSource()

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=fake_probe):
        await player.play_next()

    assert player._state == PlaybackState.PLAYING
    assert player.current_track is not None
    assert player.current_track.title == "good-first"
    assert player.queue.current_index == 0


@pytest.mark.asyncio
async def test_queue_repeat_every_track_broken_attempts_queue_size(mock_music_player):
    """Regression test 2: Every track broken under queue-repeat: exactly queue-size preparation attempts, then IDLE."""
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    queue_size = 4
    for i in range(queue_size):
        await player.queue.add(make_track(f"broken_{i}"))

    player.queue.loop_mode = 2  # Queue repeat enabled

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    attempts_count = 0

    async def failing_probe(url, **kwargs):
        nonlocal attempts_count
        attempts_count += 1
        raise RuntimeError("probe error")

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=failing_probe):
        await player.play_next()

    assert attempts_count == queue_size
    assert player._state == PlaybackState.IDLE
    assert player.current_track is None


@pytest.mark.asyncio
async def test_queue_repeat_runtime_failure_final_track_wraps_to_first(mock_music_player):
    """Regression test 3: Runtime failure on the final active track under queue-repeat: recovery wraps to the first track."""
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    vc = CapturingVoiceClient()
    player.voice_client = vc
    t0 = make_track("t0_first")
    t1_last = make_track("t1_last_broken_runtime")
    await player.queue.add(t0)
    await player.queue.add(t1_last)
    player.queue.loop_mode = 2  # Queue repeat

    player.queue.current_index = 0

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player.play_next()

    assert player.current_track == t1_last
    assert player.queue.current_index == 1
    gen = player._generation
    vc.is_playing_flag = False

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player._finish_playback(gen, RuntimeError("Decoder crash on final track"))

    assert player.current_track is not None
    assert player.current_track.title == "t0_first"
    assert player.queue.current_index == 0
    assert player._state == PlaybackState.PLAYING


@pytest.mark.asyncio
async def test_repeat_one_recovery_skips_failed_track(mock_music_player):
    """Regression test 4: Existing repeat-one recovery still skips the failed track."""
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    t_broken = make_track("t0_broken")
    t_good = make_track("t1_good")
    await player.queue.add(t_broken)
    await player.queue.add(t_good)

    player.queue.loop_mode = 1  # Repeat-one enabled

    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    async def fake_probe(url, **kwargs):
        if player.queue.current_index == 0:
            raise RuntimeError("t0 broken probe error")
        return FakeAudioSource()

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", side_effect=fake_probe):
        await player.play_next()

    assert player._state == PlaybackState.PLAYING
    assert player.current_track is not None
    assert player.current_track.title == "t1_good"
    assert player.queue.current_index == 1


@pytest.mark.asyncio
async def test_finish_playback_logs_real_traceback_and_sigsegv_posix(mock_music_player, caplog):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.current_track = make_track("t1")
    player._state = PlaybackState.PLAYING
    gen = player._generation

    try:
        raise RuntimeError("Decoder crashed abruptly")
    except RuntimeError as e:
        err = e
    setattr(err, "returncode", -11)

    with patch("os.name", "posix"), patch("sys.platform", "linux"), \
         patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player._finish_playback(gen, err)

    assert "Decoder crashed abruptly" in caplog.text
    assert "-11" in caplog.text
    assert "SIGSEGV" in caplog.text
    assert "Traceback (most recent call last):" in caplog.text
    assert "test_finish_playback_logs_real_traceback_and_sigsegv_posix" in caplog.text
    assert "NoneType: None" not in caplog.text


@pytest.mark.asyncio
async def test_finish_playback_logs_win32_no_sigsegv(mock_music_player, caplog):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.current_track = make_track("t1")
    player._state = PlaybackState.PLAYING
    gen = player._generation

    try:
        raise RuntimeError("Win32 playback crash")
    except RuntimeError as e:
        err = e
    setattr(err, "returncode", -11)

    with patch("os.name", "nt"), patch("sys.platform", "win32"), \
         patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player._finish_playback(gen, err)

    assert "Win32 playback crash" in caplog.text
    assert "-11" in caplog.text
    assert "SIGSEGV" not in caplog.text
    assert "NoneType: None" not in caplog.text


@pytest.mark.asyncio
async def test_finish_playback_logs_exception_without_traceback(mock_music_player, caplog):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.current_track = make_track("t1")
    player._state = PlaybackState.PLAYING
    gen = player._generation

    err = RuntimeError("No traceback exception")
    setattr(err, "returncode", 1)

    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player._finish_playback(gen, err)

    assert "No traceback exception" in caplog.text
    assert "1" in caplog.text
    assert "NoneType: None" not in caplog.text
    assert "Traceback" not in caplog.text


# --- Skip / previous under track-repeat, and exceptional-advance notification ---


async def _play_first(player):
    """Start the first queued track so current_track/index are set."""
    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player.play_next()


@pytest.mark.asyncio
async def test_skip_under_track_repeat_advances(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    t0, t1 = make_track("t0"), make_track("t1")
    await player.queue.add(t0)
    await player.queue.add(t1)
    player.queue.set_loop_mode(1)  # track repeat

    await _play_first(player)
    assert player.current_track is t0
    assert player.queue.current_index == 0

    player.voice_client.is_playing_flag = True
    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        result = await player.skip()

    assert result is t1
    assert player.current_track is t1
    assert player.queue.current_index == 1


@pytest.mark.asyncio
async def test_skip_under_track_repeat_single_item_goes_idle(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    t0 = make_track("only")
    await player.queue.add(t0)
    player.queue.set_loop_mode(1)  # track repeat

    await _play_first(player)
    assert player.current_track is t0

    player.voice_client.is_playing_flag = True
    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        result = await player.skip()

    assert result is None
    assert player._state == PlaybackState.IDLE
    assert player.current_track is None


@pytest.mark.asyncio
async def test_previous_under_track_repeat_from_index_2(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    t0, t1, t2 = make_track("t0"), make_track("t1"), make_track("t2")
    for t in (t0, t1, t2):
        await player.queue.add(t)
    player.queue.current_index = 2
    player.queue.set_loop_mode(1)  # track repeat

    player.voice_client.is_playing_flag = True
    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        result = await player.previous()

    assert result is t1
    assert player.current_track is t1
    assert player.queue.current_index == 1


@pytest.mark.asyncio
async def test_previous_under_track_repeat_from_index_1(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    t0, t1 = make_track("t0"), make_track("t1")
    await player.queue.add(t0)
    await player.queue.add(t1)
    player.queue.current_index = 1
    player.queue.set_loop_mode(1)  # track repeat

    player.voice_client.is_playing_flag = True
    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        result = await player.previous()

    assert result is t0
    assert player.current_track is t0
    assert player.queue.current_index == 0


@pytest.mark.asyncio
async def test_stale_callback_after_skip_does_not_clear_new_track(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.voice_client = CapturingVoiceClient()
    mock_music_player.youtube.get_stream_url = AsyncMock(return_value="http://stream")

    t0, t1 = make_track("t0"), make_track("t1")
    await player.queue.add(t0)
    await player.queue.add(t1)

    await _play_first(player)
    stale_gen = player._generation

    player.voice_client.is_playing_flag = True
    with patch("src.player.find_ffmpeg_executable", return_value="/usr/bin/ffmpeg"), \
         patch("discord.FFmpegOpusAudio.from_probe", return_value=FakeAudioSource()):
        await player.skip()

    assert player.current_track is t1

    # Stale callback from the skipped track must be a no-op.
    await player._finish_playback(stale_gen, None)
    assert player.current_track is t1
    assert player._state == PlaybackState.PLAYING


@pytest.mark.asyncio
async def test_finish_playback_propagates_cancellation_without_notifying(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.current_track = make_track("failed")
    player._state = PlaybackState.PLAYING
    gen = player._generation

    channel = AsyncMock()
    player.set_notification_channel(channel)

    async def cancel(*a, **k):
        raise asyncio.CancelledError()

    with patch.object(player, "play_next", side_effect=cancel):
        with pytest.raises(asyncio.CancelledError):
            await player._finish_playback(gen, RuntimeError("boom"))

    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_finish_playback_propagates_runtime_error_without_notifying(mock_music_player):
    player = GuildPlayer(guild_id=1, music_player=mock_music_player)
    player.current_track = make_track("failed")
    player._state = PlaybackState.PLAYING
    gen = player._generation

    channel = AsyncMock()
    player.set_notification_channel(channel)

    async def boom(*a, **k):
        raise RuntimeError("advance failed")

    with patch.object(player, "play_next", side_effect=boom):
        with pytest.raises(RuntimeError, match="advance failed"):
            await player._finish_playback(gen, RuntimeError("orig"))

    channel.send.assert_not_called()
