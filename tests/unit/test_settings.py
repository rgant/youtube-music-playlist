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
    """The README documents each of these default values.

    A changed default contradicts the README, and nothing fails until the owner reaches the wrong
    speaker or hears an unexpected repeat.
    """
    settings = load_settings()

    assert settings.speaker_name == "Kitchen"


def test_environment_overrides_a_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`YTM_RADIO_SPEAKER_NAME` is the owner's one way to name a different room.

    If the environment read breaks, `stop`, `status`, `harvest`, and `queue` all reach the Kitchen
    speaker.
    """
    monkeypatch.setenv("YTM_RADIO_SPEAKER_NAME", "Office")

    settings = load_settings()

    assert settings.speaker_name == "Office"


def test_settings_reject_a_value_that_is_not_a_whole_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_settings` raises naming the field and its environment variable when the value is not a number.

    Every integer setting shares one reader. The bare `int()` error names the text alone and names
    no setting, so it fits every one of them.
    """
    monkeypatch.setenv("YTM_RADIO_MISSING_THRESHOLD", "three")

    with pytest.raises(ValueError, match=r"missing_threshold.*YTM_RADIO_MISSING_THRESHOLD"):
        _ = load_settings()


def test_database_path_expands_the_home_directory() -> None:
    """`open_catalogue` calls `path.parent.mkdir(parents=True)`, so an unexpanded `~` builds a directory named `~`.

    Each run from a different working directory then opens a different, empty catalogue.
    """
    settings = load_settings()

    assert "~" not in str(settings.database_path)
    assert settings.database_path == Path.home() / ".local/share/youtube-music-library-radio/catalogue.sqlite3"


def test_the_removal_settings_have_documented_defaults() -> None:
    """A short library read must not remove songs. The owner acts on each default, so each is pinned."""
    settings = load_settings()

    assert settings.trust_ratio == 0.9
    assert settings.missing_threshold == 3


def test_settings_reject_a_trust_ratio_of_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ratio of zero trusts a read that returns almost nothing, which removes almost everything."""
    monkeypatch.setenv("YTM_RADIO_TRUST_RATIO", "0")

    with pytest.raises(ValueError, match=r"trust_ratio.*YTM_RADIO_TRUST_RATIO"):
        _ = load_settings()


def test_settings_reject_a_trust_ratio_above_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ratio above one refuses every read, so no refresh can ever change presence."""
    monkeypatch.setenv("YTM_RADIO_TRUST_RATIO", "1.5")

    with pytest.raises(ValueError, match=r"trust_ratio.*YTM_RADIO_TRUST_RATIO"):
        _ = load_settings()


def test_settings_reject_a_trust_ratio_that_is_not_a_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """`load_settings` names the field and its variable, as it does for a bad whole number."""
    monkeypatch.setenv("YTM_RADIO_TRUST_RATIO", "most")

    with pytest.raises(ValueError, match=r"trust_ratio.*YTM_RADIO_TRUST_RATIO"):
        _ = load_settings()


def test_settings_reject_a_missing_threshold_below_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """A threshold below one removes a song on its first absent read."""
    monkeypatch.setenv("YTM_RADIO_MISSING_THRESHOLD", "0")

    with pytest.raises(ValueError, match=r"missing_threshold.*YTM_RADIO_MISSING_THRESHOLD"):
        _ = load_settings()


def test_the_refresh_schedule_has_documented_defaults() -> None:
    """`render_plists` writes these two values into the LaunchAgent, so a change moves the daily run.

    4 AM is quiet. A read of 21,000 songs takes minutes, and it holds the catalogue open.
    """
    settings = load_settings()

    assert settings.refresh_hour == 4
    assert settings.refresh_minute == 0


def test_settings_reject_a_refresh_hour_outside_the_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plist names `Hour` as a clock position. launchd never fires an agent that names hour 24."""
    monkeypatch.setenv("YTM_RADIO_REFRESH_HOUR", "24")

    with pytest.raises(ValueError, match=r"refresh_hour.*YTM_RADIO_REFRESH_HOUR"):
        _ = load_settings()


def test_settings_reject_a_refresh_minute_outside_the_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plist names `Minute` the same way, and a rejected plist leaves the mini with no refresh."""
    monkeypatch.setenv("YTM_RADIO_REFRESH_MINUTE", "60")

    with pytest.raises(ValueError, match=r"refresh_minute.*YTM_RADIO_REFRESH_MINUTE"):
        _ = load_settings()


def test_the_harvest_tolerance_has_a_documented_default() -> None:
    """`harvest` refuses a queue whose length differs from the playlist by more than this value.

    A live queue read that runs a little short must need no code change.
    """
    settings = load_settings()

    assert settings.harvest_tolerance == 25


def test_settings_reject_a_negative_harvest_tolerance(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gap is an absolute value, so a negative tolerance refuses every harvest."""
    monkeypatch.setenv("YTM_RADIO_HARVEST_TOLERANCE", "-1")

    with pytest.raises(ValueError, match=r"harvest_tolerance.*YTM_RADIO_HARVEST_TOLERANCE"):
        _ = load_settings()
