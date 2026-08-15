"""Tests for the pure parts of youtube_music_library_radio.resolver.

`_to_resolved` turns a yt-dlp info mapping into a `Resolved`. `_classify` reads a yt-dlp error
message and says whether a retry can help. Neither one calls the network, unlike the
`network`-marked integration tests in `tests/integration/test_resolver.py`.
"""

import typing

import pytest
import yt_dlp

from youtube_music_library_radio.resolver import (
    PermanentResolutionError,
    ResolutionError,
    Resolved,
    TransientResolutionError,
    _classify,
    _to_resolved,
    resolve,
)

if typing.TYPE_CHECKING:
    from collections.abc import Callable


class _FakeYoutubeDL:
    """A stand-in for `yt_dlp.YoutubeDL`. It answers `extract_info` from what the test gave it."""

    def __init__(self, error: str | None, info: dict[str, object] | None) -> None:
        self._error: str | None = error
        self._info: dict[str, object] | None = info

    def __enter__(self) -> typing.Self:
        """Enter the context manager, as the real class does."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Leave the context manager, as the real class does."""
        return

    def extract_info(self, url: str, *, download: bool) -> dict[str, object]:
        """Raise the error this fake holds, or return the info dict it holds."""
        del url, download
        if self._error is not None:
            raise RuntimeError(self._error)
        return self._info or {}


type _YoutubeDLFactory = Callable[[dict[str, object]], _FakeYoutubeDL]


def _failing_ydl(message: str) -> _YoutubeDLFactory:
    """Build a `YoutubeDL` replacement whose `extract_info` raises `message`."""

    def build(_options: dict[str, object]) -> _FakeYoutubeDL:
        return _FakeYoutubeDL(message, None)

    return build


def _returning_ydl(info: dict[str, object]) -> _YoutubeDLFactory:
    """Build a `YoutubeDL` replacement whose `extract_info` returns `info`."""

    def build(_options: dict[str, object]) -> _FakeYoutubeDL:
        return _FakeYoutubeDL(None, info)

    return build


_VIDEO_ID = "YEUZmtb_AT8"
_COMPLETE_INFO = {
    "title": "Territorial Pissings (Live In Del Mar, California/1991)",
    "artist": "Nirvana",
    "uploader": "Nirvana - Topic",
    "url": "https://example.com/audio",
}

# `_to_resolved` raises one exception type for an absent title and for an absent URL. Each test
# therefore matches the video ID and the field its own case is about. A match on the video ID alone
# passes for either message, and the two messages then become free to swap.
_NO_TITLE = rf"{_VIDEO_ID}.*no title"
_NO_AUDIO_URL = rf"{_VIDEO_ID}.*no audio url"


def test_a_complete_info_dict_produces_the_expected_resolved() -> None:
    """A yt-dlp info dict with every field present produces a matching `Resolved`."""
    resolved = _to_resolved(_VIDEO_ID, _COMPLETE_INFO)

    assert resolved == Resolved(
        video_id=_VIDEO_ID,
        title=_COMPLETE_INFO["title"],
        artist="Nirvana",
        audio_url=_COMPLETE_INFO["url"],
    )


def test_a_missing_title_raises() -> None:
    """A yt-dlp info dict with no `title` key raises `ResolutionError`, not a `KeyError`."""
    info = {k: v for k, v in _COMPLETE_INFO.items() if k != "title"}

    with pytest.raises(ResolutionError, match=_NO_TITLE):
        _ = _to_resolved(_VIDEO_ID, info)


def test_a_none_title_raises() -> None:
    """A `title` present but `None` raises `ResolutionError`, the same as a missing `title`."""
    info = {**_COMPLETE_INFO, "title": None}

    with pytest.raises(ResolutionError, match=_NO_TITLE):
        _ = _to_resolved(_VIDEO_ID, info)


def test_a_missing_url_raises() -> None:
    """A yt-dlp info dict with no `url` key raises `ResolutionError`: no URL, no playback."""
    info = {k: v for k, v in _COMPLETE_INFO.items() if k != "url"}

    with pytest.raises(ResolutionError, match=_NO_AUDIO_URL):
        _ = _to_resolved(_VIDEO_ID, info)


