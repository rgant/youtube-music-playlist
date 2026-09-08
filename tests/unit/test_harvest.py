"""Tests for youtube_music_library_radio.harvest.

`match` takes no speaker and no database. Every test here is a pure call. The logic that decides
which Sonos track a song plays as therefore runs with no double of any kind.

The measurement that shaped these tiers is in `docs/plans/2026-08-16-library-database.md`.
"""

import typing

import pytest

from testdoubles import queue_entry
from youtube_music_library_radio.catalogue import PlaylistMembership, Song, merge_songs, record_playlists, songs_by_video_id
from youtube_music_library_radio.harvest import HarvestError, harvest, match, read_queue

if typing.TYPE_CHECKING:
    import sqlite3


_entry = queue_entry


def _song(video_id: str, title: str, artist: str, album: str = "An Album", seconds: int = 200) -> Song:
    """Build one library song."""
    return Song(video_id=video_id, title=title, artist=artist, album=album, duration_seconds=seconds)


def test_title_artist_and_album_pair_a_song() -> None:
    """The first tier. It placed 89% of one real playlist with no collision."""
    result = match([_entry("S1", "A Song", "A Band")], [_song("V1", "A Song", "A Band")])

    assert result.pairs == (("S1", "V1"),)
    assert not result.unplaced


def test_the_comparison_ignores_case_and_punctuation() -> None:
    """Sonos and YouTube spell a title differently often enough that an exact compare loses songs."""
    result = match([_entry("S1", "A Song!", "The Band")], [_song("V1", "a song", "the band")])

    assert result.pairs == (("S1", "V1"),)


def test_a_different_album_falls_to_the_title_and_artist_tier() -> None:
    """Sonos names a compilation and YouTube names the original often enough to matter."""
    result = match(
        [_entry("S1", "A Song", "A Band", album="Greatest Hits")],
        [_song("V1", "A Song", "A Band", album="The First Record")],
    )

    assert result.pairs == (("S1", "V1"),)


def test_the_album_separates_two_recordings_of_one_song() -> None:
    """A re-recording shares its title and artist. The album is what tells the two apart."""
    entries = [
        _entry("S_LIVE", "A Song", "A Band", album="Live In Berlin"),
        _entry("S_STUDIO", "A Song", "A Band", album="The First Record"),
    ]
    songs = [
        _song("V_STUDIO", "A Song", "A Band", album="The First Record"),
        _song("V_LIVE", "A Song", "A Band", album="Live In Berlin"),
    ]

    result = match(entries, songs)

    assert dict(result.pairs) == {"S_LIVE": "V_LIVE", "S_STUDIO": "V_STUDIO"}


def test_a_multi_artist_entry_matches_its_first_artist() -> None:
    """Sonos writes every artist. The library read gives the first one alone."""
    result = match([_entry("S1", "A Song", "The Kooks, Milky Chance")], [_song("V1", "A Song", "The Kooks")])

    assert result.pairs == (("S1", "V1"),)


def test_a_duration_places_an_entry_the_title_tiers_cannot() -> None:
    """A title spelled differently still pairs when the artist and the length agree."""
    result = match(
        [_entry("S1", "A Song (Bonus Track)", "A Band", album="Other", seconds=213)],
        [_song("V1", "A Song", "A Band", album="First", seconds=214)],
    )

    assert result.pairs == (("S1", "V1"),)


def test_two_identical_candidates_leave_the_entry_unplaced() -> None:
    """A guess writes the wrong Sonos track onto a song. An unplaced entry costs a report."""
    songs = [_song("V1", "A Song", "A Band"), _song("V2", "A Song", "A Band")]

    result = match([_entry("S1", "A Song", "A Band")], songs)

    assert not result.pairs
    assert [entry.track_id for entry in result.unplaced] == ["S1"]


def test_one_song_is_never_paired_to_two_entries() -> None:
    """Two Sonos tracks that look alike must not both claim one library row."""
    entries = [_entry("S1", "A Song", "A Band"), _entry("S2", "A Song", "A Band")]

    result = match(entries, [_song("V1", "A Song", "A Band")])

    assert len(result.pairs) == 1
    assert len(result.unplaced) == 1


def test_an_entry_with_no_candidate_is_reported() -> None:
    """A queue entry for a song the library dropped has no pair, and that is not a failure."""
    result = match([_entry("S1", "Gone", "A Band")], [_song("V1", "Another Song", "A Band")])

    assert not result.pairs
    assert [entry.title for entry in result.unplaced] == ["Gone"]


