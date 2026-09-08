"""Tests for youtube_music_library_radio.catalogue.

Every test runs against a real temporary SQLite database, never against a double. The `conn` fixture
in `conftest.py` opens that database and closes it after the test passes or fails.
"""

import contextlib
import sqlite3
import typing
from pathlib import Path

import pytest

from youtube_music_library_radio.catalogue import (
    PlaylistMembership,
    Song,
    count_songs,
    delete_songs,
    duplicated_video_ids,
    mark_absent,
    mark_present,
    merge_songs,
    open_catalogue,
    pair_sonos_track,
    record_playlists,
    songs_by_video_id,
    stored_playlists,
)


def _playlist(playlist_id: str, title: str, ordinal: int, video_ids: list[str]) -> PlaylistMembership:
    """Build a `PlaylistMembership` for a test. `reported_count` matches the members given."""
    return PlaylistMembership(
        playlist_id=playlist_id,
        title=title,
        ordinal=ordinal,
        reported_count=len(video_ids),
        video_ids=tuple(video_ids),
    )


def _members(conn: sqlite3.Connection, playlist_id: str) -> list[str]:
    """Return the stored video IDs of one playlist, in position order."""
    cursor = conn.execute("SELECT video_id FROM playlist_songs WHERE playlist_id = ? ORDER BY position", (playlist_id,))
    rows = typing.cast("list[sqlite3.Row]", cursor.fetchall())
    return [typing.cast("str", row[0]) for row in rows]


def _stored(conn: sqlite3.Connection) -> list[Song]:
    """Read every stored song back, through the accessor `harvest` uses."""
    rows = typing.cast("list[sqlite3.Row]", conn.execute("SELECT video_id FROM songs").fetchall())
    ids = {typing.cast("str", row["video_id"]) for row in rows}
    return songs_by_video_id(conn, ids)


def _song(video_id: str, *, title: str = "Title", artist: str = "Artist") -> Song:
    """Build a `Song` with sensible defaults for the fields a test does not care about."""
    return Song(video_id=video_id, title=title, artist=artist)


def test_open_creates_the_table_and_sets_wal(tmp_path: Path) -> None:
    """Every command but `auth` opens the catalogue first.

    On a new machine the directory, the file, and the tables are absent, so the first run builds them. WAL mode
    carries the concurrent access of a player and of `refresh`.
    """
    db_path = tmp_path / "nested" / "catalogue.sqlite3"

    with contextlib.closing(open_catalogue(db_path)) as conn:
        assert db_path.exists()
        mode = typing.cast("str", conn.execute("PRAGMA journal_mode").fetchone()[0])
        assert mode == "wal"
        version = typing.cast("int", conn.execute("PRAGMA user_version").fetchone()[0])
        assert version == 1
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'songs'").fetchall()
        assert len(tables) == 1


