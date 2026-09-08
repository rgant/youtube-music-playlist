"""Pair a library song with the Sonos track that plays it.

A Sonos queue entry carries a title, an artist, an album, a duration, and an opaque track ID. It
carries no YouTube video ID, and the ID encodes none. So the pairing must be observed, and the only
place to observe it is a queue the owner filled from one `Everything N` playlist.

Sonos does not preserve playlist order, so position tells nothing. The candidate pool is what makes
the match safe: one playlist holds about 500 songs, and a title repeat inside 500 songs does not
happen. Against one real playlist this placed 96.4% of the songs still in the library, with no
ambiguous pair at all. Against the whole library the same rules give 87% and 96 collisions.

`match` never guesses. Two candidates that fit equally well leave the entry unplaced. A wrong pair
writes the wrong Sonos track onto a song, and nothing later detects it.

Read `docs/plans/2026-08-16-library-database.md` for the measurement behind each tier.
"""

import dataclasses
import difflib
import logging
import typing
from datetime import UTC, datetime

from youtube_music_library_radio.catalogue import (
    QueueEntry,
    pair_sonos_track,
    record_sonos_track,
    songs_by_video_id,
    stored_playlists,
)

# `QueueEntry` lives with the table it is stored in. This module builds one per queue entry, so the
# name belongs in its public surface too.
__all__ = [
    "HarvestError",
    "MatchResult",
    "QueueEntry",
    "Speaker",
    "comparison_key",
    "harvest",
    "match",
    "primary_artist",
    "read_queue",
]

if typing.TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterable, Sequence

    from youtube_music_library_radio.catalogue import Song

_logger = logging.getLogger(__name__)

# How far two lengths can differ and still name one recording. Sonos and YouTube round differently,
# and a stated length moves by a second or two between them.
_DURATION_TOLERANCE_SECONDS = 3

# How alike two titles must read before the duration tier accepts them. Below this the entry stays
# unplaced, because a wrong pair is worse than no pair.
_TITLE_SIMILARITY = 0.8

# The shortest title that counts as contained in another. Without a floor a two-letter title reaches
# a longer one that shares those letters.
_MIN_CONTAINED_TITLE = 4

# How many queue entries one `get_queue` call returns. The speaker pages a long queue, and a real
# queue holds hundreds of entries.
_QUEUE_PAGE = 200

# What YouTube appends to the name of an auto-generated artist channel. It returns that name as the
# artist, and Sonos writes the artist alone.
_CHANNEL_SUFFIX = " - Topic"

# How much of the candidate pool must carry a title before a match is worth running. `bootstrap`
# seeds a song with a video ID and nothing else, and a pool of those pairs nothing at all.
_MIN_TITLED_SHARE = 0.5


class HarvestError(RuntimeError):
    """The harvest cannot run against this queue. The message names what does not line up."""


@dataclasses.dataclass(frozen=True)
class MatchResult:
    """What one match run decided.

    `pairs` holds `(track_id, video_id)` for each entry it placed. `unplaced` holds every entry it
    refused to place, so the owner sees exactly what the run left unidentified.
    """

    pairs: tuple[tuple[str, str], ...]
    unplaced: tuple[QueueEntry, ...]


def comparison_key(text: str) -> str:
    """Reduce text to lower-case letters and digits.

    Sonos and YouTube punctuate and capitalize a title differently. An exact comparison loses a song
    over a bracket or an accent, and this comparison does not.
    """
    return "".join(char for char in text.lower() if char.isalnum())


def primary_artist(artist: str) -> str:
    """Return the first artist of a Sonos artist string, without a YouTube channel suffix.

    Sonos writes every artist of a song, and `refresh` stores the first one alone. So
    `The Kooks, Milky Chance` must reach a library song by `The Kooks`.

    YouTube names an auto-generated channel `<artist> - Topic`, and it returns that name as the
    artist. Sonos writes the artist alone. The suffix needs the spaces around the hyphen, so a name
    such as `Hard-Fi` or `blink-182` keeps its own hyphen.

    A name that itself holds a comma, such as `Emerson, Lake & Palmer`, splits wrongly here. The
    tiers try the whole string before this form, so such a name still pairs on the earlier tier.
    """
    first = artist.split(",", 1)[0].strip()
    return first.removesuffix(_CHANNEL_SUFFIX).strip()


def _index(songs: Iterable[Song], fields: Sequence[str]) -> dict[tuple[str, ...], list[str]]:
    """Build a lookup from the named `Song` fields to the video IDs that share those values."""
    found: dict[tuple[str, ...], list[str]] = {}
    for song in songs:
        key = tuple(comparison_key(typing.cast("str", getattr(song, field))) for field in fields)
        found.setdefault(key, []).append(song.video_id)
    return found


