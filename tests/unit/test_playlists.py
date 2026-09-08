"""Tests for youtube_music_library_radio.playlists.

No test reaches the network. `_FakeClient` stands in for `ytmusicapi.YTMusic`, and it records every
call, so a test can assert that a dry run wrote nothing.

`plan_writes` and `uncovered` take no client at all. They are pure, so the hard logic is tested
without a double of any kind.
"""

import typing

import pytest
from ytmusicapi.exceptions import YTMusicServerError

from youtube_music_library_radio.catalogue import PlaylistMembership, Song
from youtube_music_library_radio.playlists import (
    ADD_BATCH,
    Confirmation,
    IncompleteReadError,
    PlannedWrite,
    WriteVerificationError,
    apply_plan,
    plan_repeats,
    plan_writes,
    read_all,
    require_complete,
    uncovered,
)
from youtube_music_library_radio.refresh import ExcludedSong, LibraryScan

if typing.TYPE_CHECKING:
    from youtube_music_library_radio.jsonshape import JSON


def _song(video_id: str, title: str = "A Title") -> Song:
    """Build a never-played `Song` for a test."""
    return Song(video_id=video_id, title=title, artist="A Band")


def _membership(ordinal: int, video_ids: list[str], *, reported: int | None = None) -> PlaylistMembership:
    """Build a `PlaylistMembership` named `Everything <ordinal>`."""
    return PlaylistMembership(
        playlist_id=f"PL{ordinal}",
        title=f"Everything {ordinal}",
        ordinal=ordinal,
        reported_count=len(video_ids) if reported is None else reported,
        video_ids=tuple(video_ids),
    )


class _FakeClient:
    """A `ytmusicapi.YTMusic` double for the playlist methods this module calls.

    `library` maps a title to the video IDs the playlist holds. `reported` overrides the count the
    playlist claims, so a test can build a short read. `created` and `added` record every write.
    """

    def __init__(
        self,
        library: dict[str, list[str]] | None = None,
        *,
        reported: dict[str, int] | None = None,
        titles: list[str] | None = None,
    ) -> None:
        self.library: dict[str, list[str]] = library or {}
        self.reported: dict[str, int] = reported or {}
        self.titles: list[str] = titles if titles is not None else list(self.library)
        self.list_limits: list[int | None] = []
        self.created: list[str] = []
        self.added: list[tuple[str, tuple[str, ...]]] = []

    def get_library_playlists(self, limit: int | None = 25) -> list[dict[str, JSON]]:
        """Return one entry per title, newest first, as the live API does."""
        self.list_limits.append(limit)
        return [{"title": title, "playlistId": f"PL{title.split()[-1]}"} for title in reversed(self.titles)]

    def get_playlist(self, playlistId: str, limit: int | None = 100) -> dict[str, JSON]:  # noqa: N803 -- mirrors ytmusicapi
        """Return the members of `playlistId`, with the count the playlist claims."""
        del limit
        title = next(name for name in self.titles if f"PL{name.split()[-1]}" == playlistId)
        members = self.library.get(title, [])
        return {
            "trackCount": self.reported.get(title, len(members)),
            "tracks": [{"videoId": video_id, "setVideoId": f"set-{video_id}"} for video_id in members],
        }

    def create_playlist(self, title: str, description: str, privacy_status: str = "PRIVATE") -> str | dict[str, object]:
        """Record the creation and return the new playlist ID."""
        del description, privacy_status
        self.created.append(title)
        self.titles.append(title)
        self.library[title] = []
        return f"PL{title.rsplit(maxsplit=1)[-1]}"

    def add_playlist_items(
        self,
        playlistId: str,  # noqa: N803 -- mirrors ytmusicapi.YTMusic.add_playlist_items
        videoIds: list[str] | None = None,  # noqa: N803 -- mirrors ytmusicapi.YTMusic.add_playlist_items
        *,
        duplicates: bool = False,
    ) -> str | dict[str, object]:
        """Record the addition and put the songs in the fake playlist."""
        del duplicates
        given = videoIds or []
        title = next(name for name in self.titles if f"PL{name.split()[-1]}" == playlistId)
        self.added.append((playlistId, tuple(given)))
        self.library[title].extend(given)
        return "STATUS_SUCCEEDED"


