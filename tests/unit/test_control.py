"""Tests for youtube_music_library_radio.control.

No test reaches the network: `_FakeSpeaker` stands in for `soco.SoCo`, and `find_speaker`'s tests
inject a fake `discover_fn` instead of calling the real `soco.discover`. The autouse
`_block_real_discovery` fixture in `tests/unit/conftest.py` fails any unit test that forgets to do
that. Without it, such a test blocks on real multicast discovery for five seconds.
"""

import dataclasses

import pytest

from youtube_music_library_radio.control import find_speaker, stop, transport_state


@dataclasses.dataclass
class _FakeSpeaker:
    """A `soco.SoCo` double, recording every `stop` call."""

    player_name: str = "Kitchen"
    state: str = "PLAYING"
    stop_calls: int = 0

    def stop(self) -> None:
        """Record the call."""
        self.stop_calls += 1

    def get_current_transport_info(self) -> dict[str, str]:
        """Return the fixed `state`."""
        return {"current_transport_state": self.state}


def test_stop_calls_stop() -> None:
    """`stop` calls `stop` on the speaker."""
    speaker = _FakeSpeaker()

    stop(speaker)

    assert speaker.stop_calls == 1


def test_transport_state_reads_the_current_state() -> None:
    """`transport_state` returns the speaker's reported `current_transport_state`."""
    speaker = _FakeSpeaker(state="TRANSITIONING")

    assert transport_state(speaker) == "TRANSITIONING"


def test_find_speaker_returns_the_speaker_with_the_matching_name() -> None:
    """`find_speaker` returns the discovered speaker whose `player_name` matches."""
    kitchen = _FakeSpeaker(player_name="Kitchen")
    office = _FakeSpeaker(player_name="Office")

    found = find_speaker("Office", discover_fn=lambda: [kitchen, office])

    assert found is office


def test_find_speaker_discovers_when_the_caller_passes_no_discover_fn(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no `discover_fn`, `find_speaker` calls `soco.discover` through `_default_discover`."""
    speaker = _FakeSpeaker(player_name="Kitchen")
    # `find_speaker` bound `_default_discover` as its default argument at import time, so the seam a
    # test can move is `discover` itself. The autouse fixture patches the same name.
    monkeypatch.setattr("youtube_music_library_radio.control.discover", lambda: [speaker])

    assert find_speaker("Kitchen") is speaker


def test_find_speaker_treats_no_discovery_result_as_an_empty_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """`soco.discover` returns `None` when it finds nothing. `_default_discover` turns that into an empty set."""
    monkeypatch.setattr("youtube_music_library_radio.control.discover", lambda: None)

    with pytest.raises(RuntimeError, match="none"):
        _ = find_speaker("Kitchen")


def test_find_speaker_names_the_alternatives_when_it_fails() -> None:
    """If no speaker matches, `find_speaker` raises and names every speaker discovery found."""
    kitchen = _FakeSpeaker(player_name="Kitchen")
    office = _FakeSpeaker(player_name="Office")

    with pytest.raises(RuntimeError, match=r"Kitchen.*Office|Office.*Kitchen"):
        _ = find_speaker("Bedroom", discover_fn=lambda: [kitchen, office])


def test_find_speaker_names_none_when_discovery_finds_nothing() -> None:
    """If discovery finds no speaker at all, `find_speaker` raises and names "none"."""
    with pytest.raises(RuntimeError, match="none"):
        _ = find_speaker("Kitchen", discover_fn=list)
