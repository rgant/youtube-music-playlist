"""Tests for youtube_music_playlist.control.

No test reaches the network: `_FakeSpeaker` stands in for `soco.SoCo`, and `find_speaker`'s tests
inject a fake `discover_fn` instead of calling the real `soco.discover`. The autouse
`_block_real_discovery` fixture in `tests/unit/conftest.py` fails any unit test that forgets to do
that. Without it, such a test blocks on real multicast discovery for five seconds.
"""

import dataclasses

import pytest

from testdoubles import QueuePage
from youtube_music_playlist.control import _DISCOVERY_ATTEMPTS, find_speaker, queue_depth, stop, transport_state


@dataclasses.dataclass
class _FakeSpeaker:
    """A `soco.SoCo` double, recording every `stop` call and every queue page asked for."""

    player_name: str = "Kitchen"
    state: str = "PLAYING"
    stop_calls: int = 0
    queued: int = 0
    pages_asked: list[tuple[int, int]] = dataclasses.field(default_factory=list)

    def stop(self) -> None:
        """Record the call."""
        self.stop_calls += 1

    def get_current_transport_info(self) -> dict[str, str]:
        """Return the fixed `state`."""
        return {"current_transport_state": self.state}

    def get_queue(self, start: int = 0, max_items: int = 100) -> QueuePage:
        """Record the page asked for, and report `queued` as the depth of the whole queue."""
        self.pages_asked.append((start, max_items))
        return QueuePage(total_matches=self.queued)


def test_stop_calls_stop() -> None:
    """`_stop` logs "the speaker %s stopped" after this call returns.

    If the call never reaches the speaker, playback continues and the log still reports a stop.
    """
    speaker = _FakeSpeaker()

    stop(speaker)

    assert speaker.stop_calls == 1


def test_transport_state_reads_the_current_state() -> None:
    """`status` logs this string for me. A wrong key raises `KeyError`.

    `_EXPECTED_FAILURES` does not name `KeyError`, so `main` prints a traceback.
    """
    speaker = _FakeSpeaker(state="TRANSITIONING")

    assert transport_state(speaker) == "TRANSITIONING"


def test_queue_depth_reports_the_whole_queue_and_not_the_page() -> None:
    """A power cut empties the Sonos queue, and nothing else reports that.

    `status` prints this number, so I see an empty queue without a guess.
    """
    speaker = _FakeSpeaker(queued=500)

    assert queue_depth(speaker) == 500


def test_queue_depth_asks_the_speaker_for_one_item() -> None:
    """The speaker reports the whole count beside any page.

    A larger page reads 500 tracks off the network for one number.
    """
    speaker = _FakeSpeaker(queued=500)

    _ = queue_depth(speaker)

    assert speaker.pages_asked == [(0, 1)]


def test_queue_depth_reports_zero_for_an_empty_queue() -> None:
    """`queue --if-empty` acts on this value. A non-zero answer here leaves the speaker silent."""
    assert queue_depth(_FakeSpeaker()) == 0


def test_find_speaker_returns_the_speaker_with_the_matching_name() -> None:
    """A house holds more than one Sonos room.

    A match on the wrong `player_name` sends `queue`, `harvest`, and `stop` to a room I did not
    name.
    """
    kitchen = _FakeSpeaker(player_name="Kitchen")
    office = _FakeSpeaker(player_name="Office")

    found = find_speaker("Office", discover_fn=lambda: [kitchen, office])

    assert found is office


def test_find_speaker_discovers_when_the_caller_passes_no_discover_fn(monkeypatch: pytest.MonkeyPatch) -> None:
    """`__main__` calls `find_speaker` with the name alone.

    `stop`, `status`, `harvest`, and `queue` therefore all run through the default `discover_fn`.
    """
    speaker = _FakeSpeaker(player_name="Kitchen")
    # `find_speaker` bound `_default_discover` as its default argument at import time, so the seam a
    # test can move is `discover` itself. The autouse fixture patches the same name.
    monkeypatch.setattr("youtube_music_playlist.control.discover", lambda: [speaker])

    assert find_speaker("Kitchen") is speaker


def test_find_speaker_treats_no_discovery_result_as_an_empty_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """`soco.discover` returns `None` when it finds nothing.

    Without `_default_discover`, `list(None)` raises `TypeError`, and `status` prints a traceback in
    place of the speaker fault.
    """
    monkeypatch.setattr("youtube_music_playlist.control.discover", lambda: None)

    with pytest.raises(RuntimeError, match="none"):
        _ = find_speaker("Kitchen", sleep_fn=lambda _seconds: None)


def test_find_speaker_names_the_alternatives_when_it_fails() -> None:
    """I set the speaker name with `YTM_RADIO_SPEAKER_NAME`.

    The list of discovered names gives me the exact spelling to set, so a rename or a typo
    takes one correction.
    """
    kitchen = _FakeSpeaker(player_name="Kitchen")
    office = _FakeSpeaker(player_name="Office")

    with pytest.raises(RuntimeError, match=r"Kitchen.*Office|Office.*Kitchen"):
        _ = find_speaker("Bedroom", discover_fn=lambda: [kitchen, office])


def test_find_speaker_names_none_when_discovery_finds_nothing() -> None:
    """A wrong name and a dead network each stop the command.

    "none" tells me which one happened, so I check the network and not the spelling.
    """
    with pytest.raises(RuntimeError, match="none"):
        _ = find_speaker("Kitchen", discover_fn=list, sleep_fn=lambda _seconds: None)


def test_find_speaker_looks_again_when_discovery_finds_nothing() -> None:
    """SSDP is multicast, and one round misses a speaker that is on the network and awake."""
    kitchen = _FakeSpeaker(player_name="Kitchen")
    rounds: list[list[_FakeSpeaker]] = [[], [], [kitchen]]

    found = find_speaker("Kitchen", discover_fn=lambda: rounds.pop(0), sleep_fn=lambda _seconds: None)

    assert found is kitchen


def test_find_speaker_gives_up_after_the_last_attempt() -> None:
    """A speaker that is off never answers, and the message must arrive rather than a long wait."""
    attempts = 0

    def _empty() -> list[_FakeSpeaker]:
        """Find no speaker, and count the round."""
        nonlocal attempts
        attempts += 1
        return []

    with pytest.raises(RuntimeError, match="none"):
        _ = find_speaker("Kitchen", discover_fn=_empty, sleep_fn=lambda _seconds: None)

    assert attempts == _DISCOVERY_ATTEMPTS


def test_find_speaker_does_not_look_again_when_another_speaker_answers() -> None:
    """A wrong name is not a network fault, so a second round finds the same speakers and wastes time."""
    rounds = 0

    def _office() -> list[_FakeSpeaker]:
        """Find one speaker that carries the wrong name."""
        nonlocal rounds
        rounds += 1
        return [_FakeSpeaker(player_name="Office")]

    with pytest.raises(RuntimeError, match="Office"):
        _ = find_speaker("Kitchen", discover_fn=_office, sleep_fn=lambda _seconds: None)

    assert rounds == 1