def _only_free(candidates: Sequence[str], taken: set[str]) -> str | None:
    """Return the one candidate still free, or None when zero or several are free.

    Several free candidates means the evidence does not separate them. `match` then leaves the entry
    unplaced rather than choosing one.
    """
    free = [video_id for video_id in candidates if video_id not in taken]
    return free[0] if len(free) == 1 else None


def _titles_agree(left: str, right: str) -> bool:
    """Report whether two titles name one recording.

    One title inside the other covers a suffix that Sonos carries and the library does not, such
    as `A Song` against `A Song (Bonus Track)`. A high `difflib` ratio covers a spelling difference,
    such as an accent or a bracket.
    """
    short, long = sorted((comparison_key(left), comparison_key(right)), key=len)
    if not short:
        return False
    if len(short) >= _MIN_CONTAINED_TITLE and short in long:
        return True
    return difflib.SequenceMatcher(None, short, long).ratio() >= _TITLE_SIMILARITY


def _by_duration(entry: QueueEntry, songs: Sequence[Song], taken: set[str]) -> str | None:
    """Return the video ID whose artist, length, and title all agree with `entry`, or None.

    This is the last tier. It runs only for an entry that names an artist, because a length alone
    fits hundreds of songs. The title must still agree, because one artist releases many songs of
    one length. Two songs that agree equally leave the entry unplaced.
    """
    artist = comparison_key(primary_artist(entry.artist))
    if not artist or entry.duration_seconds <= 0:
        return None

    fits = [
        song
        for song in songs
        if song.video_id not in taken
        and comparison_key(primary_artist(song.artist)) == artist
        and abs(song.duration_seconds - entry.duration_seconds) <= _DURATION_TOLERANCE_SECONDS
        and _titles_agree(song.title, entry.title)
    ]
    return fits[0].video_id if len(fits) == 1 else None


def _lookups(candidates: Sequence[Song]) -> tuple[dict[tuple[str, ...], list[str]], ...]:
    """Build the indexes the tiers read, in tier order.

    Sonos writes every artist of a song, and `refresh` stores the first one. So an index keys on the
    whole artist string, and a second index keys on the first artist alone.
    """
    primary_album: dict[tuple[str, ...], list[str]] = {}
    primary_title: dict[tuple[str, ...], list[str]] = {}
    for song in candidates:
        artist = comparison_key(primary_artist(song.artist))
        primary_album.setdefault((comparison_key(song.title), artist, comparison_key(song.album)), []).append(song.video_id)
        primary_title.setdefault((comparison_key(song.title), artist), []).append(song.video_id)

    return (
        _index(candidates, ("title", "artist", "album")),
        primary_album,
        _index(candidates, ("title", "artist")),
        primary_title,
    )


def _keys_for(entry: QueueEntry, lookups: Sequence[dict[tuple[str, ...], list[str]]]) -> tuple[list[str], ...]:
    """Return the candidate list each tier gives for `entry`, in tier order."""
    title, album = comparison_key(entry.title), comparison_key(entry.album)
    whole, first = comparison_key(entry.artist), comparison_key(primary_artist(entry.artist))
    return (
        lookups[0].get((title, whole, album), []),
        lookups[1].get((title, first, album), []),
        lookups[2].get((title, whole), []),
        lookups[3].get((title, first), []),
    )


def match(entries: Sequence[QueueEntry], candidates: Sequence[Song]) -> MatchResult:
    """Pair each queue entry with one candidate song. This function is pure and reaches nothing.

    `candidates` must be the songs of the one playlist the owner queued. A wider pool brings back
    the collisions that the per-playlist pool removes.

    Runs these tiers in order, and stops at the first that names exactly one free candidate:

    1. title, artist, and album.
    2. title, artist, and album, with the first artist of a multi-artist entry.
    3. title and artist, then title and first artist.
    4. first artist, a length within `_DURATION_TOLERANCE_SECONDS`, and a title that agrees.

    Leaves an entry unplaced when no tier names exactly one free candidate. One song never pairs to
    two entries.
    """
    lookups = _lookups(candidates)

    taken: set[str] = set()
    pairs: list[tuple[str, str]] = []
    unplaced: list[QueueEntry] = []
    for entry in entries:
        found = (_only_free(ids, taken) for ids in _keys_for(entry, lookups))
        video_id = next((video for video in found if video), None) or _by_duration(entry, candidates, taken)

        if video_id is None:
            unplaced.append(entry)
            continue
        taken.add(video_id)
        pairs.append((entry.track_id, video_id))

    return MatchResult(pairs=tuple(pairs), unplaced=tuple(unplaced))


