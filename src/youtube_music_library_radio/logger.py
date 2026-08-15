"""Colourful console logging."""

import logging
import typing


class RainbowLogRecord(logging.LogRecord):
    """Add `colorlevelname` field to the LogRecord class for the rainbow formatter."""

    colorlevelname: str = ""


class RainbowLogFormatter(logging.Formatter):
    """Adds a new key to the format string: `%(colorlevelname)s`."""

    # http://kishorelive.com/2011/12/05/printing-colors-in-the-terminal/
    levelcolors: typing.ClassVar[dict[str, str]] = {
        "CRITICAL": "\033[4;31mCRITICAL\033[0m",  # Underlined Red Text
        "ERROR": "\033[1;31mERROR\033[0m",  # Bold Red Text
        "WARN": "\033[1;33mWARNING\033[0m",  # Bold Yellow Text
        "WARNING": "\033[1;33mWARNING\033[0m",  # Bold Yellow Text
        "INFO": "\033[0;32mINFO\033[0m",  # Light Green Text
        "DEBUG": "\033[0;34mDEBUG\033[0m",  # Light Blue Text
        "NOTSET": "\033[1:30mNOTSET\033[0m",  # Bold Black Text
    }

    @typing.override
    def format(self, record: logging.LogRecord) -> str:
        """Add the new `colorlevelname` to record and then call the super."""
        color_rec = typing.cast("RainbowLogRecord", record)
        color_rec.colorlevelname = self.levelcolors[record.levelname]
        return super().format(record)


def create_handler() -> logging.StreamHandler[typing.TextIO]:
    """Create a StreamHandler for logging. A terminal stream gets colour, and any other gets none."""
    handler = logging.StreamHandler()
    # The stream defaults to `stderr`. Colour only helps on a terminal, so a pipe or a redirect
    # keeps the plain formatter.
    if handler.stream.isatty():
        frmt = "%(colorlevelname)s:%(module)s:%(funcName)s:%(lineno)d:%(message)s"
        formatter = RainbowLogFormatter(frmt)
        handler.setFormatter(formatter)
    return handler
