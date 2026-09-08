"""Fill the owner's `Everything N` YouTube playlists with every library song no playlist holds.

The playlists are the route into the Sonos queue. A song no playlist holds cannot reach the speaker.

`ytmusicapi` returns fewer songs than a playlist claims. An incomplete set
looks like "these songs are in no playlist", so a run against it adds songs the playlists already
hold.

The catalogue answers that. `catalogue.record_playlists` accumulates, so each read adds to what
earlier runs saw. `require_complete` checks that union against the count each playlist claims, and
raises only when songs remain that no run saw. A short read alone therefore costs nothing.

Read `docs/plans/2026-08-15-playlist-generator.md` for the measurement that produced these rules.
"""

import dataclasses
import logging
import re
import time
import typing

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from youtube_music_library_radio.catalogue import Song
    from youtube_music_library_radio.jsonshape import JSON
    from youtube_music_library_radio.refresh import LibraryScan

from ytmusicapi.exceptions import YTMusicError

from youtube_music_library_radio.catalogue import PlaylistMembership

_logger = logging.getLogger(__name__)

# The title this project owns. Any other playlist in the library stays untouched.
_TITLE = re.compile(r"^Everything (\d+)$")

# How many playlists to ask for. `ytmusicapi.get_library_playlists` defaults to 25, which returns
# `Liked Music` and the newest playlists alone. That default dropped `Everything 1` to
# `Everything 8` from one run, and the run then wrote 275 songs those playlists already held.
_PLAYLIST_LIST_LIMIT = 500

# How many songs one `Everything N` playlist holds. Sonos reads a playlist of this size, and the
# owner's existing playlists all use it.
PLAYLIST_SIZE = 500

# How many songs one `add_playlist_items` call carries. YouTube answered a 500-song add with
# HTTP 409 Conflict.
ADD_BATCH = 25

# How long to wait between batches. A full run sends thousands of songs. A pause keeps the run from
# arriving as one burst of writes.
_BATCH_PAUSE_SECONDS = 0.5

# How many times one batch is sent before it counts as failed, and how long to wait after a refusal.
# YouTube answers the first write to a new playlist with HTTP 409, because the playlist is not ready
# yet. The delay doubles on each attempt.
_SEND_ATTEMPTS = 4
_SEND_RETRY_SECONDS = 4.0

# How long to wait after a playlist is created, before the first write reaches it.
_CREATE_PAUSE_SECONDS = 5.0

# How many times a write is confirmed before it counts as failed, and how long to wait between
# attempts. YouTube reports a stale count right after a write. One live run raised on a write that
# worked, because the read landed before the write was visible.
_CONFIRM_ATTEMPTS = 5
_CONFIRM_DELAY_SECONDS = 3.0

_DESCRIPTION = "Every song in my YouTube Music library, in blocks of 500."


class PlaylistError(RuntimeError):
    """The playlist run cannot continue. Every subclass names the playlist and what went wrong."""


class IncompleteReadError(PlaylistError):
    """A read returned less than it claimed, so the membership set is not trustworthy.

    A write against this set adds songs the playlists already hold. A stop costs one re-run.
    """


class WriteVerificationError(PlaylistError):
    """A write reported success, and a re-read did not confirm it.

    The run stops here. A later write against unknown state makes the fault harder to undo.
    """


class PlaylistClient(typing.Protocol):
    """The `ytmusicapi.YTMusic` surface this module calls. A test fake implements only this."""

    def get_library_playlists(self, limit: int | None = ...) -> list[dict[str, JSON]]:
        """Return the playlists in the library, newest first."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs

    def get_playlist(self, playlistId: str, limit: int | None = ...) -> dict[str, JSON]:  # noqa: N803 -- mirrors ytmusicapi.YTMusic.get_playlist
        """Return the members of one playlist, with the count it claims."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs

    def create_playlist(self, title: str, description: str, privacy_status: str = ...) -> str | dict[str, object]:
        """Create an empty playlist and return its ID. A dict is an error response."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs

    def add_playlist_items(self, playlistId: str, videoIds: list[str] | None = ..., *, duplicates: bool = ...) -> str | dict[str, object]:  # noqa: N803 -- mirrors ytmusicapi.YTMusic.add_playlist_items
        """Add songs to a playlist."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs


@dataclasses.dataclass(frozen=True)
class PlannedWrite:
    """One write this run will make. `playlist_id` is None when the playlist does not exist yet."""

    playlist_id: str | None
    title: str
    ordinal: int
    video_ids: tuple[str, ...]


def _ordinal(title: str) -> int | None:
    """Return the number of an `Everything N` title, or None for any other playlist."""
    match = _TITLE.match(title)
    return int(match.group(1)) if match else None


