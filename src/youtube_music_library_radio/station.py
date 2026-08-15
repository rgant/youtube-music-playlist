"""Serve the endless MP3 stream that the Sonos speaker plays as a radio station.

Sonos limits a service playlist to 500 tracks, but a radio stream holds no queue and so holds no
limit. This module answers one GET request with one response that never ends. Inside that one
response it picks a song, resolves it, runs `ffmpeg` to transcode the audio, and writes the bytes
to the client. When a song ends, the next song starts in the same response.

Sonos opens a probe connection, drops it, then opens the connection it plays. The dropped probe
reaches the handler as a `ConnectionError`, which this module treats as a normal disconnect rather
than an error.
"""

import dataclasses
import functools
import http.server
import logging
import random
import shutil
import subprocess
import time
import typing
from http import HTTPStatus

from youtube_music_library_radio.catalogue import candidate_songs, count_songs, record_failure, record_success
from youtube_music_library_radio.icy import EMPTY_BLOCK, metadata_block
from youtube_music_library_radio.resolver import PermanentResolutionError, ResolutionError
from youtube_music_library_radio.selector import pick

if typing.TYPE_CHECKING:
    import socket
    import socketserver
    import sqlite3
    from collections.abc import Callable
    from typing import IO

    from _typeshed import SupportsWrite

    from youtube_music_library_radio.catalogue import Song
    from youtube_music_library_radio.resolver import Resolved
    from youtube_music_library_radio.settings import Settings

_logger = logging.getLogger(__name__)

_FFMPEG = "ffmpeg"

# The size of one read for a client that asked for no metadata. Nothing constrains this value, so it
# only balances syscall count against how soon a client disconnect reaches the handler.
_PLAIN_READ_SIZE = 8192

# A song is quiet when it sends the client no audio. YouTube answers a valid request with HTTP 403
# at a rate that no request option changes. See <https://github.com/yt-dlp/yt-dlp/issues/17395>,
# which records that behaviour with no root cause and no fix.
#
# The station never retries the same song. The fault is random for each attempt, so the next song
# has the same chance as a second try of this one. A retry only holds the stream silent while it
# waits, and one failed attempt already costs about a second of quiet. The failed song keeps its
# row and comes up again on a later pass.

# Above this many quiet songs in a row, wait before the next song. A fault that fails fast otherwise
# spins one core and asks YouTube for a song many times each second.
_QUIET_SONGS_BEFORE_PAUSE = 5
_QUIET_PAUSE_SECONDS = 5.0

# Above this many, end the response. A quiet stream writes nothing to the client, so it never meets
# the `ConnectionError` that reports the client left. Without this bound one thread and one `ffmpeg`
# child survive every Sonos reconnect, for as long as the service runs.
_QUIET_SONGS_BEFORE_END = 20


def find_ffmpeg() -> str:
    """Return the path to the `ffmpeg` program.

    Raises `RuntimeError` naming the program when `PATH` holds no `ffmpeg`. The Homebrew prefix
    differs between the arm64 development Mac and the Intel Mac Mini that runs the service. The
    path must therefore come from `PATH`, and never from a constant.
    """
    path = shutil.which(_FFMPEG)
    if path is None:
        message = f"{_FFMPEG} is not on PATH; install it with `brew bundle`"
        raise RuntimeError(message)
    return path


def transcode(ffmpeg: str, audio_url: str, bitrate_kbps: int) -> subprocess.Popen[bytes]:
    """Start `ffmpeg` to transcode `audio_url` to MP3 on standard output.

    `-re` paces the output at real time. Without it `ffmpeg` races ahead of the speaker and the
    buffering behaviour at the speaker changes.

    Standard output is unbuffered, so one read returns the bytes that are ready instead of waiting
    for a full buffer. The caller controls the size of each read. Standard error stays with the
    parent process, so `ffmpeg` errors reach the service log.
    """
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-re", "-i", audio_url]
    command += ["-f", "mp3", "-b:a", f"{bitrate_kbps}k", "-ar", "44100", "-ac", "2", "-"]
    # S603: `ffmpeg` comes from `shutil.which`, every flag is a constant, and the argument list
    # never reaches a shell.
    return subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=0)  # noqa: S603


