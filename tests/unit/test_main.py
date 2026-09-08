"""Tests for youtube_music_library_radio.__main__.

No test reaches the speaker or the network. Every subcommand handler takes the work that leaves this
machine as a keyword-only callable, and most tests pass a fake for it. Each fake can raise as well
as return, so a test can prove the failure path and not the success path alone.

`main` is exercised through its `handlers` parameter, which replaces the whole handler table. That
covers the routing from an argument list to a handler. The handlers themselves are called directly,
against a real temporary catalogue, so every catalogue read and write stays real.

`test_a_malformed_credential_file_returns_non_zero` fakes nothing at all.

The `auth` and `refresh` failure tests fake the terminal prompt and the library client, and reach
the real `refresh.library_songs` behind them. A fake handler that raises `RuntimeError` by
construction proves only that the fake chose the right type.
"""

import argparse
import contextlib
import json
import logging
import os
import typing
from pathlib import Path

import pytest
from ytmusicapi.exceptions import YTMusicServerError

from testdoubles import FakeLibraryClient
from youtube_music_library_radio.__main__ import (
    _AUTH_PROBE_LIMIT,
    _HANDLERS,
    _auth,
    _bootstrap,
    _credential_text,
    _log_plan,
    _playlists,
    _refresh,
    _rematch,
    _status,
    _stop,
    main,
)
from youtube_music_library_radio.authheaders import MissingHeadersError
from youtube_music_library_radio.catalogue import (
    PlaylistMembership,
    QueueEntry,
    Song,
    count_songs,
    delete_songs,
    merge_songs,
    open_catalogue,
    pair_sonos_track,
    playlist_of,
    record_playlists,
    record_sonos_track,
    stored_playlists,
)
from youtube_music_library_radio.playlists import IncompleteReadError, PlannedWrite
from youtube_music_library_radio.refresh import ExcludedSong, LibraryScan, RefreshResult, refresh

if typing.TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from youtube_music_library_radio.jsonshape import JSON
    from youtube_music_library_radio.settings import Settings

_SUBCOMMANDS = ["bootstrap", "auth", "playlists", "harvest", "rematch", "queue", "refresh", "stop", "status"]

# The argument list the parser needs for each subcommand. `harvest` names the playlist it pairs
# against, and the rest need nothing beyond their own name.
_ARGV: dict[str, list[str]] = {name: [name] for name in _SUBCOMMANDS} | {"harvest": ["harvest", "--playlist", "Everything 1"]}


@pytest.fixture(autouse=True)
def _clear_ytm_radio_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every `YTM_RADIO_` environment variable, so each test starts from the documented defaults."""
    for name in list(os.environ):
        if name.startswith("YTM_RADIO_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(name="catalogue_path")
def fixture_catalogue_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `YTM_RADIO_DATABASE_PATH` at a temporary catalogue and return that path."""
    path = tmp_path / "catalogue.sqlite3"
    monkeypatch.setenv("YTM_RADIO_DATABASE_PATH", str(path))
    return path


def _song(video_id: str, title: str = "", artist: str = "") -> Song:
    """Build a never-played `Song` for a test."""
    return Song(video_id=video_id, title=title, artist=artist)


def _seed(path: Path, count: int) -> None:
    """Put `count` songs in the catalogue at `path`."""
    with contextlib.closing(open_catalogue(path)) as conn:
        _ = merge_songs(conn, [_song(f"v{index}") for index in range(count)])


def _rows(path: Path) -> int:
    """Return the number of songs in the catalogue at `path`."""
    with contextlib.closing(open_catalogue(path)) as conn:
        return count_songs(conn)


def _args(command: str, **fields: Path) -> argparse.Namespace:
    """Build the `Namespace` that the parser produces for `command`."""
    return argparse.Namespace(command=command, **fields)


_FakeLibrary = FakeLibraryClient

def _library_entry(video_id: str) -> dict[str, JSON]:
    """Build one `get_library_songs` entry, in the shape the live response uses."""
    return {"videoId": video_id, "title": "A Plain Title", "artists": [{"name": "Some Band"}]}


def _prompt_writing(content: str, prompted: list[Path], blocks: list[str] | None = None) -> Callable[[str, str], str]:
    """Build a stand-in for `ytmusicapi.setup`: write `content` to the path given, and record it.

    The real command writes the file. This one writes it too. A handler that gives the prompt one
    path and the read another then fails on a file that is not there. `blocks` records the header
    block the handler passed, so a test can assert what reached `ytmusicapi`.
    """

    def _prompt(filepath: str, headers_raw: str) -> str:
        prompted.append(Path(filepath))
        if blocks is not None:
            blocks.append(headers_raw)
        _ = Path(filepath).write_text(content, encoding="utf-8")
        return content

    return _prompt


