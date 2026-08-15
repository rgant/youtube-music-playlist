"""Pure selection logic for choosing the next song to play."""

import typing

if typing.TYPE_CHECKING:
    import random
    from collections.abc import Sequence

    from youtube_music_library_radio.catalogue import Song


def pick(candidates: Sequence[Song], rng: random.Random) -> Song:
    """Choose the next song from `candidates`. `rng` makes the choice."""
    if not candidates:
        message = "candidates must not be empty"
        raise ValueError(message)
    return rng.choice(candidates)
