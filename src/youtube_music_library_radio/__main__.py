"""The `library-radio` command line: the one place that wires every module of this project together.

The subcommands are `serve`, `bootstrap`, `auth`, `refresh`, `play`, `stop`, and `status`.

- `serve` runs the station and blocks. launchd runs this one.
- `bootstrap` fills an empty catalogue from the owner's `Everything N` YouTube playlists.
- `auth` writes the browser headers file, then reads the library to verify it.
- `refresh` merges the live YouTube Music library into the catalogue.
- `play` points the speaker at the station.
- `stop` stops the speaker.
- `status` reports the catalogue row count and the transport state of the speaker.

`serve` starts no thread that watches the speaker, and it restarts no playback. Read the module
docstring of `control.py` for the reason. launchd restarts the process after a crash, which is a
different thing. It recovers this program, and it holds no view on what the speaker plays.

Every subcommand returns 0 after success and a non-zero value after a failure. `main` turns an
expected failure into that non-zero value and one log line, so a person sees the fault and not a
traceback. `bootstrap` raises `CredentialError` when a credential file holds the wrong shape. It
raises `RuntimeError` for every other fault, which covers a transport failure and a YouTube Data API
response of the wrong shape. `_EXPECTED_FAILURES` names both.
"""

import argparse
import contextlib
import logging
import sqlite3
import subprocess
import sys
import typing
from pathlib import Path

from ytmusicapi import YTMusic, setup

from youtube_music_library_radio.authheaders import CURL_INSTRUCTIONS, MissingHeadersError, normalise
from youtube_music_library_radio.bootstrap import CredentialError, access_token, bootstrap
from youtube_music_library_radio.catalogue import count_songs, open_catalogue
from youtube_music_library_radio.control import find_speaker, start, station_url, stop, transport_state
from youtube_music_library_radio.logger import create_handler
from youtube_music_library_radio.refresh import library_songs, refresh
from youtube_music_library_radio.resolver import resolve
from youtube_music_library_radio.settings import load_settings
from youtube_music_library_radio.station import build_server

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from youtube_music_library_radio.refresh import ClientFactory, RefreshResult
    from youtube_music_library_radio.settings import Settings

_logger = logging.getLogger(__name__)

# One subcommand, as `main` calls it.
type _Handler = Callable[[argparse.Namespace], int]

# The failures a person can act on: a missing speaker, an unreadable credential file, a setting
# outside its range, a catalogue this process cannot open. `main` reports each one and returns a
# non-zero value. `CredentialError` is here because `bootstrap.py` raises it for a credential file
# with the wrong shape. It subclasses `TypeError`, and the builtin `TypeError` stays off this list.
# A catch of the builtin also reports a programming error in a handler as operator error.
_EXPECTED_FAILURES = (OSError, RuntimeError, CredentialError, MissingHeadersError, ValueError, sqlite3.Error)

_DEFAULT_OAUTH_PATH = Path("oauth.json")
_DEFAULT_CLIENT_SECRET_PATH = Path("client_secret.apps.googleusercontent.com.json")
_DEFAULT_HEADERS_PATH = Path("browser.json")

# How many songs `auth` reads to prove the credential works. A read of the whole library takes
# minutes, and it proves nothing that a small read leaves unproven. `refresh` reads every song.
_AUTH_PROBE_LIMIT = 25


def _run_station(settings: Settings, conn: sqlite3.Connection) -> None:  # pragma: no cover -- binds a port. Task 11 proves it
    """Serve the endless stream until the process stops.

    `build_server` refuses an empty catalogue and a `PATH` that holds no `ffmpeg`, before it binds
    the port.
    """
    with build_server(settings, conn, resolve) as server:
        _logger.info("the station serves port %d from the catalogue at %s", settings.station_port, settings.database_path)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            _logger.info("the station stops")


