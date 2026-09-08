"""Integration tests proving the catalogue survives writes from separate, concurrent connections."""

import threading
import time
import typing

from youtube_music_library_radio.catalogue import Song, count_songs, mark_queued, merge_songs, open_catalogue

if typing.TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

_SONG_A = Song(video_id="a", title="A", artist="Artist")
_SONG_B = Song(video_id="b", title="B", artist="Artist")


def test_one_connection_reads_and_writes_from_a_second_thread(tmp_path: Path) -> None:
    """A thread that did not open the connection can still read and write through it.

    A process that serves each request on its own thread reads and writes through the one
    connection it opened at start-up. Without `check_same_thread=False`, the first query on a second
    thread raises `sqlite3.ProgrammingError`.
    """
    db_path = tmp_path / "catalogue.sqlite3"
    conn = open_catalogue(db_path)
    _ = merge_songs(conn, [_SONG_A])
    counts: list[int] = []
    errors: list[BaseException] = []

    def read_and_write_on_another_thread() -> None:
        """Run one read and one write through the connection the main thread opened."""
        try:
            counts.append(count_songs(conn))
            mark_queued(conn, ["a"])
        except BaseException as exc:  # noqa: BLE001 (surface the failure to the main thread instead of a bare thread crash)
            errors.append(exc)

    thread = threading.Thread(target=read_and_write_on_another_thread)
    thread.start()
    thread.join(timeout=5)

    assert not errors
    assert counts == [1]
    row = typing.cast("sqlite3.Row", conn.execute("SELECT last_queued FROM songs WHERE video_id = 'a'").fetchone())
    assert row["last_queued"] is not None
    conn.close()


def test_a_queued_write_and_a_merge_write_both_survive(tmp_path: Path) -> None:
    """A failure recorded on one connection and songs merged on a second connection both persist.

    Each write starts while a third connection holds a read transaction open on the same database
    file. That mirrors the production shape. The player process records failures, and the refresh
    command merges songs. They run as two separate processes with two separate connections.
    """
    db_path = tmp_path / "catalogue.sqlite3"
    setup_conn = open_catalogue(db_path)
    _ = merge_songs(setup_conn, [_SONG_A])
    setup_conn.close()

    reader_conn = open_catalogue(db_path)
    _ = reader_conn.execute("BEGIN")
    _ = reader_conn.execute("SELECT * FROM songs").fetchall()  # Open a read transaction and hold it.

    write_done = threading.Event()
    write_errors: list[BaseException] = []

    def write_from_two_writer_connections() -> None:
        """Record a failure on one connection, then merge a new song on a second, distinct connection."""
        try:
            failure_conn = open_catalogue(db_path)
            mark_queued(failure_conn, ["a"])
            failure_conn.close()

            merge_conn = open_catalogue(db_path)
            _ = merge_songs(merge_conn, [_SONG_B])
            merge_conn.close()
        except BaseException as exc:  # noqa: BLE001 (surface the failure to the main thread instead of a bare thread crash)
            write_errors.append(exc)
        finally:
            write_done.set()

    writer_thread = threading.Thread(target=write_from_two_writer_connections)
    writer_thread.start()
    completed_while_the_reader_held_its_lock = write_done.wait(timeout=5)
    _ = reader_conn.execute("COMMIT")
    reader_conn.close()
    writer_thread.join(timeout=5)

    assert not write_errors
    assert completed_while_the_reader_held_its_lock

    verify_conn = open_catalogue(db_path)
    assert count_songs(verify_conn) == 2
    row = typing.cast("sqlite3.Row", verify_conn.execute("SELECT last_queued FROM songs WHERE video_id = 'a'").fetchone())
    assert row["last_queued"] is not None
    verify_conn.close()


def test_two_writer_connections_contending_for_the_lock_both_land(tmp_path: Path) -> None:
    """Two writer connections, on two separate threads, both persist their write.

    The first thread holds SQLite's write lock for 6 seconds, longer than SQLite's 5-second default
    busy timeout. The second thread's write must wait for the first to release the lock.
    `open_catalogue`'s `timeout=30.0` is what lets it wait long enough, instead of raising
    `sqlite3.OperationalError: database is locked` after 5 seconds.
    """
    db_path = tmp_path / "catalogue.sqlite3"
    setup_conn = open_catalogue(db_path)
    _ = merge_songs(setup_conn, [_SONG_A])
    setup_conn.close()

    lock_held = threading.Event()
    errors: list[BaseException] = []

    def hold_the_write_lock_for_a_while() -> None:
        """Acquire SQLite's write lock on its own connection, and hold it for 6 seconds."""
        try:
            holder_conn = open_catalogue(db_path)
            _ = holder_conn.execute("BEGIN IMMEDIATE")
            lock_held.set()
            time.sleep(6)
            _ = holder_conn.execute("COMMIT")
            holder_conn.close()
        except BaseException as exc:  # noqa: BLE001 (surface the failure to the main thread instead of a bare thread crash)
            errors.append(exc)

    def write_while_the_lock_is_held() -> None:
        """Record a failure on its own connection, once the other thread confirms it holds the lock."""
        try:
            assert lock_held.wait(timeout=5)
            writer_conn = open_catalogue(db_path)
            mark_queued(writer_conn, ["a"])
            writer_conn.close()
        except BaseException as exc:  # noqa: BLE001 (surface the failure to the main thread instead of a bare thread crash)
            errors.append(exc)

    holder_thread = threading.Thread(target=hold_the_write_lock_for_a_while)
    writer_thread = threading.Thread(target=write_while_the_lock_is_held)
    holder_thread.start()
    writer_thread.start()
    holder_thread.join(timeout=15)
    writer_thread.join(timeout=15)

    assert not errors

    verify_conn = open_catalogue(db_path)
    row = typing.cast("sqlite3.Row", verify_conn.execute("SELECT last_queued FROM songs WHERE video_id = 'a'").fetchone())
    assert row["last_queued"] is not None
    verify_conn.close()
