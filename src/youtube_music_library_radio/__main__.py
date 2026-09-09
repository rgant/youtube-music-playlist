"""The `library-radio` command line: the one place that wires every module of this project together.

`build_parser` names every subcommand and what it does. Run `library-radio --help` to read them.

Every subcommand returns 0 after success and a non-zero value after a failure. `main` turns an
expected failure into that non-zero value and one log line, so a person sees the fault and not a
traceback.
"""

import argparse
import contextlib
import logging
import random
import sqlite3
import subprocess
import sys
import time
import typing
from pathlib import Path

from ytmusicapi import YTMusic, setup
from ytmusicapi.exceptions import YTMusicError

from youtube_music_library_radio.authheaders import CURL_INSTRUCTIONS, MissingHeadersError, normalise
from youtube_music_library_radio.bootstrap import CredentialError, access_token, bootstrap
from youtube_music_library_radio.catalogue import (
    count_songs,
    duplicated_video_ids,
    mark_queued,
    open_catalogue,
    record_playlists,
    stored_playlists,
    unpaired_songs,
)
from youtube_music_library_radio.control import find_speaker, stop, transport_state
from youtube_music_library_radio.harvest import harvest, read_queue
from youtube_music_library_radio.logger import create_handler
from youtube_music_library_radio.notify import notify
from youtube_music_library_radio.playlists import (
    PLAYLIST_SIZE,
    apply_plan,
    plan_repeats,
    plan_writes,
    read_all,
    require_complete,
    uncovered,
)
from youtube_music_library_radio.queue import pick_queue, send_queue
from youtube_music_library_radio.refresh import library_songs, refresh
from youtube_music_library_radio.rematch import apply_rematch, plan_rematch
from youtube_music_library_radio.settings import load_settings

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from youtube_music_library_radio.catalogue import PlaylistMembership
    from youtube_music_library_radio.harvest import Speaker as HarvestSpeaker
    from youtube_music_library_radio.playlists import PlannedWrite, PlaylistClient
    from youtube_music_library_radio.queue import Speaker as QueueSpeaker
    from youtube_music_library_radio.refresh import ClientFactory, LibraryScan, RefreshResult
    from youtube_music_library_radio.settings import Settings

_logger = logging.getLogger(__name__)

# One subcommand, as `main` calls it.
type _Handler = Callable[[argparse.Namespace], int]

# The failures a person can act on. `main` reports each one and returns a non-zero value.
# `CredentialError` subclasses `TypeError`, and the builtin `TypeError` stays off this list. A catch
# of the builtin reports a programming error in a handler as operator error.
# YouTube answers a bad request with `YTMusicError`. A too-large playlist write gets an HTTP 409.
_EXPECTED_FAILURES = (OSError, RuntimeError, CredentialError, MissingHeadersError, ValueError, sqlite3.Error, YTMusicError)

_DEFAULT_OAUTH_PATH = Path("oauth.json")
_DEFAULT_CLIENT_SECRET_PATH = Path("client_secret.apps.googleusercontent.com.json")
_DEFAULT_HEADERS_PATH = Path("browser.json")

# How many songs `auth` reads to prove the credential works. A read of the whole library takes
# minutes, and it proves nothing that a small read leaves unproven. `refresh` reads every song.
_AUTH_PROBE_LIMIT = 25

# How many songs `queue` names in the log before it reports a total. A 500-song listing buries the
# one line the owner wants.
_QUEUE_PREVIEW = 5


def _stop_station(settings: Settings) -> None:  # pragma: no cover -- reaches the speaker. A live run proves it
    """Stop the speaker named in `settings`."""
    stop(find_speaker(settings.speaker_name))


def _speaker_state(settings: Settings) -> str:  # pragma: no cover -- reaches the speaker. A live run proves it
    """Return the transport state of the speaker named in `settings`."""
    return transport_state(find_speaker(settings.speaker_name))


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

    The clipboard is the default, because the instructions put the capture there. A
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

    This reads the paste itself and hands `ytmusicapi.setup` a normalized header block. The
    `ytmusicapi` prompt names no request to capture and no format. Chrome offers no header copy, so
    a person who follows that prompt has nothing that works. `authheaders.normalise` accepts a
    Chrome "Copy as cURL" as well, and names what is absent.

    The read that follows is the point of this subcommand. A browser cookie expires, and YouTube
    answers an expired session with an empty library and no error. A written file therefore proves
    nothing on its own.

    The new headers go to `<headers>.new` first, and reach `headers_path` only after the read
    returns songs. A person runs `auth` when authentication is in doubt, and the headers they paste
    can be wrong. A write straight onto `headers_path` destroys a working credential before anything
    checks the new one, and the person then holds nothing that works.

    The temporary file sits beside `headers_path`, never under `TMPDIR`. `Path.replace` is atomic on
    one filesystem alone, and a rename across two can fail part way. A failed read deletes the
    temporary file, so this call leaves no second copy of live cookies on disk. If a crash leaves
    one behind, `.gitignore` matches `browser.json*` and git ignores it.

    Raises `RuntimeError` when the read fails. The message names `headers_path` as unchanged. A
    person after a failed run needs to know that the existing credential still stands. `main`
    reports that as one line and a non-zero code.

    Touches no catalogue.
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


