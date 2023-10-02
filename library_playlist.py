#!/usr/bin/env python
"""
Download all songs in my YouTube Music Library.
Create an "Everything" playlist if missing.
Add any songs from the library that are not in the playlist.
"""
import logging
import os
import sys
import typing

from ytmusicapi import YTMusic


class ApiError(Exception):
    """Wrapper around error responses from the `ytmusicapi`."""


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


def chunk_set(songs: set[str]) -> typing.Generator[list[str], None, None]:
    """Batch set of missing song ids for API."""
    step = 25
    song_ids = list(songs)
    for i in range(0, len(song_ids), step):
        yield song_ids[i:i + step]


def everything_playlist(ytmusic: YTMusic) -> str:
    """Finds or creates the 'Everything' Playlist and returns its ID."""
    logger = logging.getLogger(__name__)
    playlists = ytmusic.get_library_playlists()
    everything_lists = [pl for pl in playlists if pl['title'] == 'Everything 1']
    num_lists = len(everything_lists)
    if num_lists > 1:
        logger.warning('Too many playlists found.')

    if num_lists == 0:
        ret = ytmusic.create_playlist(
            title='Everything',
            description='Every song in my library',
            privacy_status='PRIVATE',
        )
        if isinstance(ret, str):
            return ret
        logger.error('Failed to create Everything playlist\n%r', ret)
        raise ApiError(ret)

    return everything_lists[0]['playlistId']


def library_songs(ytmusic: YTMusic) -> set[str]:
    """Fetch every song in my library except 'Radio Edit's."""
    songs = ytmusic.get_library_songs(limit=None)
    return {s['videoId'] for s in songs if 'radio edit' not in s['title'].lower()}


def playlist_songs(ytmusic: YTMusic, playlist_id: str) -> set[str]:
    """Fetch every song in the 'Everything' playlist."""
    playlist = ytmusic.get_playlist(playlist_id, limit=None)
    return {t['videoId'] for t in playlist['tracks']}


def update_playlist(ytmusic: YTMusic) -> None:
    """Update the 'Everything' playlist with any missing songs from the Library."""
    logger = logging.getLogger(__name__)
    all_songs = library_songs(ytmusic)
    logger.info('All Songs in Library: %s', f'{len(all_songs):,}')
    playlist_id = everything_playlist(ytmusic)
    logger.debug('Everything Playlist %r', playlist_id)
    existing_songs = playlist_songs(ytmusic, playlist_id)
    logger.info('Songs in Playlist: %s', f'{len(existing_songs):,}')

    missing_songs = all_songs - existing_songs
    logger.info('Everything playlist is missing %s songs', f'{len(missing_songs):,}')

    for chunk in chunk_set(missing_songs):
        logger.info('Adding Songs %d', len(chunk))
        logger.debug('Song Ids %r', chunk)
        ytmusic.add_playlist_items(playlist_id, chunk)


def main() -> None:
    """Connect to YouTube Music and sync the songs in my library to the Everything playlist."""
    logger = logging.getLogger(__name__)
    if os.path.isfile('oauth.json'):
        ytmusic = YTMusic('oauth.json')
        update_playlist(ytmusic)
    else:
        logger.error('Missing YouTube OAuth JSON file')


if __name__ == '__main__':
    logging.basicConfig(handlers=[create_handler()], level=logging.INFO)
    try:
        main()
    except KeyboardInterrupt:
        logging.info('Exiting')
        # Exit without a useless stack trace
        sys.exit(130)  # Script terminated by Control-C. Control-C is fatal error signal is 130