def _start_station(settings: Settings) -> None:  # pragma: no cover -- reaches the speaker. Task 11 proves it
    """Point the speaker at the station.

    Discovery takes about five seconds. This finds the speaker one time and passes it to
    `station_url`, which needs the speaker's address when `YTM_RADIO_STATION_HOST` is empty.
    """
    speaker = find_speaker(settings.speaker_name)
    start(speaker, station_url(settings, speaker=speaker), settings.station_name)


def _stop_station(settings: Settings) -> None:  # pragma: no cover -- reaches the speaker. Task 11 proves it
    """Stop the speaker named in `settings`."""
    stop(find_speaker(settings.speaker_name))


def _speaker_state(settings: Settings) -> str:  # pragma: no cover -- reaches the speaker. Task 11 proves it
    """Return the transport state of the speaker named in `settings`."""
    return transport_state(find_speaker(settings.speaker_name))


def _serve(args: argparse.Namespace, *, run_fn: Callable[[Settings, sqlite3.Connection], None] = _run_station) -> int:
    """Open the catalogue and serve the station. Returns when the station stops."""
    del args
    settings = load_settings()
    with contextlib.closing(open_catalogue(settings.database_path)) as conn:
        run_fn(settings, conn)
    return 0


def _bootstrap(
    args: argparse.Namespace,
    *,
    token_fn: Callable[[Path, Path], str] = access_token,
    bootstrap_fn: Callable[[sqlite3.Connection, str], int] = bootstrap,
) -> int:
    """Fill the catalogue from the owner's `Everything N` YouTube playlists."""
    oauth_path = typing.cast("Path", args.oauth)
    client_secret_path = typing.cast("Path", args.client_secret)
    settings = load_settings()
    token = token_fn(oauth_path, client_secret_path)
    with contextlib.closing(open_catalogue(settings.database_path)) as conn:
        added = bootstrap_fn(conn, token)
        _logger.info("bootstrap added %d rows. the catalogue holds %d songs", added, count_songs(conn))
    return 0


def _read_clipboard() -> str:  # pragma: no cover -- runs a program. `clipboard_fn` replaces it in a test
    """Return the clipboard, through `pbpaste`."""
    # S603: `pbpaste` is a fixed absolute path, it takes no argument, and no shell runs.
    result = subprocess.run(["/usr/bin/pbpaste"], capture_output=True, timeout=30, check=False)
    return result.stdout.decode("utf-8", errors="replace")


def _read_stdin() -> str:  # pragma: no cover -- reads the terminal. `stdin_fn` replaces it in a test
    """Return everything on standard input."""
    return sys.stdin.read()


def _credential_text(
    source: Path | None,
    *,
    clipboard_fn: Callable[[], str] = _read_clipboard,
    stdin_fn: Callable[[], str] = _read_stdin,
) -> str:
    """Return the browser capture, from the clipboard, a file, or standard input.

    The clipboard is the default, because step 6 of the instructions puts the capture there. A
    terminal cannot carry this text. One line of cookies runs past 2000 characters, and the request
    body past 6000. A terminal in canonical mode caps one line near 1024 bytes. A paste therefore
    stalls, and Ctrl-D does nothing.

    `--from-file -` still reads standard input, so a pipe works.

    Raises `RuntimeError` naming the source when it holds nothing.
    """
    if source is not None and str(source) == "-":
        text, origin = stdin_fn(), "standard input"
    elif source is not None:
        text, origin = source.read_text(encoding="utf-8"), str(source)
    else:
        text, origin = clipboard_fn(), "clipboard"

    if not text.strip():
        message = f"the {origin} holds nothing. Copy the request again with 'Copy as cURL', then run this again."
        raise RuntimeError(message)
    return text