def _known_playlists(conn: sqlite3.Connection) -> tuple[PlaylistMembership, ...]:
    """Return the accumulated playlist membership, once it is complete enough to write against.

    Raises `IncompleteReadError` when the store still knows fewer songs than a playlist claims.
    """
    known = stored_playlists(conn)
    require_complete(known)
    _logger.info("%d playlists hold %d known songs", len(known), sum(len(item.video_ids) for item in known))

    repeated = duplicated_video_ids(conn)
    if repeated:
        _logger.warning("%d songs sit in more than one playlist. `playlists` never adds one", len(repeated))
    return known


def _log_plan(plan: Sequence[PlannedWrite], *, wanting: str = "a playlist") -> None:
    """Report every planned write, so a person reads the whole change before `--commit` makes it.

    `wanting` names what the songs lack. An `--unpaired` run gathers songs that sit in a playlist
    already, so "a playlist" names the wrong lack for it.
    """
    total = sum(len(write.video_ids) for write in plan)
    _logger.info("%d song(s) need %s, across %d write(s)", total, wanting, len(plan))
    for write in plan:
        target = "a new playlist" if write.playlist_id is None else "an existing playlist"
        _logger.info("  %s: %d song(s) into %s", write.title, len(write.video_ids), target)


def _commit_plan(
    conn: sqlite3.Connection,
    plan: Sequence[PlannedWrite],
    *,
    client: PlaylistClient,
    sleep_fn: Callable[[float], None],
) -> int:
    """Perform every planned write, and record each playlist as its write lands.

    The read that starts a run cannot hold a playlist the run creates, and `harvest` refuses a
    playlist the store does not know. A record after each playlist also keeps every finished write
    when a later one fails.
    """

    def _record(written: PlaylistMembership) -> None:
        """Store one finished write."""
        record_playlists(conn, [written])

    return apply_plan(plan, client=client, sleep_fn=sleep_fn, on_write=_record)


def _record_and_know(conn: sqlite3.Connection, client: PlaylistClient) -> tuple[PlaylistMembership, ...]:
    """Read every playlist into the store, then return what the store knows they hold.

    Records one playlist per call, so a timeout part way keeps every playlist read before it.
    """
    for playlist in read_all(client):
        record_playlists(conn, [playlist])
    return _known_playlists(conn)