def test_a_far_duration_does_not_pair() -> None:
    """A different length means a different recording, so the last tier must refuse it."""
    result = match(
        [_entry("S1", "Something Else", "A Band", album="Other", seconds=100)],
        [_song("V1", "A Song", "A Band", album="First", seconds=300)],
    )

    assert not result.pairs


def test_an_empty_queue_pairs_nothing() -> None:
    """A harvest against an empty queue is a no-op, not an error."""
    result = match([], [_song("V1", "A Song", "A Band")])

    assert not result.pairs
    assert not result.unplaced


@pytest.mark.parametrize("artist", ["", "  "])
def test_an_entry_with_no_artist_never_pairs_on_duration_alone(artist: str) -> None:
    """Duration alone is far too weak. Without an artist the last tier must not run."""
    result = match(
        [_entry("S1", "Unknown Title", artist, album="Other", seconds=200)],
        [_song("V1", "A Song", "", album="First", seconds=200)],
    )

    assert not result.pairs


class _FakeSpeaker:
    """A `soco.SoCo` double whose queue returns fixed pages."""

    def __init__(self, entries: list[tuple[str, str, str, str, str]]) -> None:
        self.entries: list[tuple[str, str, str, str, str]] = entries

    def get_queue(self, start: int = 0, max_items: int = 100) -> list[object]:
        """Return one page of fake DIDL items."""
        return [_FakeItem(*row) for row in self.entries[start : start + max_items]]


class _FakeResource:
    """A `soco.data_structures.DidlResource` double."""

    def __init__(self, uri: str, duration: str) -> None:
        self.uri: str = uri
        self.duration: str = duration


class _FakeItem:
    """A `soco.data_structures.DidlItem` double."""

    def __init__(self, uri: str, title: str, creator: str, album: str, duration: str) -> None:
        self.resources: list[_FakeResource] = [_FakeResource(uri, duration)]
        self.title: str = title
        self.creator: str = creator
        self.album: str = album


def _row(track_id: str, title: str, artist: str, album: str = "An Album", duration: str = "0:03:20") -> tuple[str, str, str, str, str]:
    """Build the tuple `_FakeSpeaker` turns into a DIDL item."""
    return (f"x-sonosapi-hls-static:{track_id}?sid=284&flags=0", title, artist, album, duration)


def test_read_queue_parses_every_field() -> None:
    """The track ID sits inside the URI, and the duration arrives as `H:MM:SS`."""
    speaker = _FakeSpeaker([_row("S1", "A Song", "A Band", "An Album", "0:04:46")])

    entries = read_queue(speaker)

    assert entries[0].track_id == "S1"
    assert entries[0].uri == "x-sonosapi-hls-static:S1?sid=284&flags=0"
    assert (entries[0].title, entries[0].artist, entries[0].album) == ("A Song", "A Band", "An Album")
    assert entries[0].duration_seconds == 286


def test_read_queue_pages_through_a_long_queue() -> None:
    """A real queue holds hundreds of entries, and `get_queue` returns one page at a time."""
    speaker = _FakeSpeaker([_row(f"S{index}", f"Song {index}", "A Band") for index in range(250)])

    entries = read_queue(speaker, page=100)

    assert len(entries) == 250
    assert entries[-1].track_id == "S249"


def test_harvest_writes_the_pairing_onto_the_songs(conn: sqlite3.Connection) -> None:
    """The pairing is the point. A song without it cannot reach the Sonos queue."""
    _seed_playlist(conn, [Song(video_id="V1", title="A Song", artist="A Band", album="An Album", duration_seconds=200)])
    speaker = _FakeSpeaker([_row("S1", "A Song", "A Band")])

    result = harvest(conn, read_queue(speaker), "Everything 1")

    assert result.pairs == (("S1", "V1"),)
    stored = songs_by_video_id(conn, {"V1"})[0]
    assert stored.sonos_track_id == "S1"
    assert stored.sonos_uri == "x-sonosapi-hls-static:S1?sid=284&flags=0"


def test_harvest_records_every_entry_as_evidence(conn: sqlite3.Connection) -> None:
    """`sonos_tracks` keeps what the read saw, so a better matcher runs again with no second read."""
    _seed_playlist(conn, [Song(video_id="V1", title="A Song", artist="A Band", album="An Album", duration_seconds=200)])
    speaker = _FakeSpeaker([_row("S1", "A Song", "A Band"), _row("S_GONE", "Dropped Song", "Another Band")])

    result = harvest(conn, read_queue(speaker), "Everything 1")

    stored = typing.cast("list[sqlite3.Row]", conn.execute("SELECT track_id, source FROM sonos_tracks").fetchall())
    assert sorted(typing.cast("str", row[0]) for row in stored) == ["S1", "S_GONE"]
    assert all(typing.cast("str", row[1]) == "Everything 1" for row in stored)
    assert len(result.unplaced) == 1