def test_read_all_returns_the_playlists_in_ordinal_order() -> None:
    """`read_all` sorts by ordinal, because the live API returns the newest playlist first."""
    client = _FakeClient({"Everything 1": ["a"], "Everything 2": ["b"], "Everything 3": ["c"]})

    found = tuple(read_all(client))

    assert [item.ordinal for item in found] == [1, 2, 3]
    assert found[0].video_ids == ("a",)


def test_read_all_asks_for_more_playlists_than_the_ytmusicapi_default() -> None:
    """`get_library_playlists` defaults to 25, which drops the oldest playlists. That default caused the live defect."""
    client = _FakeClient({"Everything 1": ["a"]})

    _ = tuple(read_all(client))

    assert client.list_limits
    assert all(limit is not None and limit > 25 for limit in client.list_limits)


def test_read_all_reports_a_short_read_without_raising() -> None:
    """`read_all` returns what it got and judges nothing. The store decides whether the run knows enough."""
    client = _FakeClient({"Everything 1": ["a", "b"]}, reported={"Everything 1": 500})

    found = tuple(read_all(client))

    assert found[0].reported_count == 500
    assert found[0].video_ids == ("a", "b")


def test_require_complete_accepts_a_playlist_the_store_covers() -> None:
    """A short read costs nothing when an earlier run already saw every song of that playlist."""
    require_complete([_membership(1, [f"v{index}" for index in range(500)], reported=500)])


def test_require_complete_refuses_a_playlist_the_store_does_not_cover() -> None:
    """Known songs below the claimed count means songs exist that no run has ever seen."""
    with pytest.raises(IncompleteReadError, match="Everything 1"):
        require_complete([_membership(1, ["a", "b"], reported=500)])


def test_require_complete_refuses_a_gap_in_the_ordinals() -> None:
    """A gap means neither the listing nor the store holds that playlist, so its songs are invisible."""
    with pytest.raises(IncompleteReadError, match="2"):
        require_complete([_membership(1, ["a"]), _membership(3, ["c"])])


def test_require_complete_accepts_more_songs_than_the_playlist_claims() -> None:
    """The union over-states after a hand deletion. Over-statement is safe, so it must not raise."""
    require_complete([_membership(1, ["a", "b", "c"], reported=2)])


def test_read_all_ignores_a_playlist_with_another_name() -> None:
    """`Liked Music` and `Episodes for Later` are not `Everything N`, and they hold no library backlog."""
    client = _FakeClient({"Everything 1": ["a"]}, titles=["Everything 1", "Liked Music"])
    client.library["Liked Music"] = ["x"]

    found = tuple(read_all(client))

    assert [item.title for item in found] == ["Everything 1"]


def test_uncovered_returns_a_song_no_playlist_holds() -> None:
    """The uncovered set is the library minus every playlist, and it drives every write."""
    scan = LibraryScan(songs=(_song("held"), _song("free")), excluded=())

    found = uncovered(scan, [_membership(1, ["held"])])

    assert [song.video_id for song in found] == ["free"]


def test_uncovered_omits_an_excluded_song() -> None:
    """`library_songs` puts a `radio edit` title in `excluded`, so no playlist can ever receive it."""
    scan = LibraryScan(
        songs=(_song("keep"),),
        excluded=(ExcludedSong(video_id="drop", title="Song (Radio Edit)", artist="A Band", pattern="radio edit"),),
    )

    found = uncovered(scan, [])

    assert [song.video_id for song in found] == ["keep"]


