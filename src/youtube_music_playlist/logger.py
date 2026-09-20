"""Colorful console logging."""

import logging
import typing


class RainbowLogRecord(logging.LogRecord):
    """The `LogRecord` shape `RainbowLogFormatter` writes to.

    Nothing creates a record as this class. `format` casts to it, so the attribute write
    type-checks.
    """

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
        "NOTSET": "\033[1;30mNOTSET\033[0m",  # Bold Black Text
    }

    @typing.override
    def format(self, record: logging.LogRecord) -> str:
        """Set `colorlevelname`, then format the record."""
        color_rec = typing.cast("RainbowLogRecord", record)
        color_rec.colorlevelname = self.levelcolors[record.levelname]
        return super().format(record)


def create_handler() -> logging.StreamHandler[typing.TextIO]:
    """Create a StreamHandler for logging. A terminal stream gets color, and any other gets none."""
    handler = logging.StreamHandler()
    # The stream defaults to `stderr`. Color only helps on a terminal, so a pipe or a redirect
    # keeps the plain formatter.
    if handler.stream.isatty():
        frmt = "%(colorlevelname)s:%(module)s:%(funcName)s:%(lineno)d:%(message)s"
        formatter = RainbowLogFormatter(frmt)
        handler.setFormatter(formatter)
    return handler