def _members(payload: dict[str, JSON], title: str) -> tuple[int, tuple[str, ...]]:
    """Return the count `payload` claims and the video IDs it holds.

    Raises `IncompleteReadError` when the payload states no count. Without a count there is nothing
    to check the read against, and an unchecked read is the fault this module exists to prevent.
    """
    reported = payload.get("trackCount")
    if not isinstance(reported, int) or isinstance(reported, bool):
        message = f"playlist {title!r} reported no track count, so this read cannot be checked"
        raise IncompleteReadError(message)

    return reported, _video_ids(payload)


def _video_ids(payload: dict[str, JSON]) -> tuple[str, ...]:
    """Return every usable video ID of a `get_playlist` payload, in playlist order.

    An entry with no video ID names a track YouTube does not serve. It counts as absent here, which
    is what the completeness check must see.
    """
    tracks = payload.get("tracks")
    entries = tracks if isinstance(tracks, list) else []
    found: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("videoId")
        if isinstance(video_id, str) and video_id:
            found.append(video_id)
    return tuple(found)


def read_all(client: PlaylistClient) -> Iterator[PlaylistMembership]:
    """Yield each `Everything N` playlist and the members this read returned, in ordinal order.

    Yields one playlist at a time so a caller can store each result as it arrives. A read of 32
    playlists takes minutes, and YouTube times one out now and then. A caller that stores each
    playlist keeps the work of every playlist read before the fault.

    Judges nothing. A read that comes back short is normal, and `catalogue.record_playlists` folds
    it into the accumulated store. `require_complete` reads that union and decides whether the run
    knows enough to write.

    `reported_count` carries the count the playlist claims. That value stays right when the item
    fetch is short, so it is the authority on capacity.
    """
    listing = client.get_library_playlists(limit=_PLAYLIST_LIST_LIMIT)
    wanted: list[tuple[int, str, str]] = []
    for item in listing:
        title = item.get("title")
        playlist_id = item.get("playlistId")
        if not isinstance(title, str) or not isinstance(playlist_id, str):
            continue
        ordinal = _ordinal(title)
        if ordinal is not None:
            wanted.append((ordinal, title, playlist_id))
    wanted.sort()

    for ordinal, title, playlist_id in wanted:
        reported, video_ids = _members(client.get_playlist(playlist_id, limit=None), title)
        if len(video_ids) < reported:
            _logger.warning("%s claims %d songs and returned %d. The store covers the rest", title, reported, len(video_ids))
        _logger.info("read %s: %d songs", title, len(video_ids))
        yield PlaylistMembership(
            playlist_id=playlist_id,
            title=title,
            ordinal=ordinal,
            reported_count=reported,
            video_ids=video_ids,
        )


def require_complete(playlists: Sequence[PlaylistMembership]) -> None:
    """Raise `IncompleteReadError` unless the run knows every song the playlists hold.

    Takes the accumulated union, not one read. Call it after `catalogue.record_playlists` folds the
    latest read into the store.

    A playlist that knows fewer songs than it claims hides songs that no run saw, and a write
    against that set repeats them. A gap in the ordinals means neither the listing nor the store
    holds that playlist at all.

    More known songs than the playlist claims is not a fault. The union over-states after a hand
    deletion, and over-statement only makes a song wait.
    """
    for expected, playlist in enumerate(sorted(playlists, key=lambda item: item.ordinal), start=1):
        if playlist.ordinal != expected:
            message = f"nothing here holds 'Everything {expected}'. The playlist listing came back short"
            raise IncompleteReadError(message)
        known = len(playlist.video_ids)
        if known < playlist.reported_count:
            message = (
                f"{playlist.title} claims {playlist.reported_count} songs and this run knows {known}. "
                "Run it again, so the store fills the gap before anything writes"
            )
            raise IncompleteReadError(message)


def uncovered(scan: LibraryScan, playlists: Iterable[PlaylistMembership]) -> tuple[Song, ...]:
    """Return the library songs no playlist holds, in the order the library gave them.

    `scan.excluded` never reaches this result, because `library_songs` already keeps an excluded
    title out of `scan.songs`. One exclusion rule therefore serves this module and `refresh`.
    """
    held = {video_id for playlist in playlists for video_id in playlist.video_ids}
    return tuple(song for song in scan.songs if song.video_id not in held)