def _playlists(
    args: argparse.Namespace,
    *,
    client_factory: Callable[[str], PlaylistClient] = YTMusic,
    library_fn: Callable[..., LibraryScan] = library_songs,
    size: int = PLAYLIST_SIZE,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """Fill the `Everything N` playlists with every library song no playlist holds.

    With `--unpaired`, gathers the songs that carry no Sonos pairing into new playlists instead.
    Those songs already sit in playlists, about 25 in each, so a harvest of every one of those costs
    many rounds. Gathered together they need one queue read each. That run reads no library, because
    the catalogue already knows which songs carry no pairing.

    Writes nothing without `--commit`. A write reaches the owner's Google account, so a person who
    runs this by mistake must lose nothing.

    Records the playlists in the catalogue on a dry run as well. A read of every playlist is the
    expensive part of this command. The record is worth keeping whatever the owner decides next.

    The store, not one read, decides what the playlists hold. `record_playlists` folds this read into
    the accumulated union, and `require_complete` checks that union against the count each playlist
    claims. A short read therefore costs nothing once an earlier run saw those songs.

    Raises `IncompleteReadError` when the union is still smaller than a playlist claims. `main` turns
    that into one log line and a non-zero code.
    """
    headers_path = typing.cast("Path", args.headers)
    commit = bool(getattr(args, "commit", False))
    settings = load_settings()

    client = client_factory(str(headers_path))
    with contextlib.closing(open_catalogue(settings.database_path)) as conn:
        known = _record_and_know(conn, client)
        unpaired = bool(getattr(args, "unpaired", False))
        if unpaired:
            plan = plan_repeats(unpaired_songs(conn), known=known, size=size)
        else:
            scan = library_fn(headers_path, client_factory=client_factory)
            plan = plan_writes(known, uncovered(scan, known), size=size)

        _log_plan(plan, wanting="a Sonos pairing" if unpaired else "a playlist")
        if not commit:
            _logger.info("this was a dry run and nothing changed. Run it again with --commit to write")
            return 0

        total = _commit_plan(conn, plan, client=client, sleep_fn=sleep_fn)

    _logger.info("wrote %d songs", total)
    return 0


def _settings_refresh(conn: sqlite3.Connection, headers_path: Path, settings: Settings) -> RefreshResult:
    """Call `refresh` with the removal rules the environment sets."""
    return refresh(conn, headers_path, trust_ratio=settings.trust_ratio, missing_threshold=settings.missing_threshold)


def _harvest(args: argparse.Namespace, *, speaker_fn: Callable[[str], object] = find_speaker) -> int:
    """Pair the Sonos queue against one playlist, and store each Sonos track ID.

    The owner adds that playlist to the queue in the Sonos app first. A Sonos track ID exists nowhere
    else, so this read is the only way the catalogue learns it.

    Reads the speaker and the catalogue. Writes nothing to YouTube.
    """
    playlist_title = typing.cast("str", args.playlist)
    settings = load_settings()
    speaker = speaker_fn(settings.speaker_name)
    entries = read_queue(typing.cast("HarvestSpeaker", speaker))
    _logger.info("the Sonos queue holds %d entries", len(entries))

    with contextlib.closing(open_catalogue(settings.database_path)) as conn:
        result = harvest(conn, entries, playlist_title, tolerance=settings.harvest_tolerance)
    _logger.info("harvest paired %d songs and left %d entries unplaced", len(result.pairs), len(result.unplaced))
    return 0


def _rematch(args: argparse.Namespace) -> int:
    """Pair library songs with the Sonos tracks the catalogue already observed.

    Writes nothing without `--commit`. A wrong pairing sends the wrong song to the speaker, and
    nothing later detects it, so a person reads the count before the write.

    This reaches neither YouTube nor the speaker. It spends evidence a past queue read produced.
    """
    commit = bool(getattr(args, "commit", False))
    settings = load_settings()
    with contextlib.closing(open_catalogue(settings.database_path)) as conn:
        plan = plan_rematch(conn)
        if not commit:
            _logger.info("this was a dry run and nothing changed. Run it again with --commit to write")
            return 0

        written = apply_rematch(conn, plan.pairs)
    _logger.info("paired %d songs from stored evidence", written)
    return 0


def _queue(args: argparse.Namespace, *, speaker_fn: Callable[[str], object] = find_speaker) -> int:
    """Send a random sample of paired songs to the speaker, with no recent repeat.

    Writes nothing without `--commit`. A dry run prints the sample and leaves the speaker alone.

    `last_queued` moves for the songs the speaker took, and for no other. A song marked without a
    send stays out of the next queue and never plays.
    """
    commit = bool(getattr(args, "commit", False))
    settings = load_settings()
    with contextlib.closing(open_catalogue(settings.database_path)) as conn:
        songs = pick_queue(conn, size=settings.queue_size, window_days=settings.queue_window_days, rng=random.SystemRandom())
        _logger.info("chose %d songs the speaker has not held for %d days", len(songs), settings.queue_window_days)
        for song in songs[:_QUEUE_PREVIEW]:
            _logger.info("  %s - %s", song.artist, song.title)
        if len(songs) > _QUEUE_PREVIEW:
            _logger.info("  and %d more", len(songs) - _QUEUE_PREVIEW)

        if not commit:
            _logger.info("this was a dry run and the speaker did not change. Run it again with --commit to send")
            return 0

        speaker = speaker_fn(settings.speaker_name)
        sent = send_queue(typing.cast("QueueSpeaker", speaker), songs)
        mark_queued(conn, [song.video_id for song in sent])
    _logger.info("the speaker %s now holds %d of the %d songs chosen", settings.speaker_name, len(sent), len(songs))
    return 0


def _refresh(
    args: argparse.Namespace,
    *,
    refresh_fn: Callable[[sqlite3.Connection, Path, Settings], RefreshResult] = _settings_refresh,
) -> int:
    """Merge the live YouTube Music library into the catalogue, and delete every excluded song.

    `refresh` logs one line per excluded song. This adds the totals, so the owner sees the size of
    the exclusion against the size of the catalogue.
    """
    headers_path = typing.cast("Path", args.headers)
    settings = load_settings()
    with contextlib.closing(open_catalogue(settings.database_path)) as conn:
        result = refresh_fn(conn, headers_path, settings)
        _logger.info(
            "refresh added %d rows, excluded %d songs, deleted %d rows, removed %d absent songs. the catalogue holds %d songs",
            result.added,
            len(result.excluded),
            result.deleted,
            result.removed,
            count_songs(conn),
        )
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
    "bootstrap": _bootstrap,
    "auth": _auth,
    "playlists": _playlists,
    "harvest": _harvest,
    "rematch": _rematch,
    "queue": _queue,
    "refresh": _refresh,
    "stop": _stop,
    "status": _status,
}


