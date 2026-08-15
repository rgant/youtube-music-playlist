"""Command the Sonos speaker: discover it, play the station on it, stop it, and read its state.

This module is the only part of the project that talks to the speaker. Everything else builds the
stream (`station`), or feeds the catalogue that stream draws from (`bootstrap`, `refresh`).
`find_speaker` discovers the speaker by name over the LAN. `start` plays the station's URL on it as
a forced radio stream. `stop` stops it. `transport_state` reads its current transport state.

This module never restarts playback on its own. The station is a passive server, and the speaker
starts playback. A radio station serves bytes to whoever connects. It holds no view on whether a
speaker must play.

A `STOPPED` transport cannot say whether a person stopped the speaker or the stream broke. Both look
the same from the network. Any rule written against that state either fights the owner or goes
silent for good. `start` exists for a person, or a future process, to call on purpose. This module
never calls it on its own judgment.

See `docs/specs/2026-08-08-youtube-music-library-radio-design.md`, section `control`, for the full
argument.
"""

import socket
import typing

from soco import discover

if typing.TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from youtube_music_library_radio.settings import Settings

# The UDP connect below sends no packet, whatever the port holds. The kernel only needs a value it
# accepts while it builds the route.
_LAN_PROBE_PORT = 80


class _Speaker(typing.Protocol):
    """The `soco.SoCo` surface this module calls. A test fake implements only this.

    A real `SoCo` instance satisfies this Protocol structurally. Nothing in this module imports
    `soco.SoCo` as a type. A test therefore builds a fake with no subclass and no network.
    """

    player_name: str
    ip_address: str

    def play_uri(
        self,
        uri: str = ...,
        meta: str = ...,
        title: str = ...,
        # The name `start` mirrors the real `SoCo.play_uri`, and `start_playback` does not.
        # pyright and basedpyright check a Protocol method's parameter names against every class
        # asked to satisfy it. That includes `soco.SoCo` at the return of `_default_discover`. A
        # renamed parameter here makes a real `SoCo` fail that structural check. This name shadows
        # the module-level `start` function below, inside this signature alone. No body here reads
        # either name.
        # pylint: disable-next=redefined-outer-name
        start: bool = ...,  # noqa: FBT001 -- mirrors soco.SoCo.play_uri's own parameter, not this project's choice
        force_radio: bool = ...,  # noqa: FBT001 -- mirrors soco.SoCo.play_uri's own parameter, not this project's choice
        **kwargs: object,
    ) -> bool | None:
        """Play `uri`, as `soco.SoCo.play_uri` does."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs

    def stop(self) -> None:
        """Stop playback, as `soco.SoCo.stop` does."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs

    def get_current_transport_info(self) -> dict[str, str]:
        """Return transport info, as `soco.SoCo.get_current_transport_info` does."""
        ...  # pragma: no cover -- a Protocol's method body never runs. Only an implementation's body runs


def _default_discover() -> Iterable[_Speaker]:
    """Call the real `soco.discover` and turn its `None` (nothing found) into an empty set."""
    return discover() or set()


def lan_address(speaker_ip: str) -> str:
    """Return this machine's address on the network interface that reaches `speaker_ip`.

    This function opens a UDP socket and calls `connect`. That call sends no packet. It makes the
    kernel pick the route, and so the interface, for that destination. `getsockname` then reads the
    local address on that interface.

    A machine can hold more than one interface up at once, such as Wi-Fi and Ethernet together. It
    reaches the speaker on one of them alone. The machine's own host name can name the wrong one.

    This function has no test here. It opens a real socket, and the speaker gets a live test in
    Task 11.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((speaker_ip, _LAN_PROBE_PORT))
        # `getsockname`'s stub types its result as `Any`, to cover every address family in one
        # signature. This socket is `AF_INET`, whose first element is always the host address, as
        # a `str`. The cast reads past that stub imprecision, as `station.py` does for
        # `process.stdout`. It assumes nothing about external input.
        return typing.cast("str", sock.getsockname()[0])


def station_url(settings: Settings, *, speaker: _Speaker | None = None, address_fn: Callable[[str], str] = lan_address) -> str:
    """Build the URL the speaker fetches the endless stream from.

    Uses `settings.station_host` when the owner set one. Otherwise asks `address_fn` for the address
    that reaches the speaker. `localhost` is never reachable from the speaker, because the speaker
    fetches the stream over the network and not from the machine that runs this process.

    `speaker` is the speaker the caller already found. Discovery takes about five seconds, so a
    caller that needs both this URL and the speaker itself discovers one time and passes the result
    here. Without `speaker`, this function discovers the speaker on its own.

    `address_fn` is a parameter so a test can build the URL without a real socket.
    """
    host = settings.station_host
    if not host:
        found = speaker if speaker is not None else find_speaker(settings.speaker_name)
        host = address_fn(found.ip_address)
    return f"http://{host}:{settings.station_port}/"


def find_speaker(name: str, *, discover_fn: Callable[[], Iterable[_Speaker]] = _default_discover) -> _Speaker:
    """Discover Sonos speakers on the network and return the one named `name`.

    Raises `RuntimeError` naming every speaker discovery found, sorted, when none of them is named
    `name`. Names "none" when discovery finds no speaker at all, so a person reading the error
    knows whether to check the speaker's name or the network.
    """
    speakers = list(discover_fn())
    for speaker in speakers:
        if speaker.player_name == name:
            return speaker
    found = ", ".join(sorted(speaker.player_name for speaker in speakers)) or "none"
    message = f"no speaker named {name!r} found; discovery found: {found}"
    raise RuntimeError(message)


def start(speaker: _Speaker, url: str, station_name: str) -> None:
    """Play `url` on `speaker`, displayed as the radio station `station_name`.

    `force_radio=True` makes Sonos rewrite the scheme of `url` to `x-rincon-mp3radio://`. The
    speaker then shows radio controls, and not the elapsed time and seek bar of a track. A stream
    with no end has neither.

    A person calls this through the CLI. A future process can call it on purpose. Read the module
    docstring for the reason nothing in this module calls it on its own judgment.
    """
    _ = speaker.play_uri(url, title=station_name, force_radio=True)


def stop(speaker: _Speaker) -> None:
    """Stop playback on `speaker`."""
    speaker.stop()


def transport_state(speaker: _Speaker) -> str:
    """Return the speaker's current transport state: PLAYING, TRANSITIONING, PAUSED_PLAYBACK, or STOPPED."""
    return speaker.get_current_transport_info()["current_transport_state"]
