"""Tests for youtube_music_library_radio.control.

No test reaches the network: `_FakeSpeaker` stands in for `soco.SoCo`, and `find_speaker`'s tests
inject a fake `discover_fn` instead of calling the real `soco.discover`. The autouse
`_block_real_discovery` fixture in `tests/unit/conftest.py` fails any unit test that forgets to do
that. Without it, such a test blocks on real multicast discovery for five seconds. `lan_address`
opens a real UDP socket and has no test here. Task 11 gives it a live test.
"""

import dataclasses
from pathlib import Path

import pytest

from youtube_music_library_radio.control import find_speaker, start, station_url, stop, transport_state
from youtube_music_library_radio.settings import Settings

_STATION_NAME = "My Library Radio"
_URL = "http://192.168.1.50:8900/"


@dataclasses.dataclass
class _FakeSpeaker:
    """A `soco.SoCo` double, recording every `play_uri` and `stop` call."""

    player_name: str = "Kitchen"
    ip_address: str = "192.168.1.50"
    state: str = "PLAYING"
    play_uri_calls: list[tuple[str, str, bool, bool]] = dataclasses.field(default_factory=list)
    stop_calls: int = 0

    def play_uri(
        self,
        uri: str = "",
        meta: str = "",
        title: str = "",
        # The name `start` mirrors the parameter name of the `_Speaker` protocol. See control.py.
        # pylint: disable-next=redefined-outer-name
        start: bool = True,  # noqa: FBT001, FBT002 -- matches the `_Speaker` protocol's own signature
        force_radio: bool = False,  # noqa: FBT001, FBT002 -- matches the `_Speaker` protocol's own signature
        **kwargs: object,
    ) -> bool | None:
        """Record the call, including `start` and `force_radio`, and report success."""
        del meta, kwargs
        self.play_uri_calls.append((uri, title, start, force_radio))
        return None

    def stop(self) -> None:
        """Record the call."""
        self.stop_calls += 1

    def get_current_transport_info(self) -> dict[str, str]:
        """Return the fixed `state`."""
        return {"current_transport_state": self.state}


def _settings(*, station_host: str = "", station_port: int = 8900) -> Settings:
    """Build a valid `Settings` for a test. Every field but `station_host` and `station_port` is fixed."""
    return Settings(
        database_path=Path("catalogue.sqlite3"),
        speaker_name="Kitchen",
        station_host=station_host,
        station_port=station_port,
        bitrate_kbps=128,
        no_repeat_window=2000,
        prune_threshold=3,
        metadata_interval=16000,
        station_name=_STATION_NAME,
    )


def test_station_url_uses_the_settings_host_and_port() -> None:
    """`station_url` builds the URL from `station_host` and `station_port` when `station_host` is set."""
    settings = _settings(station_host="10.0.0.5", station_port=9100)

    assert station_url(settings) == "http://10.0.0.5:9100/"


def test_station_url_uses_the_speaker_the_caller_already_found() -> None:
    """With an empty `station_host`, `station_url` reads the address of the speaker the caller passes.

    The autouse `_block_real_discovery` fixture fails this test if `station_url` discovers a speaker
    of its own. That is the point. A caller that already holds the speaker must not pay for a second
    discovery.
    """
    speaker = _FakeSpeaker(ip_address="192.168.1.50")
    settings = _settings(station_host="", station_port=9100)

    url = station_url(settings, speaker=speaker, address_fn=lambda speaker_ip: f"local-for-{speaker_ip}")

    assert url == "http://local-for-192.168.1.50:9100/"


def test_station_url_discovers_the_speaker_when_the_caller_passes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """With an empty `station_host` and no `speaker`, `station_url` discovers the speaker itself."""
    speaker = _FakeSpeaker(player_name="Kitchen", ip_address="192.168.1.77")
    # `find_speaker` bound `_default_discover` as its default argument at import time, so the seam a
    # test can move is `discover` itself. The autouse fixture patches the same name.
    monkeypatch.setattr("youtube_music_library_radio.control.discover", lambda: [speaker])
    settings = _settings(station_host="", station_port=8900)

    url = station_url(settings, address_fn=lambda speaker_ip: f"local-for-{speaker_ip}")

    assert url == "http://local-for-192.168.1.77:8900/"


def test_start_calls_play_uri_with_force_radio() -> None:
    """`start` calls `play_uri` with the station's URL and name, begins playback, and forces radio display."""
    speaker = _FakeSpeaker()

    start(speaker, _URL, _STATION_NAME)

    assert speaker.play_uri_calls == [(_URL, _STATION_NAME, True, True)]


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


def test_find_speaker_discovers_when_the_caller_passes_no_discover_fn(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no `discover_fn`, `find_speaker` calls `soco.discover` through `_default_discover`."""
    speaker = _FakeSpeaker(player_name="Kitchen")
    monkeypatch.setattr("youtube_music_library_radio.control.discover", lambda: [speaker])

    assert find_speaker("Kitchen") is speaker


def test_find_speaker_treats_no_discovery_result_as_an_empty_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """`soco.discover` returns `None` when it finds nothing. `_default_discover` turns that into an empty set."""
    monkeypatch.setattr("youtube_music_library_radio.control.discover", lambda: None)

    with pytest.raises(RuntimeError, match="none"):
        _ = find_speaker("Kitchen")
