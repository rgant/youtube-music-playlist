"""Fill the gap between the catalogue and the live YouTube Music library, and keep it current after that.

Task 7's `bootstrap` seeds the catalogue one time from the owner's `Everything N` YouTube playlists,
which the YouTube Data API can read cheaply. The library holds more songs than those playlists ever
held. New songs also arrive after that one-time seed. This module reads the library directly, so it
covers the songs the playlists missed and the songs that arrive later.

Every authenticated `ytmusicapi` call over OAuth returns HTTP 400 against this project's account.
That covers `get_library_playlists`, `get_library_songs`, `get_liked_songs`, `get_playlist`,
`get_home`, and a plain `search`. See <https://github.com/sigma67/ytmusicapi/issues/813>, open since
2025-09-02.

The Data API cannot read the YouTube Music library at all. Its `relatedPlaylists.likes` returns
YouTube likes, such as gaming videos, and not library songs. Browser-cookie auth is the only route
left. This module therefore authenticates with the headers file that `uv run library-radio auth`
writes, and never with OAuth.

Browser cookies expire. YouTube answers an expired session with an empty library and no error, so an
empty read is a broken credential here. `library_songs` raises on that read. Read its docstring for
the reason.

This module sits off the playback path. The owner runs it by hand, and it is the only part of the
system that needs a credential. If that credential breaks, `refresh` raises and no new song arrives.
A player keeps playing from what the catalogue already holds.
"""

import dataclasses
import json
import logging
import re
import typing

from ytmusicapi import YTMusic
from ytmusicapi.exceptions import YTMusicError

from youtube_music_library_radio.catalogue import Song, delete_songs, merge_songs

if typing.TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable
    from pathlib import Path

    from youtube_music_library_radio.jsonshape import JSON

_logger = logging.getLogger(__name__)

# The library holds roughly 18,000 songs (see the design doc). `get_library_songs` needs a limit
# above the library size to read every song in one call, so this leaves comfortable headroom.
_LIBRARY_LIMIT = 25_000

# A library song whose title holds one of these as a whole word leaves the catalogue. A radio edit
# and a censored cut both duplicate a song the library already holds in full. Add a lowercase
# phrase here, and `refresh` deletes the songs it matches on the next run. Keep each pattern narrow.
# `refresh` lists every song it excludes. The owner reads that list, then widens or narrows a
# pattern that matches the wrong songs.
#
# THE RULE: each pattern must start and end with a letter or a digit. `radio edit` obeys the rule.
# `(explicit)`, `#1 hit`, and `radio edit ` with a space at the end all break it. `_compile_matchers`
# rejects a pattern that breaks the rule, and the module then fails to load. Read its docstring for
# the reason.
_EXCLUDED_PATTERNS: tuple[str, ...] = ("radio edit", "censored")


def _compile_matchers(patterns: tuple[str, ...]) -> tuple[tuple[str, re.Pattern[str]], ...]:
    r"""Return one compiled matcher for each pattern, paired with that pattern, in the order given.

    Raise `ValueError` for a pattern that does not start and end with a letter or a digit. The
    message names the pattern and the rule. This check runs when the module loads, which is before
    `refresh` can delete a row.

    The rule comes from the `\b` on each end of the matcher. `\b` stands only between a word
    character and a non-word character. A pattern that breaks the rule breaks the matcher:

    - `""` compiles to `\b\b`, which matches every title that holds a word character. A refresh then
      deletes every song in the catalogue.
    - `(explicit)`, `#1 hit`, and `radio edit ` put a non-word character next to a `\b`. Each one
      compiles, and then matches no title at all.

    Neither fault reports itself. The empty pattern destroys the catalogue, so the module must
    refuse to load instead.

    `\b` on both ends holds the match to a whole word, so `censored` matches `(Censored)` and leaves
    `The Uncensored Mix`. An uncensored cut is the full song, which is the song the owner wants to
    keep. `re.escape` makes each pattern a literal, so a pattern needs no hand-written escaping.
    `re.IGNORECASE` gives the comparison without case.
    """
    matchers: list[tuple[str, re.Pattern[str]]] = []
    for pattern in patterns:
        if not pattern or not pattern[0].isalnum() or not pattern[-1].isalnum():
            message = (
                f"exclusion pattern {pattern!r} must start and end with a letter or a digit. "
                f"Any other pattern matches every song title or no song title at all."
            )
            raise ValueError(message)
        matchers.append((pattern, re.compile(rf"\b{re.escape(pattern)}\b", re.IGNORECASE)))
    return tuple(matchers)


# One compiled matcher per pattern, in the order of `_EXCLUDED_PATTERNS`.
_EXCLUDED_MATCHERS: tuple[tuple[str, re.Pattern[str]], ...] = _compile_matchers(_EXCLUDED_PATTERNS)

_RECREATE_HEADERS_COMMAND = "uv run library-radio auth"


