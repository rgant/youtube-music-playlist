"""Tests for youtube_music_library_radio.settings."""

import os
from pathlib import Path

import pytest

from youtube_music_library_radio.settings import load_settings


@pytest.fixture(autouse=True)
def _clear_ytm_radio_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every `YTM_RADIO_` environment variable so defaults are visible in each test."""
    for name in list(os.environ):
        if name.startswith("YTM_RADIO_"):
            monkeypatch.delenv(name, raising=False)


def test_defaults_match_the_documented_values() -> None:
    """Every setting falls back to its documented default when no environment variable is set."""
    settings = load_settings()

    assert settings.speaker_name == "Kitchen"
    assert settings.no_repeat_window == 2000
    assert settings.prune_threshold == 3


def test_environment_overrides_a_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`YTM_RADIO_SPEAKER_NAME` overrides the default `speaker_name`."""
    monkeypatch.setenv("YTM_RADIO_SPEAKER_NAME", "Office")

    settings = load_settings()

    assert settings.speaker_name == "Office"


def test_settings_reject_a_value_that_is_not_a_whole_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_settings` raises naming the field and its environment variable when the value is not a number.

    Every integer setting shares one reader. The bare `int()` error names the text alone and names
    no setting, so it fits every one of them.
    """
    monkeypatch.setenv("YTM_RADIO_NO_REPEAT_WINDOW", "two thousand")

    with pytest.raises(ValueError, match=r"no_repeat_window.*YTM_RADIO_NO_REPEAT_WINDOW"):
        _ = load_settings()


def test_database_path_expands_the_home_directory() -> None:
    """The default `database_path` is an absolute path with the home directory expanded."""
    settings = load_settings()

    assert "~" not in str(settings.database_path)
    assert settings.database_path == Path.home() / ".local/share/youtube-music-library-radio/catalogue.sqlite3"


def test_settings_reject_a_negative_no_repeat_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_settings` raises naming `no_repeat_window` and its environment variable when the value is negative."""
    monkeypatch.setenv("YTM_RADIO_NO_REPEAT_WINDOW", "-1")

    with pytest.raises(ValueError, match=r"no_repeat_window.*YTM_RADIO_NO_REPEAT_WINDOW"):
        _ = load_settings()


def test_settings_accept_a_no_repeat_window_of_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `no_repeat_window` of exactly zero is valid: the lower bound is rejected, not the boundary itself."""
    monkeypatch.setenv("YTM_RADIO_NO_REPEAT_WINDOW", "0")

    settings = load_settings()

    assert settings.no_repeat_window == 0


def test_settings_reject_a_prune_threshold_below_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_settings` raises naming `prune_threshold` and its environment variable when the value is below one."""
    monkeypatch.setenv("YTM_RADIO_PRUNE_THRESHOLD", "0")

    with pytest.raises(ValueError, match=r"prune_threshold.*YTM_RADIO_PRUNE_THRESHOLD"):
        _ = load_settings()


def test_settings_accept_a_prune_threshold_of_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `prune_threshold` of exactly one is valid: the lower bound is rejected, not the boundary itself."""
    monkeypatch.setenv("YTM_RADIO_PRUNE_THRESHOLD", "1")

    settings = load_settings()

    assert settings.prune_threshold == 1
