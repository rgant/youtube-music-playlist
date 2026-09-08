"""Builders and fakes that more than one test module needs.

Two test modules that define the same fake hold two copies of one decision. The copies drift, and
pylint reports the block as duplicate code. Each item here therefore has one home.
"""

import typing

from youtube_music_library_radio.catalogue import QueueEntry

if typing.TYPE_CHECKING:
    from youtube_music_library_radio.jsonshape import JSON


class FakeLibraryClient:
    """A stand-in for `ytmusicapi.YTMusic`: `get_library_songs` returns a payload fixed at construction.

    Built with an empty payload, this fake stands for an expired browser cookie. YouTube answers an
    expired session with an empty result and no error. The fake therefore needs no failure mode of
    its own for that fault.
    """

    def __init__(self, payload: list[dict[str, JSON]]) -> None:
        """Hold the payload every read returns, and start an empty record of the limits asked for."""
        self._payload: list[dict[str, JSON]] = payload
        self.limits: list[int] = []

    def get_library_songs(self, limit: int, *, validate_responses: bool = False) -> list[dict[str, JSON]]:
        """Return the payload this fake holds, and record the limit the caller asked for."""
        _ = validate_responses
        self.limits.append(limit)
        return self._payload


def queue_entry(track_id: str, title: str, artist: str, album: str = "An Album", seconds: int = 200) -> QueueEntry:
    """Build one Sonos queue entry, as `read_queue` returns it."""
    return QueueEntry(
        track_id=track_id,
        uri=f"x-sonosapi-hls-static:{track_id}?sid=284",
        title=title,
        artist=artist,
        album=album,
        duration_seconds=seconds,
    )