def test_plan_writes_fills_a_partial_playlist_before_it_names_a_new_one() -> None:
    """A playlist with free slots takes songs first. A new playlist costs a create call."""
    playlists = [_membership(1, ["a", "b"])]

    plan = plan_writes(playlists, [_song("c"), _song("d")], size=3)

    assert plan == (
        PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=("c",)),
        PlannedWrite(playlist_id=None, title="Everything 2", ordinal=2, video_ids=("d",)),
    )


def test_plan_writes_continues_the_ordinal_run() -> None:
    """A new playlist follows the highest ordinal, so the names stay in one unbroken sequence."""
    plan = plan_writes([_membership(1, ["a"]), _membership(2, ["b"])], [_song("c"), _song("d")], size=1)

    assert [write.title for write in plan] == ["Everything 3", "Everything 4"]


def test_plan_writes_never_names_a_song_a_playlist_already_holds() -> None:
    """A repeated song is the defect this project measures. The plan must not be able to express it."""
    plan = plan_writes([_membership(1, ["dup"])], [_song("dup"), _song("new")], size=500)

    planned = [video_id for write in plan for video_id in write.video_ids]
    assert planned == ["new"]


def test_plan_writes_returns_nothing_when_every_song_is_covered() -> None:
    """A second run must plan no write. That is how the owner knows the first run finished."""
    assert not plan_writes([_membership(1, ["a"])], [_song("a")], size=500)


def test_apply_adds_to_an_existing_playlist() -> None:
    """A write against a playlist that exists costs one add call and no create call."""
    client = _FakeClient({"Everything 1": ["a"]})
    plan = (PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=("b",)),)

    written = apply_plan(plan, client=client, sleep_fn=lambda _seconds: None)

    assert written == 1
    assert not client.created
    assert client.added == [("PL1", ("b",))]


def test_apply_creates_a_new_playlist_then_adds_to_it() -> None:
    """A planned write with no playlist ID creates the playlist first, then fills it."""
    client = _FakeClient({"Everything 1": ["a"]})
    plan = (PlannedWrite(playlist_id=None, title="Everything 2", ordinal=2, video_ids=("b", "c")),)

    written = apply_plan(plan, client=client, sleep_fn=lambda _seconds: None)

    assert written == 2
    assert client.created == ["Everything 2"]
    assert client.added == [("PL2", ("b", "c"))]


def test_apply_reports_each_finished_write() -> None:
    """The store learns a new playlist from this report. A read before the run cannot hold it."""
    client = _FakeClient({"Everything 1": ["a"]})
    plan = (PlannedWrite(playlist_id=None, title="Everything 2", ordinal=2, video_ids=("b", "c")),)
    reported: list[PlaylistMembership] = []

    _ = apply_plan(plan, client=client, sleep_fn=lambda _seconds: None, on_write=reported.append)

    assert reported == [PlaylistMembership(playlist_id="PL2", title="Everything 2", ordinal=2, reported_count=2, video_ids=("b", "c"))]


def test_a_report_counts_the_songs_the_playlist_already_held() -> None:
    """`reported_count` is the length of the playlist, so `require_complete` reads it against the union."""
    client = _FakeClient({"Everything 1": ["a", "b"]})
    plan = (PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=("c",)),)
    reported: list[PlaylistMembership] = []

    _ = apply_plan(plan, client=client, sleep_fn=lambda _seconds: None, on_write=reported.append)

    assert [(item.reported_count, item.video_ids) for item in reported] == [(3, ("c",))]


def test_an_unconfirmed_write_is_never_reported() -> None:
    """A report the re-read did not confirm teaches the store a playlist state that does not exist."""
    client = _FakeClient({"Everything 1": ["a"]})
    plan = (PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=("b",)),)
    reported: list[PlaylistMembership] = []

    with pytest.raises(WriteVerificationError):
        _ = apply_plan(
            plan,
            client=client,
            sleep_fn=lambda _seconds: None,
            confirm=Confirmation(count_fn=lambda _playlist_id: 1, attempts=2),
            on_write=reported.append,
        )

    assert not reported