_A_CURL_COPY = """curl --url 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \\
  -H 'accept: */*' \\
  -b 'SID=fake-sid; SAPISID=fake-sapisid' \\
  -H 'x-goog-authuser: 0' \\
  --data-raw '{"browseId":"FEmusic_library_landing"}'"""


def test_credential_text_reads_a_file(tmp_path: Path) -> None:
    """`--from-file` reads the capture from disk, which no terminal limit touches."""
    source = tmp_path / "capture.txt"
    _ = source.write_text(_A_CURL_COPY, encoding="utf-8")

    text = _credential_text(source, clipboard_fn=lambda: "", stdin_fn=lambda: "")

    assert text == _A_CURL_COPY


def test_credential_text_reads_the_clipboard_by_default() -> None:
    """With no path, the capture comes from the clipboard, where the person put it."""
    text = _credential_text(None, clipboard_fn=lambda: _A_CURL_COPY, stdin_fn=lambda: "from stdin")

    assert text == _A_CURL_COPY


def test_credential_text_reads_stdin_for_a_dash() -> None:
    """`--from-file -` reads standard input, so a pipe still works."""
    text = _credential_text(Path("-"), clipboard_fn=lambda: "clip", stdin_fn=lambda: _A_CURL_COPY)

    assert text == _A_CURL_COPY


def test_an_empty_clipboard_names_the_fault() -> None:
    """An empty clipboard is a common mistake. The error must say what to copy."""
    with pytest.raises(RuntimeError, match="clipboard"):
        _ = _credential_text(None, clipboard_fn=lambda: "   ", stdin_fn=lambda: "")


def test_auth_accepts_a_chrome_curl_copy(tmp_path: Path) -> None:
    """`auth` turns a Chrome cURL copy into the header block `ytmusicapi` wants.

    Chrome offers no header copy, so a person following the `ytmusicapi` prompt has nothing that
    works. The handler must accept what the browser actually gives.
    """
    headers_path = tmp_path / "browser.json"
    prompted: list[Path] = []
    blocks: list[str] = []

    code = _auth(
        argparse.Namespace(headers=headers_path),
        setup_fn=_prompt_writing('{"cookie": "new"}', prompted, blocks),
        client_factory=lambda _auth_arg: _FakeLibrary([_library_entry("LIB_A")]),
        read_fn=lambda: _A_CURL_COPY,
    )

    assert code == 0
    assert "cookie: SID=fake-sid; SAPISID=fake-sapisid" in blocks[0]
    assert "x-goog-authuser: 0" in blocks[0]
    assert "browseId" not in blocks[0]


def test_auth_refuses_a_paste_with_no_credential(tmp_path: Path) -> None:
    """A page-load capture carries no cookie. `auth` names the fault and writes nothing."""
    headers_path = tmp_path / "browser.json"
    _ = headers_path.write_text('{"cookie": "old"}', encoding="utf-8")

    with pytest.raises(MissingHeadersError, match="youtubei/v1/browse"):
        _ = _auth(
            argparse.Namespace(headers=headers_path),
            setup_fn=_prompt_writing("{}", []),
            client_factory=lambda _auth_arg: _FakeLibrary([]),
            read_fn=lambda: "accept: */*\nuser-agent: Mozilla/5.0",
        )

    assert headers_path.read_text(encoding="utf-8") == '{"cookie": "old"}'


def test_unknown_subcommand_returns_non_zero() -> None:
    """`main` returns a non-zero code for a subcommand the parser does not know."""
    assert main(["bogus"]) != 0


def test_help_returns_zero() -> None:
    """`main` returns zero for `--help`, which the parser answers before any handler runs."""
    assert main(["--help"]) == 0


def test_each_subcommand_is_registered() -> None:
    """The parser accepts every subcommand, and `main` routes each one to the handler of that name.

    The last assertion reads the handler behind each key, and not the key set alone. A key set holds
    no evidence that `stop` reaches `_stop`. Two swapped values leave the key set equal, and
    `library-radio status` then stops the speaker.

    It derives the expected mapping from the naming rule `_HANDLERS` already follows: the handler
    for `name` is `_name`. A new subcommand in `_SUBCOMMANDS` and `_HANDLERS` therefore needs no
    edit here, and still gets the same check.
    """
    reached: list[str] = []

    def _record(args: argparse.Namespace) -> int:
        reached.append(typing.cast("str", args.command))
        return 0

    handlers = dict.fromkeys(_SUBCOMMANDS, _record)

    codes = [main(_ARGV[name], handlers=handlers) for name in _SUBCOMMANDS]

    assert codes == [0] * len(_SUBCOMMANDS)
    assert sorted(reached) == sorted(_SUBCOMMANDS)
    assert {name: fn.__name__ for name, fn in _HANDLERS.items()} == {name: f"_{name}" for name in _SUBCOMMANDS}


