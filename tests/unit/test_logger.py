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


def test_create_handler_uses_the_color_formatter_on_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner runs each command at a terminal and reads the level name by its color.

    Without the formatter, an `ERROR` line looks the same as an `INFO` line.
    """
    monkeypatch.setattr("sys.stderr", _FakeTTYStream())

    handler = create_handler()

    assert isinstance(handler.formatter, RainbowLogFormatter)


def test_create_handler_uses_no_formatter_off_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `cron` run or a redirect sends the log to a file.

    A file has no terminal to interpret an escape code, so the codes become noise in the text.
    """
    monkeypatch.setattr("sys.stderr", io.StringIO())

    handler = create_handler()

    assert handler.formatter is None


def test_format_colors_the_level_name_and_keeps_the_message() -> None:
    """The exact escape codes are the output the owner reads.

    A wrong code paints the wrong level. If `format` returns before `super().format`, the line
    carries a level name and no message.
    """
    formatter = RainbowLogFormatter("%(colorlevelname)s:%(message)s")

    info_output = formatter.format(_make_record(logging.INFO, "starting up"))
    error_output = formatter.format(_make_record(logging.ERROR, "it broke"))

    assert info_output == "\033[0;32mINFO\033[0m:starting up"
    assert error_output == "\033[1;31mERROR\033[0m:it broke"
