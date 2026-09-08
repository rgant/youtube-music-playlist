"""The song catalogue: the `Song` record and the SQLite-backed store other modules build on."""

import dataclasses
import logging
import sqlite3
import typing
from datetime import UTC, datetime, timedelta

if typing.TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

_logger = logging.getLogger(__name__)

# `sqlite3.threadsafety` 3 means that threads can share the module, the connections, and the
# cursors. SQLite reports that level only when it is built in serialized mode.
_REQUIRED_THREADSAFETY = 3


# `Song` mirrors one `songs` row, so its field count follows the table. R0902 targets a class that
# carries too much behavior, and this record carries none.
@dataclasses.dataclass(frozen=True)
class Song:  # pylint: disable=too-many-instance-attributes
    """One library song: what it is, where Sonos keeps it, and what this project did with it.

    `album` and `duration_seconds` are match keys. The Sonos queue reports both, and together with
    the title and the artist they identify a song inside one playlist without ambiguity.

    `sonos_track_id` and `sonos_uri` stay None until a harvest pairs the song. The queue builder
    sends `sonos_uri` to the speaker, so a song without one cannot reach the queue.

    `missing_count` counts trusted library reads in a row that did not return this song. `refresh`
    deletes the row once that count reaches the threshold. One absent read is a short read more often
    than a removal.
    """

    video_id: str
    title: str
    artist: str
    album: str = ""
    duration_seconds: int = 0
    sonos_track_id: str | None = None
    sonos_uri: str | None = None
    missing_count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    last_queued: datetime | None = None


@dataclasses.dataclass(frozen=True)
class QueueEntry:
    """One entry of the Sonos queue, as the speaker reports it.

    This is the shape `sonos_tracks` stores, so it lives beside that table. `harvest` builds one per
    queue entry and re-exports the name.
    """

    track_id: str
    uri: str
    title: str
    artist: str
    album: str
    duration_seconds: int


@dataclasses.dataclass(frozen=True)
class PlaylistMembership:
    """One `Everything N` playlist and the video IDs it holds, in playlist order.

    `reported_count` is the length the API states for the playlist. `video_ids` is what the read
    returned. A caller compares the two to detect a short read.
    """

    playlist_id: str
    title: str
    ordinal: int
    reported_count: int
    video_ids: tuple[str, ...]


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS songs (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL
)
"""

# Columns absent from `_CREATE_TABLE`. `open_catalogue` adds any that a database lacks, so an
# existing catalogue keeps its rows and its play history. SQLite allows one ADD COLUMN at a time,
# and it accepts a constant default alone, so every entry here carries one.
_SONG_COLUMNS: tuple[tuple[str, str], ...] = (
    ("album", "TEXT NOT NULL DEFAULT ''"),
    ("duration_seconds", "INTEGER NOT NULL DEFAULT 0"),
    ("sonos_track_id", "TEXT"),
    ("sonos_uri", "TEXT"),
    ("missing_count", "INTEGER NOT NULL DEFAULT 0"),
    ("first_seen", "TEXT"),
    ("last_seen", "TEXT"),
    ("last_queued", "TEXT"),
)

# `playlist_songs` keys on the pair, and not on `video_id` alone. A video ID in two playlists is the
# defect this project measures, so the schema must be able to hold it and report it.
_CREATE_PLAYLISTS = """
CREATE TABLE IF NOT EXISTS playlists (
    playlist_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    reported_count INTEGER NOT NULL,
    last_read TEXT NOT NULL
)
"""

_CREATE_PLAYLIST_SONGS = """
CREATE TABLE IF NOT EXISTS playlist_songs (
    playlist_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (playlist_id, video_id)
)
"""

_CREATE_PLAYLIST_SONGS_INDEX = "CREATE INDEX IF NOT EXISTS playlist_songs_video_id ON playlist_songs (video_id)"

# The raw record of what a Sonos queue read saw. It stays separate from the pairing that `songs`
# carries, so a better matcher runs again on the same evidence with no second queue read.
_CREATE_SONOS_TRACKS = """
CREATE TABLE IF NOT EXISTS sonos_tracks (
    track_id TEXT PRIMARY KEY,
    uri TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    source TEXT,
    queue_position INTEGER,
    playlist_ordinal INTEGER
)
"""


def open_catalogue(path: Path) -> sqlite3.Connection:
    """Open the catalogue database at `path`. This creates the directory, the file, and every table.

    Raises RuntimeError if SQLite cannot set WAL journal mode for `path`. Another mode cannot carry
    the concurrent access of a player and of `refresh`.

    `check_same_thread=False` lets one connection serve every thread of the process that plays
    songs. Such a process reads the candidate songs and writes the play result from more than one
    thread.

    Raises RuntimeError if `sqlite3.threadsafety` is below `_REQUIRED_THREADSAFETY`. A lower build
    gives silent data corruption instead of an exception, so the check runs before the connection
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
    _add_missing_columns(conn)
    _ = conn.execute(_CREATE_PLAYLISTS)
    _ = conn.execute(_CREATE_PLAYLIST_SONGS)
    _ = conn.execute(_CREATE_PLAYLIST_SONGS_INDEX)
    _ = conn.execute(_CREATE_SONOS_TRACKS)
    return conn


