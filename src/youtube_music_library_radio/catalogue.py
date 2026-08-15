"""The song catalogue: the `Song` record and the SQLite-backed store other modules build on."""

import dataclasses
import logging
import sqlite3
import typing
from datetime import UTC, datetime

if typing.TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

_logger = logging.getLogger(__name__)

# `sqlite3.threadsafety` 3 means that threads can share the module, the connections, and the
# cursors. SQLite reports that level only when it is built in serialized mode.
_REQUIRED_THREADSAFETY = 3


@dataclasses.dataclass(frozen=True)
class Song:
    """A single library track and its playback history."""

    video_id: str
    title: str
    artist: str
    failure_count: int
    last_success: datetime | None
    last_played: datetime | None


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS songs (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_success TEXT,
    last_played TEXT
)
"""


def open_catalogue(path: Path) -> sqlite3.Connection:
    """Open the catalogue database at `path`. This creates the directory, the file, and the table.

    Raises RuntimeError if SQLite cannot set WAL journal mode for `path`. Another mode cannot carry
    the concurrent access of a player and of `refresh`.

    `check_same_thread=False` lets one connection serve every thread of the process that plays
    songs. Such a process reads the candidate songs and writes the play result from more than one
    thread.

    Raises RuntimeError if `sqlite3.threadsafety` is below 3, for the same reason as the WAL check.
    `check_same_thread=False` removes the barrier that Python puts between a connection and a second
    thread. Only SQLite's own serialized mode replaces that barrier. A build below level 3 gives
    silent data corruption instead of an exception, so this check must run before the connection
    opens.
    """
    if sqlite3.threadsafety < _REQUIRED_THREADSAFETY:
        message = (
            f"this catalogue shares one connection across threads, which needs sqlite3 threadsafety "
            f"{_REQUIRED_THREADSAFETY}, but this build reports {sqlite3.threadsafety}"
        )
        raise RuntimeError(message)

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0, autocommit=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    mode_row = typing.cast("sqlite3.Row", conn.execute("PRAGMA journal_mode = WAL").fetchone())
    mode = typing.cast("str", mode_row[0])
    if mode != "wal":
        conn.close()
        message = f"catalogue at {path} requires WAL journal mode, sqlite3 reported {mode!r}"
        raise RuntimeError(message)

    version_row = typing.cast("sqlite3.Row", conn.execute("PRAGMA user_version").fetchone())
    if typing.cast("int", version_row[0]) == 0:
        _ = conn.execute("PRAGMA user_version = 1")

    _ = conn.execute(_CREATE_TABLE)
    return conn


def merge_songs(conn: sqlite3.Connection, songs: Iterable[Song]) -> int:
    """Insert songs unseen in the catalogue and refresh title/artist on existing rows. Return the rows added."""
    params = [{"video_id": song.video_id, "title": song.title, "artist": song.artist} for song in songs]
    if not params:
        return 0

    insert_cursor = conn.executemany(
        """
        INSERT INTO songs (video_id, title, artist, failure_count, last_success, last_played)
        VALUES (:video_id, :title, :artist, 0, NULL, NULL)
        ON CONFLICT (video_id) DO NOTHING
        """,
        params,
    )
    _ = conn.executemany(
        """
        UPDATE songs
        SET title = CASE WHEN :title <> '' THEN :title ELSE title END,
            artist = CASE WHEN :artist <> '' THEN :artist ELSE artist END
        WHERE video_id = :video_id
        """,
        params,
    )

    return insert_cursor.rowcount


def delete_songs(conn: sqlite3.Connection, video_ids: Iterable[str]) -> int:
    """Delete the row of every `video_id` given. Return the rows deleted.

    A `video_id` absent from the catalogue is a no-op, as `record_success` treats an unknown ID.
    A `video_id` given twice therefore counts one deletion, not two.

    `executemany` runs one statement per ID, with one host parameter each, as `merge_songs` does.
    The SQLite cap of 32,766 host parameters applies to one statement. This call therefore stays
    under the cap for a list of any length. A `WHERE video_id IN (...)` form needs one host
    parameter per ID in one statement, and needs chunks. This form needs none.
    """
    params = [{"video_id": video_id} for video_id in video_ids]
    if not params:
        return 0

    cursor = conn.executemany("DELETE FROM songs WHERE video_id = :video_id", params)
    return cursor.rowcount


def candidate_songs(conn: sqlite3.Connection, window: int) -> list[Song]:
    """Return every song except the `window` most recently played.

    A never-played song is always a candidate. A `window` of zero or less excludes nothing. A
    `window` at least as large as the row count also excludes nothing. Every song is then a
    candidate.
    """
    total = count_songs(conn)
    if window <= 0 or window >= total:
        cursor = conn.execute("SELECT * FROM songs")
    else:
        cursor = conn.execute(
            """
            SELECT * FROM songs
            WHERE video_id NOT IN (
                SELECT video_id FROM songs WHERE last_played IS NOT NULL ORDER BY last_played DESC LIMIT ?
            )
            """,
            (window,),
        )
    rows = typing.cast("list[sqlite3.Row]", cursor.fetchall())
    return [_row_to_song(row) for row in rows]


def record_success(conn: sqlite3.Connection, video_id: str, title: str, artist: str) -> None:
    """Record a successful play: fill an empty title or artist, set last_success and last_played, clear failure_count.

    A stored non-empty title or artist stays, the same guard `merge_songs` applies. The library is
    the authority on the title and the artist. A player that reads neither an artist nor an uploader
    passes `"Unknown"` here. Without the guard one play replaces a real artist with `"Unknown"`.
    A row that `bootstrap` seeds carries an empty title and artist, and this fills them.

    A `video_id` absent from the catalogue is a no-op: nothing is recorded, and nothing raises.
    """
    now = datetime.now(UTC).isoformat()
    _ = conn.execute(
        """
        UPDATE songs
        SET title = CASE WHEN title <> '' THEN title ELSE ? END,
            artist = CASE WHEN artist <> '' THEN artist ELSE ? END,
            failure_count = 0,
            last_success = ?,
            last_played = ?
        WHERE video_id = ?
        """,
        (title, artist, now, now, video_id),
    )


def record_failure(conn: sqlite3.Connection, video_id: str, prune_threshold: int) -> bool:
    """Increment failure_count. Delete the row and return True once it reaches `prune_threshold`.

    A `video_id` absent from the catalogue also returns True, and logs a warning. The caller's
    post-condition is "this song is not in the catalogue", and that already holds.

    A player holds a `candidate_songs` snapshot and works through it over time. An earlier
    `record_failure` call can prune a song in that snapshot before the player reaches it. That is
    an expected condition, not a fault.
    """
    cursor = conn.execute(
        "UPDATE songs SET failure_count = failure_count + 1 WHERE video_id = ? RETURNING failure_count",
        (video_id,),
    )
    row = typing.cast("sqlite3.Row | None", cursor.fetchone())
    if row is None:
        _logger.warning("record_failure: video_id %r is not in the catalogue; treating it as already pruned", video_id)
        return True

    failure_count = typing.cast("int", row["failure_count"])
    if failure_count >= prune_threshold:
        _ = conn.execute("DELETE FROM songs WHERE video_id = ?", (video_id,))
        return True
    return False


def count_songs(conn: sqlite3.Connection) -> int:
    """Return the number of rows in the catalogue."""
    row = typing.cast("sqlite3.Row", conn.execute("SELECT COUNT(*) FROM songs").fetchone())
    return typing.cast("int", row[0])


def _row_to_song(row: sqlite3.Row) -> Song:
    """Convert one `songs` row into a `Song`."""
    return Song(
        video_id=typing.cast("str", row["video_id"]),
        title=typing.cast("str", row["title"]),
        artist=typing.cast("str", row["artist"]),
        failure_count=typing.cast("int", row["failure_count"]),
        last_success=_parse_datetime(typing.cast("str | None", row["last_success"])),
        last_played=_parse_datetime(typing.cast("str | None", row["last_played"])),
    )


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse a stored ISO-8601 timestamp, or return None when the column is NULL."""
    if value is None:
        return None
    return datetime.fromisoformat(value)