def build_parser() -> argparse.ArgumentParser:
    """Build the `library-radio` argument parser.

    The credential options default to the file names the repository root already holds. They are
    per-invocation inputs to `bootstrap`, `auth`, and `refresh`, not service configuration, so they
    stay off `Settings`. `stop` and `status` need none of them.

    `auth` and `refresh` share the `--headers` default, because `auth` writes the file that
    `refresh` reads. A different default on one of them writes one file and reads another.
    """
    parser = argparse.ArgumentParser(
        prog="library-radio",
        description="Keep a catalogue of the owner's YouTube Music library, and command a Sonos speaker.",
    )
    # Only `refresh` offers `--notify`, and `main` reads `args.notify` for every subcommand.
    parser.set_defaults(notify=False)
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="SUBCOMMAND")

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
    playlists_parser = subparsers.add_parser("playlists", help="fill the Everything N playlists with every library song no playlist holds")
    _ = playlists_parser.add_argument(
        "--commit",
        action="store_true",
        help="write to YouTube. Without this flag the command reads, prints the plan, and changes nothing",
    )
    _ = playlists_parser.add_argument(
        "--unpaired",
        action="store_true",
        help="gather the songs that carry no Sonos pairing into new playlists, so one harvest reaches them",
    )
    harvest_parser = subparsers.add_parser("harvest", help="pair the Sonos queue against one playlist")
    _ = harvest_parser.add_argument(
        "--playlist",
        required=True,
        help="the playlist now in the Sonos queue, such as 'Everything 32'",
    )

    rematch_parser = subparsers.add_parser("rematch", help="pair songs with Sonos tracks the catalogue already observed")
    _ = rematch_parser.add_argument(
        "--commit",
        action="store_true",
        help="write the pairings. Without this flag the command prints the count and changes nothing",
    )

    queue_parser = subparsers.add_parser("queue", help="send a random sample of paired songs to the speaker")
    _ = queue_parser.add_argument(
        "--commit",
        action="store_true",
        help="send the queue to the speaker. Without this flag the command prints the sample and sends nothing",
    )

    refresh_parser = subparsers.add_parser("refresh", help="merge the YouTube Music library into the catalogue")
    for agent_parser in (refresh_parser, queue_parser):
        _ = agent_parser.add_argument(
            "--notify",
            action="store_true",
            help="show a macOS notification banner after a failure. The LaunchAgent passes this flag",
        )
    for headers_parser in (auth_parser, playlists_parser, refresh_parser):
        _ = headers_parser.add_argument(
            "--headers",
            type=Path,
            default=_DEFAULT_HEADERS_PATH,
            help="the browser headers file that `library-radio auth` writes",
        )

    _ = subparsers.add_parser("stop", help="stop the speaker")
    _ = subparsers.add_parser("status", help="report the catalogue row count and the transport state")

    return parser


def _notify_failure(args: argparse.Namespace, command: str, body: str, *, notify_fn: Callable[[str, str], bool]) -> None:
    """Show a banner for a failed run that asked for one.

    Only the LaunchAgent passes `--notify`. A terminal run already shows the fault, so a banner
    there is noise.
    """
    if not typing.cast("bool", args.notify):
        return
    _ = notify_fn(f"library-radio {command} failed", body)


def main(
    argv: Sequence[str] | None = None,
    *,
    handlers: Mapping[str, _Handler] | None = None,
    notify_fn: Callable[[str, str], bool] = notify,
) -> int:
    """Run one subcommand and return its exit code.

    `argv` defaults to the arguments of this process. `handlers` replaces the handler table, so a
    test can route a subcommand to a fake and reach neither the speaker nor the network. `notify_fn`
    replaces the banner, so a test shows nothing on the screen.
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
    except _EXPECTED_FAILURES as exc:
        _logger.exception("%s failed", command)
        _notify_failure(args, command, str(exc) or type(exc).__name__, notify_fn=notify_fn)
        return 1

    if code != 0:
        _notify_failure(args, command, f"{command} returned {code}", notify_fn=notify_fn)
    return code


if __name__ == "__main__":  # pragma: no cover -- the console script calls `main` directly
    sys.exit(main())