class LibraryClient(typing.Protocol):
    """The one `ytmusicapi.YTMusic` method this module calls. A test fake implements only this.

    Public because `__main__` names it: the `auth` subcommand verifies a new credential through
    `library_songs`, and it takes the same injected client factory that `refresh` takes.
    """

    def get_library_songs(self, limit: int) -> list[dict[str, JSON]]:
        """Return every song in the library, as `ytmusicapi.YTMusic.get_library_songs` does."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs


# One client, built from the path of a headers file. `ytmusicapi.YTMusic` is the real one. Public
# and named because `library_songs` and `refresh` take it here, and `__main__._auth` takes it for
# the credential the `auth` subcommand writes.
type ClientFactory = Callable[[str], LibraryClient]


@dataclasses.dataclass(frozen=True)
class ExcludedSong:
    """One library song that an exclusion pattern matched.

    Carries the artist and the full title, not the video ID alone. The owner reads this listing to
    judge a pattern, and a video ID alone tells nobody whether the match was right.
    """

    video_id: str
    title: str
    artist: str
    pattern: str


@dataclasses.dataclass(frozen=True)
class LibraryScan:
    """One read of the library: the songs to keep, and the songs an exclusion pattern matched."""

    songs: tuple[Song, ...]
    excluded: tuple[ExcludedSong, ...]


@dataclasses.dataclass(frozen=True)
class RefreshResult:
    """What one `refresh` call did to the catalogue.

    `deleted` can be below the length of `excluded`. An excluded song enters the catalogue only when
    an earlier run put it there, and a song the catalogue never held deletes nothing.
    """

    added: int
    excluded: tuple[ExcludedSong, ...]
    deleted: int


def _first_artist(entry: dict[str, JSON], video_id: str) -> str:
    """Return the name of `entry`'s first artist. An absent, empty, or malformed `artists` gives "".

    A song with no artists is real, not hypothetical. An instrumental or a video-only upload can
    carry an empty `artists` list. `merge_songs` keeps a stored non-empty artist over an incoming
    empty one, so a return of "" here never erases a name the catalogue already holds.
    """
    artists = entry.get("artists")
    if not isinstance(artists, list) or not artists:
        return ""

    first = artists[0]
    if not isinstance(first, dict):
        _logger.warning("refresh: video_id %s has a malformed artists[0] entry; using an empty artist", video_id)
        return ""

    name = first.get("name")
    if not isinstance(name, str):
        _logger.warning("refresh: video_id %s has a malformed artists[0].name; using an empty artist", video_id)
        return ""

    return name


def _title(entry: dict[str, JSON], video_id: str) -> str:
    """Return `entry`'s title. An absent `title`, or a `title` that is not a string, gives "".

    An absent `title` is normal, so it gets no warning. A `title` of another type is a change in the
    library response. It gets a warning, as a malformed `artists` entry does.
    """
    if "title" not in entry:
        return ""

    title = entry["title"]
    if not isinstance(title, str):
        _logger.warning("refresh: video_id %s has a malformed title; using an empty title", video_id)
        return ""

    return title


def _matched_pattern(title: str) -> str | None:
    """Return the first pattern of `_EXCLUDED_PATTERNS` that `title` holds as a whole word, or None.

    The match ignores case and reads the whole title, so a pattern matches wherever it stands. A
    library title marks a radio edit in many forms: `(Radio Edit)`, `- radio edit`, `Radio Edit
    Version`. A word boundary on each end keeps a longer word out of the match, so `censored`
    leaves `The Uncensored Mix` in the catalogue.
    """
    for pattern, matcher in _EXCLUDED_MATCHERS:
        if matcher.search(title) is not None:
            return pattern
    return None


def _songs_from_payload(payload: list[dict[str, JSON]]) -> LibraryScan:
    """Turn a `get_library_songs` payload into the songs to keep and the songs to exclude.

    This function drops an entry with no usable `videoId`. A song with no video ID cannot play, and
    it must never enter the catalogue. The drop logs a warning and moves on. One malformed entry out
    of thousands must not fail the whole refresh.

    An absent `title`, or a `title` of the wrong type, degrades to "". `_first_artist` treats
    `artists` the same way. The catalogue already reads an empty title as normal, and fills it later
    in `catalogue.merge_songs` and in `catalogue.record_success`. `bootstrap` seeds every song that
    way.

    Puts a song whose title holds a pattern of `_EXCLUDED_PATTERNS` as a whole word in `excluded`,
    and not in `songs`.

    `refresh` deletes each of those from the catalogue. A skip alone leaves the row in place. The
    library is not the only way in, and `bootstrap` seeds every song it reads with an empty title.
    """
    songs: list[Song] = []
    excluded: list[ExcludedSong] = []
    for entry in payload:
        video_id = entry.get("videoId")
        if not isinstance(video_id, str) or not video_id:
            _logger.warning("refresh: dropping a library entry with no usable videoId")
            continue

        title = _title(entry, video_id)
        artist = _first_artist(entry, video_id)

        pattern = _matched_pattern(title)
        if pattern is not None:
            excluded.append(ExcludedSong(video_id=video_id, title=title, artist=artist, pattern=pattern))
            continue

        songs.append(Song(video_id=video_id, title=title, artist=artist, failure_count=0, last_success=None, last_played=None))

    return LibraryScan(songs=tuple(songs), excluded=tuple(excluded))


def _headers_error(headers_path: Path, fault: str) -> RuntimeError:
    """Build the error `library_songs` raises for a credential fault. `fault` names that fault.

    Names the path, the fault, and the command that recreates the file. It never names the file's
    contents. A headers file holds cookies, and this project never logs a cookie or a header value.

    Every credential fault here has the same fix and the same type, so they share one shape. The
    `fault` text keeps them apart. A missing file and an expired cookie are different problems, and
    a message that names the wrong one sends the reader to the wrong place.
    """
    return RuntimeError(f"{headers_path}: {fault}. Recreate the file with `{_RECREATE_HEADERS_COMMAND}`.")


def _headers_file_error(headers_path: Path) -> RuntimeError:
    """Build the error for an absent `headers_path`, or for a headers file `ytmusicapi` rejects."""
    return _headers_error(headers_path, "missing or rejected browser headers file")


def _empty_library_error(headers_path: Path) -> RuntimeError:
    """Build the error for a library read that returns no songs.

    The message says that the read succeeded and returned nothing. The file is present and
    `ytmusicapi` accepted it, so a message about a missing file names the wrong problem.
    """
    return _headers_error(headers_path, "the library read succeeded and returned no songs. An expired cookie reads this way")


def library_songs(headers_path: Path, *, client_factory: ClientFactory = YTMusic, limit: int = _LIBRARY_LIMIT) -> LibraryScan:
    """Read the owner's YouTube Music library. Return the songs to keep and the songs to exclude.

    Returns the songs and the exclusions together. The caller acts on each one. `refresh` merges
    `songs` and deletes `excluded`. A return of `songs` alone hides the exclusions from every caller.

    Authenticates with the browser-cookie headers file at `headers_path`. Read the module docstring
    for the reason this project cannot use OAuth here.

    `limit` caps the read. The default covers the whole library, which `refresh` needs and which
    takes minutes. A caller that only wants proof of a working credential passes a small number.

    Raises `RuntimeError` naming `uv run library-radio auth` for an absent file, and for a file
    `ytmusicapi` rejects. A rejected file holds malformed JSON, or the library backend refuses its
    cookie. The message names the fix, so a person months from now does not search for it.

    Raises the same `RuntimeError` when `get_library_songs` returns no songs at all. YouTube answers
    an expired browser cookie with an empty result and no error. An empty read and a dead credential
    look the same from here. This project plays a library of about 16,000 songs, so an empty read is
    a broken credential.

    Without this check, `refresh` reports "added 0 rows" against a dead credential and returns 0. The
    owner then reads that as "the library holds no new songs".

    The cost: a library that truly holds no songs also raises. That case cannot happen for this
    owner. A flag to allow it only adds a way to switch the check off.
    """
    if not headers_path.is_file():
        raise _headers_file_error(headers_path)

    try:
        client = client_factory(str(headers_path))
        _logger.info("reading up to %d songs from the library. A full read takes a few minutes", limit)
        payload = client.get_library_songs(limit=limit)
        _logger.info("the library read returned %d songs", len(payload))
    except (YTMusicError, json.JSONDecodeError) as exc:
        raise _headers_file_error(headers_path) from exc

    if not payload:
        raise _empty_library_error(headers_path)

    return _songs_from_payload(payload)


def refresh(conn: sqlite3.Connection, headers_path: Path, *, client_factory: ClientFactory = YTMusic) -> RefreshResult:
    """Read the library, merge it into the catalogue, and delete every excluded song from the catalogue.

    Logs one INFO line per excluded song, which names the pattern, the video ID, the artist, and the
    title. The owner reads that listing to confirm that a pattern matches the right songs.

    Raises the same `RuntimeError` as `library_songs` for an absent headers file, a rejected one, or
    an empty library read. This function never runs on the playback path. The owner runs it by hand.
    A failure here stops new songs from arriving, and never stops the song that plays. It writes
    nothing to the catalogue after that failure, because the read comes first.
    """
    scan = library_songs(headers_path, client_factory=client_factory)
    added = merge_songs(conn, scan.songs)

    for song in scan.excluded:
        _logger.info(
            "refresh: pattern %r excludes video_id %s, artist %r, title %r",
            song.pattern,
            song.video_id,
            song.artist,
            song.title,
        )
    deleted = delete_songs(conn, [song.video_id for song in scan.excluded])

    _logger.info(
        "refresh: kept %d library songs, added %d new rows, excluded %d songs, deleted %d rows",
        len(scan.songs),
        added,
        len(scan.excluded),
        deleted,
    )
    return RefreshResult(added=added, excluded=scan.excluded, deleted=deleted)