class _Sink:
    """Writes the response body: the audio, and the ICY metadata blocks between it.

    `interval` is the `icy-metaint` value, or None for a client that did not ask for metadata. With
    metadata, one block must land after every `interval` bytes of audio. A block one byte early or
    late desynchronises the stream, and the speaker then plays static. The caller therefore reads at
    most `read_size()` bytes at a time, so no single write carries audio past a block offset.
    """

    def __init__(self, writer: SupportsWrite[bytes], interval: int | None) -> None:
        self._writer: SupportsWrite[bytes] = writer
        self._interval: int | None = interval
        self._audio_since_block: int = 0
        self._pending_title: str | None = None

    def announce(self, title: str) -> None:
        """Hold `title` for the next metadata block. A client without metadata never sees it."""
        self._pending_title = title

    def read_size(self) -> int:
        """Return how many bytes of audio to read next.

        With metadata this is the distance to the next block offset. Without metadata no offset
        constrains the read, so it is a plain chunk size.
        """
        if self._interval is None:
            return _PLAIN_READ_SIZE
        return self._interval - self._audio_since_block

    def write_audio(self, audio: bytes) -> None:
        """Write `audio`, then a metadata block when that audio reaches the next block offset."""
        _ = self._writer.write(audio)
        if self._interval is None:
            return
        self._audio_since_block += len(audio)
        if self._audio_since_block >= self._interval:
            _ = self._writer.write(self._next_block())
            self._audio_since_block = 0

    def _next_block(self) -> bytes:
        """Return the block for this offset: a new title one time, then empty blocks."""
        if self._pending_title is None:
            return EMPTY_BLOCK
        block = metadata_block(self._pending_title)
        self._pending_title = None
        return block


@dataclasses.dataclass(frozen=True)
class _Station:
    """Everything one request handler needs to produce the endless stream."""

    settings: Settings
    conn: sqlite3.Connection
    resolve_fn: Callable[[str], Resolved]
    ffmpeg: str

    def stream(self, writer: SupportsWrite[bytes], *, wants_metadata: bool) -> None:
        """Write one song after another to `writer`.

        Returns when the catalogue offers no candidate, and when too many songs in a row send no
        audio. Every other end comes from the client, as an exception the caller handles.

        The `_Sink` is built one time for the whole response. Its byte count runs across song
        boundaries, because `icy-metaint` counts the bytes of the response and not of one song.
        """
        rng = random.Random()
        sink = _Sink(writer, self.settings.metadata_interval if wants_metadata else None)
        quiet_run = 0
        while True:
            if quiet_run >= _QUIET_SONGS_BEFORE_END:
                _logger.error(
                    "%d songs in a row sent no audio; ending the stream so this thread cannot outlive its client",
                    quiet_run,
                )
                return
            if quiet_run >= _QUIET_SONGS_BEFORE_PAUSE:
                _logger.warning("%d songs in a row sent no audio; waiting %s seconds", quiet_run, _QUIET_PAUSE_SECONDS)
                time.sleep(_QUIET_PAUSE_SECONDS)

            candidates = candidate_songs(self.conn, self.settings.no_repeat_window)
            if not candidates:
                self._report_an_empty_catalogue()
                return

            song = pick(candidates, rng)
            if self._play_one(song, sink):
                quiet_run = 0
            else:
                quiet_run += 1

    def _play_one(self, song: Song, sink: _Sink) -> bool:
        """Play `song` one time. Return True when audio reached the client.

        A failed attempt falls into one of two classes. `PermanentResolutionError` names a video
        that is gone. That raises the failure count, and the row leaves at the prune threshold.
        Every other failure can clear, so this method keeps the row and returns to the caller.

        A transient failure never raises the failure count. YouTube answers a valid request with
        403 often enough that counting it deletes working songs, and a long outage then empties the
        catalogue.

        This method never asks twice for one song. Read the comment above `_QUIET_SONGS_BEFORE_PAUSE`
        for the reason.

        `record_success` runs after the song sends audio, and never before it. `record_success`
        clears the failure count, so a call before playback lets a song that never plays cycle
        between zero and one. Such a row never reaches the prune threshold, and the station picks
        it again for as long as the catalogue holds it.

        A song therefore records its played time after it plays, and fills an empty title and artist
        at the same time. The ICY display reads the resolver's own values, so nothing the owner
        sees waits for that write. A process that stops during a song leaves the played time alone.
        The station can then pick that one song again at the next start.
        """
        try:
            resolved = self.resolve_fn(song.video_id)
        except PermanentResolutionError:
            _logger.info("dropping %s: the video is gone", song.video_id)
            _ = record_failure(self.conn, song.video_id, self.settings.prune_threshold)
            return False
        except ResolutionError:
            _logger.info("%s did not resolve; moving to the next song and keeping the row", song.video_id)
            return False

        # Sonos splits the title on the dash and shows the artist and the song separately.
        sink.announce(f"{resolved.artist} - {resolved.title}")
        if self._play(resolved, sink) == 0:
            _logger.info("%s sent no audio; moving to the next song and keeping the row", resolved.video_id)
            return False

        record_success(self.conn, resolved.video_id, resolved.title, resolved.artist)
        return True

    def _report_an_empty_catalogue(self) -> None:
        """Report the terminal state. The response already sent its headers, so it can only stop."""
        _logger.error(
            "the catalogue at %s holds %d rows and offers no candidate song. The stream stops here. "
            "Run `library-radio bootstrap` to fill the catalogue again.",
            self.settings.database_path,
            count_songs(self.conn),
        )

    def _play(self, resolved: Resolved, sink: _Sink) -> int:
        """Write the audio of one song, and return the number of bytes it wrote.

        Returns when the song ends or when `ffmpeg` stops. A count of zero means `ffmpeg` gave
        nothing, which the caller counts as a quiet song.
        """
        written = 0
        process = transcode(self.ffmpeg, resolved.audio_url, self.settings.bitrate_kbps)
        try:
            # `Popen.stdout` is typed `IO[Any]`. `transcode` asks for a byte pipe, so it is bytes.
            stdout = typing.cast("IO[bytes] | None", process.stdout)
            if stdout is None:  # pragma: no cover - transcode always asks for a stdout pipe
                return 0
            while True:
                audio = stdout.read(sink.read_size())
                if not audio:
                    break
                sink.write_audio(audio)
                written += len(audio)
        finally:
            process.kill()
            _ = process.wait()
            if process.stdout is not None:
                process.stdout.close()
        return written


