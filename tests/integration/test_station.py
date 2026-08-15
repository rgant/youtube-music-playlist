"""Integration tests for the endless MP3 stream the Sonos speaker plays as a radio station.

No test reaches the network. The fake resolver returns a path to a local tone file. `ffmpeg` reads
that path exactly as it reads a YouTube audio URL, so the station code runs unchanged.
"""

import contextlib
import dataclasses
import io
import os
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import typing

import pytest

from make_tone import make_tone
from youtube_music_library_radio import station
from youtube_music_library_radio.catalogue import Song, count_songs, merge_songs, open_catalogue
from youtube_music_library_radio.resolver import PermanentResolutionError, Resolved, TransientResolutionError
from youtube_music_library_radio.settings import Settings
from youtube_music_library_radio.station import build_server, find_ffmpeg

if typing.TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Generator, Iterator, Sequence
    from http.server import ThreadingHTTPServer
    from pathlib import Path
    from typing import BinaryIO

_METADATA_INTERVAL = 1600
_ICY_BLOCK_SIZE = 16
_BITRATE_KBPS = 320
_PRUNE_THRESHOLD = 3

# The short tone lasts half a second at 320 kbps, so one song yields about 22000 bytes of stream.
# A read of 30000 bytes therefore crosses a song boundary, and proves the response carries the next
# song as well. `ffmpeg -re` sends a half second at once, so a short song streams in milliseconds.
_SHORT_TONE_SECONDS = 0.5
_TWO_SONG_BYTES = 30000

# The long tone runs far past that first half second. `ffmpeg` therefore still runs, and media time
# still paces it, while the disconnect test looks for it. It also lasts much longer than the cleanup
# timeout below. Without that gap the test passes on an `ffmpeg` that only reached the end of its
# own audio, and not on one the station killed.
_LONG_TONE_SECONDS = 10.0

_ARTIST = "Nirvana"
_TITLE = "Territorial Pissings"
_STREAM_TITLE = f"{_ARTIST} - {_TITLE}"

_READ_TIMEOUT_SECONDS = 30.0
_CLEANUP_TIMEOUT_SECONDS = 3.0
_POLL_SECONDS = 0.02

# The blocks below cover 32000 bytes of audio. One song of the short tone yields about 22000 bytes,
# so the read crosses one song boundary.
_BLOCKS_ACROSS_A_BOUNDARY = 20

# The outage tests need more than one song, so an assertion about the whole catalogue means
# something. A prune threshold of one turns any failure count into a visible deletion.
_MANY_SONGS = tuple(f"song-{number}" for number in range(8))
_PRUNE_ON_THE_FIRST_FAILURE = 1


@dataclasses.dataclass
class _FakeResolver:
    """A `resolve_fn` double that works through `plan`, one entry for each call.

    A call past the end of `plan` repeats the last entry, so a one-entry plan describes every call.
    `None` raises `TransientResolutionError`. An exception instance is raised as it stands. A path
    resolves to that local file.
    """

    plan: Sequence[Path | BaseException | None]
    calls: list[str] = dataclasses.field(default_factory=list)

    def __call__(self, video_id: str) -> Resolved:
        """Resolve `video_id` to the entry this call reaches in the plan."""
        self.calls.append(video_id)
        entry = self.plan[min(len(self.calls), len(self.plan)) - 1]
        if entry is None:
            message = f"the fake resolver refuses {video_id!r}"
            raise TransientResolutionError(message)
        if isinstance(entry, BaseException):
            raise entry
        return Resolved(video_id=video_id, title=_TITLE, artist=_ARTIST, audio_url=str(entry))


@dataclasses.dataclass(frozen=True)
class _RunningStation:
    """A station server listening on an ephemeral port, and the catalogue behind it."""

    port: int
    conn: sqlite3.Connection


class _Serve(typing.Protocol):
    """Starts one station server for the calling test."""

    def __call__(
        self,
        resolve_fn: Callable[[str], Resolved],
        *,
        video_ids: Sequence[str] = ...,
        prune_threshold: int = ...,
    ) -> _RunningStation:
        """Fill a fresh catalogue with `video_ids`, then start a server on an ephemeral port."""
        ...


def _song(video_id: str) -> Song:
    """Build a catalogue row with no title and no artist, as `bootstrap` writes them."""
    return Song(video_id=video_id, title="", artist="", failure_count=0, last_success=None, last_played=None)


