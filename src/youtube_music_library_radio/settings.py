"""Runtime configuration, read from environment variables with the `YTM_RADIO_` prefix."""

import dataclasses
import os
from pathlib import Path

_PREFIX = "YTM_RADIO_"
_DEFAULT_DATABASE_PATH = Path("~/.local/share/youtube-music-library-radio/catalogue.sqlite3").expanduser()


# `Settings` holds one field per environment variable, so its field count follows the settings the
# project reads. R0902 targets a class that carries too much behavior, and this record carries none.
@dataclasses.dataclass(frozen=True)
class Settings:  # pylint: disable=too-many-instance-attributes
    """Runtime configuration for this project.

    `__post_init__` rejects a value the project cannot act on. A `missing_threshold` of zero
    deletes every song on the next trusted read.
    `mark_absent` deletes each row whose `missing_count` reaches the threshold, and a present song
    holds zero. A `trust_ratio` of zero trusts every read, including one that returns almost
    nothing.

    One clear error at start-up is easier to correct than the downstream failure each fault causes
    minutes or hours later.
    """

    database_path: Path
    speaker_name: str
    trust_ratio: float
    missing_threshold: int
    queue_size: int
    queue_window_days: int

    def __post_init__(self) -> None:
        """Reject a value the rest of the project cannot act on."""
        _require_at_least(self.missing_threshold, 1, "missing_threshold", "MISSING_THRESHOLD")
        _require_at_least(self.queue_size, 1, "queue_size", "QUEUE_SIZE")
        _require_at_least(self.queue_window_days, 0, "queue_window_days", "QUEUE_WINDOW_DAYS")
        if not 0.0 < self.trust_ratio <= 1.0:
            message = f"trust_ratio must be above 0 and at most 1, not {self.trust_ratio}; set {_PREFIX}TRUST_RATIO"
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
    whole number. The `int()` error names the bad text and not the variable, so the owner cannot tell which
    setting to correct.
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


def _env_float(name: str, default: float) -> float:
    """Read a fractional setting from the environment. An unset variable gives `default`.

    Raises `ValueError` naming the field and its environment variable for a value that is not a
    number. The `float()` error names the bad text and not the variable, so the owner cannot tell which
    setting to correct.
    """
    value = os.environ.get(_PREFIX + name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError as exc:
        message = f"{name.lower()} must be a number, not {value!r}; set {_PREFIX}{name}"
        raise ValueError(message) from exc


def load_settings() -> Settings:
    """Build a `Settings` instance from the current environment."""
    database_path = Path(_env_str("DATABASE_PATH", str(_DEFAULT_DATABASE_PATH))).expanduser()
    return Settings(
        database_path=database_path,
        speaker_name=_env_str("SPEAKER_NAME", "Kitchen"),
        trust_ratio=_env_float("TRUST_RATIO", 0.9),
        missing_threshold=_env_int("MISSING_THRESHOLD", 3),
        queue_size=_env_int("QUEUE_SIZE", 500),
        queue_window_days=_env_int("QUEUE_WINDOW_DAYS", 7),
    )