def test_apply_refuses_a_write_the_re_read_does_not_confirm() -> None:
    """A write that reports success and moves no count is the failure mode this project must catch."""
    client = _FakeClient({"Everything 1": ["a"]})

    def _lies(_playlist_id: str) -> int:
        return 1

    plan = (PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=("b",)),)

    with pytest.raises(WriteVerificationError, match="Everything 1"):
        _ = apply_plan(plan, client=client, sleep_fn=lambda _seconds: None, confirm=Confirmation(count_fn=_lies, attempts=2))


def test_apply_stops_at_the_first_failed_verification() -> None:
    """A bad write stops the run. A later write against unknown state makes the fault harder to undo."""
    client = _FakeClient({"Everything 1": ["a"], "Everything 2": ["b"]})
    plan = (
        PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=("x",)),
        PlannedWrite(playlist_id="PL2", title="Everything 2", ordinal=2, video_ids=("y",)),
    )

    with pytest.raises(WriteVerificationError):
        _ = apply_plan(
            plan,
            client=client,
            sleep_fn=lambda _seconds: None,
            confirm=Confirmation(count_fn=lambda _playlist_id: 0, attempts=2),
        )

    assert [call[0] for call in client.added] == ["PL1"]


def test_a_write_is_confirmed_once_the_count_catches_up() -> None:
    """YouTube reports a stale count right after a write, so one read is not evidence of failure.

    The first live run raised on a write that succeeded. The playlist held 500 songs a
    moment later. A single read therefore cannot decide.
    """
    client = _FakeClient({"Everything 1": ["a"]})
    counts = iter([1, 1, 2])  # the count lags twice, then catches up
    waits: list[float] = []
    plan = (PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=("b",)),)

    written = apply_plan(
        plan,
        client=client,
        sleep_fn=waits.append,
        confirm=Confirmation(count_fn=lambda _playlist_id: next(counts)),
    )

    assert written == 1
    assert len(waits) == 1  # one read before the write, one retry after it


def test_a_write_that_never_confirms_still_raises() -> None:
    """A count that never moves is a real failure, and the run must stop before the next write."""
    client = _FakeClient({"Everything 1": ["a"]})
    waits: list[float] = []
    plan = (PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=("b",)),)

    with pytest.raises(WriteVerificationError, match="Everything 1"):
        _ = apply_plan(
            plan,
            client=client,
            sleep_fn=waits.append,
            confirm=Confirmation(count_fn=lambda _playlist_id: 1, attempts=3),
        )

    assert len(waits) == 2  # it waits between attempts, and not after the last one


def test_a_confirmed_write_never_waits() -> None:
    """A count that is right at once must cost no delay. Ten writes must not add ten pauses."""
    client = _FakeClient({"Everything 1": ["a"]})
    waits: list[float] = []
    plan = (PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=("b",)),)

    _ = apply_plan(plan, client=client, sleep_fn=waits.append)

    assert not waits


def test_a_large_write_is_sent_in_batches() -> None:
    """YouTube answered a 500-song add with HTTP 409. It accepts a small batch.

    A batch of 25 songs is the size YouTube accepts. A larger batch draws the 409.
    """
    client = _FakeClient({"Everything 1": []})
    songs = tuple(f"v{index}" for index in range(120))
    plan = (PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=songs),)

    waits: list[float] = []
    written = apply_plan(plan, client=client, sleep_fn=waits.append)

    assert written == 120
    assert all(len(sent) <= ADD_BATCH for _playlist_id, sent in client.added)
    assert len(waits) == len(client.added) - 1  # one pause between batches, none before the first
    assert tuple(video_id for _playlist_id, sent in client.added for video_id in sent) == songs


def test_every_batch_reaches_one_playlist() -> None:
    """A batched write must not spill into another playlist, whatever the batching does."""
    client = _FakeClient({"Everything 1": [], "Everything 2": []})
    plan = (
        PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=tuple(f"a{i}" for i in range(30))),
        PlannedWrite(playlist_id="PL2", title="Everything 2", ordinal=2, video_ids=tuple(f"b{i}" for i in range(30))),
    )

    _ = apply_plan(plan, client=client, sleep_fn=lambda _seconds: None)

    assert all(video_id.startswith("a") for pid, sent in client.added if pid == "PL1" for video_id in sent)
    assert all(video_id.startswith("b") for pid, sent in client.added if pid == "PL2" for video_id in sent)