def _free_port() -> int:
    """Return a TCP port free on the loopback interface right now.

    This binds a throwaway socket to port 0, which asks the OS for a free port. It reads that port
    back, then closes the socket at once, so the real server can bind the same port next.

    `Settings` rejects a `station_port` of 0, because it holds the port to a real one. An ephemeral
    port therefore resolves before `Settings` is built, and never through it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return typing.cast("int", probe.getsockname()[1])


def _settings(
    database_path: Path,
    *,
    port: int | None = None,
    prune_threshold: int = _PRUNE_THRESHOLD,
    metadata_interval: int = _METADATA_INTERVAL,
) -> Settings:
    """Build settings that bind a free port on the loopback interface. `port` names one instead."""
    return Settings(
        database_path=database_path,
        speaker_name="",
        station_host="127.0.0.1",
        station_port=port if port is not None else _free_port(),
        bitrate_kbps=_BITRATE_KBPS,
        no_repeat_window=0,
        prune_threshold=prune_threshold,
        metadata_interval=metadata_interval,
        station_name="Test Radio",
    )


@pytest.fixture(autouse=True)
def _no_pause_between_quiet_songs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the pause the station takes after a run of quiet songs, for every test in this module.

    Several tests drive a run of songs that send no audio. The station sleeps `_QUIET_PAUSE_SECONDS`
    for each quiet song past `_QUIET_SONGS_BEFORE_PAUSE`. The length of the run follows from
    `prune_threshold` and from the station's own constants. A change to one of them adds minutes to
    a test. The test then fails on a socket timeout, which names the timeout and not the cause.
    """
    monkeypatch.setattr(station, "_QUIET_PAUSE_SECONDS", 0.0)


@pytest.fixture(name="tone_path", scope="session")
def fixture_tone_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the short tone one time for the whole session."""
    return make_tone(find_ffmpeg(), tmp_path_factory.mktemp("tone") / "short.mp3", _SHORT_TONE_SECONDS)


@pytest.fixture(name="long_tone_path", scope="session")
def fixture_long_tone_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the long tone one time for the whole session."""
    return make_tone(find_ffmpeg(), tmp_path_factory.mktemp("long-tone") / "long.mp3", _LONG_TONE_SECONDS)


