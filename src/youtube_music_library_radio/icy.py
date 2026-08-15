"""Build ICY metadata blocks for the Sonos-facing MP3 stream.

Every `icy-metaint` bytes of audio, the streaming server writes one of these blocks so the Sonos app
can show the song currently playing. The block layout is fixed by the ICY protocol: one length
byte, then a `StreamTitle='...';` payload padded to a multiple of 16 bytes. This module performs no
input and no output. It turns a title string into bytes.
"""

_BLOCK_SIZE = 16

# Sonos parses `Icy-MetaData: 1` responses. A single quote inside the title closes the
# `StreamTitle='...'` value early and corrupts the payload. This module removes each quote before
# it builds the payload.
_DISALLOWED_CHARACTER = "'"

EMPTY_BLOCK: bytes = b"\x00"
"""One zero byte. Sonos reads a zero length byte as "no change" and expects no payload after it."""


def metadata_block(title: str) -> bytes:
    """Build an ICY metadata block announcing `title` as the current stream title.

    This function uses `title` exactly as given. The caller builds the `Artist - Title` form first,
    because Sonos splits on the dash itself.

    The ICY length field is a single byte, so the padded payload cannot exceed 4080 bytes
    (255 * 16). No real YouTube Music title comes near that bound. This function therefore checks no
    length and cuts no title. A title long enough to cross the bound raises `ValueError` from
    `bytes([length_byte])`.
    """
    safe_title = title.replace(_DISALLOWED_CHARACTER, "")
    payload = f"StreamTitle='{safe_title}';".encode()

    remainder = len(payload) % _BLOCK_SIZE
    if remainder:
        payload += b"\x00" * (_BLOCK_SIZE - remainder)

    length_byte = len(payload) // _BLOCK_SIZE
    return bytes([length_byte]) + payload