def plan_writes(
    playlists: Sequence[PlaylistMembership],
    songs: Sequence[Song],
    *,
    size: int = PLAYLIST_SIZE,
) -> tuple[PlannedWrite, ...]:
    """Return the writes that put `songs` into playlists. This function is pure and reaches nothing.

    Fills the free slots of an existing playlist before it names a new one, so a partial playlist
    does not stay partial forever. A new playlist continues the ordinal run.

    Names no video ID that any playlist holds, whatever `songs` carries. `uncovered` already filters
    that set. This second guard means no caller can plan a repeat, even with a `songs` list that
    holds one.
    """
    held = {video_id for playlist in playlists for video_id in playlist.video_ids}
    pending = [song.video_id for song in songs if song.video_id not in held]
    if not pending:
        return ()

    writes: list[PlannedWrite] = []
    index = 0
    for playlist in sorted(playlists, key=lambda item: item.ordinal):
        free = size - len(playlist.video_ids)
        if free <= 0 or index >= len(pending):
            continue
        take = pending[index : index + free]
        index += len(take)
        writes.append(
            PlannedWrite(
                playlist_id=playlist.playlist_id,
                title=playlist.title,
                ordinal=playlist.ordinal,
                video_ids=tuple(take),
            )
        )

    ordinal = max((playlist.ordinal for playlist in playlists), default=0)
    while index < len(pending):
        ordinal += 1
        take = pending[index : index + size]
        index += len(take)
        writes.append(PlannedWrite(playlist_id=None, title=f"Everything {ordinal}", ordinal=ordinal, video_ids=tuple(take)))
    return tuple(writes)


def plan_repeats(
    songs: Sequence[Song],
    *,
    known: Sequence[PlaylistMembership],
    size: int = PLAYLIST_SIZE,
) -> tuple[PlannedWrite, ...]:
    """Return the writes that put `songs` into new playlists. This function is pure and reaches nothing.

    `plan_writes` names no song a playlist already holds, which is right when the goal is coverage.
    This one repeats such a song on purpose. A song that carries no Sonos pairing sits in a playlist
    a harvest already read. A second harvest of that playlist recovers about 25 songs. Gathered into
    new playlists, the same songs need one queue read each.

    Never writes into an existing playlist. A harvest already ran against those, and a later write
    changes what their queue holds.
    """
    pending = [song.video_id for song in songs]
    if not pending:
        return ()

    writes: list[PlannedWrite] = []
    ordinal = max((playlist.ordinal for playlist in known), default=0)
    for index in range(0, len(pending), size):
        ordinal += 1
        take = tuple(pending[index : index + size])
        writes.append(PlannedWrite(playlist_id=None, title=f"Everything {ordinal}", ordinal=ordinal, video_ids=take))
    return tuple(writes)


def _count_playlist(client: PlaylistClient, playlist_id: str) -> int:
    """Return how many songs a playlist holds now, by reading it again."""
    return len(_video_ids(client.get_playlist(playlist_id, limit=None)))


def _new_playlist(client: PlaylistClient, title: str) -> str:
    """Create `title` and return its ID.

    `ytmusicapi.create_playlist` returns the ID as a string, and an error response as a dict. A dict
    here means the playlist does not exist, so the run must stop before it writes songs nowhere.
    """
    created = client.create_playlist(title, _DESCRIPTION, "PRIVATE")
    if not isinstance(created, str):
        message = f"creating {title!r} returned {created!r} instead of a playlist ID"
        raise WriteVerificationError(message)
    return created


@dataclasses.dataclass(frozen=True)
class Confirmation:
    """How `apply_plan` confirms a write. A test replaces it to prove the failure path with no delay.

    `count_fn` reads the length of one playlist. None reads it through the client of the run.
    """

    count_fn: Callable[[str], int] | None = None
    attempts: int = _CONFIRM_ATTEMPTS


@dataclasses.dataclass(frozen=True)
class _Target:
    """What one write must produce, and how patiently to look for it."""

    playlist_id: str
    title: str
    expected: int
    attempts: int


def _confirm(counter: Callable[[str], int], target: _Target, sleep_fn: Callable[[float], None]) -> None:
    """Read the playlist until its count reaches `expected`, or raise `WriteVerificationError`.

    YouTube reports a stale count right after a write. One read that disagrees is therefore not
    evidence of failure. A run that stops on it reports a failure for a write that worked.

    A count that never reaches `expected` is a real failure. The run stops there, so a later write
    never runs against unknown state.
    """
    actual = 0
    for attempt in range(1, target.attempts + 1):
        actual = counter(target.playlist_id)
        if actual == target.expected:
            return
        if attempt < target.attempts:
            _logger.info("%s reports %d songs and not %d. Reading it again", target.title, actual, target.expected)
            sleep_fn(_CONFIRM_DELAY_SECONDS)

    message = (
        f"{target.title} must hold {target.expected} songs after this write, and it holds {actual} "
        f"after {target.attempts} reads. Check the playlist before you run this again"
    )
    raise WriteVerificationError(message)