@pytest.fixture(name="unplayable_path", scope="session")
def fixture_unplayable_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write a file that holds no audio, so `ffmpeg` opens it, fails, and writes nothing."""
    path = tmp_path_factory.mktemp("unplayable") / "not-audio.txt"
    _ = path.write_text("this file holds no audio\n")
    return path


@pytest.fixture(name="serve")
def fixture_serve(tmp_path: Path) -> Iterator[_Serve]:
    """Yield a factory that starts a station, then stop every station the test started.

    The teardown clears `daemon_threads`, so `server_close` waits for every request thread to end
    before the catalogue connection closes. It then proves that no `ffmpeg` process outlived the
    test. A leaked process fails whichever test runs next.
    """
    servers: list[ThreadingHTTPServer] = []
    connections: list[sqlite3.Connection] = []

    def start(
        resolve_fn: Callable[[str], Resolved],
        *,
        video_ids: Sequence[str] = ("song-1",),
        prune_threshold: int = _PRUNE_THRESHOLD,
    ) -> _RunningStation:
        """Fill a fresh catalogue with `video_ids`, then start a server on an ephemeral port."""
        database_path = tmp_path / f"catalogue-{len(servers)}.sqlite3"
        conn = open_catalogue(database_path)
        connections.append(conn)
        _ = merge_songs(conn, [_song(video_id) for video_id in video_ids])

        server = build_server(_settings(database_path, prune_threshold=prune_threshold), conn, resolve_fn)
        server.daemon_threads = False
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return _RunningStation(port=server.server_address[1], conn=conn)

    yield start

    for server in servers:
        server.shutdown()
        server.server_close()
    assert _ffmpeg_child_count() == 0
    for conn in connections:
        conn.close()


class _Client:
    """A plain socket HTTP client.

    `http.client` hands its socket to the response object after it sees a response that ends at the
    close. Nothing can then force a reset. These tests need that control, and they need reads
    of an exact byte count, so they speak HTTP over the socket directly.
    """

    def __init__(self, port: int, *, want_metadata: bool) -> None:
        self._socket: socket.socket = socket.create_connection(("127.0.0.1", port), timeout=_READ_TIMEOUT_SECONDS)
        lines = ["GET / HTTP/1.1", "Host: 127.0.0.1"]
        if want_metadata:
            lines.append("Icy-MetaData: 1")
        self._socket.sendall(("\r\n".join([*lines, "", ""])).encode())
        self._reader: BinaryIO = self._socket.makefile("rb")
        self.status: int
        self.headers: dict[str, str]
        self.status, self.headers = self._read_head()

    def read(self, count: int) -> bytes:
        """Read exactly `count` bytes of the body, or fewer when the stream ends first."""
        return self._reader.read(count)

    def read_to_end(self) -> bytes:
        """Read the rest of the body until the server closes the connection."""
        return self._reader.read()

    def close(self) -> None:
        """Close with a TCP reset, so the server sees the disconnect at once."""
        with contextlib.suppress(OSError):
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        self._reader.close()
        self._socket.close()

    def _read_head(self) -> tuple[int, dict[str, str]]:
        """Read the status line and the headers, and leave the reader at the first body byte."""
        status = int(self._reader.readline().decode().split()[1])
        headers: dict[str, str] = {}
        while True:
            line = self._reader.readline()
            # An empty line ends the headers. No line at all means the server closed. A further
            # read then loops without end.
            if line in {b"\r\n", b"\n", b""}:
                return status, headers
            name, _, value = line.decode().partition(":")
            headers[name.strip().lower()] = value.strip()


@contextlib.contextmanager
def _stream(port: int, *, want_metadata: bool) -> Generator[_Client]:
    """Send one GET request to the station and yield the open client."""
    client = _Client(port, want_metadata=want_metadata)
    try:
        yield client
    finally:
        client.close()


def _read_metadata_titles(response: _Client, *, blocks: int) -> list[str]:
    """Read `blocks` ICY metadata blocks, each after exactly `_METADATA_INTERVAL` audio bytes.

    The exact offset is the point of the test. A block one byte early or late means the station
    miscounted the audio, and the speaker plays static instead of music.
    """
    titles: list[str] = []
    for _ in range(blocks):
        audio = response.read(_METADATA_INTERVAL)
        assert len(audio) == _METADATA_INTERVAL
        length = response.read(1)
        assert len(length) == 1
        titles.append(_stream_title(response.read(length[0] * _ICY_BLOCK_SIZE)))
    return titles


def _stream_title(payload: bytes) -> str:
    """Return the `StreamTitle` value in `payload`, or an empty string for an empty block."""
    text = payload.rstrip(b"\x00").decode()
    if not text:
        return ""
    match = re.fullmatch(r"StreamTitle='(.*)';", text)
    assert match is not None, f"the ICY payload {text!r} is not a StreamTitle"
    return match.group(1)


def _ffmpeg_child_count() -> int:
    """Return the number of `ffmpeg` processes that are direct children of the test process."""
    pgrep = shutil.which("pgrep")
    assert pgrep is not None
    # S603: every element of the command is a constant or an integer this process produced.
    found = subprocess.run([pgrep, "-P", str(os.getpid()), "ffmpeg"], capture_output=True, text=True, check=False)  # noqa: S603
    return len(found.stdout.split())


def _total_failure_count(conn: sqlite3.Connection) -> int:
    """Return the sum of `failure_count` over every row of the catalogue."""
    row = typing.cast("sqlite3.Row", conn.execute("SELECT COALESCE(SUM(failure_count), 0) FROM songs").fetchone())
    return typing.cast("int", row[0])


def _wait_until(condition: Callable[[], bool], *, timeout: float) -> bool:
    """Poll `condition` until it holds or `timeout` passes, then return its final value."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(_POLL_SECONDS)
    return condition()


def test_response_carries_an_audio_content_type(serve: _Serve, tone_path: Path) -> None:
    """A GET request answers 200 with an MP3 content type, and audio bytes follow."""
    radio = serve(_FakeResolver([tone_path]))

    with _stream(radio.port, want_metadata=False) as client:
        assert client.status == 200
        assert client.headers["content-type"] == "audio/mpeg"
        assert len(client.read(_METADATA_INTERVAL)) == _METADATA_INTERVAL


