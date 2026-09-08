"""Tests for youtube_music_library_radio.rematch.

`rematch` pairs a library song with a Sonos track the catalogue already observed. It reads the whole
library, not one playlist, so every test here proves that it refuses an ambiguous key.

No test reaches a speaker or the network.
"""

import sqlite3  # noqa: TC003 -- typing.cast reads this name at run time
import typing

from testdoubles import queue_entry
from youtube_music_library_radio.catalogue import QueueEntry, Song, merge_songs, pair_sonos_track, record_sonos_track
from youtube_music_library_radio.rematch import apply_rematch, plan_rematch


def _song(video_id: str, title: str, artist: str, album: str = "An Album") -> Song:
    """Build one library song with no Sonos pairing."""
    return Song(video_id=video_id, title=title, artist=artist, album=album, duration_seconds=200)


_track = queue_entry


def _store(conn: sqlite3.Connection, songs: list[Song], tracks: list[QueueEntry]) -> None:
    """Put songs and observed tracks in the catalogue."""
    _ = merge_songs(conn, songs)
    for track in tracks:
        record_sonos_track(conn, track, source="test", seen="2026-09-07T00:00:00+00:00")


def test_a_unique_key_on_both_sides_pairs(conn: sqlite3.Connection) -> None:
    """The point of the module: the queue read that saw this track never needs to happen again."""
    _store(conn, [_song("V1", "A Song", "A Band")], [_track("S1", "A Song", "A Band")])

    plan = plan_rematch(conn)

    assert plan.pairs == ((("V1"), "S1", "x-sonosapi-hls-static:S1?sid=284"),)


def test_two_library_songs_that_share_a_key_pair_nothing(conn: sqlite3.Connection) -> None:
    """A wrong pair writes the wrong Sonos track onto a song, and nothing later detects it."""
    songs = [_song("V1", "A Song", "A Band"), _song("V2", "A Song", "A Band")]
    _store(conn, songs, [_track("S1", "A Song", "A Band")])

    plan = plan_rematch(conn)

    assert not plan.pairs
    assert plan.ambiguous == 1


def test_two_tracks_that_share_a_key_pair_nothing(conn: sqlite3.Connection) -> None:
    """Two observed tracks fit equally well, so the evidence does not separate them."""
    tracks = [_track("S1", "A Song", "A Band"), _track("S2", "A Song", "A Band")]
    _store(conn, [_song("V1", "A Song", "A Band")], tracks)

    plan = plan_rematch(conn)

    assert not plan.pairs
    assert plan.ambiguous == 1


def test_a_song_that_already_carries_a_pairing_is_left_alone(conn: sqlite3.Connection) -> None:
    """A harvest saw that song against one playlist, which is the stronger evidence."""
    _store(conn, [_song("V1", "A Song", "A Band")], [_track("S1", "A Song", "A Band")])
    _ = pair_sonos_track(conn, "V1", track_id="S9", uri="x-sonosapi-hls-static:S9?sid=284")

    plan = plan_rematch(conn)

    assert not plan.pairs


def test_a_track_another_song_already_uses_pairs_nothing(conn: sqlite3.Connection) -> None:
    """One Sonos track never plays for two library songs."""
    _store(conn, [_song("V1", "A Song", "A Band"), _song("V2", "Other", "A Band")], [_track("S1", "Other", "A Band")])
    _ = pair_sonos_track(conn, "V1", track_id="S1", uri="x-sonosapi-hls-static:S1?sid=284")

    plan = plan_rematch(conn)

    assert not plan.pairs


def test_an_empty_album_never_pairs(conn: sqlite3.Connection) -> None:
    """An empty album collapses the key, and the library is too wide a pool for that risk."""
    _store(conn, [_song("V1", "A Song", "A Band", album="")], [_track("S1", "A Song", "A Band", album="")])

    plan = plan_rematch(conn)

    assert not plan.pairs


def test_the_comparison_ignores_case_and_punctuation(conn: sqlite3.Connection) -> None:
    """Sonos and YouTube punctuate a title differently, as they do for `harvest`."""
    _store(conn, [_song("V1", "A Song!", "The Band")], [_track("S1", "a song", "the band")])

    plan = plan_rematch(conn)

    assert [pair[0] for pair in plan.pairs] == ["V1"]


def test_the_first_artist_of_a_sonos_track_reaches_the_library_song(conn: sqlite3.Connection) -> None:
    """Sonos writes every artist of a song, and `refresh` stores the first one alone."""
    _store(conn, [_song("V1", "A Song", "The Kooks")], [_track("S1", "A Song", "The Kooks, Milky Chance")])

    plan = plan_rematch(conn)

    assert [pair[0] for pair in plan.pairs] == ["V1"]


def test_apply_writes_the_pairing(conn: sqlite3.Connection) -> None:
    """The queue builder sends `sonos_uri`, so a plan that never lands changes nothing."""
    _store(conn, [_song("V1", "A Song", "A Band")], [_track("S1", "A Song", "A Band")])
    plan = plan_rematch(conn)

    written = apply_rematch(conn, plan.pairs)

    assert written == 1
    row = typing.cast("sqlite3.Row", conn.execute("SELECT sonos_track_id, sonos_uri FROM songs WHERE video_id = 'V1'").fetchone())
    assert (row["sonos_track_id"], row["sonos_uri"]) == ("S1", "x-sonosapi-hls-static:S1?sid=284")