def test_harvest_refuses_a_playlist_the_catalogue_does_not_know(conn: sqlite3.Connection) -> None:
    """Without the playlist there is no candidate pool, so a match runs against nothing."""
    with pytest.raises(HarvestError, match="Everything 99"):
        _ = harvest(conn, [], "Everything 99")


def test_harvest_refuses_a_queue_of_the_wrong_size(conn: sqlite3.Connection) -> None:
    """A queue far from the playlist length holds something else, and the wrong pool writes wrong pairs."""
    _seed_playlist(conn, [Song(video_id=f"V{index}", title=f"Song {index}", artist="A Band") for index in range(100)])
    speaker = _FakeSpeaker([_row("S1", "Song 1", "A Band")])

    with pytest.raises(HarvestError, match="tolerance"):
        _ = harvest(conn, read_queue(speaker), "Everything 1", tolerance=25)


def test_a_second_harvest_changes_nothing(conn: sqlite3.Connection) -> None:
    """A repeat run must be safe. The owner re-queues a playlist for other reasons."""
    _seed_playlist(conn, [Song(video_id="V1", title="A Song", artist="A Band", album="An Album", duration_seconds=200)])
    speaker = _FakeSpeaker([_row("S1", "A Song", "A Band")])

    first = harvest(conn, read_queue(speaker), "Everything 1")
    second = harvest(conn, read_queue(speaker), "Everything 1")

    assert first.pairs == second.pairs
    assert count_sonos_tracks(conn) == 1


def _seed_playlist(conn: sqlite3.Connection, songs: list[Song], title: str = "Everything 1") -> None:
    """Put `songs` in the catalogue and record them as the membership of one playlist."""
    _ = merge_songs(conn, songs)
    record_playlists(
        conn,
        [
            PlaylistMembership(
                playlist_id="PL1",
                title=title,
                ordinal=1,
                reported_count=len(songs),
                video_ids=tuple(song.video_id for song in songs),
            )
        ],
    )


def count_sonos_tracks(conn: sqlite3.Connection) -> int:
    """Return how many rows `sonos_tracks` holds."""
    row = typing.cast("sqlite3.Row", conn.execute("SELECT COUNT(*) FROM sonos_tracks").fetchone())
    return typing.cast("int", row[0])


def test_harvest_refuses_a_pool_with_no_titles(conn: sqlite3.Connection) -> None:
    """`bootstrap` seeds a video ID with no title. A match against those pairs nothing, silently.

    The catalogue must carry titles before a harvest can work, and `refresh` is what puts them
    there. Without this guard the run reports "0 paired" and looks like a Sonos fault.
    """
    seeded = [Song(video_id=f"V{index}", title="", artist="") for index in range(10)]
    _seed_playlist(conn, seeded)
    speaker = _FakeSpeaker([_row(f"S{index}", f"Song {index}", "A Band") for index in range(10)])

    with pytest.raises(HarvestError, match="refresh"):
        _ = harvest(conn, read_queue(speaker), "Everything 1")


def test_harvest_runs_when_most_of_the_pool_carries_a_title(conn: sqlite3.Connection) -> None:
    """A few untitled rows are normal. Only a pool that is mostly untitled stops the run."""
    songs = [Song(video_id=f"V{index}", title=f"Song {index}", artist="A Band", album="An Album") for index in range(9)]
    songs.append(Song(video_id="V9", title="", artist=""))
    _seed_playlist(conn, songs)
    speaker = _FakeSpeaker([_row(f"S{index}", f"Song {index}", "A Band") for index in range(9)])

    result = harvest(conn, read_queue(speaker), "Everything 1")

    assert len(result.pairs) == 9


def test_a_topic_channel_name_reaches_the_artist() -> None:
    """YouTube names an auto-generated channel `<artist> - Topic`, and Sonos writes the artist alone."""
    result = match([_entry("S1", "A Song", "A Band")], [_song("V1", "A Song", "A Band - Topic")])

    assert result.pairs == (("S1", "V1"),)


def test_a_hyphen_inside_a_name_survives() -> None:
    """`Hard-Fi` and `blink-182` carry a hyphen, and the suffix rule must not cut one."""
    result = match([_entry("S1", "A Song", "Hard-Fi")], [_song("V1", "A Song", "Hard-Fi")])

    assert result.pairs == (("S1", "V1"),)