def test_metaint_header_appears_only_when_requested(serve: _Serve, tone_path: Path) -> None:
    """`icy-metaint` answers `Icy-MetaData: 1` alone, and the blocks land at that exact offset.

    The first block carries the song title because the song changed. The second block is empty
    because the same song is still playing.
    """
    radio = serve(_FakeResolver([tone_path]))

    with _stream(radio.port, want_metadata=False) as plain:
        assert "icy-metaint" not in plain.headers
        assert len(plain.read(_METADATA_INTERVAL)) == _METADATA_INTERVAL

    with _stream(radio.port, want_metadata=True) as icy:
        assert icy.headers["icy-metaint"] == str(_METADATA_INTERVAL)
        titles = _read_metadata_titles(icy, blocks=2)

    assert titles == [_STREAM_TITLE, ""]


def test_stream_continues_after_a_resolution_failure(serve: _Serve, tone_path: Path) -> None:
    """A song that does not resolve is skipped, and the response still carries audio afterwards."""
    resolver = _FakeResolver([None, tone_path])
    radio = serve(resolver)

    with _stream(radio.port, want_metadata=False) as client:
        assert client.status == 200
        audio = client.read(_TWO_SONG_BYTES)

    assert len(audio) == _TWO_SONG_BYTES
    # One refusal, then the two songs it takes to carry more than one song of audio.
    assert len(resolver.calls) >= 3


def test_a_song_ffmpeg_cannot_play_is_skipped(serve: _Serve, tone_path: Path, unplayable_path: Path) -> None:
    """A resolved song that yields no audio is skipped, and the next song still reaches the client.

    `yt-dlp` can return a URL that `ffmpeg` cannot read. That is a skip, not the end of the
    response.
    """
    resolver = _FakeResolver([unplayable_path, tone_path])
    radio = serve(resolver)

    with _stream(radio.port, want_metadata=False) as client:
        audio = client.read(_METADATA_INTERVAL)

    assert len(audio) == _METADATA_INTERVAL
    assert len(resolver.calls) >= 2


def test_a_resolution_outage_does_not_drain_the_catalogue(serve: _Serve) -> None:
    """A run of resolution failures records a bounded number of failures, and prunes no row.

    Every resolve raises a transient error, which is what a lost uplink and a throttled read both
    look like. A transient failure never raises a failure count, so no row can reach the prune
    threshold however long the outage runs.

    The run also ends the response, so the request thread cannot outlive its client.
    """
    resolver = _FakeResolver([None])
    radio = serve(resolver, video_ids=_MANY_SONGS, prune_threshold=_PRUNE_ON_THE_FIRST_FAILURE)

    with _stream(radio.port, want_metadata=False) as client:
        body = client.read_to_end()

    assert body == b""
    assert len(resolver.calls) == station._QUIET_SONGS_BEFORE_END
    assert _total_failure_count(radio.conn) == 0
    assert count_songs(radio.conn) == len(_MANY_SONGS)


def test_a_run_of_silent_songs_ends_the_response(serve: _Serve, unplayable_path: Path) -> None:
    """Songs that resolve but send no audio end the response instead of looping without end.

    A `yt-dlp` resolve can succeed while the audio URL it returns answers 403 to `ffmpeg`. That
    fault reaches every song at once. The response sends nothing, so it never meets the
    `ConnectionError` that reports the client left, and the loop spawns `ffmpeg` as fast as it can.
    """
    resolver = _FakeResolver([unplayable_path])
    radio = serve(resolver, video_ids=_MANY_SONGS, prune_threshold=_PRUNE_ON_THE_FIRST_FAILURE)

    with _stream(radio.port, want_metadata=False) as client:
        body = client.read_to_end()

    assert body == b""
    assert len(resolver.calls) == station._QUIET_SONGS_BEFORE_END
    assert _total_failure_count(radio.conn) == 0
    assert count_songs(radio.conn) == len(_MANY_SONGS)