def test_status_reports_the_row_count(catalogue_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """`status` reports the catalogue row count and the transport state, and returns zero."""
    _seed(catalogue_path, 3)

    def _playing(_settings: Settings) -> str:
        return "PLAYING"

    with caplog.at_level(logging.INFO):
        code = _status(_args("status"), state_fn=_playing)

    assert code == 0
    assert "holds 3 songs" in caplog.text
    assert "PLAYING" in caplog.text


def test_status_reports_the_row_count_when_the_speaker_is_missing(catalogue_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """`status` still reports the row count when discovery fails, and returns a non-zero code."""
    _seed(catalogue_path, 2)

    def _no_speaker(_settings: Settings) -> str:
        message = "no speaker named 'Kitchen' found; discovery found: none"
        raise RuntimeError(message)

    with caplog.at_level(logging.INFO):
        code = _status(_args("status"), state_fn=_no_speaker)

    assert code != 0
    assert "holds 2 songs" in caplog.text
    assert "Kitchen" in caplog.text


def test_bootstrap_reports_the_rows_it_added(catalogue_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """`bootstrap` reads the credential files named on the command line and reports the rows it added."""
    credentials: list[tuple[Path, Path]] = []
    granted: list[str] = []

    def _token(oauth_path: Path, client_secret_path: Path) -> str:
        credentials.append((oauth_path, client_secret_path))
        return "a-token"

    def _fill(conn: sqlite3.Connection, token: str) -> int:
        granted.append(token)
        return merge_songs(conn, [_song("new")])

    args = _args("bootstrap", oauth=Path("o.json"), client_secret=Path("c.json"))
    with caplog.at_level(logging.INFO):
        code = _bootstrap(args, token_fn=_token, bootstrap_fn=_fill)

    assert code == 0
    assert credentials == [(Path("o.json"), Path("c.json"))]
    assert granted == ["a-token"]
    assert "added 1" in caplog.text
    assert _rows(catalogue_path) == 1


def test_refresh_reports_the_rows_it_added_excluded_and_deleted(catalogue_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """`refresh` reads the headers file named on the command line, and reports every total.

    The exclusion totals belong on this line as much as the added rows do. A run that deletes rows
    must say so, because a deletion is the one thing here a person cannot undo.
    """
    headers: list[Path] = []

    def _merge(conn: sqlite3.Connection, headers_path: Path, _settings: Settings) -> RefreshResult:
        headers.append(headers_path)
        _ = merge_songs(conn, [_song("stale", title="Song (Radio Edit)", artist="Band")])
        added = merge_songs(conn, [_song("new", title="Song", artist="Band")])
        excluded = (ExcludedSong(video_id="stale", title="Song (Radio Edit)", artist="Band", pattern="radio edit"),)
        return RefreshResult(added=added, excluded=excluded, deleted=delete_songs(conn, ["stale"]), removed=2)

    args = _args("refresh", headers=Path("browser.json"))
    with caplog.at_level(logging.INFO):
        code = _refresh(args, refresh_fn=_merge)

    assert code == 0
    assert headers == [Path("browser.json")]
    assert "added 1" in caplog.text
    assert "excluded 1" in caplog.text
    assert "deleted 1" in caplog.text
    assert "removed 2" in caplog.text
    assert _rows(catalogue_path) == 1


def test_auth_replaces_the_headers_file_after_a_successful_read(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """`auth` installs the new headers over the old file, and reports the song count.

    The prompt receives `browser.json.new`, and the read runs against that candidate file. The move
    happens after the read. It leaves no `.new` file behind, because `.gitignore` covers
    `browser.json` and no name derived from it.
    """
    headers_path = tmp_path / "browser.json"
    _ = headers_path.write_text('{"cookie": "old"}', encoding="utf-8")
    prompted: list[Path] = []
    payload = [_library_entry("LIB_VIDEO_ONE"), _library_entry("LIB_VIDEO_TWO")]

    with caplog.at_level(logging.INFO):
        code = _auth(
            _args("auth", headers=headers_path),
            setup_fn=_prompt_writing('{"cookie": "new"}', prompted),
            read_fn=lambda: _A_CURL_COPY,
            client_factory=lambda _auth: _FakeLibrary(payload),
        )

    assert code == 0
    assert prompted == [tmp_path / "browser.json.new"]
    assert headers_path.read_text(encoding="utf-8") == '{"cookie": "new"}'
    assert not (tmp_path / "browser.json.new").exists()
    assert "returned 2 songs" in caplog.text


def test_auth_leaves_the_existing_headers_file_untouched_when_the_read_returns_no_songs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed verification keeps the old credential byte for byte, and returns a non-zero code.

    A person runs `auth` when authentication is in doubt, and the headers they paste can be wrong.
    A write straight onto `browser.json` destroys the one credential that still works, and the
    read then reports that loss instead of preventing it.

    The message must say that the old file stands. A person who sees this failure needs to know
    whether they are worse off than before.
    """
    headers_path = tmp_path / "browser.json"
    original = b'{"cookie": "still-good"}'
    _ = headers_path.write_bytes(original)
    prompted: list[Path] = []

    def _verify(args: argparse.Namespace) -> int:
        return _auth(
            args,
            setup_fn=_prompt_writing('{"cookie": "pasted-wrong"}', prompted),
            read_fn=lambda: _A_CURL_COPY,
            client_factory=lambda _auth: _FakeLibrary([]),
        )

    with caplog.at_level(logging.ERROR):
        code = main(["auth", "--headers", str(headers_path)], handlers={"auth": _verify})

    assert headers_path.read_bytes() == original  # the assertion that matters: the old credential survives
    assert code != 0
    assert prompted == [tmp_path / "browser.json.new"]
    assert not (tmp_path / "browser.json.new").exists()
    assert "returned no songs" in caplog.text
    assert f"{headers_path} is unchanged" in caplog.text


def test_refresh_returns_non_zero_when_the_library_reads_empty(
    catalogue_path: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`main` reports the empty library read of `refresh` as operator error, and not as a traceback.

    The real `_refresh`, the real `refresh`, and the real `library_songs` run here. The library
    client alone is a fake, and it returns the empty result an expired cookie produces. A run that
    reported "added 0 rows" and returned zero is the defect this test locks out.
    """
    _seed(catalogue_path, 1)
    headers_path = tmp_path / "browser.json"
    _ = headers_path.write_text("{}", encoding="utf-8")

    def _read_empty(conn: sqlite3.Connection, path: Path, _settings: Settings) -> RefreshResult:
        return refresh(conn, path, client_factory=lambda _auth: _FakeLibrary([]))

    with caplog.at_level(logging.ERROR):
        code = main(
            ["refresh", "--headers", str(headers_path)],
            handlers={"refresh": lambda args: _refresh(args, refresh_fn=_read_empty)},
        )

    assert code != 0
    assert "returned no songs" in caplog.text
    assert _rows(catalogue_path) == 1


def test_stop_stops_the_speaker() -> None:
    """`stop` stops the speaker named in the settings, and returns zero."""
    stopped: list[str] = []

    def _stop_speaker(settings: Settings) -> None:
        stopped.append(settings.speaker_name)

    code = _stop(_args("stop"), stop_fn=_stop_speaker)

    assert code == 0
    assert stopped == ["Kitchen"]


def test_a_malformed_credential_file_returns_non_zero(catalogue_path: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """`main` turns the `CredentialError` of a wrongly shaped credential file into a non-zero code.

    This test fakes nothing. The real `_bootstrap` handler runs, the real `access_token` reads the
    file, and the real `_require_str` raises. A fake handler that raises `CredentialError` by
    construction proves only that the fake chose the right type. It cannot detect a change of the
    raise type inside `bootstrap.py`, which is the fault this test exists to catch.

    `access_token` reads `oauth.json` before the client secret file, so the run stops on the first
    file and never reads a second one.
    """
    del catalogue_path
    oauth_path = tmp_path / "oauth.json"
    _ = oauth_path.write_text(json.dumps({"token_type": "Bearer"}), encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        code = main(["bootstrap", "--oauth", str(oauth_path)])

    assert code != 0
    assert "refresh_token" in caplog.text


def test_a_plain_type_error_is_not_reported_as_operator_error() -> None:
    """`main` lets a bare `TypeError` out, because that is a defect in this code and not a bad file.

    `_EXPECTED_FAILURES` names `CredentialError`, which subclasses `TypeError`. It never names the
    builtin. A handler that calls a function with the wrong arguments must reach the developer as a
    traceback, and never the owner as an operator error.
    """

    def _a_bug(args: argparse.Namespace) -> int:
        del args
        message = "unsupported operand type(s)"
        raise TypeError(message)

    with pytest.raises(TypeError, match="unsupported operand"):
        _ = main(["status"], handlers={"status": _a_bug})


def test_an_unusable_headers_file_returns_non_zero(caplog: pytest.LogCaptureFixture) -> None:
    """`main` turns the `RuntimeError` of an unusable headers file into a non-zero code."""

    def _rejected(args: argparse.Namespace) -> int:
        del args
        message = "browser.json: rejected browser headers file; recreate it with `uv run ytmusicapi browser`"
        raise RuntimeError(message)

    with caplog.at_level(logging.ERROR):
        code = main(["refresh"], handlers={"refresh": _rejected})

    assert code != 0
    assert "ytmusicapi browser" in caplog.text


def test_a_setting_outside_its_range_returns_non_zero(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` turns the `ValueError` of an out-of-range setting into a non-zero code.

    `load_settings` runs before `_status` reaches the catalogue or the speaker, so the bad value
    stops the subcommand at its first line.
    """
    monkeypatch.setenv("YTM_RADIO_MISSING_THRESHOLD", "0")

    with caplog.at_level(logging.ERROR):
        code = main(["status"])

    assert code != 0
    assert "YTM_RADIO_MISSING_THRESHOLD" in caplog.text


def test_the_credential_options_default_to_the_repository_file_names() -> None:
    """Each credential option defaults to the file name the repository root already holds.

    The result is keyed by subcommand, so `auth` and `refresh` each carry their own `--headers`
    default. `auth` writes that file and `refresh` reads it. Two different defaults write one file
    and read another, and a single flat result cannot show that.
    """
    seen: dict[str, dict[str, Path]] = {}

    def _capture(args: argparse.Namespace) -> int:
        command = typing.cast("str", args.command)
        names = ("oauth", "client_secret", "headers")
        seen[command] = {name: typing.cast("Path", getattr(args, name)) for name in names if hasattr(args, name)}
        return 0

    for command in ("bootstrap", "auth", "refresh"):
        _ = main([command], handlers={command: _capture})

    assert seen == {
        "bootstrap": {
            "oauth": Path("oauth.json"),
            "client_secret": Path("client_secret.apps.googleusercontent.com.json"),
        },
        "auth": {"headers": Path("browser.json")},
        "refresh": {"headers": Path("browser.json")},
    }


def test_auth_checks_the_credential_with_a_small_read(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """`auth` reads a few songs, not the whole library.

    A full read of about 19,000 songs takes minutes, and it proves nothing that 25 songs leave
    unproven. An `auth` command that runs for minutes with no output reads as a hang.
    """
    library = _FakeLibrary([_library_entry("LIB_A")])

    with caplog.at_level(logging.INFO):
        code = _auth(
            argparse.Namespace(headers=tmp_path / "browser.json"),
            setup_fn=_prompt_writing('{"cookie": "new"}', []),
            client_factory=lambda _auth_arg: library,
            read_fn=lambda: _A_CURL_COPY,
        )

    assert code == 0
    assert library.limits == [_AUTH_PROBE_LIMIT]
    assert any("checking the new headers" in record.message for record in caplog.records)


class _FakePlaylistClient:
    """A `ytmusicapi.YTMusic` double for the playlist methods. It records every write."""

    def __init__(self, library: dict[str, list[str]], *, reported: dict[str, int] | None = None) -> None:
        self.library: dict[str, list[str]] = library
        self.reported: dict[str, int] = reported or {}
        self.created: list[str] = []
        self.added: list[tuple[str, tuple[str, ...]]] = []

    def get_library_playlists(self, limit: int | None = 25) -> list[dict[str, JSON]]:
        """Return one entry per playlist, newest first."""
        del limit
        return [{"title": title, "playlistId": f"PL{title.split()[-1]}"} for title in reversed(list(self.library))]

    def get_playlist(self, playlistId: str, limit: int | None = 100) -> dict[str, JSON]:  # noqa: N803 -- mirrors ytmusicapi
        """Return the members of `playlistId`, with a count that matches them."""
        del limit
        title = next(name for name in self.library if f"PL{name.split()[-1]}" == playlistId)
        members = self.library[title]
        return {
            "trackCount": self.reported.get(title, len(members)),
            "tracks": [{"videoId": video_id} for video_id in members],
        }

    def create_playlist(self, title: str, description: str, privacy_status: str = "PRIVATE") -> str | dict[str, object]:
        """Record the creation and return the new playlist ID."""
        del description, privacy_status
        self.created.append(title)
        self.library[title] = []
        return f"PL{title.rsplit(maxsplit=1)[-1]}"

    def add_playlist_items(
        self,
        playlistId: str,  # noqa: N803 -- mirrors ytmusicapi.YTMusic.add_playlist_items
        videoIds: list[str] | None = None,  # noqa: N803 -- mirrors ytmusicapi.YTMusic.add_playlist_items
        *,
        duplicates: bool = False,
    ) -> str | dict[str, object]:
        """Record the addition and put the songs in the fake playlist."""
        del duplicates
        given = videoIds or []
        title = next(name for name in self.library if f"PL{name.split()[-1]}" == playlistId)
        self.added.append((playlistId, tuple(given)))
        self.library[title].extend(given)
        return "STATUS_SUCCEEDED"


def _scan(*video_ids: str) -> LibraryScan:
    """Build a `LibraryScan` holding one song per video ID and no exclusion."""
    return LibraryScan(
        songs=tuple(
            Song(video_id=vid, title="A Title", artist="A Band") for vid in video_ids
        ),
        excluded=(),
    )


@pytest.mark.usefixtures("catalogue_path")
def test_playlists_writes_nothing_without_commit(caplog: pytest.LogCaptureFixture) -> None:
    """A dry run is the default, because a write reaches the owner's Google account."""
    client = _FakePlaylistClient({"Everything 1": ["held"]})

    with caplog.at_level(logging.INFO):
        code = _playlists(
            argparse.Namespace(headers=Path("browser.json"), commit=False),
            client_factory=lambda _path: client,
            library_fn=lambda *_args, **_kwargs: _scan("held", "new"),
        )

    assert code == 0
    assert not client.added
    assert not client.created
    assert "1 song" in caplog.text


def test_playlists_records_the_playlists_on_a_dry_run(catalogue_path: Path) -> None:
    """The read is the expensive part, so a dry run must still store what it read."""
    client = _FakePlaylistClient({"Everything 1": ["held"]})

    _ = _playlists(
        argparse.Namespace(headers=Path("browser.json"), commit=False),
        client_factory=lambda _path: client,
        library_fn=lambda *_args, **_kwargs: _scan("held"),
    )

    with contextlib.closing(open_catalogue(catalogue_path)) as conn:
        assert playlist_of(conn, "held") == "Everything 1"


@pytest.mark.usefixtures("catalogue_path")
def test_playlists_writes_the_plan_with_commit() -> None:
    """`--commit` reaches `apply_plan`, which fills the free slots before it creates a playlist."""
    client = _FakePlaylistClient({"Everything 1": ["held"]})

    code = _playlists(
        argparse.Namespace(headers=Path("browser.json"), commit=True),
        client_factory=lambda _path: client,
        library_fn=lambda *_args, **_kwargs: _scan("held", "new"),
        size=2,
        sleep_fn=lambda _seconds: None,
    )

    assert code == 0
    assert client.added == [("PL1", ("new",))]
    assert not client.created


@pytest.mark.usefixtures("catalogue_path")
def test_playlists_creates_a_playlist_when_every_existing_one_is_full() -> None:
    """A full run continues the ordinal sequence rather than overfilling a playlist."""
    client = _FakePlaylistClient({"Everything 1": ["held"]})

    _ = _playlists(
        argparse.Namespace(headers=Path("browser.json"), commit=True),
        client_factory=lambda _path: client,
        library_fn=lambda *_args, **_kwargs: _scan("held", "new"),
        size=1,
        sleep_fn=lambda _seconds: None,
    )

    assert client.created == ["Everything 2"]
    assert client.added == [("PL2", ("new",))]


def test_playlists_records_a_playlist_it_created(catalogue_path: Path) -> None:
    """`harvest` refuses a playlist the store does not know, and this run is what creates it."""
    client = _FakePlaylistClient({"Everything 1": ["held"]})

    _ = _playlists(
        argparse.Namespace(headers=Path("browser.json"), commit=True),
        client_factory=lambda _path: client,
        library_fn=lambda *_args, **_kwargs: _scan("held", "new"),
        size=1,
        sleep_fn=lambda _seconds: None,
    )

    with contextlib.closing(open_catalogue(catalogue_path)) as conn:
        assert playlist_of(conn, "new") == "Everything 2"
        assert [(item.title, item.reported_count) for item in stored_playlists(conn)] == [("Everything 1", 1), ("Everything 2", 1)]


def test_playlists_records_a_song_it_added_to_an_existing_playlist(catalogue_path: Path) -> None:
    """The store must hold every song the run wrote. A later run reads it to plan the next write."""
    client = _FakePlaylistClient({"Everything 1": ["held"]})

    _ = _playlists(
        argparse.Namespace(headers=Path("browser.json"), commit=True),
        client_factory=lambda _path: client,
        library_fn=lambda *_args, **_kwargs: _scan("held", "new"),
        size=2,
        sleep_fn=lambda _seconds: None,
    )

    with contextlib.closing(open_catalogue(catalogue_path)) as conn:
        assert playlist_of(conn, "new") == "Everything 1"


def test_an_incomplete_playlist_read_returns_non_zero(caplog: pytest.LogCaptureFixture) -> None:
    """A short read must stop the run with a non-zero code, not a warning and a bad write."""

    def _short(_args: argparse.Namespace) -> int:
        message = "playlist 'Everything 1' claims 500 songs and returned 465. Run this again"
        raise IncompleteReadError(message)

    with caplog.at_level(logging.ERROR):
        code = main(["playlists"], handlers={"playlists": _short})

    assert code != 0
    assert "Everything 1" in caplog.text


def test_a_short_read_is_harmless_when_the_store_covers_the_playlist(catalogue_path: Path) -> None:
    """A short read must not make the run add songs the playlist already holds.

    This is the point of the store. `ytmusicapi` returns short reads often enough to matter, and the
    accumulated membership is what keeps such a read from writing duplicates.
    """
    every = [f"v{index}" for index in range(500)]
    with contextlib.closing(open_catalogue(catalogue_path)) as conn:
        record_playlists(conn, [PlaylistMembership("PL1", "Everything 1", 1, 500, tuple(every))])

    # The live read returns two of the 500 songs and still claims 500.
    client = _FakePlaylistClient({"Everything 1": every[:2]}, reported={"Everything 1": 500})

    code = _playlists(
        argparse.Namespace(headers=Path("browser.json"), commit=True),
        client_factory=lambda _path: client,
        library_fn=lambda *_args, **_kwargs: _scan(*every, "brand-new"),
        size=500,
        sleep_fn=lambda _seconds: None,
    )

    assert code == 0
    # A run that trusts the read alone re-adds the 498 songs `Everything 1` already holds.
    assert client.created == ["Everything 2"]
    assert client.added == [("PL2", ("brand-new",))]


@pytest.mark.usefixtures("catalogue_path")
def test_a_short_read_stops_the_run_when_the_store_knows_nothing() -> None:
    """With no earlier read to fall back on, the union is still short, so nothing can write."""
    client = _FakePlaylistClient({"Everything 1": ["a", "b"]}, reported={"Everything 1": 500})

    with pytest.raises(IncompleteReadError, match="Everything 1"):
        _ = _playlists(
            argparse.Namespace(headers=Path("browser.json"), commit=True),
            client_factory=lambda _path: client,
            library_fn=lambda *_args, **_kwargs: _scan("a", "b", "c"),
        )

    assert not client.added


@pytest.mark.usefixtures("catalogue_path")
def test_a_read_that_fails_part_way_keeps_the_playlists_it_already_read(catalogue_path: Path) -> None:
    """A timeout on playlist 3 must not throw away playlists 1 and 2.

    A read of every playlist takes minutes, and YouTube times one out now and then. The store commits
    each playlist as it arrives, so the next run starts from what this one managed.
    """

    class _FailsOnThird(_FakePlaylistClient):
        """Reads two playlists, then raises the timeout `requests` raises."""

        @typing.override
        def get_playlist(self, playlistId: str, limit: int | None = 100) -> dict[str, JSON]:
            if playlistId == "PL3":
                message = "Read timed out"
                raise TimeoutError(message)
            return super().get_playlist(playlistId, limit)

    client = _FailsOnThird({"Everything 1": ["a"], "Everything 2": ["b"], "Everything 3": ["c"]})

    with pytest.raises(TimeoutError):
        _ = _playlists(
            argparse.Namespace(headers=Path("browser.json"), commit=False),
            client_factory=lambda _path: client,
            library_fn=lambda *_args, **_kwargs: _scan("a", "b", "c"),
        )

    with contextlib.closing(open_catalogue(catalogue_path)) as conn:
        assert [item.title for item in stored_playlists(conn)] == ["Everything 1", "Everything 2"]


def test_a_ytmusicapi_server_error_returns_non_zero(caplog: pytest.LogCaptureFixture) -> None:
    """YouTube answers a bad request with an error `ytmusicapi` raises. A person cannot act on a traceback.

    `YTMusicError` subclasses `Exception`, not `OSError`, so it escapes the network-fault catch. It
    reaches the owner on a real run, so `main` must report it as one line and a non-zero code.
    """

    def _server_error(_args: argparse.Namespace) -> int:
        message = "Server returned HTTP 409: Conflict."
        raise YTMusicServerError(message)

    with caplog.at_level(logging.ERROR):
        code = main(["playlists"], handlers={"playlists": _server_error})

    assert code != 0
    assert "409" in caplog.text


def test_rematch_writes_nothing_without_commit(catalogue_path: Path) -> None:
    """A pairing reaches the queue builder, so a dry run must leave every song alone."""
    with contextlib.closing(open_catalogue(catalogue_path)) as conn:
        _ = merge_songs(conn, [Song(video_id="V1", title="A Song", artist="A Band", album="An Album")])
        record_sonos_track(
            conn,
            QueueEntry(
                track_id="S1",
                uri="x-sonosapi-hls-static:S1",
                title="A Song",
                artist="A Band",
                album="An Album",
                duration_seconds=0,
            ),
            source="test",
            seen="2026-09-07T00:00:00+00:00",
        )

    assert _rematch(argparse.Namespace(commit=False)) == 0

    with contextlib.closing(open_catalogue(catalogue_path)) as conn:
        assert conn.execute("SELECT sonos_uri FROM songs WHERE video_id = 'V1'").fetchone()["sonos_uri"] is None


def test_rematch_writes_the_pairing_with_commit(catalogue_path: Path) -> None:
    """The gain: the song reaches the queue with no Sonos app work."""
    with contextlib.closing(open_catalogue(catalogue_path)) as conn:
        _ = merge_songs(conn, [Song(video_id="V1", title="A Song", artist="A Band", album="An Album")])
        record_sonos_track(
            conn,
            QueueEntry(
                track_id="S1",
                uri="x-sonosapi-hls-static:S1",
                title="A Song",
                artist="A Band",
                album="An Album",
                duration_seconds=0,
            ),
            source="test",
            seen="2026-09-07T00:00:00+00:00",
        )

    assert _rematch(argparse.Namespace(commit=True)) == 0

    with contextlib.closing(open_catalogue(catalogue_path)) as conn:
        assert conn.execute("SELECT sonos_uri FROM songs WHERE video_id = 'V1'").fetchone()["sonos_uri"] == "x-sonosapi-hls-static:S1"


def test_playlists_unpaired_gathers_songs_that_carry_no_pairing(catalogue_path: Path) -> None:
    """One harvest of a new playlist reaches them all, against 29 harvests of the playlists holding them."""
    client = _FakePlaylistClient({"Everything 1": ["paired", "gap"]})
    with contextlib.closing(open_catalogue(catalogue_path)) as conn:
        _ = merge_songs(conn, [Song(video_id="paired", title="P", artist="B"), Song(video_id="gap", title="G", artist="B")])
        _ = pair_sonos_track(conn, "paired", track_id="S1", uri="x-sonosapi-hls-static:S1?sid=284")

    _ = _playlists(
        argparse.Namespace(headers=Path("browser.json"), commit=True, unpaired=True),
        client_factory=lambda _path: client,
        library_fn=lambda *_args, **_kwargs: _scan("paired", "gap"),
        size=500,
        sleep_fn=lambda _seconds: None,
    )

    assert client.created == ["Everything 2"]
    assert client.added == [("PL2", ("gap",))]


def test_playlists_unpaired_reads_no_library(catalogue_path: Path) -> None:
    """The catalogue knows which songs carry no pairing, so a four-minute library read buys nothing."""
    client = _FakePlaylistClient({"Everything 1": ["gap"]})
    with contextlib.closing(open_catalogue(catalogue_path)) as conn:
        _ = merge_songs(conn, [Song(video_id="gap", title="G", artist="B")])

    def _never(*_args: object, **_kwargs: object) -> LibraryScan:
        """Fail the test if the command reads the library."""
        message = "the unpaired run must not read the library"
        raise AssertionError(message)

    _ = _playlists(
        argparse.Namespace(headers=Path("browser.json"), commit=True, unpaired=True),
        client_factory=lambda _path: client,
        library_fn=_never,
        size=500,
        sleep_fn=lambda _seconds: None,
    )

    assert client.created == ["Everything 2"]


def test_the_unpaired_plan_says_the_songs_need_a_pairing(caplog: pytest.LogCaptureFixture) -> None:
    """Those songs already sit in a playlist, so "need a playlist" names the wrong lack."""
    plan = (PlannedWrite(playlist_id=None, title="Everything 45", ordinal=45, video_ids=("a",)),)

    with caplog.at_level(logging.INFO):
        _log_plan(plan, wanting="a Sonos pairing")

    assert "1 song(s) need a Sonos pairing, across 1 write(s)" in caplog.text