def _auth(
    args: argparse.Namespace,
    *,
    setup_fn: Callable[[str, str], str] = setup,
    client_factory: ClientFactory = YTMusic,
    read_fn: Callable[[], str] | None = None,
) -> int:
    """Write new browser headers, check them against the library, and install them after that.

    This reads the paste itself and hands `ytmusicapi.setup` a normalised header block. The
    `ytmusicapi` prompt names no request to capture and no format. Chrome offers no header copy, so
    a person who follows that prompt has nothing that works. `authheaders.normalise` accepts a
    Chrome "Copy as cURL" as well, and names what is absent.

    The read that follows is the point of this subcommand. A browser cookie expires, and YouTube
    answers an expired session with an empty library and no error. A written file therefore proves
    nothing on its own.

    The new headers go to `<headers>.new` first, and reach `headers_path` only after the read
    returns songs. A person runs `auth` when authentication is in doubt, and the headers they paste
    can be wrong. A write straight onto `headers_path` destroys a working credential before anything
    checks the new one, and the person then holds nothing that works. `render_plist` in
    `scripts/install_launchagent.sh` follows the same shape, for the same reason.

    The temporary file sits beside `headers_path`, never under `TMPDIR`. `Path.replace` is atomic on
    one filesystem alone, and a rename across two can fail part way. A failed read deletes the
    temporary file, so this call leaves no second copy of live cookies on disk. If a crash leaves
    one behind, `.gitignore` matches `browser.json*` and git ignores it.

    Raises `RuntimeError` when the read fails. The message names `headers_path` as unchanged. A
    person after a failed run needs to know that the existing credential still stands. `main`
    reports that as one line and a non-zero code. Without the read, the owner finds out that the
    new credential is wrong on the next `refresh`.

    Touches no catalogue. This subcommand reads the library and writes one file.
    """
    headers_path = typing.cast("Path", args.headers)
    pending_path = headers_path.with_name(f"{headers_path.name}.new")
    # `normalise` raises before anything writes, so a useless capture leaves the old file alone.
    source = typing.cast("Path | None", getattr(args, "from_file", None))
    try:
        raw = read_fn() if read_fn is not None else _credential_text(source)
        header_block = normalise(raw)
    except RuntimeError, MissingHeadersError:
        # The instructions belong with the failure. On success they are noise.
        print(CURL_INSTRUCTIONS)  # noqa: T201 -- a terminal prompt, not a log line
        raise
    _ = setup_fn(str(pending_path), header_block)
    _logger.info("checking the new headers against the library")
    try:
        scan = library_songs(pending_path, client_factory=client_factory, limit=_AUTH_PROBE_LIMIT)
    except RuntimeError as exc:
        pending_path.unlink(missing_ok=True)
        message = f"{exc} The new headers are discarded, and {headers_path} is unchanged."
        raise RuntimeError(message) from exc
    _ = pending_path.replace(headers_path)
    _logger.info("auth wrote %s and the library read returned %d songs", headers_path, len(scan.songs) + len(scan.excluded))
    return 0


def _refresh(args: argparse.Namespace, *, refresh_fn: Callable[[sqlite3.Connection, Path], RefreshResult] = refresh) -> int:
    """Merge the live YouTube Music library into the catalogue, and delete every excluded song.

    `refresh` logs one line per excluded song. This adds the totals, so the owner sees the size of
    the exclusion against the size of the catalogue.
    """
    headers_path = typing.cast("Path", args.headers)
    settings = load_settings()
    with contextlib.closing(open_catalogue(settings.database_path)) as conn:
        result = refresh_fn(conn, headers_path)
        _logger.info(
            "refresh added %d rows, excluded %d songs, deleted %d rows. the catalogue holds %d songs",
            result.added,
            len(result.excluded),
            result.deleted,
            count_songs(conn),
        )
    return 0


def _play(args: argparse.Namespace, *, start_fn: Callable[[Settings], None] = _start_station) -> int:
    """Point the speaker at the station."""
    del args
    settings = load_settings()
    start_fn(settings)
    _logger.info("the speaker %s plays the station", settings.speaker_name)
    return 0


def _stop(args: argparse.Namespace, *, stop_fn: Callable[[Settings], None] = _stop_station) -> int:
    """Stop the speaker."""
    del args
    settings = load_settings()
    stop_fn(settings)
    _logger.info("the speaker %s stopped", settings.speaker_name)
    return 0