def test_a_song_that_never_plays_keeps_its_row(serve: _Serve, unplayable_path: Path) -> None:
    """A song that resolves but never sends audio stays in the catalogue.

    An empty read is what a 403 from `ffmpeg` looks like from here, and that 403 hits a working
    song. The station cannot tell it from a song that is truly unplayable, so it keeps the row and
    skips the song. Only a resolve that reports the video gone deletes anything.
    """
    resolver = _FakeResolver([unplayable_path])
    radio = serve(resolver, prune_threshold=_PRUNE_ON_THE_FIRST_FAILURE)

    with _stream(radio.port, want_metadata=False) as client:
        body = client.read_to_end()

    assert body == b""
    assert count_songs(radio.conn) == 1
    # The one song comes up again on each pass, and each pass gives it every attempt.
    assert len(resolver.calls) == station._QUIET_SONGS_BEFORE_END


def test_a_transient_failure_never_prunes_a_row(serve: _Serve) -> None:
    """A 403 or a bot check leaves every row in place, whatever the prune threshold says.

    YouTube answers a valid request with 403 at a rate that no request option changes. See
    <https://github.com/yt-dlp/yt-dlp/issues/17395>. A count against the song therefore deletes a
    working song, and a long outage deletes the library.
    """
    resolver = _FakeResolver([None])
    radio = serve(resolver, video_ids=_MANY_SONGS, prune_threshold=_PRUNE_ON_THE_FIRST_FAILURE)

    with _stream(radio.port, want_metadata=False) as client:
        body = client.read_to_end()

    assert body == b""
    assert _total_failure_count(radio.conn) == 0
    assert count_songs(radio.conn) == len(_MANY_SONGS)


def test_a_permanent_failure_prunes_at_the_threshold(serve: _Serve) -> None:
    """A video that is gone raises its failure count, and the row goes at the threshold.

    A deleted video never plays again. It must leave the catalogue, or the station picks it forever.
    """
    resolver = _FakeResolver([PermanentResolutionError("the video is gone")])
    radio = serve(resolver, prune_threshold=_PRUNE_ON_THE_FIRST_FAILURE)

    with _stream(radio.port, want_metadata=False) as client:
        body = client.read_to_end()

    assert body == b""
    assert count_songs(radio.conn) == 0


