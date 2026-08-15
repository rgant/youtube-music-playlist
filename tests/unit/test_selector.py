"""Tests for youtube_music_library_radio.selector."""

import random

import pytest

from youtube_music_library_radio.catalogue import Song
from youtube_music_library_radio.selector import pick

_CANDIDATES = (
    Song(video_id="a", title="Song A", artist="Artist A", failure_count=0, last_success=None, last_played=None),
    Song(video_id="b", title="Song B", artist="Artist B", failure_count=0, last_success=None, last_played=None),
    Song(video_id="c", title="Song C", artist="Artist C", failure_count=0, last_success=None, last_played=None),
)


def test_pick_returns_a_member_of_the_candidates() -> None:
    """`pick` returns one of the songs it was given, not a copy or an unrelated value."""
    chosen = pick(_CANDIDATES, random.Random(0))

    assert chosen in _CANDIDATES


def test_pick_is_deterministic_for_a_seeded_generator() -> None:
    """`pick` returns exactly what `random.Random.choice` picks for the same seed, not a fixed song."""
    expected = random.Random(42).choice(_CANDIDATES)

    actual = pick(_CANDIDATES, random.Random(42))

    assert actual == expected


def test_pick_rejects_an_empty_sequence() -> None:
    """`pick` raises `ValueError` when `candidates` is empty."""
    with pytest.raises(ValueError, match="candidates"):
        _ = pick((), random.Random(0))