def _status(args: argparse.Namespace, *, state_fn: Callable[[Settings], str] = _speaker_state) -> int:
    """Report the catalogue row count and the transport state of the speaker.

    The row count is local and always available. The transport state needs the speaker on the LAN.
    If discovery fails, this reports the row count, names the fault, and returns a non-zero value.
    """
    del args
    settings = load_settings()
    with contextlib.closing(open_catalogue(settings.database_path)) as conn:
        total = count_songs(conn)
    _logger.info("the catalogue at %s holds %d songs", settings.database_path, total)

    try:
        state = state_fn(settings)
    except RuntimeError:
        _logger.exception("the speaker is not available")
        return 1
    _logger.info("the speaker %s reports %s", settings.speaker_name, state)
    return 0


_HANDLERS: dict[str, _Handler] = {
    "serve": _serve,
    "bootstrap": _bootstrap,
    "auth": _auth,
    "refresh": _refresh,
    "play": _play,
    "stop": _stop,
    "status": _status,
}


def build_parser() -> argparse.ArgumentParser:
    """Build the `library-radio` argument parser.

    The credential options default to the file names the repository root already holds. They are
    per-invocation inputs to `bootstrap`, `auth`, and `refresh`, not service configuration, so they
    stay off `Settings`. `serve` needs none of them, and launchd runs `serve` alone.

    `auth` and `refresh` share the `--headers` default, because `auth` writes the file that
    `refresh` reads. A different default on one of them writes one file and reads another.
    """
    parser = argparse.ArgumentParser(
        prog="library-radio",
        description="Stream the owner's YouTube Music library to a Sonos speaker as an endless radio station.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="SUBCOMMAND")

    _ = subparsers.add_parser("serve", help="serve the endless stream and block. launchd runs this one")

    bootstrap_parser = subparsers.add_parser("bootstrap", help="fill an empty catalogue from the Everything N playlists")
    _ = bootstrap_parser.add_argument("--oauth", type=Path, default=_DEFAULT_OAUTH_PATH, help="the OAuth token file")
    _ = bootstrap_parser.add_argument(
        "--client-secret",
        type=Path,
        default=_DEFAULT_CLIENT_SECRET_PATH,
        help="the client secret file the Google Cloud console exports",
    )

    auth_parser = subparsers.add_parser("auth", help="write the browser headers file and check it against the library")
    _ = auth_parser.add_argument(
        "--from-file",
        type=Path,
        default=None,
        help="read the browser capture from this file instead of the clipboard. `-` reads standard input",
    )
    refresh_parser = subparsers.add_parser("refresh", help="merge the YouTube Music library into the catalogue")
    for headers_parser in (auth_parser, refresh_parser):
        _ = headers_parser.add_argument(
            "--headers",
            type=Path,
            default=_DEFAULT_HEADERS_PATH,
            help="the browser headers file that `library-radio auth` writes",
        )

    _ = subparsers.add_parser("play", help="point the speaker at the station")
    _ = subparsers.add_parser("stop", help="stop the speaker")
    _ = subparsers.add_parser("status", help="report the catalogue row count and the transport state")

    return parser


def main(argv: Sequence[str] | None = None, *, handlers: Mapping[str, _Handler] | None = None) -> int:
    """Run one subcommand and return its exit code.

    `argv` defaults to the arguments of this process. `handlers` replaces the handler table, so a
    test can route a subcommand to a fake and reach neither the speaker nor the network.
    """
    logging.basicConfig(level=logging.INFO, handlers=[create_handler()])
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse answers `--help` and reports a usage error by raising `SystemExit`. This function
        # returns that code, so it keeps one exit path and an honest return type.
        if isinstance(exc.code, int):
            return exc.code
        return 1  # pragma: no cover -- argparse always exits with an integer code

    command = typing.cast("str", args.command)
    handler = (_HANDLERS if handlers is None else handlers)[command]
    try:
        code = handler(args)
    except _EXPECTED_FAILURES:
        _logger.exception("%s failed", command)
        return 1
    return code


if __name__ == "__main__":  # pragma: no cover -- the console script calls `main` directly
    sys.exit(main())
