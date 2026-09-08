"""Build a Sonos queue from the catalogue, with no recent repeat.

Every song the queue can hold carries a `sonos_uri`, which a harvest wrote. This module never talks
to YouTube. It reads the catalogue and it commands the speaker, so a queue costs no Google request
at all.

`last_queued` is what keeps a song out of the next queue. The window is a setting, so the owner
trades variety against repetition without a code change.
"""

import logging
import typing
import urllib.parse

from soco.data_structures import DidlMusicTrack, DidlResource

from youtube_music_library_radio.catalogue import queueable_songs

if typing.TYPE_CHECKING:
    import random
    import sqlite3
    from collections.abc import Sequence

    from youtube_music_library_radio.catalogue import Song

_logger = logging.getLogger(__name__)

# Sonos names a music service by a type number, and a service URI carries the shorter `sid`. The
# type is the `sid` times this factor, plus this offset. YouTube Music is sid 284, so type 72711.
_SERVICE_TYPE_FACTOR = 256
_SERVICE_TYPE_OFFSET = 7

# What a Sonos item id carries in front of the track id of a music service track.
_TRACK_ID_PREFIX = "10032020"


class NotEnoughSongsError(RuntimeError):
    """Too few songs carry a Sonos pairing to fill the queue the owner asked for.

    A short queue hides how much of the library the speaker cannot reach. The message names the
    fixes.
    """


class Speaker(typing.Protocol):
    """The `soco.SoCo` surface this module calls. A test fake implements only this."""

    def clear_queue(self) -> None:
        """Empty the queue, as `soco.SoCo.clear_queue` does."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs

    def add_to_queue(self, queueable_item: DidlMusicTrack) -> int:
        """Add one described track to the queue, as `soco.SoCo.add_to_queue` does.

        `add_uri_to_queue` sends an item with an empty title, so the Sonos app then shows the raw
        URI as the name of the track. This project builds the item itself for that reason.
        """
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs


def pick_queue(conn: sqlite3.Connection, *, size: int, window_days: int, rng: random.Random) -> tuple[Song, ...]:
    """Choose `size` songs at random from those the speaker can play and did not hold lately.

    A song qualifies when it carries a `sonos_uri` and its `last_queued` is outside `window_days`.
    A song never queued always qualifies.

    `rng` makes the choice, so a caller seeds it and replays the same set after a failed send.

    Raises `NotEnoughSongsError` when too few songs qualify. The message names the pool and the size
    asked for. The fix is a harvest of the playlists that hold the unpaired songs.
    """
    pool = queueable_songs(conn, window_days=window_days)
    if len(pool) < size:
        message = f"only {len(pool)} songs qualify and the queue needs {size}. Harvest more playlists, or lower the queue size"
        raise NotEnoughSongsError(message)

    return tuple(rng.sample(pool, size))


def _service_description(uri: str) -> str:
    """Return the content directory identifier of the music service that holds `uri`.

    Sonos refuses a track whose `desc` does not name its service. The identifier holds the service
    type, which this derives from the `sid` of the URI.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(uri).query)
    sid = int(query["sid"][0])
    service_type = sid * _SERVICE_TYPE_FACTOR + _SERVICE_TYPE_OFFSET
    return f"SA_RINCON{service_type}_X_#Svc{service_type}-0-Token"


def _described(song: Song) -> DidlMusicTrack:
    """Build the queue item for one song, with the title, artist, and album the app shows."""
    uri = typing.cast("str", song.sonos_uri)
    resource = DidlResource(uri=uri, protocol_info="x-rincon-playlist:*:*:*")
    return DidlMusicTrack(
        title=song.title,
        parent_id="0",
        item_id=f"{_TRACK_ID_PREFIX}{song.sonos_track_id}",
        creator=song.artist,
        album=song.album,
        resources=[resource],
        desc=_service_description(uri),
    )


def send_queue(speaker: Speaker, songs: Sequence[Song]) -> int:
    """Replace the speaker's queue with `songs`. Return how many tracks reached it.

    Clears the queue first. A new sample replaces what the speaker holds. An add onto a full queue
    grows it without limit.

    A song with no `sonos_uri` never reaches here, because `pick_queue` returns none.
    """
    speaker.clear_queue()
    sent = 0
    for song in songs:
        if song.sonos_uri is None:
            continue
        _ = speaker.add_to_queue(_described(song))
        sent += 1
    _logger.info("the queue now holds %d tracks", sent)
    return sent