def _report(status: str | dict[str, object]) -> str:
    """Return the short status of a write. `ytmusicapi` returns one string or a large result dict."""
    if isinstance(status, dict):
        return str(status.get("status", "an unnamed result"))
    return status


def _send_one(
    client: PlaylistClient,
    playlist_id: str,
    batch: list[str],
    sleep_fn: Callable[[float], None],
) -> str:
    """Send one batch, and send it again after a refusal. Return the status YouTube reported.

    YouTube answers the first write to a new playlist with HTTP 409, because the playlist is not
    ready yet. One refusal is therefore not evidence that the songs cannot be written.

    Raises the `YTMusicError` of the last attempt when every attempt fails.
    """
    delay = _SEND_RETRY_SECONDS
    for attempt in range(1, _SEND_ATTEMPTS + 1):
        try:
            return _report(client.add_playlist_items(playlist_id, batch, duplicates=False))
        except YTMusicError:
            if attempt == _SEND_ATTEMPTS:
                raise
            _logger.warning("YouTube refused a batch of %d songs. Sending it again in %.0f seconds", len(batch), delay)
            sleep_fn(delay)
            delay *= 2
    raise AssertionError  # pragma: no cover -- the loop returns or raises on every path


def _send_batches(
    client: PlaylistClient,
    playlist_id: str,
    write: PlannedWrite,
    sleep_fn: Callable[[float], None],
) -> None:
    """Send one planned write to YouTube, `ADD_BATCH` songs at a time.

    YouTube answered a 500-song add with HTTP 409 Conflict, so the batch stays small. A pause
    separates the batches, so a full run does not arrive as one burst of writes.
    """
    for start in range(0, len(write.video_ids), ADD_BATCH):
        if start:
            sleep_fn(_BATCH_PAUSE_SECONDS)
        batch = list(write.video_ids[start : start + ADD_BATCH])
        status = _send_one(client, playlist_id, batch, sleep_fn)
        _logger.info("%s: sent %d of %d songs, YouTube reported %s", write.title, start + len(batch), len(write.video_ids), status)


def apply_plan(
    plan: Sequence[PlannedWrite],
    *,
    client: PlaylistClient,
    sleep_fn: Callable[[float], None] = time.sleep,
    confirm: Confirmation | None = None,
    on_write: Callable[[PlaylistMembership], None] | None = None,
) -> int:
    """Perform every write in `plan` and return how many songs reached a playlist.

    Sends each playlist's songs in batches of `ADD_BATCH`. YouTube answered a 500-song add with
    HTTP 409 Conflict. A short pause separates the batches, so a full run does not arrive as one
    burst of writes.

    Re-reads each playlist after its batches. `ytmusicapi` reports a status string, and a status is
    not evidence that the songs arrived. The re-read is.

    Reads again when the count disagrees, as many times as `confirm` allows. YouTube reports a stale
    count right after a write, so one disagreement means nothing.

    Raises `WriteVerificationError` at the first playlist whose count never rises by the number
    written. The run stops there, so a later write never runs against unknown state.

    Reports each confirmed write to `on_write`, one playlist at a time. The caller records it. This
    run creates playlists, and the read that started the run cannot hold one of them. A report after
    each playlist also keeps every finished write when a later one fails.

    `confirm` holds the read and its attempt limit, and `sleep_fn` waits between attempts. Both are
    parameters so a test proves the failure path with no delay.
    """

    def _read_count(playlist_id: str) -> int:
        """Read the current length of a playlist through the client this call was given."""
        return _count_playlist(client, playlist_id)

    rule = confirm if confirm is not None else Confirmation()
    counter: Callable[[str], int] = rule.count_fn if rule.count_fn is not None else _read_count
    written = 0
    for write in plan:
        playlist_id = write.playlist_id
        before = 0
        if playlist_id is None:
            playlist_id = _new_playlist(client, write.title)
            # A just-created playlist refuses a write. The pause costs less than the retry it saves.
            _logger.info("created %s. Waiting %.0f seconds before the first write", write.title, _CREATE_PAUSE_SECONDS)
            sleep_fn(_CREATE_PAUSE_SECONDS)
        else:
            before = counter(playlist_id)

        _send_batches(client, playlist_id, write, sleep_fn)

        expected = before + len(write.video_ids)
        _confirm(counter, _Target(playlist_id, write.title, expected, rule.attempts), sleep_fn)

        written += len(write.video_ids)
        _logger.info("wrote %d songs to %s, which now holds %d", len(write.video_ids), write.title, expected)
        if on_write is not None:
            on_write(
                PlaylistMembership(
                    playlist_id=playlist_id,
                    title=write.title,
                    ordinal=write.ordinal,
                    reported_count=expected,
                    video_ids=write.video_ids,
                )
            )
    return written
