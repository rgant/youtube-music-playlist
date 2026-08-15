"""Integration tests proving `resolve` reaches the real yt-dlp/YouTube Music service."""

import pytest

from youtube_music_library_radio.resolver import ResolutionError, resolve

_KNOWN_GOOD_VIDEO_ID = "YEUZmtb_AT8"
_INVALID_VIDEO_ID = "not-a-real-video-id"


@pytest.mark.network
def test_resolve_returns_an_audio_url_for_a_known_song() -> None:
    """A known-good video ID resolves to its title, artist, and a playable audio URL."""
    resolved = resolve(_KNOWN_GOOD_VIDEO_ID)

    assert resolved.video_id == _KNOWN_GOOD_VIDEO_ID
    assert resolved.artist == "Nirvana"
    assert resolved.title.startswith("Territorial Pissings")
    assert resolved.audio_url


@pytest.mark.network
def test_resolve_raises_for_an_unknown_video_id() -> None:
    """An invalid video ID raises `ResolutionError`, not a raw yt-dlp exception."""
    with pytest.raises(ResolutionError):
        _ = resolve(_INVALID_VIDEO_ID)
