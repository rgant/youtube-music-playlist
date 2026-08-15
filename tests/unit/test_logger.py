"""Tests for youtube_music_library_radio.logger."""

import io
import logging
import typing

from youtube_music_library_radio.logger import RainbowLogFormatter, create_handler

if typing.TYPE_CHECKING:
    import pytest


class _FakeTTYStream(io.StringIO):
    """A stream that reports itself as a terminal, unlike a plain `io.StringIO`."""

    @typing.override
    def isatty(self) -> bool:
        return True


def _make_record(level: int, message: str) -> logging.LogRecord:
    """Build a real `LogRecord` for the given level and message."""
    return logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )


def test_create_handler_uses_the_colour_formatter_on_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """`create_handler` attaches a `RainbowLogFormatter` when the stream is a terminal."""
    monkeypatch.setattr("sys.stderr", _FakeTTYStream())

    handler = create_handler()

    assert isinstance(handler.formatter, RainbowLogFormatter)


def test_create_handler_uses_no_formatter_off_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """`create_handler` leaves the default formatter alone when the stream is not a terminal."""
    monkeypatch.setattr("sys.stderr", io.StringIO())

    handler = create_handler()

    assert handler.formatter is None


def test_format_colours_the_level_name_and_keeps_the_message() -> None:
    """`RainbowLogFormatter.format` injects the coloured level name and keeps the message text."""
    formatter = RainbowLogFormatter("%(colorlevelname)s:%(message)s")

    info_output = formatter.format(_make_record(logging.INFO, "starting up"))
    error_output = formatter.format(_make_record(logging.ERROR, "it broke"))

    assert info_output == "\033[0;32mINFO\033[0m:starting up"
    assert error_output == "\033[1;31mERROR\033[0m:it broke"
