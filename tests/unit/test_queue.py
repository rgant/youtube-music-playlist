"""Tests for youtube_music_library_radio.queue.

`pick_queue` reads a real temporary catalogue and takes a seeded `random.Random`, so its choice is
repeatable and its tests need no double. `send_queue` runs against a fake speaker that records every
call, so no test reaches the network.
"""

import random
import typing
from datetime import UTC, datetime, timedelta

import pytest

from youtube_music_library_radio.catalogue import Song, mark_queued, merge_songs, pair_sonos_track
from youtube_music_library_radio.queue import NotEnoughSongsError, pick_queue, send_queue

if typing.TYPE_CHECKING:
    import sqlite3

    from soco.data_structures import DidlMusicTrack


def _paired(conn: sqlite3.Connection, count: int, *, start: int = 0) -> None:
    """Put `count` songs in the catalogue, each with a Sonos pairing."""
    songs = [Song(video_id=f"V{index}", title=f"Song {index}", artist="A Band") for index in range(start, start + count)]
    _ = merge_songs(conn, songs)
    for song in songs:
        track_id = f"S{song.video_id}"
        _ = pair_sonos_track(conn, song.video_id, track_id=track_id, uri=f"x-sonosapi-hls-static:{track_id}?sid=284&flags=0&sn=7")


def test_pick_queue_returns_only_paired_songs(conn: sqlite3.Connection) -> None:
    """A song with no Sonos URI cannot reach the speaker, so it must never enter a queue."""
    _paired(conn, 2)
    _ = merge_songs(conn, [Song(video_id="UNPAIRED", title="Song", artist="A Band")])

    picked = pick_queue(conn, size=2, window_days=7, rng=random.Random(1))

    assert {song.video_id for song in picked} == {"V0", "V1"}


def test_pick_queue_omits_a_song_queued_inside_the_window(conn: sqlite3.Connection) -> None:
    """No recent repeat is the point. A song sent two days ago stays out of a seven-day window."""
    _paired(conn, 3)
    mark_queued(conn, ["V0"], when=datetime.now(UTC) - timedelta(days=2))

    picked = pick_queue(conn, size=2, window_days=7, rng=random.Random(1))

    assert "V0" not in {song.video_id for song in picked}


def test_pick_queue_takes_a_song_queued_before_the_window(conn: sqlite3.Connection) -> None:
    """A song outside the window is due again, so the pool does not shrink forever."""
    _paired(conn, 1)
    mark_queued(conn, ["V0"], when=datetime.now(UTC) - timedelta(days=30))

    picked = pick_queue(conn, size=1, window_days=7, rng=random.Random(1))

    assert [song.video_id for song in picked] == ["V0"]


def test_pick_queue_returns_the_size_asked_for(conn: sqlite3.Connection) -> None:
    """The owner asks for 500 songs, and a short queue is a silent failure of the whole feature."""
    _paired(conn, 50)

    picked = pick_queue(conn, size=20, window_days=7, rng=random.Random(1))

    assert len(picked) == 20
    assert len({song.video_id for song in picked}) == 20


def test_pick_queue_raises_when_too_few_songs_qualify(conn: sqlite3.Connection) -> None:
    """A queue shorter than asked for means the harvest is incomplete. The message names each number."""
    _paired(conn, 3)

    with pytest.raises(NotEnoughSongsError, match=r"3 .*10"):
        _ = pick_queue(conn, size=10, window_days=7, rng=random.Random(1))


def test_two_seeds_choose_different_songs(conn: sqlite3.Connection) -> None:
    """The queue is semi-random, so two runs over one pool must not give the same 500 songs."""
    _paired(conn, 100)

    first = [song.video_id for song in pick_queue(conn, size=10, window_days=7, rng=random.Random(1))]
    second = [song.video_id for song in pick_queue(conn, size=10, window_days=7, rng=random.Random(2))]

    assert first != second


def test_one_seed_chooses_the_same_songs(conn: sqlite3.Connection) -> None:
    """The choice is repeatable, so a failed send can be replayed against the same set."""
    _paired(conn, 100)

    first = [song.video_id for song in pick_queue(conn, size=10, window_days=7, rng=random.Random(7))]
    second = [song.video_id for song in pick_queue(conn, size=10, window_days=7, rng=random.Random(7))]

    assert first == second


class _FakeSpeaker:
    """A `soco.SoCo` double that records the queue calls, and can fail on demand."""

    def __init__(self, *, fail_at: int | None = None) -> None:
        self.cleared: int = 0
        self.added: list[str] = []
        self.items: list[DidlMusicTrack] = []
        self._fail_at: int | None = fail_at

    def clear_queue(self) -> None:
        """Record the clear."""
        self.cleared += 1

    def add_to_queue(self, queueable_item: DidlMusicTrack) -> int:
        """Record the addition, or raise once the failure point is reached."""
        if self._fail_at is not None and len(self.added) >= self._fail_at:
            message = "the speaker refused the track"
            raise OSError(message)
        self.items.append(queueable_item)
        self.added.append(queueable_item.resources[0].uri)
        return len(self.added)


def test_send_queue_clears_then_adds_every_track(conn: sqlite3.Connection) -> None:
    """A new sample replaces what the speaker holds. An add onto a full queue grows it without limit."""
    _paired(conn, 3)
    songs = pick_queue(conn, size=3, window_days=7, rng=random.Random(1))
    speaker = _FakeSpeaker()

    sent = send_queue(speaker, songs)

    assert sent == 3
    assert speaker.cleared == 1
    assert len(speaker.added) == 3


def test_mark_queued_records_the_time(conn: sqlite3.Connection) -> None:
    """`last_queued` is what keeps the next run from repeating these songs."""
    _paired(conn, 2)

    mark_queued(conn, ["V0"])

    picked = pick_queue(conn, size=1, window_days=7, rng=random.Random(1))
    assert [song.video_id for song in picked] == ["V1"]


def _described(conn: sqlite3.Connection) -> Song:
    """Put one song with a full title, artist, album, and a service URI in the catalogue."""
    song = Song(video_id="V1", title="A Song", artist="A Band", album="An Album")
    _ = merge_songs(conn, [song])
    _ = pair_sonos_track(conn, "V1", track_id="S1", uri="x-sonosapi-hls-static:S1?sid=284&flags=0&sn=7")
    return song


def test_the_speaker_receives_the_title_artist_and_album(conn: sqlite3.Connection) -> None:
    """Without metadata the Sonos app shows the raw URI as the name of every track."""
    _ = _described(conn)
    speaker = _FakeSpeaker()

    _ = send_queue(speaker, pick_queue(conn, size=1, window_days=7, rng=random.Random(1)))

    item = speaker.items[0]
    assert (item.title, item.creator, item.album) == ("A Song", "A Band", "An Album")
    assert item.resources[0].uri == "x-sonosapi-hls-static:S1?sid=284&flags=0&sn=7"


def test_the_item_names_the_music_service_of_the_uri(conn: sqlite3.Connection) -> None:
    """Sonos rejects the track when `desc` does not name the service that holds it.

    The service type is the `sid` of the URI times 256, plus 7. YouTube Music is sid 284, so 72711.
    """
    _ = _described(conn)
    speaker = _FakeSpeaker()

    _ = send_queue(speaker, pick_queue(conn, size=1, window_days=7, rng=random.Random(1)))

    assert speaker.items[0].desc == "SA_RINCON72711_X_#Svc72711-0-Token"
