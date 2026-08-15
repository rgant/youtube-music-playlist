"""Tests for youtube_music_library_radio.catalogue.

Every test runs against a real temporary SQLite database, never against a double. The `conn` fixture
in `conftest.py` opens that database and closes it after the test passes or fails.
"""

import contextlib
import logging
import sqlite3
import typing
from pathlib import Path

import pytest

from youtube_music_library_radio.catalogue import (
    Song,
    candidate_songs,
    count_songs,
    delete_songs,
    merge_songs,
    open_catalogue,
    record_failure,
    record_success,
)


def _song(video_id: str, *, title: str = "Title", artist: str = "Artist") -> Song:
    """Build a `Song` with sensible defaults for the fields a test does not care about."""
    return Song(video_id=video_id, title=title, artist=artist, failure_count=0, last_success=None, last_played=None)


def test_open_creates_the_table_and_sets_wal(tmp_path: Path) -> None:
    """`open_catalogue` creates missing parent directories, the songs table, and WAL mode."""
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
    """`open_catalogue` does not stamp `user_version` back to 1 on a database a newer schema already versioned."""
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
    """`merge_songs` inserts every unseen song and returns how many rows it added."""
    added = merge_songs(conn, [_song("a"), _song("b")])

    assert added == 2
    assert count_songs(conn) == 2


def test_merge_with_no_songs_adds_nothing(conn: sqlite3.Connection) -> None:
    """`merge_songs` returns 0 and leaves the table empty when given no songs."""
    added = merge_songs(conn, [])

    assert added == 0
    assert count_songs(conn) == 0


def test_merge_tolerates_a_duplicate_video_id_within_one_batch(conn: sqlite3.Connection) -> None:
    """`merge_songs` does not crash or half-apply when the same `video_id` appears twice in one batch."""
    added = merge_songs(conn, [_song("a"), _song("a"), _song("b")])

    assert added == 2
    assert count_songs(conn) == 2


def test_merge_keeps_counts_on_an_existing_row(conn: sqlite3.Connection) -> None:
    """`merge_songs` does not reset failure_count, last_success, or last_played on an existing row."""
    _ = merge_songs(conn, [_song("a")])
    record_success(conn, "a", "Title", "Artist")
    _ = record_failure(conn, "a", prune_threshold=5)
    before = candidate_songs(conn, window=0)[0]

    added = merge_songs(conn, [_song("a")])

    after = candidate_songs(conn, window=0)[0]
    assert added == 0
    assert after.failure_count == before.failure_count
    assert after.last_success == before.last_success
    assert after.last_played == before.last_played


def test_merge_fills_an_empty_title_from_the_incoming_row(conn: sqlite3.Connection) -> None:
    """`merge_songs` overwrites an existing row's title and artist with non-empty incoming values."""
    _ = merge_songs(conn, [_song("a", title="", artist="")])

    _ = merge_songs(conn, [_song("a", title="Real Title", artist="Real Artist")])

    song = candidate_songs(conn, window=0)[0]
    assert song.title == "Real Title"
    assert song.artist == "Real Artist"


def test_merge_keeps_an_existing_title_when_the_incoming_one_is_empty(conn: sqlite3.Connection) -> None:
    """`merge_songs` does not blank a good stored title or artist with an empty incoming value."""
    _ = merge_songs(conn, [_song("a", title="Good Title", artist="Good Artist")])

    _ = merge_songs(conn, [_song("a", title="", artist="")])

    song = candidate_songs(conn, window=0)[0]
    assert song.title == "Good Title"
    assert song.artist == "Good Artist"


def test_delete_removes_only_the_named_rows_and_reports_the_count(conn: sqlite3.Connection) -> None:
    """`delete_songs` deletes the row of every named `video_id`, leaves the rest, and returns the rows deleted."""
    _ = merge_songs(conn, [_song("a"), _song("b"), _song("c")])

    deleted = delete_songs(conn, ["a", "c"])

    assert deleted == 2
    assert [song.video_id for song in candidate_songs(conn, window=0)] == ["b"]


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
    """`delete_songs` returns the rows deleted, not the IDs given, so a repeated `video_id` counts one."""
    _ = merge_songs(conn, [_song("a"), _song("b")])

    deleted = delete_songs(conn, ["a", "a"])

    assert deleted == 1
    assert [song.video_id for song in candidate_songs(conn, window=0)] == ["b"]


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
    assert {song.video_id for song in candidate_songs(conn, window=0)} == {"a", "b", "c"}


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
    assert [song.video_id for song in candidate_songs(conn, window=0)] == ["keep"]


def test_candidates_exclude_the_recently_played(conn: sqlite3.Connection) -> None:
    """`candidate_songs` excludes the `window` most recently played songs."""
    _ = merge_songs(conn, [_song("a"), _song("b"), _song("c")])
    _ = conn.execute("UPDATE songs SET last_played = '2024-01-01T00:00:00+00:00' WHERE video_id = 'a'")
    _ = conn.execute("UPDATE songs SET last_played = '2024-01-02T00:00:00+00:00' WHERE video_id = 'b'")
    _ = conn.execute("UPDATE songs SET last_played = '2024-01-03T00:00:00+00:00' WHERE video_id = 'c'")

    candidates = candidate_songs(conn, window=1)

    ids = {song.video_id for song in candidates}
    assert ids == {"a", "b"}