def test_a_none_url_raises() -> None:
    """A `url` present but `None` raises `ResolutionError`, the same as a missing `url`."""
    info = {**_COMPLETE_INFO, "url": None}

    with pytest.raises(ResolutionError, match=_NO_AUDIO_URL):
        _ = _to_resolved(_VIDEO_ID, info)


def test_a_missing_artist_falls_back_to_uploader() -> None:
    """A missing `artist` falls back to `uploader`, instead of raising."""
    info = {k: v for k, v in _COMPLETE_INFO.items() if k != "artist"}

    resolved = _to_resolved(_VIDEO_ID, info)

    assert resolved.artist == _COMPLETE_INFO["uploader"]


def test_a_missing_artist_and_uploader_falls_back_to_unknown() -> None:
    """A missing `artist` and a missing `uploader` fall back to `"Unknown"`, instead of raising."""
    info = {k: v for k, v in _COMPLETE_INFO.items() if k not in {"artist", "uploader"}}

    resolved = _to_resolved(_VIDEO_ID, info)

    assert resolved.artist == "Unknown"


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: [youtube] abc: Video unavailable. This video is not available",
        "ERROR: [youtube] abc: This video is DRM protected",
        "ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video",
        "ERROR: [youtube] abc: This video has been removed by the uploader",
    ],
)
def test_a_gone_video_is_permanent(message: str) -> None:
    """A video that is gone or unplayable gives `PermanentResolutionError`. A retry cannot help."""
    error = _classify(_VIDEO_ID, Exception(message))

    assert isinstance(error, PermanentResolutionError)


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: unable to download video data: HTTP Error 403: Forbidden",
        "ERROR: [youtube] abc: Sign in to confirm you're not a bot",
        "ERROR: [youtube] abc: Unable to download API page: <urlopen error timed out>",
    ],
)
def test_a_throttled_or_blocked_read_is_transient(message: str) -> None:
    """A 403, a bot check, and a transport failure each give `TransientResolutionError`.

    yt-dlp issue #17395 records a 403 that clears on a retry, with no change to the request. The
    station must retry such a song, and it must never count the failure against that song.
    """
    error = _classify(_VIDEO_ID, Exception(message))

    assert isinstance(error, TransientResolutionError)


def test_an_unrecognised_failure_is_transient() -> None:
    """A message this module does not recognise gives `TransientResolutionError`.

    A permanent verdict deletes a row from the catalogue. An unknown fault must never do that, so
    the safe default is the one that keeps the song.
    """
    error = _classify(_VIDEO_ID, Exception("ERROR: something nobody has seen before"))

    assert isinstance(error, TransientResolutionError)


def test_both_classes_stay_catchable_as_one() -> None:
    """Each class subclasses `ResolutionError`, so an existing `except ResolutionError` still works."""
    assert issubclass(PermanentResolutionError, ResolutionError)
    assert issubclass(TransientResolutionError, ResolutionError)


def test_the_classified_error_names_the_video_id() -> None:
    """The message names the video ID, so a log line points at one row of the catalogue."""
    error = _classify(_VIDEO_ID, Exception("ERROR: [youtube] abc: Video unavailable"))

    assert _VIDEO_ID in str(error)


def test_resolve_reports_a_gone_video_as_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`resolve` classifies the yt-dlp exception it catches, instead of raising one flat type.

    `yt_dlp.YoutubeDL` is a third-party SDK and the boundary this project does not own, so this
    test replaces it. Without the classification, `station` counts a dead video and a throttled
    read the same way.
    """
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _failing_ydl("ERROR: [youtube] x: Video unavailable"))

    with pytest.raises(PermanentResolutionError):
        _ = resolve(_VIDEO_ID)


def test_resolve_reports_a_403_as_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    """`resolve` reports a 403 as transient, so `station` retries the song and keeps the row."""
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _failing_ydl("ERROR: unable to download video data: HTTP Error 403: Forbidden"))

    with pytest.raises(TransientResolutionError):
        _ = resolve(_VIDEO_ID)


def test_resolve_reports_an_incomplete_info_dict_as_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    """An info dict with no audio URL is transient. A later attempt can return a complete one."""
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _returning_ydl({"title": "A Title", "artist": "A Band"}))

    with pytest.raises(TransientResolutionError):
        _ = resolve(_VIDEO_ID)