class _FlakyClient(_FakeClient):
    """A `_FakeClient` whose first `fail_times` add calls raise the 409 YouTube returns."""

    def __init__(self, library: dict[str, list[str]], *, fail_times: int) -> None:
        super().__init__(library)
        self.failures: int = 0
        self._fail_times: int = fail_times

    @typing.override
    def add_playlist_items(
        self,
        playlistId: str,
        videoIds: list[str] | None = None,
        *,
        duplicates: bool = False,
    ) -> str | dict[str, object]:
        """Raise for the first `fail_times` calls, then behave normally."""
        if self.failures < self._fail_times:
            self.failures += 1
            message = "Server returned HTTP 409: Conflict."
            raise YTMusicServerError(message)
        return super().add_playlist_items(playlistId, videoIds, duplicates=duplicates)


def test_a_batch_that_fails_once_is_sent_again() -> None:
    """YouTube answers the first write to a new playlist with 409. The playlist is not ready yet.

    One 409 is not evidence that the songs cannot be written. A retry after a pause writes them.
    """
    client = _FlakyClient({"Everything 1": []}, fail_times=1)
    plan = (PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=("a", "b")),)

    written = apply_plan(plan, client=client, sleep_fn=lambda _seconds: None)

    assert written == 2
    assert client.failures == 1
    assert client.added == [("PL1", ("a", "b"))]


def test_a_batch_that_always_fails_raises() -> None:
    """A write that never lands is a real failure, and the run must stop before the next playlist."""
    client = _FlakyClient({"Everything 1": []}, fail_times=99)
    plan = (PlannedWrite(playlist_id="PL1", title="Everything 1", ordinal=1, video_ids=("a",)),)

    with pytest.raises(YTMusicServerError):
        _ = apply_plan(plan, client=client, sleep_fn=lambda _seconds: None)

    assert not client.added


def test_a_new_playlist_waits_before_its_first_write() -> None:
    """A new playlist rejects the first write. The pause is cheaper than the retry it saves."""
    client = _FakeClient({"Everything 1": ["a"]})
    waits: list[float] = []
    plan = (PlannedWrite(playlist_id=None, title="Everything 2", ordinal=2, video_ids=("b",)),)

    _ = apply_plan(plan, client=client, sleep_fn=waits.append)

    assert waits  # one pause after the create, before the first batch


def test_plan_repeats_puts_a_held_song_in_a_new_playlist() -> None:
    """`plan_writes` refuses a repeat. A song that carries no Sonos pairing needs one anyway."""
    held = _membership(1, ["a", "b"])

    plan = plan_repeats([_song("a")], known=[held], size=500)

    assert plan == (PlannedWrite(playlist_id=None, title="Everything 2", ordinal=2, video_ids=("a",)),)


def test_plan_repeats_splits_at_the_playlist_size() -> None:
    """Sonos reads a playlist of this size, and one queue holds one playlist."""
    songs = [_song(f"v{index}") for index in range(3)]

    plan = plan_repeats(songs, known=[], size=2)

    assert [(write.title, write.video_ids) for write in plan] == [
        ("Everything 1", ("v0", "v1")),
        ("Everything 2", ("v2",)),
    ]


def test_plan_repeats_never_fills_an_existing_playlist() -> None:
    """A harvest already ran against that playlist, and a later write changes what its queue holds."""
    held = _membership(1, ["a"])

    plan = plan_repeats([_song("b")], known=[held], size=500)

    assert [write.playlist_id for write in plan] == [None]


def test_plan_repeats_plans_nothing_for_no_songs() -> None:
    """Every song carries a pairing, so no playlist is needed."""
    assert not plan_repeats([], known=[], size=500)
