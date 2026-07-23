"""
System test verifying real local FFmpeg runtime execution and Opus encoding capability.
"""
import shutil
import subprocess
import sys
import pytest

from src.player import resolve_ffmpeg


@pytest.mark.system
def test_real_ffmpeg_runtime_capability():
    resolve_ffmpeg.cache_clear()
    res = resolve_ffmpeg()

    if not res:
        pytest.skip("No working local FFmpeg executable found")

    assert res.executable is not None
    assert res.version is not None

    if sys.platform.startswith("linux"):
        assert "site-packages/imageio_ffmpeg" not in res.executable, (
            "System FFmpeg must be preferred on Linux over bundled imageio-ffmpeg"
        )

    # 1. Version check
    ver_proc = subprocess.run(
        [res.executable, "-hide_banner", "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
        check=False,
    )
    assert ver_proc.returncode == 0, f"FFmpeg version check failed: {ver_proc.stderr}"

    # 2. Local Opus encode capability check
    encode_proc = subprocess.run(
        [
            res.executable,
            "-hide_banner",
            "-loglevel", "error",
            "-f", "lavfi",
            "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "0.1",
            "-c:a", "libopus",
            "-f", "opus",
            "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    assert encode_proc.returncode == 0, f"FFmpeg Opus encode check failed: {encode_proc.stderr.decode('utf-8', errors='ignore')}"
