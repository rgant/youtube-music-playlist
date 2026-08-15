"""Build a short MP3 file for the station tests to serve in place of a YouTube audio URL.

The station tests must not touch the network, so the fake resolver hands the station a local
path instead of a stream URL. `ffmpeg` reads a local path exactly as it reads a URL, so the
station code under test runs unchanged.
"""

import subprocess
import typing

if typing.TYPE_CHECKING:
    from pathlib import Path

_FREQUENCY_HZ = 440
_BITRATE = "320k"


def make_tone(ffmpeg: str, destination: Path, seconds: float) -> Path:
    """Write a sine-tone MP3 of `seconds` to `destination`, and return that path.

    The tone holds 320 kbps, so one second of it yields about 40000 bytes of stream.

    `ffmpeg -re` streams the first half second at once, then paces the rest by media time. A tone
    of half a second therefore streams in a few milliseconds, and a longer tone keeps `ffmpeg`
    running while the test does something else.
    """
    source = f"sine=frequency={_FREQUENCY_HZ}:duration={seconds}"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", source]
    command += ["-b:a", _BITRATE, "-ar", "44100", "-ac", "2", str(destination)]
    # S603: the command holds no caller input. `ffmpeg` comes from `shutil.which` and every
    # other element is a constant in this module.
    _ = subprocess.run(command, check=True)  # noqa: S603
    return destination
