"""Tests for youtube_music_library_radio.authheaders.

The tests use invented cookie values. No real credential belongs in this file, or in any file this
project tracks.
"""

import pytest

from youtube_music_library_radio.authheaders import CURL_INSTRUCTIONS, MissingHeadersError, normalise, parse_curl

_CURL = """curl --url 'https://music.youtube.com/youtubei/v1/browse?prettyPrint=false' \\
  -H 'accept: */*' \\
  -H 'authorization: SAPISIDHASH 1234_abc_u' \\
  -b 'SID=fake-sid; SAPISID=fake-sapisid' \\
  -H 'origin: https://music.youtube.com' \\
  -H 'x-goog-authuser: 0' \\
  -H 'x-youtube-client-name: 67' \\
  --data-raw '{"context":{"client":{"hl":"en"}},"browseId":"FEmusic_library_landing"}'"""

_HEADER_BLOCK = """accept: */*
authorization: SAPISIDHASH 1234_abc_u
cookie: SID=fake-sid; SAPISID=fake-sapisid
x-goog-authuser: 0"""


def test_curl_headers_become_a_mapping() -> None:
    """`parse_curl` reads every `-H` flag into a mapping, with lower-case names."""
    headers = parse_curl(_CURL)

    assert headers["accept"] == "*/*"
    assert headers["origin"] == "https://music.youtube.com"
    assert headers["x-youtube-client-name"] == "67"


def test_the_cookie_flag_becomes_the_cookie_header() -> None:
    """`-b` carries the cookies in a Chrome copy, and `ytmusicapi` wants them as `cookie`."""
    headers = parse_curl(_CURL)

    assert headers["cookie"] == "SID=fake-sid; SAPISID=fake-sapisid"


def test_the_request_body_never_becomes_a_header() -> None:
    """`--data-raw` holds JSON that can look like a flag. It must not reach the headers."""
    headers = parse_curl(_CURL)

    assert not any("browseId" in value for value in headers.values())
    assert "--data-raw" not in headers


def test_a_header_block_passes_through() -> None:
    """A raw header block is already the shape `ytmusicapi` wants, so `normalise` keeps it."""
    block = normalise(_HEADER_BLOCK)

    assert "cookie: SID=fake-sid; SAPISID=fake-sapisid" in block
    assert "x-goog-authuser: 0" in block


def test_a_curl_command_becomes_a_header_block() -> None:
    """`normalise` accepts a Chrome cURL copy, because Chrome offers no header copy."""
    block = normalise(_CURL)

    lines = dict(line.split(": ", 1) for line in block.splitlines())
    assert lines["cookie"] == "SID=fake-sid; SAPISID=fake-sapisid"
    assert lines["x-goog-authuser"] == "0"


def test_input_with_no_cookie_names_the_fault() -> None:
    """A page-load capture carries no cookie. The error must say so, and say what to do."""
    with pytest.raises(MissingHeadersError, match="cookie"):
        _ = normalise("accept: */*\nuser-agent: Mozilla/5.0")


def test_input_with_no_authuser_names_the_fault() -> None:
    """`x-goog-authuser` appears only on a signed-in API call. Its absence has one fix."""
    with pytest.raises(MissingHeadersError, match="x-goog-authuser"):
        _ = normalise("cookie: SID=fake-sid\naccept: */*")


def test_the_error_names_the_request_to_capture() -> None:
    """The message must name `/youtubei/v1/browse`, so a reader knows which request to copy."""
    with pytest.raises(MissingHeadersError, match="youtubei/v1/browse"):
        _ = normalise("accept: */*")


def test_the_instructions_name_copy_as_curl() -> None:
    """Chrome offers `Copy as cURL` and no header copy, so the instructions must name it."""
    assert "Copy as cURL" in CURL_INSTRUCTIONS
    assert "youtubei/v1/browse" in CURL_INSTRUCTIONS


def test_the_instructions_name_the_clipboard_and_the_file_option() -> None:
    """A terminal cannot carry this text, so the instructions must name the two routes that work."""
    assert "clipboard" in CURL_INSTRUCTIONS
    assert "--from-file" in CURL_INSTRUCTIONS
