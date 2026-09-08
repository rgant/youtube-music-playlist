"""Pair a library song with a Sonos track the catalogue already observed.

`harvest` learns a pairing from one queue read, and it needs the owner to put one playlist in the
Sonos queue by hand. Every entry it reads lands in `sonos_tracks`, matched or not. That table
therefore holds evidence about songs that no harvest reaches yet.

This module spends that evidence. It compares title, artist, and album across the whole library,
which `harvest` refuses to do. One rule makes the wider pool safe: the key must name exactly one
song and exactly one free track. A key that fits two songs, or two tracks, pairs nothing.

Measured against the 1,752 pairs three real harvests made, the rule chose the same track 1,700 times
and a different one 6 times. All six name albums that Sonos re-keyed, so the song is right and the
handle is old. A stale handle fails when the speaker plays it.

Read `docs/plans/2026-08-16-library-database.md` for the per-playlist match this one supplements.
"""

import dataclasses
import logging
import typing

from youtube_music_library_radio.catalogue import free_sonos_tracks, pair_sonos_track, unpaired_songs
from youtube_music_library_radio.harvest import comparison_key, primary_artist

if typing.TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterable, Sequence

_logger = logging.getLogger(__name__)

# One pairing to write: the library song, the Sonos track, and the URI the queue builder sends.
type Pairing = tuple[str, str, str]


@dataclasses.dataclass(frozen=True)
class RematchPlan:
    """What one rematch run can write.

    `ambiguous` counts the keys that name a song and a track but not exactly one of each. The owner
    reads it as the work this rule cannot do, and a harvest of that playlist is what does it.
    """

    pairs: tuple[Pairing, ...]
    ambiguous: int


def _key_of(title: str, artist: str, album: str) -> tuple[str, str, str] | None:
    """Return the comparison key of one song or track, or None when it is too weak to use.

    An empty title or album collapses the key. Inside one playlist that risk is small, and across
    20,000 songs it is not.
    """
    keyed = (comparison_key(title), comparison_key(primary_artist(artist)), comparison_key(album))
    return None if not all(keyed) else keyed


def _grouped(items: Iterable[tuple[str, str, str, str]]) -> dict[tuple[str, str, str], list[str]]:
    """Group identifiers by the comparison key of their title, artist, and album."""
    found: dict[tuple[str, str, str], list[str]] = {}
    for identifier, title, artist, album in items:
        key = _key_of(title, artist, album)
        if key is not None:
            found.setdefault(key, []).append(identifier)
    return found


def plan_rematch(conn: sqlite3.Connection) -> RematchPlan:
    """Choose every pairing the stored evidence names without doubt. This function writes nothing.

    Reads the songs that carry no pairing, and the observed tracks no song claims. A key that names
    exactly one of each becomes a pair. Every other key counts toward `ambiguous`.
    """
    songs = unpaired_songs(conn)
    tracks = free_sonos_tracks(conn)
    by_song = _grouped((song.video_id, song.title, song.artist, song.album) for song in songs)
    by_track = _grouped((track.track_id, track.title, track.artist, track.album) for track in tracks)
    uris = {track.track_id: track.uri for track in tracks}

    pairs: list[Pairing] = []
    ambiguous = 0
    for key, video_ids in by_song.items():
        track_ids = by_track.get(key)
        if not track_ids:
            continue
        if len(video_ids) == 1 and len(track_ids) == 1:
            pairs.append((video_ids[0], track_ids[0], uris[track_ids[0]]))
        else:
            ambiguous += 1

    _logger.info("%d songs pair from stored evidence, and %d keys stay ambiguous", len(pairs), ambiguous)
    return RematchPlan(pairs=tuple(pairs), ambiguous=ambiguous)


def apply_rematch(conn: sqlite3.Connection, pairs: Sequence[Pairing]) -> int:
    """Write every pairing. Return the rows changed.

    A song with no `sonos_uri` never reaches the Sonos queue, so a plan that never lands is no gain.
    """
    written = sum(pair_sonos_track(conn, video_id, track_id=track_id, uri=uri) for video_id, track_id, uri in pairs)
    conn.commit()
    _logger.info("wrote %d pairings", written)
    return written
