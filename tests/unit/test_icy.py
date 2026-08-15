"""Tests for youtube_music_library_radio.icy."""

from youtube_music_library_radio.icy import EMPTY_BLOCK, metadata_block


def test_block_length_byte_matches_the_payload() -> None:
    """The first byte equals the remaining payload length divided by 16."""
    block = metadata_block("Nirvana - Territorial Pissings")

    length_byte = block[0]
    payload = block[1:]

    assert length_byte == len(payload) // 16


def test_payload_pads_to_a_multiple_of_sixteen() -> None:
    """The payload pads with null bytes until its length is a multiple of 16.

    "Nirvana - Territorial Pissings" is chosen because its unpadded payload,
    `StreamTitle='Nirvana - Territorial Pissings';`, is 45 bytes: not already a multiple of 16. That
    forces the padding branch to run, unlike a title whose unpadded payload happens to land on a
    16-byte boundary already.
    """
    block = metadata_block("Nirvana - Territorial Pissings")

    payload = block[1:]
    unpadded = b"StreamTitle='Nirvana - Territorial Pissings';"

    assert len(payload) % 16 == 0
    assert payload == unpadded + b"\x00" * 3


def test_a_payload_already_aligned_gets_no_padding() -> None:
    """A title whose unpadded payload is already a multiple of 16 gets no null bytes appended.

    `"A"` gives `StreamTitle='A';`, exactly 16 bytes. This exercises the other side of the
    `if remainder:` branch: `test_payload_pads_to_a_multiple_of_sixteen` covers the "needs padding"
    side, and this test covers the "already aligned, skip padding" side.
    """
    block = metadata_block("A")

    unpadded = b"StreamTitle='A';"

    assert block[1:] == unpadded
    assert block[0] == 1
    assert len(block) == 17


def test_payload_carries_the_title() -> None:
    """The payload wraps the exact title in `StreamTitle='...';`, then pads with nulls to 16 bytes."""
    block = metadata_block("Nirvana - Territorial Pissings")

    payload = block[1:]
    expected_payload = b"StreamTitle='Nirvana - Territorial Pissings';" + b"\x00" * 3
    expected_block = bytes([len(expected_payload) // 16]) + expected_payload

    assert payload == expected_payload
    assert block == expected_block


def test_empty_block_is_one_zero_byte() -> None:
    """`EMPTY_BLOCK` is exactly one zero byte, the Sonos "no change" signal."""
    assert EMPTY_BLOCK == b"\x00"
    assert len(EMPTY_BLOCK) == 1


def test_a_quote_in_the_title_does_not_break_the_payload() -> None:
    """A single quote in the title does not terminate the `StreamTitle='...'` value early."""
    block = metadata_block("Guns N' Roses - Don't Cry")

    length_byte = block[0]
    payload = block[1:]

    assert length_byte == len(payload) // 16
    assert len(payload) % 16 == 0
    assert payload.startswith(b"StreamTitle='")
    assert payload.rstrip(b"\x00").endswith(b"';")
    # Only the outer quotes remain. Every quote from the title itself is gone.
    inner = payload.rstrip(b"\x00")[len(b"StreamTitle='") : -len(b"';")]
    assert b"'" not in inner
