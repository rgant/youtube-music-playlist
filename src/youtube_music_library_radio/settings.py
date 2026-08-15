"""Runtime configuration, read from environment variables with the `YTM_RADIO_` prefix."""

import dataclasses
import os
from pathlib import Path

_PREFIX = "YTM_RADIO_"
_DEFAULT_DATABASE_PATH = Path("~/.local/share/youtube-music-library-radio/catalogue.sqlite3").expanduser()


@dataclasses.dataclass(frozen=True)
class Settings:
    """Runtime configuration for this project.

    `__post_init__` rejects a `no_repeat_window` below zero and a `prune_threshold` below one.
    `candidate_songs` reads a window of zero or less as "exclude nothing", so a negative value hides
    the owner's mistake instead of reporting it. A `prune_threshold` below one deletes a song on its
    first failure. One clear error at start-up is easier to correct than the downstream failure each
    fault causes minutes or hours later.
    """

    database_path: Path
    speaker_name: str
    no_repeat_window: int
    prune_threshold: int

    def __post_init__(self) -> None:
        """Reject a value the rest of the project cannot act on."""
        _require_at_least(self.no_repeat_window, 0, "no_repeat_window", "NO_REPEAT_WINDOW")
        _require_at_least(self.prune_threshold, 1, "prune_threshold", "PRUNE_THRESHOLD")


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
        no_repeat_window=_env_int("NO_REPEAT_WINDOW", 2000),
        prune_threshold=_env_int("PRUNE_THRESHOLD", 3),
    )
