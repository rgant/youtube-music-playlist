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

if typing.TYPE_CHECKING:
    from ytmusicapi.mixins.library import PlaylistSummary


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


def add_songs(ytmusic: YTMusic, playlist_id: str, songs: list[str]) -> None:
    """Add songs to playlist"""
    logger = logging.getLogger(__name__)
    logger.info('Adding Songs %d', len(songs))
    logger.debug('Song Ids %r', songs)
    ytmusic.add_playlist_items(playlist_id, songs)


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
        yield song_ids[i : i + step]


def create_playlist(ytmusic: YTMusic, indx: int) -> str:
    """Create a new Everything playlist."""
    ret = ytmusic.create_playlist(
        title=f'Everything {indx}',
        description='Every song in my library',
        privacy_status='PRIVATE',
    )
    if isinstance(ret, str):
        return ret

    logger = logging.getLogger(__name__)
    logger.error('Failed to create Everything playlist\n%r', ret)
    raise ApiError(ret)


def get_everything_playlist(ytmusic: YTMusic) -> list['PlaylistSummary']:
    """Finds or creates the 'Everything' Playlist and returns its ID."""
    playlists = ytmusic.get_library_playlists()
    everything_lists = [pl for pl in playlists if pl['title'].startswith('Everything ')]
    return everything_lists


def get_next_playlist(
    ytmusic: YTMusic, playlists: list['PlaylistSummary']
) -> typing.Generator[tuple[str, int], None, None]:
    """Get or create the next playlist that has room for songs."""
    for playlist in playlists:
        count = playlist_count(playlist)
        if count < 5000:
            yield playlist['playlistId'], count

    cnt = len(playlists)
    while True:
        cnt += 1
        yield create_playlist(ytmusic, cnt), 0


def get_playlist_songs(ytmusic: YTMusic, playlists: list['PlaylistSummary']) -> set[str]:
    """Get Song IDs for all playlists."""
    logger = logging.getLogger(__name__)
    existing_songs: set[str] = set()
    for playlist in playlists:
        count = playlist_count(playlist)
        songs = playlist_songs(ytmusic, playlist['playlistId'])
        if len(songs) != count:
            logger.warning(
                'Failed to fetch all songs from playlist %r %r',
                playlist['title'],
                playlist['playlistId'],
            )
        existing_songs.update(songs)
    return existing_songs


def library_songs(ytmusic: YTMusic) -> set[str]:
    """Fetch every song in my library except 'Radio Edit's."""
    songs = ytmusic.get_library_songs(limit=None)
    return {s['videoId'] for s in songs if 'radio edit' not in s['title'].lower()}


def playlist_count(playlist: 'PlaylistSummary') -> int:
    """Convert Playlist count string to integer."""
    return int(playlist.get('count', '0').replace(',', ''))


def playlist_songs(ytmusic: YTMusic, playlist_id: str) -> set[str]:
    """Fetch every song in the 'Everything' playlist."""
    playlist = ytmusic.get_playlist(playlist_id, limit=None)
    return {t['videoId'] for t in playlist['tracks']}


def update_playlist(ytmusic: YTMusic) -> None:
    """Update the 'Everything' playlist with any missing songs from the Library."""
    logger = logging.getLogger(__name__)

    all_songs = library_songs(ytmusic)
    logger.info('All Songs in Library: %s', f'{len(all_songs):,}')

    playlists = get_everything_playlist(ytmusic)
    logger.info('Everything Playlists Count: %r', len(playlists))

    existing_songs = get_playlist_songs(ytmusic, playlists)
    logger.info('Songs in all Playlists: %s', f'{len(existing_songs):,}')

    missing_songs = all_songs - existing_songs
    logger.info('Everything playlist is missing %s songs.', f'{len(missing_songs):,}')

    playlist_generator = get_next_playlist(ytmusic, playlists)
    playlist_id, count = next(playlist_generator)
    for chunk in chunk_set(missing_songs):
        count += len(chunk)
        if count <= 5000:
            add_songs(ytmusic, playlist_id, chunk)
        else:
            # count remaining songs for playlist
            offset = 5001 - count
            # Add remaining songs to current playlist, if there are any
            if offset > 0:
                add_songs(ytmusic, playlist_id, chunk[:offset])

            # Add the rest of the songs to the next playlist
            playlist_id, count = next(playlist_generator)
            add_songs(ytmusic, playlist_id, chunk[offset:])
            count += len(chunk[:offset])


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
