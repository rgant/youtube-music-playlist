#!/usr/bin/env python
"""
"""
import logging
import os
import sys
import typing


class RainbowLogRecord(logging.LogRecord):  # pylint: disable=too-few-public-methods
    """Add `colorlevelname` field to the LogRecord class for the rainbow formatter."""

    colorlevelname: str


class RainbowLogFormatter(logging.Formatter):
    """Adds a new key to the format string: `%(colorlevelname)s`."""

    # http://kishorelive.com/2011/12/05/printing-colors-in-the-terminal/
    levelcolors = {
        'CRITICAL': '\033[4;31mCRITICAL\033[0m',  # Underlined Red Text
        'ERROR': '\033[1;31mERROR\033[0m',  # Bold Red Text
        'WARN': '\033[1;33mWARNING\033[0m',  # Bold Yellow Text
        'WARNING': '\033[1;33mWARNING\033[0m',  # Bold Yellow Text
        'INFO': '\033[0;32mINFO\033[0m',  # Light Green Text
        'DEBUG': '\033[0;34mDEBUG\033[0m',  # Light Blue Text
        'NOTSET': '\033[1:30mNOTSET\033[0m',  # Bold Black Text
    }

    def format(self, record: logging.LogRecord) -> str:
        """Adds the new `colorlevelname` to record and then calls the super."""
        color_rec = typing.cast('RainbowLogRecord', record)
        color_rec.colorlevelname = self.levelcolors[record.levelname]
        return super().format(record)


def create_handler() -> 'logging.StreamHandler[typing.TextIO]':
    """Creates a colorful StreamHandler for logging. But only if this is outputting to a terminal"""
    handler = logging.StreamHandler()
    # Check that the stream (default is `stderr`) is a terminal, not a pipe or redirect before using
    # fancy formatter
    if handler.stream.isatty():
        frmt = '%(colorlevelname)s:%(module)s:%(funcName)s:%(lineno)d:%(message)s'
        formatter = RainbowLogFormatter(frmt)
        handler.setFormatter(formatter)
    return handler