def record_playlists(conn: sqlite3.Connection, playlists: Iterable[PlaylistMembership]) -> None:
    """Add each playlist and its membership to the store. This deletes no row.

    The store accumulates. `ytmusicapi` returns fewer songs than a playlist claims often enough to
    matter. A replace shrinks the stored membership to the worst read. A short membership then looks
    like "these songs are in no playlist", which is the fault that writes duplicates.

    The union over-states membership when the owner deletes a song from a playlist by hand. That
    song then waits, and no run puts it back. Over-statement costs a wait. Under-statement costs a
    duplicate, so the store leans this way on purpose.

    `reported_count` takes the new value on every call. The count a playlist claims stays right even
    when the item fetch is short, so it is the authority on capacity.

    `first_seen` and `position` come from the first read that saw the song. A later read leaves both
    alone, so the earliest evidence wins.
    """
    members = list(playlists)
    if not members:
        return

    now = datetime.now(UTC).isoformat()
    with conn:
        for playlist in members:
            _ = conn.execute(
                """
                INSERT INTO playlists (playlist_id, title, ordinal, reported_count, last_read)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (playlist_id) DO UPDATE SET
                    title = excluded.title,
                    ordinal = excluded.ordinal,
                    reported_count = excluded.reported_count,
                    last_read = excluded.last_read
                """,
                (playlist.playlist_id, playlist.title, playlist.ordinal, playlist.reported_count, now),
            )
            _ = conn.executemany(
                """
                INSERT INTO playlist_songs (playlist_id, video_id, position, first_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (playlist_id, video_id) DO NOTHING
                """,
                [(playlist.playlist_id, video_id, index, now) for index, video_id in enumerate(playlist.video_ids)],
            )


def stored_playlists(conn: sqlite3.Connection) -> tuple[PlaylistMembership, ...]:
    """Return every playlist the store knows, with its accumulated membership, in ordinal order.

    This is the union each run builds on. A playlist absent from one listing still appears here, so
    a short listing hides nothing that an earlier run already saw.
    """
    rows = typing.cast(
        "list[sqlite3.Row]",
        conn.execute("SELECT playlist_id, title, ordinal, reported_count FROM playlists ORDER BY ordinal").fetchall(),
    )
    found: list[PlaylistMembership] = []
    for row in rows:
        playlist_id = typing.cast("str", row["playlist_id"])
        songs = typing.cast(
            "list[sqlite3.Row]",
            conn.execute("SELECT video_id FROM playlist_songs WHERE playlist_id = ? ORDER BY position", (playlist_id,)).fetchall(),
        )
        found.append(
            PlaylistMembership(
                playlist_id=playlist_id,
                title=typing.cast("str", row["title"]),
                ordinal=typing.cast("int", row["ordinal"]),
                reported_count=typing.cast("int", row["reported_count"]),
                video_ids=tuple(typing.cast("str", song[0]) for song in songs),
            )
        )
    return tuple(found)