@pytest.fixture(name="one_song")
def fixture_one_song(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a catalogue that holds one song, and close it after the test."""
    conn = open_catalogue(tmp_path / "catalogue.sqlite3")
    _ = merge_songs(conn, [_song("only-song")])
    try:
        yield conn
    finally:
        conn.close()


def _play_one(resolver: _FakeResolver, conn: sqlite3.Connection, database_path: Path) -> tuple[bool, int]:
    """Run one song through `_Station._play_one`. Return the verdict and the bytes it wrote.

    This calls the method rather than the HTTP server, so the assertion counts the resolver calls
    for one song. Through the server, the outer loop picks a song again after a failure. A second
    pick of the only song then looks like a retry from outside.
    """
    body = io.BytesIO()
    radio = station._Station(
        settings=_settings(database_path),
        conn=conn,
        resolve_fn=resolver,
        ffmpeg=find_ffmpeg(),
    )
    played = radio._play_one(_song("only-song"), station._Sink(body, None))
    return played, len(body.getvalue())


def test_a_failed_song_costs_exactly_one_resolve(tmp_path: Path, one_song: sqlite3.Connection) -> None:
    """A song that does not resolve is asked for one time, and the station moves on.

    A retry of the same song holds the stream silent while it waits. The fault is random per
    attempt, so the next song has the same chance as a second try of this one. The row stays, and
    the song comes up again on a later pass.
    """
    resolver = _FakeResolver([None])

    played, written = _play_one(resolver, one_song, tmp_path / "catalogue.sqlite3")

    assert not played
    assert written == 0
    assert len(resolver.calls) == 1
    assert _total_failure_count(one_song) == 0
    assert count_songs(one_song) == 1


def test_a_silent_song_costs_exactly_one_resolve(tmp_path: Path, one_song: sqlite3.Connection, unplayable_path: Path) -> None:
    """A song that resolves but sends no audio is asked for one time, and the station moves on.

    A 403 from `ffmpeg` reaches the station as an empty read, not as an exception. It gets the same
    treatment as a failed resolve: no retry, no failure count, and the row stays.
    """
    resolver = _FakeResolver([unplayable_path])

    played, written = _play_one(resolver, one_song, tmp_path / "catalogue.sqlite3")

    assert not played
    assert written == 0
    assert len(resolver.calls) == 1
    assert _total_failure_count(one_song) == 0
    assert count_songs(one_song) == 1


def test_a_permanent_failure_raises_the_count(tmp_path: Path, one_song: sqlite3.Connection) -> None:
    """A video that is gone raises its failure count, so the row leaves at the threshold."""
    resolver = _FakeResolver([PermanentResolutionError("the video is gone")])

    played, _written = _play_one(resolver, one_song, tmp_path / "catalogue.sqlite3")

    assert not played
    assert len(resolver.calls) == 1
    assert _total_failure_count(one_song) == 1


def test_metadata_blocks_stay_aligned_across_a_song_boundary(serve: _Serve, tone_path: Path) -> None:
    """The block offsets keep counting across a song change, and the next song announces its title.

    `icy-metaint` counts the bytes of the response, not the bytes of one song. A `_Sink` built for
    each song, or a byte count reset at a song boundary, puts every later block at the wrong offset.
    `_read_metadata_titles` reads at exact offsets, so a wrong offset fails the read.
    """
    radio = serve(_FakeResolver([tone_path]))

    with _stream(radio.port, want_metadata=True) as icy:
        titles = _read_metadata_titles(icy, blocks=_BLOCKS_ACROSS_A_BOUNDARY)

    announcements = [index for index, title in enumerate(titles) if title]
    assert set(titles) == {_STREAM_TITLE, ""}  # No block carries a title the station did not send.
    assert announcements[0] == 0
    assert len(announcements) >= 2  # The second song announced itself.
    assert announcements[1] > 1  # Empty blocks sit between the two announcements.


def test_a_catalogue_that_drains_refuses_the_request(serve: _Serve, tone_path: Path) -> None:
    """A catalogue that empties after the server starts answers 503, not an empty 200.

    `build_server` checks the row count one time. A stream that prunes its last row leaves the
    server up. Without this answer, every later request sends 200 and then no bytes.
    """
    radio = serve(_FakeResolver([tone_path]))
    _ = radio.conn.execute("DELETE FROM songs")

    with _stream(radio.port, want_metadata=False) as client:
        assert client.status == 503


def test_settings_refuses_a_metadata_interval_below_one_before_a_server_can_bind(tmp_path: Path) -> None:
    """`Settings` rejects a `metadata_interval` below 1 at construction, before `build_server` runs.

    Zero makes each read return no bytes, which the player reads as the end of every song.
    `Settings.__post_init__` catches this before any `Settings` value can exist to build a server
    from, so `build_server` itself never has to guard against it.
    """
    database_path = tmp_path / "catalogue.sqlite3"

    with pytest.raises(ValueError, match="metadata_interval"):
        _ = _settings(database_path, metadata_interval=0)


def test_find_ffmpeg_names_the_program_it_cannot_find(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty `PATH` raises `RuntimeError`, and the message names `ffmpeg`."""
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(RuntimeError, match="ffmpeg"):
        _ = find_ffmpeg()


def test_no_ffmpeg_process_survives_a_client_disconnect(serve: _Serve, long_tone_path: Path) -> None:
    """Every `ffmpeg` child process ends after the client drops the connection.

    The long tone holds one `ffmpeg` process open while the client stays connected. The count
    before the disconnect therefore gives the count after it a meaning.
    """
    radio = serve(_FakeResolver([long_tone_path]))

    with _stream(radio.port, want_metadata=False) as client:
        assert len(client.read(_METADATA_INTERVAL)) == _METADATA_INTERVAL
        assert _ffmpeg_child_count() >= 1

    assert _wait_until(lambda: _ffmpeg_child_count() == 0, timeout=_CLEANUP_TIMEOUT_SECONDS)


def test_build_server_refuses_an_empty_catalogue(tmp_path: Path, tone_path: Path) -> None:
    """An empty catalogue raises `RuntimeError`, and no port is bound.

    The settings name a port that this test already holds. A bind raises `OSError`, so a
    `RuntimeError` proves that the row count check comes first.
    """
    database_path = tmp_path / "empty.sqlite3"
    conn = open_catalogue(database_path)
    blocker = socket.socket()
    try:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        settings = _settings(database_path, port=typing.cast("int", blocker.getsockname()[1]))

        with pytest.raises(RuntimeError, match="no songs"):
            _ = build_server(settings, conn, _FakeResolver([tone_path]))
    finally:
        blocker.close()
        conn.close()