def test_candidates_return_every_song_when_the_window_covers_the_table(conn: sqlite3.Connection) -> None:
    """`candidate_songs` returns every row when `window` is at least the row count."""
    _ = merge_songs(conn, [_song("a"), _song("b")])
    _ = conn.execute("UPDATE songs SET last_played = '2024-01-01T00:00:00+00:00' WHERE video_id = 'a'")
    _ = conn.execute("UPDATE songs SET last_played = '2024-01-02T00:00:00+00:00' WHERE video_id = 'b'")

    candidates = candidate_songs(conn, window=2)

    ids = {song.video_id for song in candidates}
    assert ids == {"a", "b"}


def test_candidates_include_a_song_that_never_played(conn: sqlite3.Connection) -> None:
    """`candidate_songs` always includes a song whose `last_played` is `None`, regardless of `window`."""
    _ = merge_songs(conn, [_song("a"), _song("b")])
    _ = conn.execute("UPDATE songs SET last_played = '2024-01-01T00:00:00+00:00' WHERE video_id = 'a'")

    candidates = candidate_songs(conn, window=1)

    ids = {song.video_id for song in candidates}
    assert "b" in ids


def test_candidates_with_a_negative_window_returns_every_song(conn: sqlite3.Connection) -> None:
    """`candidate_songs` treats a negative `window` as excluding nothing, not as SQLite's unlimited `LIMIT`."""
    _ = merge_songs(conn, [_song("a")])
    _ = conn.execute("UPDATE songs SET last_played = '2024-01-01T00:00:00+00:00' WHERE video_id = 'a'")

    candidates = candidate_songs(conn, window=-1)

    ids = {song.video_id for song in candidates}
    assert ids == {"a"}


def test_success_fills_an_empty_title_and_resets_the_failure_count(conn: sqlite3.Connection) -> None:
    """`record_success` fills an empty title and artist, sets last_success and last_played, and clears failure_count.

    `bootstrap` seeds every row with an empty title and artist, so the first play is what puts a
    real name on the row.
    """
    _ = merge_songs(conn, [_song("a", title="", artist="")])
    _ = record_failure(conn, "a", prune_threshold=5)
    _ = record_failure(conn, "a", prune_threshold=5)

    record_success(conn, "a", "New Title", "New Artist")

    song = candidate_songs(conn, window=0)[0]
    assert song.title == "New Title"
    assert song.artist == "New Artist"
    assert song.failure_count == 0
    assert song.last_success is not None
    assert song.last_played is not None


def test_success_keeps_a_stored_title_and_artist(conn: sqlite3.Connection) -> None:
    """`record_success` does not replace a stored title or artist, and still records the play.

    A player that reads neither an artist nor an uploader passes `"Unknown"` here. The library,
    through `merge_songs`, is the authority on the title and the artist. One play must never
    overwrite a real name with a value the player failed to read.
    """
    _ = merge_songs(conn, [_song("a", title="Real Title", artist="Real Artist")])

    record_success(conn, "a", "Some Video Title", "Unknown")

    song = candidate_songs(conn, window=0)[0]
    assert song.title == "Real Title"
    assert song.artist == "Real Artist"
    assert song.last_played is not None


def test_success_on_an_unknown_video_id_is_a_no_op(conn: sqlite3.Connection) -> None:
    """`record_success` does not raise or insert a row for a `video_id` absent from the catalogue."""
    record_success(conn, "missing", "Title", "Artist")

    assert count_songs(conn) == 0


def test_failure_raises_the_count_below_the_threshold(conn: sqlite3.Connection) -> None:
    """`record_failure` increments failure_count and returns False when it stays below `prune_threshold`."""
    _ = merge_songs(conn, [_song("a")])

    pruned = record_failure(conn, "a", prune_threshold=3)

    assert pruned is False
    song = candidate_songs(conn, window=0)[0]
    assert song.failure_count == 1


def test_failure_on_an_unknown_video_id_returns_true_and_warns(conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture) -> None:
    """`record_failure` treats an absent `video_id` as already pruned: returns True, and warns naming the row.

    The warning is the only signal an operator gets for this condition, so the message must name the
    row it is about.
    """
    with caplog.at_level(logging.WARNING):
        pruned = record_failure(conn, "absent-video-id", prune_threshold=3)

    assert pruned is True
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "absent-video-id" in warnings[0]
    assert "not in the catalogue" in warnings[0]


def test_failure_deletes_the_row_at_the_threshold(conn: sqlite3.Connection) -> None:
    """`record_failure` deletes the row and returns True once failure_count reaches `prune_threshold`."""
    _ = merge_songs(conn, [_song("a")])
    _ = record_failure(conn, "a", prune_threshold=2)

    pruned = record_failure(conn, "a", prune_threshold=2)

    assert pruned is True
    assert count_songs(conn) == 0