class _StationHandler(http.server.BaseHTTPRequestHandler):
    """Answers one GET request with the endless MP3 stream."""

    # The response carries no length and ends only when the connection closes. HTTP/1.0 says exactly
    # that, and the prototype confirmed the speaker accepts it.
    protocol_version: str = "HTTP/1.0"

    def __init__(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
        server: socketserver.BaseServer,
        *,
        station: _Station,
    ) -> None:
        # `BaseHTTPRequestHandler.__init__` answers the whole request, so this runs first.
        self._station: _Station = station
        super().__init__(request, client_address, server)

    def do_GET(self) -> None:
        """Answer with an MP3 stream that never ends on its own."""
        wants_metadata = (self.headers.get("Icy-MetaData") or "").strip() == "1"
        try:
            if count_songs(self._station.conn) == 0:
                # `build_server` refuses an empty catalogue, so the catalogue drained while the
                # service ran. Refuse the request, or the speaker reads an empty 200 as silence.
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "the catalogue holds no songs")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/mpeg")
            if wants_metadata:
                self.send_header("icy-metaint", str(self._station.settings.metadata_interval))
            self.end_headers()
            self._station.stream(self.wfile, wants_metadata=wants_metadata)
        except ConnectionError:
            # Sonos opens a probe connection and drops it before it opens the one it plays. The base
            # class covers the reset, the broken pipe, and the abort that macOS also sends.
            _logger.info("the client at %s closed the connection", self.client_address[0])

    @typing.override
    def log_message(self, format: str, *args: object) -> None:
        """Send the request log to the module logger instead of standard error."""
        _logger.info("%s %s", self.client_address[0], format % args)


def build_server(settings: Settings, conn: sqlite3.Connection, resolve_fn: Callable[[str], Resolved]) -> http.server.ThreadingHTTPServer:
    """Build the station server. The caller starts it with `serve_forever`.

    `resolve_fn` is a parameter so a test can pass a fake that returns a local file and never
    reaches the network.

    Raises `RuntimeError` before it binds a port for two faults: an empty catalogue, and a `PATH`
    that holds no `ffmpeg`. Neither gets better after the port is open. `Settings.__post_init__`
    already rejects a `metadata_interval` below 1, so no `Settings` value that reaches this
    function can hold one.
    """
    if count_songs(conn) == 0:
        message = f"the catalogue at {settings.database_path} holds no songs; run the bootstrap command first"
        raise RuntimeError(message)

    station = _Station(settings=settings, conn=conn, resolve_fn=resolve_fn, ffmpeg=find_ffmpeg())
    handler = functools.partial(_StationHandler, station=station)
    return http.server.ThreadingHTTPServer((settings.station_host, settings.station_port), handler)
