"""Command the Sonos speaker: discover it by name, stop it, and read its transport state.

This module discovers the speaker, stops it, and reads its transport state. `harvest` reads the
queue, and `queue` fills it.

This module starts no playback. A `STOPPED` transport cannot say whether a person stopped the
speaker or the source broke. Both look the same from the network. Any rule written against that
state either fights the owner or goes silent for good. A caller decides what the speaker plays, and
this module carries out that decision alone.
"""

import logging
import time
import typing

from soco import discover

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_logger = logging.getLogger(__name__)

# How many times `find_speaker` asks the network. Discovery is SSDP, which is multicast UDP, and one
# round misses a speaker that is awake and reachable.
_DISCOVERY_ATTEMPTS = 3

# How long `find_speaker` waits between two rounds.
_DISCOVERY_PAUSE_SECONDS = 3.0


class _Speaker(typing.Protocol):
    """The `soco.SoCo` surface this module calls. A test fake implements only this."""

    player_name: str

    def stop(self) -> None:
        """Stop playback, as `soco.SoCo.stop` does."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs

    def get_current_transport_info(self) -> dict[str, str]:
        """Return transport info, as `soco.SoCo.get_current_transport_info` does."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs


def _default_discover() -> Iterable[_Speaker]:
    """Call the real `soco.discover` and turn its `None` (nothing found) into an empty set."""
    return discover() or set()


def find_speaker(
    name: str,
    *,
    discover_fn: Callable[[], Iterable[_Speaker]] = _default_discover,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> _Speaker:
    """Discover Sonos speakers on the network and return the one named `name`.

    Asks the network `_DISCOVERY_ATTEMPTS` times while it finds nothing at all. A round that
    answers with any speaker ends the search, because a wrong name is not a network fault.

    Raises `RuntimeError` naming every speaker discovery found, sorted, when none of them is named
    `name`. Names "none" when discovery finds no speaker at all, so a person reading the error
    knows whether to check the speaker's name or the network.
    """
    speakers: list[_Speaker] = []
    for attempt in range(1, _DISCOVERY_ATTEMPTS + 1):
        speakers = list(discover_fn())
        if speakers:
            break
        if attempt < _DISCOVERY_ATTEMPTS:
            _logger.warning("discovery found no speaker. Looking again in %.0f seconds", _DISCOVERY_PAUSE_SECONDS)
            sleep_fn(_DISCOVERY_PAUSE_SECONDS)

    for speaker in speakers:
        if speaker.player_name == name:
            return speaker
    found = ", ".join(sorted(speaker.player_name for speaker in speakers)) or "none"
    message = f"no speaker named {name!r} found. Discovery found: {found}"
    raise RuntimeError(message)


def stop(speaker: _Speaker) -> None:
    """Stop playback on `speaker`."""
    speaker.stop()


def transport_state(speaker: _Speaker) -> str:
    """Return the speaker's current transport state: PLAYING, TRANSITIONING, PAUSED_PLAYBACK, or STOPPED."""
    return speaker.get_current_transport_info()["current_transport_state"]