def duplicated_video_ids(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Return each video ID that sits in more than one playlist, with the titles in ordinal order."""
    rows = typing.cast(
        "list[sqlite3.Row]",
        conn.execute(
            """
            SELECT playlist_songs.video_id, playlists.title FROM playlist_songs
            JOIN playlists ON playlists.playlist_id = playlist_songs.playlist_id
            WHERE playlist_songs.video_id IN (
                SELECT video_id FROM playlist_songs GROUP BY video_id HAVING COUNT(*) > 1
            )
            ORDER BY playlist_songs.video_id, playlists.ordinal
            """
        ).fetchall(),
    )
    found: dict[str, list[str]] = {}
    for row in rows:
        found.setdefault(typing.cast("str", row[0]), []).append(typing.cast("str", row[1]))
    return found


def merge_songs(conn: sqlite3.Connection, songs: Iterable[Song]) -> int:
    """Insert songs unseen in the catalogue and refresh the library fields. Return the rows added.

    An empty incoming value never replaces a stored one. `bootstrap` seeds a song with a video ID
    alone, and a later library read fills the rest. A degraded read must not undo that.
    """
    now = datetime.now(UTC).isoformat()
    params = [
        {
            "video_id": song.video_id,
            "title": song.title,
            "artist": song.artist,
            "album": song.album,
            "duration_seconds": song.duration_seconds,
            "now": now,
        }
        for song in songs
    ]
    if not params:
        return 0

    insert_cursor = conn.executemany(
        """
        INSERT INTO songs (video_id, title, artist, album, duration_seconds, first_seen, last_seen)
        VALUES (:video_id, :title, :artist, :album, :duration_seconds, :now, :now)
        ON CONFLICT (video_id) DO NOTHING
        """,
        params,
    )
    _ = conn.executemany(
        """
        UPDATE songs
        SET title = CASE WHEN :title <> '' THEN :title ELSE title END,
            artist = CASE WHEN :artist <> '' THEN :artist ELSE artist END,
            album = CASE WHEN :album <> '' THEN :album ELSE album END,
            duration_seconds = CASE WHEN :duration_seconds > 0 THEN :duration_seconds ELSE duration_seconds END,
            first_seen = COALESCE(first_seen, :now)
        WHERE video_id = :video_id
        """,
        params,
    )

    return insert_cursor.rowcount


def songs_by_video_id(conn: sqlite3.Connection, video_ids: set[str]) -> list[Song]:
    """Return the catalogue rows for `video_ids`, in no set order.

    The harvest passes one playlist's membership. That pool is what keeps a title match unambiguous,
    so this function never widens it.
    """
    if not video_ids:
        return []

    rows = typing.cast("list[sqlite3.Row]", conn.execute("SELECT * FROM songs").fetchall())
    return [_row_to_song(row) for row in rows if typing.cast("str", row["video_id"]) in video_ids]


def record_sonos_track(conn: sqlite3.Connection, entry: QueueEntry, *, source: str, seen: str) -> None:
    """Record one queue entry in `sonos_tracks`, whatever the match makes of it.

    A track already stored keeps its first row. The earliest observation wins, as it does for
    `playlist_songs`.
    """
    _ = conn.execute(
        """
        INSERT INTO sonos_tracks (track_id, uri, title, artist, album, first_seen, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (track_id) DO NOTHING
        """,
        (entry.track_id, entry.uri, entry.title, entry.artist, entry.album, seen, source),
    )


def queueable_songs(conn: sqlite3.Connection, *, window_days: int) -> list[Song]:
    """Return every song the speaker can play and did not hold in the queue lately.

    A song qualifies when it carries a `sonos_uri` and its `last_queued` is older than `window_days`.
    A song never queued always qualifies, because its `last_queued` is NULL.

    A `window_days` of zero or less excludes nothing.
    """
    if window_days <= 0:
        cursor = conn.execute("SELECT * FROM songs WHERE sonos_uri IS NOT NULL")
    else:
        cutoff = (datetime.now(UTC) - timedelta(days=window_days)).isoformat()
        cursor = conn.execute(
            "SELECT * FROM songs WHERE sonos_uri IS NOT NULL AND (last_queued IS NULL OR last_queued < ?)",
            (cutoff,),
        )
    rows = typing.cast("list[sqlite3.Row]", cursor.fetchall())
    return [_row_to_song(row) for row in rows]


def mark_queued(conn: sqlite3.Connection, video_ids: Iterable[str], *, when: datetime | None = None) -> None:
    """Record that each song reached the Sonos queue. This is what keeps the next queue different.

    Call it after the send succeeds. A song marked before a failed send never played. It stays out
    of the next queue.
    """
    stamp = (when if when is not None else datetime.now(UTC)).isoformat()
    params = [{"video_id": video_id, "when": stamp} for video_id in video_ids]
    if not params:
        return

    _ = conn.executemany("UPDATE songs SET last_queued = :when WHERE video_id = :video_id", params)


def mark_present(conn: sqlite3.Connection, video_ids: Iterable[str]) -> None:
    """Record that a trusted library read returned each song. This clears its `missing_count`.

    A song that comes back after an absence starts again from zero. The count measures absences in a
    row, and one return breaks the run.
    """
    now = datetime.now(UTC).isoformat()
    params = [{"video_id": video_id, "now": now} for video_id in video_ids]
    if not params:
        return

    _ = conn.executemany(
        "UPDATE songs SET last_seen = :now, missing_count = 0 WHERE video_id = :video_id",
        params,
    )


def mark_absent(conn: sqlite3.Connection, *, present: Iterable[str], threshold: int) -> int:
    """Raise `missing_count` on every song outside `present`, then delete those that reach `threshold`.

    Return the rows deleted.

    Call this only after a trusted read. A short read returns fewer songs than the library holds, and
    every song it missed looks removed. The trust rule in `refresh` decides which reads reach here.

    A removal therefore needs `threshold` trusted reads in a row to agree. A song the owner deletes
    on purpose leaves after that many refreshes. A transient fault costs a wait and nothing else.

    The temporary table carries the present IDs. A `WHERE video_id NOT IN (...)` form needs one host
    parameter per song, and the library holds far more than the SQLite cap of 32,766 allows in one
    statement.
    """
    with conn:
        _ = conn.execute("CREATE TEMP TABLE IF NOT EXISTS present_ids (video_id TEXT PRIMARY KEY)")
        _ = conn.execute("DELETE FROM present_ids")
        _ = conn.executemany("INSERT OR IGNORE INTO present_ids (video_id) VALUES (?)", [(vid,) for vid in present])
        _ = conn.execute("UPDATE songs SET missing_count = missing_count + 1 WHERE video_id NOT IN (SELECT video_id FROM present_ids)")
        cursor = conn.execute("DELETE FROM songs WHERE missing_count >= ?", (threshold,))
        deleted = cursor.rowcount
        _ = conn.execute("DROP TABLE present_ids")
    return deleted


def pair_sonos_track(conn: sqlite3.Connection, video_id: str, *, track_id: str, uri: str) -> bool:
    """Record the Sonos track a library song plays as. Return True when a row changed.

    A song with no pairing cannot reach the Sonos queue, because the queue builder sends `sonos_uri`.
    """
    cursor = conn.execute(
        "UPDATE songs SET sonos_track_id = ?, sonos_uri = ? WHERE video_id = ?",
        (track_id, uri, video_id),
    )
    return cursor.rowcount > 0


def delete_songs(conn: sqlite3.Connection, video_ids: Iterable[str]) -> int:
    """Delete the row of every `video_id` given. Return the rows deleted.

    A `video_id` absent from the catalogue is a no-op.
    A `video_id` given twice therefore counts one deletion, not two.

    `executemany` runs one statement per ID, so a list of any length stays under the SQLite cap of
    32,766 host parameters. A `WHERE video_id IN (...)` form needs chunks.
    """
    params = [{"video_id": video_id} for video_id in video_ids]
    if not params:
        return 0

    cursor = conn.executemany("DELETE FROM songs WHERE video_id = :video_id", params)
    return cursor.rowcount


def count_songs(conn: sqlite3.Connection) -> int:
    """Return the number of rows in the catalogue."""
    row = typing.cast("sqlite3.Row", conn.execute("SELECT COUNT(*) FROM songs").fetchone())
    return typing.cast("int", row[0])


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Add every column of `_SONG_COLUMNS` the `songs` table lacks.

    An existing catalogue holds a play history that no read rebuilds, so a schema change must never
    need a fresh database.
    """
    info = typing.cast("list[sqlite3.Row]", conn.execute("PRAGMA table_info(songs)").fetchall())
    existing = {typing.cast("str", row[1]) for row in info}
    for name, definition in _SONG_COLUMNS:
        if name not in existing:
            # `name` and `definition` are literals of `_SONG_COLUMNS`, never external input.
            _ = conn.execute(f"ALTER TABLE songs ADD COLUMN {name} {definition}")
            _logger.info("catalogue: added column %s to songs", name)


def _row_to_song(row: sqlite3.Row) -> Song:
    """Convert one `songs` row into a `Song`."""
    return Song(
        video_id=typing.cast("str", row["video_id"]),
        title=typing.cast("str", row["title"]),
        artist=typing.cast("str", row["artist"]),
        album=typing.cast("str", row["album"]),
        duration_seconds=typing.cast("int", row["duration_seconds"]),
        sonos_track_id=typing.cast("str | None", row["sonos_track_id"]),
        sonos_uri=typing.cast("str | None", row["sonos_uri"]),
        missing_count=typing.cast("int", row["missing_count"]),
        first_seen=_parse_datetime(typing.cast("str | None", row["first_seen"])),
        last_seen=_parse_datetime(typing.cast("str | None", row["last_seen"])),
        last_queued=_parse_datetime(typing.cast("str | None", row["last_queued"])),
    )


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse a stored ISO-8601 timestamp, or return None when the column is NULL."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


def unpaired_songs(conn: sqlite3.Connection) -> list[Song]:
    """Return every library song that carries no Sonos pairing.

    `rematch` reads these against the tracks a queue read already observed.
    """
    rows = typing.cast("list[sqlite3.Row]", conn.execute("SELECT * FROM songs WHERE sonos_uri IS NULL").fetchall())
    return [_row_to_song(row) for row in rows]


def free_sonos_tracks(conn: sqlite3.Connection) -> list[QueueEntry]:
    """Return every observed Sonos track that no library song claims.

    `sonos_tracks` holds no length, so each entry reads back with a length of 0. `rematch` compares
    title, artist, and album alone, so it needs none.
    """
    rows = typing.cast(
        "list[sqlite3.Row]",
        conn.execute(
            """
            SELECT track_id, uri, title, artist, album FROM sonos_tracks
            WHERE track_id NOT IN (SELECT sonos_track_id FROM songs WHERE sonos_track_id IS NOT NULL)
            """
        ).fetchall(),
    )
    return [
        QueueEntry(
            track_id=typing.cast("str", row["track_id"]),
            uri=typing.cast("str", row["uri"]),
            title=typing.cast("str", row["title"]),
            artist=typing.cast("str", row["artist"]),
            album=typing.cast("str", row["album"]),
            duration_seconds=0,
        )
        for row in rows
    ]