def test_open_refuses_a_python_that_cannot_share_a_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A `sqlite3` below threadsafety 3 raises, instead of sharing a connection unsafely.

    `open_catalogue` passes `check_same_thread=False` so a player can read and write from every
    thread it runs. That removes Python's barrier, and only SQLite's serialized mode replaces it.
    A build below level 3 gives silent data corruption, so the fault must stop the service instead.
    """
    monkeypatch.setattr(sqlite3, "threadsafety", 0)

    with pytest.raises(RuntimeError, match="threadsafety"):
        _ = open_catalogue(tmp_path / "catalogue.sqlite3")


def test_open_does_not_downgrade_an_existing_user_version(tmp_path: Path) -> None:
    """`user_version` is the one mark of which schema a database carries.

    Nothing reads it yet, so a stamp back to 1 loses the mark and nothing reports the loss.
    """
    db_path = tmp_path / "catalogue.sqlite3"
    with contextlib.closing(open_catalogue(db_path)) as conn:
        _ = conn.execute("PRAGMA user_version = 2")

    with contextlib.closing(open_catalogue(db_path)) as reopened:
        version = typing.cast("int", reopened.execute("PRAGMA user_version").fetchone()[0])
        assert version == 2


def test_open_raises_when_the_backend_cannot_use_wal() -> None:
    """`open_catalogue` raises `RuntimeError` instead of running silently in a non-WAL journal mode.

    An in-memory database is a real, unmocked way to trigger this: SQLite reports `journal_mode`
    `memory` for `:memory:`, never `wal`, no matter what is requested.
    """
    with pytest.raises(RuntimeError, match="WAL"):
        _ = open_catalogue(Path(":memory:"))


def test_merge_adds_new_songs_and_reports_the_count(conn: sqlite3.Connection) -> None:
    """The return value is what `refresh` and `bootstrap` report to the owner.

    That number is what the owner reads as the size of the run.
    """
    added = merge_songs(conn, [_song("a"), _song("b")])

    assert added == 2
    assert count_songs(conn) == 2


def test_merge_with_no_songs_adds_nothing(conn: sqlite3.Connection) -> None:
    """An empty batch must change nothing.

    The account can hold no `Everything` playlist, and `bootstrap` then merges an empty list.
    """
    added = merge_songs(conn, [])

    assert added == 0
    assert count_songs(conn) == 0


def test_merge_tolerates_a_duplicate_video_id_within_one_batch(conn: sqlite3.Connection) -> None:
    """A video ID in two playlists is the defect this project measures.

    `bootstrap` merges every playlist together and removes no repeat, so one batch carries the same ID twice. A crash
    part way through leaves a half-built catalogue, because the connection runs with `autocommit=True`.
    """
    added = merge_songs(conn, [_song("a"), _song("a"), _song("b")])

    assert added == 2
    assert count_songs(conn) == 2


def test_merge_fills_an_empty_title_from_the_incoming_row(conn: sqlite3.Connection) -> None:
    """`bootstrap` seeds a row with an empty title and an empty artist.

    The library read is what puts a real name on the row, and `harvest` matches the Sonos queue on that name.
    """
    _ = merge_songs(conn, [_song("a", title="", artist="")])

    _ = merge_songs(conn, [_song("a", title="Real Title", artist="Real Artist")])

    song = _stored(conn)[0]
    assert song.title == "Real Title"
    assert song.artist == "Real Artist"


def test_merge_keeps_an_existing_title_when_the_incoming_one_is_empty(conn: sqlite3.Connection) -> None:
    """`refresh` degrades a malformed title or artist to an empty value.

    The library is the authority on the title and the artist. One degraded read must never erase a name `harvest`
    matches on.
    """
    _ = merge_songs(conn, [_song("a", title="Good Title", artist="Good Artist")])

    _ = merge_songs(conn, [_song("a", title="", artist="")])

    song = _stored(conn)[0]
    assert song.title == "Good Title"
    assert song.artist == "Good Artist"


def test_delete_removes_only_the_named_rows_and_reports_the_count(conn: sqlite3.Connection) -> None:
    """`refresh` deletes every excluded song by ID.

    A delete that reaches past those IDs takes out the catalogue, and the catalogue holds no history to restore from.
    """
    _ = merge_songs(conn, [_song("a"), _song("b"), _song("c")])

    deleted = delete_songs(conn, ["a", "c"])

    assert deleted == 2
    assert [song.video_id for song in _stored(conn)] == ["b"]


def test_delete_ignores_a_video_id_absent_from_the_catalogue(conn: sqlite3.Connection) -> None:
    """`delete_songs` counts only the rows it deleted, and an absent `video_id` neither raises nor counts.

    `refresh` passes every excluded song, and the catalogue holds no row for a song a run excluded
    before this one.
    """
    _ = merge_songs(conn, [_song("a")])

    deleted = delete_songs(conn, ["a", "never-stored"])

    assert deleted == 1
    assert count_songs(conn) == 0


def test_delete_counts_a_repeated_video_id_one_time(conn: sqlite3.Connection) -> None:
    """`refresh` reports this number to the owner as the rows it deleted.

    A count of the IDs given, rather than of the rows deleted, overstates the change to the catalogue.
    """
    _ = merge_songs(conn, [_song("a"), _song("b")])

    deleted = delete_songs(conn, ["a", "a"])

    assert deleted == 1
    assert [song.video_id for song in _stored(conn)] == ["b"]


def test_delete_with_no_video_ids_leaves_every_row_in_place(conn: sqlite3.Connection) -> None:
    """`delete_songs` deletes no row and returns 0 when the ID list is empty.

    An empty list is the normal case: a refresh that excludes nothing passes one. "Delete no row"
    must never widen into "delete every row", so this seeds three rows and names all three
    survivors. A rewrite that builds the `WHERE` clause only for a non-empty list runs
    `DELETE FROM songs`, and this test fails on that rewrite.
    """
    _ = merge_songs(conn, [_song("a"), _song("b"), _song("c")])

    deleted = delete_songs(conn, [])

    assert deleted == 0
    assert {song.video_id for song in _stored(conn)} == {"a", "b", "c"}


def test_delete_accepts_more_ids_than_sqlite_allows_host_parameters(conn: sqlite3.Connection) -> None:
    """`delete_songs` deletes a list longer than the SQLite cap of 32,766 host parameters per statement.

    A `WHERE video_id IN (...)` form binds one host parameter per ID in one statement, and raises
    `sqlite3.OperationalError` above that cap. The owner's catalogue holds more than 15,000 rows,
    so a list that long is realistic.
    """
    _ = merge_songs(conn, [_song("keep"), _song("v00039999")])
    video_ids = [f"v{index:08d}" for index in range(40_000)]

    deleted = delete_songs(conn, video_ids)

    assert deleted == 1  # "v00039999" is the one ID of the 40,000 that the catalogue holds
    assert [song.video_id for song in _stored(conn)] == ["keep"]


def test_open_creates_the_playlist_tables(tmp_path: Path) -> None:
    """`playlists` and `harvest` read these tables on every run.

    A catalogue from an earlier schema must gain them on open, because no other step creates a table.
    """
    with contextlib.closing(open_catalogue(tmp_path / "catalogue.sqlite3")) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('playlists', 'playlist_songs')")
        rows = typing.cast("list[sqlite3.Row]", cursor.fetchall())

    assert sorted(typing.cast("str", row[0]) for row in rows) == ["playlist_songs", "playlists"]


def test_record_playlists_stores_the_membership(conn: sqlite3.Connection) -> None:
    """`require_complete` compares the stored member rows against the count each playlist claims.

    A member row that never lands makes the playlist look short, and every `playlists` run then refuses to write.
    """
    record_playlists(conn, [_playlist("PL1", "Everything 1", 1, ["a", "b"])])

    cursor = conn.execute("SELECT video_id, position FROM playlist_songs WHERE playlist_id = 'PL1' ORDER BY position")
    stored = typing.cast("list[sqlite3.Row]", cursor.fetchall())

    assert [(typing.cast("str", row[0]), typing.cast("int", row[1])) for row in stored] == [("a", 0), ("b", 1)]


def test_record_playlists_accumulates_rather_than_replaces(conn: sqlite3.Connection) -> None:
    """A short read must lose nothing. A later call adds what it saw and drops no earlier row.

    `ytmusicapi` returns fewer songs than a playlist claims often enough to matter. A replace makes
    the stored membership as short as the worst read. A short membership then makes the generator
    write songs the playlist already holds.
    """
    record_playlists(conn, [_playlist("PL1", "Everything 1", 1, ["a", "b"])])

    record_playlists(conn, [_playlist("PL1", "Everything 1", 1, ["b", "c"])])

    assert _members(conn, "PL1") == ["a", "b", "c"]


def test_record_playlists_updates_the_reported_count(conn: sqlite3.Connection) -> None:
    """The count a playlist claims is authoritative even when the item fetch is short."""
    record_playlists(conn, [_playlist("PL1", "Everything 1", 1, ["a"])])

    record_playlists(conn, [PlaylistMembership("PL1", "Everything 1", 1, 500, ("a",))])

    stored = stored_playlists(conn)
    assert [item.reported_count for item in stored] == [500]


def test_stored_playlists_returns_the_accumulated_membership(conn: sqlite3.Connection) -> None:
    """`stored_playlists` is the union every run builds on, in ordinal order."""
    record_playlists(conn, [_playlist("PL2", "Everything 2", 2, ["c"]), _playlist("PL1", "Everything 1", 1, ["a"])])

    stored = stored_playlists(conn)

    assert [(item.title, item.video_ids) for item in stored] == [("Everything 1", ("a",)), ("Everything 2", ("c",))]


def test_stored_playlists_is_empty_before_any_read(conn: sqlite3.Connection) -> None:
    """A catalogue that never read a playlist reports none, and that is not a failure."""
    assert not stored_playlists(conn)


def test_duplicated_video_ids_finds_a_song_in_two_playlists(conn: sqlite3.Connection) -> None:
    """`playlists` warns the owner about each song that sits in more than one playlist.

    A song in one playlist must stay out of that warning, or the warning fires on every run and means nothing.
    """
    record_playlists(conn, [_playlist("PL1", "Everything 1", 1, ["dup", "solo"]), _playlist("PL2", "Everything 2", 2, ["dup"])])

    found = duplicated_video_ids(conn)

    assert found == {"dup": ["Everything 1", "Everything 2"]}


_NEW_COLUMNS = (
    "album",
    "duration_seconds",
    "sonos_track_id",
    "sonos_uri",
    "first_seen",
    "last_seen",
    "missing_count",
    "last_queued",
)


def test_open_adds_the_library_columns_to_an_older_database(tmp_path: Path) -> None:
    """A catalogue written before these columns must open and keep every row.

    The owner's catalogue holds thousands of rows and a play history no read can rebuild. A schema
    change that needs a fresh database throws that away.
    """
    db_path = tmp_path / "catalogue.sqlite3"
    plain = sqlite3.connect(db_path)
    _ = plain.execute("CREATE TABLE songs (video_id TEXT PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL)")
    _ = plain.execute("INSERT INTO songs VALUES ('old', 'Kept Title', 'Kept Artist')")
    plain.commit()
    plain.close()

    with contextlib.closing(open_catalogue(db_path)) as conn:
        info = typing.cast("list[sqlite3.Row]", conn.execute("PRAGMA table_info(songs)").fetchall())
        columns = {typing.cast("str", row[1]) for row in info}
        kept = typing.cast("list[sqlite3.Row]", conn.execute("SELECT title FROM songs WHERE video_id = 'old'").fetchall())

    assert set(_NEW_COLUMNS) <= columns
    assert [typing.cast("str", row[0]) for row in kept] == ["Kept Title"]


def test_merge_songs_stores_the_album_and_the_duration(conn: sqlite3.Connection) -> None:
    """The match needs both. They come from the library read and belong beside the title."""
    _ = merge_songs(conn, [Song(video_id="a", title="T", artist="A", album="Al", duration_seconds=210)])

    stored = _stored(conn)

    assert (stored[0].album, stored[0].duration_seconds) == ("Al", 210)


def test_merge_songs_keeps_a_stored_album_against_an_empty_one(conn: sqlite3.Connection) -> None:
    """An absent album degrades to "", and an empty value must never replace a real one."""
    _ = merge_songs(conn, [Song(video_id="a", title="T", artist="A", album="Real Album", duration_seconds=210)])

    _ = merge_songs(conn, [Song(video_id="a", title="T", artist="A", album="", duration_seconds=0)])

    stored = _stored(conn)
    assert (stored[0].album, stored[0].duration_seconds) == ("Real Album", 210)


def test_a_song_round_trips_every_new_field(conn: sqlite3.Connection) -> None:
    """Every column the queue builder and the harvest write must survive a read."""
    _ = merge_songs(conn, [Song(video_id="a", title="T", artist="A", album="Al", duration_seconds=210)])
    _ = pair_sonos_track(conn, "a", track_id="SONOS1", uri="x-sonosapi-hls-static:SONOS1?sid=284")

    stored = _stored(conn)[0]

    assert stored.sonos_track_id == "SONOS1"
    assert stored.sonos_uri == "x-sonosapi-hls-static:SONOS1?sid=284"
    assert stored.first_seen is not None
    assert stored.last_seen is not None
    assert stored.last_queued is None


def _presence(conn: sqlite3.Connection, video_id: str) -> tuple[int, bool]:
    """Return the `missing_count` of one song and whether it carries a `last_seen`."""
    song = next(s for s in _stored(conn) if s.video_id == video_id)
    return song.missing_count, song.last_seen is not None


def test_mark_present_clears_the_missing_count(conn: sqlite3.Connection) -> None:
    """A song the library returned is present. The count measures absences in a row, so it resets."""
    _ = merge_songs(conn, [Song(video_id="a", title="T", artist="A")])
    _ = mark_absent(conn, present=[], threshold=99)
    _ = mark_absent(conn, present=[], threshold=99)

    mark_present(conn, ["a"])

    assert _presence(conn, "a") == (0, True)


def test_mark_absent_raises_the_count_of_a_song_the_read_missed(conn: sqlite3.Connection) -> None:
    """One absence is evidence, not a verdict. The count carries that evidence to the next run."""
    _ = merge_songs(conn, [Song(video_id="kept", title="T", artist="A"), Song(video_id="gone", title="T", artist="A")])

    _ = mark_absent(conn, present=["kept"], threshold=99)

    assert _presence(conn, "gone")[0] == 1
    assert _presence(conn, "kept")[0] == 0


def test_mark_absent_deletes_a_song_that_reaches_the_threshold(conn: sqlite3.Connection) -> None:
    """Several trusted reads in a row must agree before a row goes. One short read must not decide."""
    _ = merge_songs(conn, [Song(video_id="gone", title="T", artist="A")])

    counts = [mark_absent(conn, present=[], threshold=3) for _ in range(3)]

    assert counts == [0, 0, 1]
    assert _catalogue_video_ids(conn) == set()


def test_mark_absent_keeps_a_song_below_the_threshold(conn: sqlite3.Connection) -> None:
    """A song absent from fewer reads than the threshold stays, so a transient fault costs nothing."""
    _ = merge_songs(conn, [Song(video_id="gone", title="T", artist="A")])

    _ = mark_absent(conn, present=[], threshold=3)
    _ = mark_absent(conn, present=[], threshold=3)

    assert _catalogue_video_ids(conn) == {"gone"}
    assert _presence(conn, "gone")[0] == 2


def _catalogue_video_ids(conn: sqlite3.Connection) -> set[str]:
    """Return the `video_id` of every row the catalogue holds."""
    return {song.video_id for song in _stored(conn)}
