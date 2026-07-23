"""
Tests for FFmpeg candidate discovery, validation, and resolution.
"""
import sys
import subprocess
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from config import Config
from src.player import (
    FFmpegCandidate,
    FFmpegResolution,
    _iter_ffmpeg_candidates,
    validate_ffmpeg_candidate,
    resolve_ffmpeg,
)


def test_candidate_data_structures():
    cand = FFmpegCandidate(executable="/usr/bin/ffmpeg", source="system-path", bundled=False)
    assert cand.executable == "/usr/bin/ffmpeg"
    assert cand.source == "system-path"
    assert cand.bundled is False

    res = FFmpegResolution(executable="/usr/bin/ffmpeg", source="path", version="ffmpeg version 6.0")
    assert res.executable == "/usr/bin/ffmpeg"
    assert res.source == "path"
    assert res.version == "ffmpeg version 6.0"


def test_iter_ffmpeg_candidates_order(monkeypatch):
    monkeypatch.setattr(Config, "FFMPEG_EXECUTABLE", "/custom/ffmpeg")

    def fake_exists(self):
        return str(self).replace("\\", "/") in ["/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg", "/bundled/ffmpeg"]

    with patch("src.player.shutil.which", return_value="/path/ffmpeg"), \
         patch("src.player.sys.platform", "linux"), \
         patch("pathlib.Path.exists", fake_exists), \
         patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="/bundled/ffmpeg"):

        candidates = list(_iter_ffmpeg_candidates())
        sources = [c.source for c in candidates]
        execs = [c.executable for c in candidates]

        assert sources == ["configured", "path", "system-path", "system-path", "imageio"]
        assert execs[0] == "/custom/ffmpeg"
        assert execs[1] == "/path/ffmpeg"


def test_validate_ffmpeg_candidate_success():
    candidate = FFmpegCandidate(executable="/usr/bin/ffmpeg", source="path")

    version_res = MagicMock(returncode=0, stdout="ffmpeg version 6.1.1\nbuilt with gcc", stderr="")
    codec_res = MagicMock(returncode=0, stdout="", stderr="")

    def fake_run(cmd, **kwargs):
        if "-version" in cmd:
            return version_res
        return codec_res

    with patch("subprocess.run", side_effect=fake_run):
        resolution = validate_ffmpeg_candidate(candidate)
        assert resolution is not None
        assert resolution.executable == "/usr/bin/ffmpeg"
        assert resolution.source == "path"
        assert "6.1.1" in resolution.version


def test_validate_ffmpeg_candidate_sigsegv_returncode_minus_11():
    candidate = FFmpegCandidate(executable="/bundled/ffmpeg", source="imageio", bundled=True)

    def fake_run(cmd, **kwargs):
        if "-version" in cmd:
            return MagicMock(returncode=-11, stdout="", stderr="Segmentation fault")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        resolution = validate_ffmpeg_candidate(candidate)
        assert resolution is None


def test_validate_ffmpeg_candidate_nonzero_returncode():
    candidate = FFmpegCandidate(executable="/usr/bin/ffmpeg", source="path")

    def fake_run(cmd, **kwargs):
        if "-version" in cmd:
            return MagicMock(returncode=1, stdout="", stderr="Unknown option")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        resolution = validate_ffmpeg_candidate(candidate)
        assert resolution is None


def test_validate_ffmpeg_candidate_timeout():
    candidate = FFmpegCandidate(executable="/usr/bin/ffmpeg", source="path")

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5)):
        resolution = validate_ffmpeg_candidate(candidate)
        assert resolution is None


def test_validate_ffmpeg_candidate_oserror():
    candidate = FFmpegCandidate(executable="/usr/bin/ffmpeg", source="path")

    with patch("subprocess.run", side_effect=OSError("Permission denied")):
        resolution = validate_ffmpeg_candidate(candidate)
        assert resolution is None


def test_resolve_ffmpeg_fallback(monkeypatch):
    resolve_ffmpeg.cache_clear()
    monkeypatch.setattr(Config, "FFMPEG_EXECUTABLE", None)

    cand1 = FFmpegCandidate(executable="/bad/ffmpeg", source="path")
    cand2 = FFmpegCandidate(executable="/good/ffmpeg", source="system-path")

    with patch("src.player._iter_ffmpeg_candidates", return_value=[cand1, cand2]), \
         patch("src.player.validate_ffmpeg_candidate", side_effect=[None, FFmpegResolution("/good/ffmpeg", "system-path", "ffmpeg 6.0")]):

        res = resolve_ffmpeg()
        assert res is not None
        assert res.executable == "/good/ffmpeg"
        assert res.source == "system-path"
    resolve_ffmpeg.cache_clear()


def test_resolve_ffmpeg_none_available(monkeypatch):
    resolve_ffmpeg.cache_clear()
    monkeypatch.setattr(Config, "FFMPEG_EXECUTABLE", None)

    with patch("src.player._iter_ffmpeg_candidates", return_value=[]):
        res = resolve_ffmpeg()
        assert res is None
    resolve_ffmpeg.cache_clear()
