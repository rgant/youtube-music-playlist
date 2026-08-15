"""Runtime configuration, read from environment variables with the `YTM_RADIO_` prefix."""

import dataclasses
import os
from pathlib import Path

_PREFIX = "YTM_RADIO_"
_DEFAULT_DATABASE_PATH = Path("~/.local/share/youtube-music-library-radio/catalogue.sqlite3").expanduser()

_MIN_PORT = 1
_MAX_PORT = 65535


@dataclasses.dataclass(frozen=True)
class Settings:
    """Runtime configuration for the radio service.

    `__post_init__` rejects a `no_repeat_window` below zero, a `prune_threshold` below one, a
    `metadata_interval` below one, a `bitrate_kbps` below one, and a `station_port` outside 1 to
    65535. A negative `no_repeat_window` silences the radio. A negative `metadata_interval` plays
    static. One clear error at start-up is easier to correct than the downstream failure each fault
    causes minutes or hours later.
    """

    database_path: Path
    speaker_name: str
    station_host: str
    station_port: int
    bitrate_kbps: int
    no_repeat_window: int
    prune_threshold: int
    metadata_interval: int
    station_name: str

    def __post_init__(self) -> None:
        """Reject a value that silences the radio, plays static, or binds no usable port."""
        _require_at_least(self.no_repeat_window, 0, "no_repeat_window", "NO_REPEAT_WINDOW")
        _require_at_least(self.prune_threshold, 1, "prune_threshold", "PRUNE_THRESHOLD")
        _require_at_least(self.metadata_interval, 1, "metadata_interval", "METADATA_INTERVAL")
        _require_at_least(self.bitrate_kbps, 1, "bitrate_kbps", "BITRATE_KBPS")
        if not _MIN_PORT <= self.station_port <= _MAX_PORT:
            message = f"station_port must be between {_MIN_PORT} and {_MAX_PORT}, not {self.station_port}; set {_PREFIX}STATION_PORT"
            raise ValueError(message)


def _require_at_least(value: int, minimum: int, field: str, env_suffix: str) -> None:
    """Raise `ValueError` naming `field` and its environment variable when `value` is below `minimum`."""
    if value < minimum:
        message = f"{field} must be {minimum} or more, not {value}; set {_PREFIX}{env_suffix}"
        raise ValueError(message)


def _env_str(name: str, default: str) -> str:
    """Read a string setting from the environment. An unset variable gives `default`."""
    return os.environ.get(_PREFIX + name, default)


def _env_int(name: str, default: int) -> int:
    """Read an integer setting from the environment. An unset variable gives `default`.

    Raises `ValueError` naming the field and its environment variable for a value that is not a
    whole number. The `int()` error names the text alone, which fits every integer setting equally.
    """
    value = os.environ.get(_PREFIX + name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError as exc:
        # Each integer field's name is the lower case of its environment suffix. One message
        # therefore names the field and the variable with no second argument at each call site.
        message = f"{name.lower()} must be a whole number, not {value!r}; set {_PREFIX}{name}"
        raise ValueError(message) from exc


def load_settings() -> Settings:
    """Build a `Settings` instance from the current environment."""
    database_path = Path(_env_str("DATABASE_PATH", str(_DEFAULT_DATABASE_PATH))).expanduser()
    return Settings(
        database_path=database_path,
        speaker_name=_env_str("SPEAKER_NAME", "Kitchen"),
        station_host=_env_str("STATION_HOST", ""),
        station_port=_env_int("STATION_PORT", 8900),
        bitrate_kbps=_env_int("BITRATE_KBPS", 128),
        no_repeat_window=_env_int("NO_REPEAT_WINDOW", 2000),
        prune_threshold=_env_int("PRUNE_THRESHOLD", 3),
        metadata_interval=_env_int("METADATA_INTERVAL", 16000),
        station_name=_env_str("STATION_NAME", "My Library Radio"),
    )