class Speaker(typing.Protocol):
    """The `soco.SoCo` surface this module calls. A test fake implements only this."""

    def get_queue(self, start: int = ..., max_items: int = ...) -> Sequence[object]:
        """Return one page of the queue, as `soco.SoCo.get_queue` does."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs


def _seconds(duration: object) -> int:
    """Turn the Sonos `H:MM:SS` duration into whole seconds. Anything else gives 0."""
    if not isinstance(duration, str) or not duration:
        return 0
    total = 0
    for part in duration.split(":"):
        if not part.isdigit():
            return 0
        total = total * 60 + int(part)
    return total


def read_queue(speaker: Speaker, *, page: int = _QUEUE_PAGE) -> tuple[QueueEntry, ...]:
    """Read the whole Sonos queue, in order, one page at a time.

    The track ID is the part of the resource URI between the scheme and the query. That value is
    what `sonos_uri` sends back to the speaker.
    """
    entries: list[QueueEntry] = []
    start = 0
    while True:
        items = speaker.get_queue(start, page)
        if not items:
            break
        for item in items:
            resources = typing.cast("Sequence[object]", getattr(item, "resources", ()))
            resource = resources[0] if resources else None
            uri = typing.cast("str", getattr(resource, "uri", "") or "")
            entries.append(
                QueueEntry(
                    track_id=uri.split(":", 1)[-1].split("?", 1)[0],
                    uri=uri,
                    title=str(getattr(item, "title", "") or ""),
                    artist=str(getattr(item, "creator", "") or ""),
                    album=str(getattr(item, "album", "") or ""),
                    duration_seconds=_seconds(getattr(resource, "duration", None)),
                )
            )
        start += len(items)
    return tuple(entries)


def _titled_pool(conn: sqlite3.Connection, video_ids: Sequence[str], playlist_title: str) -> list[Song]:
    """Return the candidate songs, once enough of them carry a title to match against.

    Raises `HarvestError` naming `refresh` when they do not. `bootstrap` seeds a song with a video
    ID and nothing else, and a match against those pairs nothing. Without this the run reports
    "0 paired" and reads like a Sonos fault.
    """
    candidates = songs_by_video_id(conn, set(video_ids))
    titled = sum(1 for song in candidates if song.title)
    if candidates and titled < len(candidates) * _MIN_TITLED_SHARE:
        message = (
            f"only {titled} of the {len(candidates)} songs in {playlist_title} carry a title, so no match can run. "
            "Run `library-radio refresh` first, which reads the title, artist, album, and length of every song"
        )
        raise HarvestError(message)
    return candidates


def harvest(
    conn: sqlite3.Connection,
    entries: Sequence[QueueEntry],
    playlist_title: str,
    *,
    tolerance: int = 25,
) -> MatchResult:
    """Pair the queue against one playlist's songs, and store what it decides. Return the result.

    Records every entry in `sonos_tracks` first, matched or not. That table is the evidence a queue
    read produced, and it stays whatever the match makes of it. A better matcher can run again on
    the same evidence without a second queue read.

    Raises `HarvestError` when the store knows no playlist by that title, or when the queue length
    and the playlist length differ by more than `tolerance`. A large difference means the queue
    holds something other than that playlist, and matching against the wrong pool writes wrong
    pairs.
    """
    membership = next((item for item in stored_playlists(conn) if item.title == playlist_title), None)
    if membership is None:
        message = f"no playlist named {playlist_title!r} is in the catalogue. Run `library-radio playlists` first"
        raise HarvestError(message)

    gap = abs(len(entries) - len(membership.video_ids))
    if gap > tolerance:
        message = (
            f"the queue holds {len(entries)} entries and {playlist_title} holds {len(membership.video_ids)} songs. "
            f"That gap of {gap} is above the tolerance of {tolerance}, so the queue holds something else"
        )
        raise HarvestError(message)

    candidates = _titled_pool(conn, membership.video_ids, playlist_title)

    now = datetime.now(UTC).isoformat()
    for entry in entries:
        record_sonos_track(conn, entry, source=playlist_title, seen=now)

    result = match(entries, candidates)

    by_track = {entry.track_id: entry for entry in entries}
    paired = 0
    for track_id, video_id in result.pairs:
        if pair_sonos_track(conn, video_id, track_id=track_id, uri=by_track[track_id].uri):
            paired += 1

    _logger.info(
        "harvest %s: %d queue entries, %d paired, %d unplaced",
        playlist_title,
        len(entries),
        paired,
        len(result.unplaced),
    )
    for entry in result.unplaced:
        _logger.info("  unplaced: %s - %s", entry.artist, entry.title)
    return result
