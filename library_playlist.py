#!/usr/bin/env python
"""
Download all songs in my YouTube Music Library.
Create "Everything #" playlists as needed.
Add any songs from the library that are not in a playlist already.
"""
import logging
import os
import sys
import typing

from ytmusicapi import YTMusic

from common.logger import create_handler

if typing.TYPE_CHECKING:
    from ytmusicapi.mixins.library import PlaylistSummary


YOUTUBE_PLAYLIST_SIZE = 5_000


class ApiError(Exception):
    """Wrapper around error responses from the `ytmusicapi`."""


def add_songs(ytmusic: YTMusic, playlist_id: str, songs: list[str]) -> None:
    """Add songs to playlist"""
    logger = logging.getLogger(__name__)
    logger.info('Adding Songs %d', len(songs))
    logger.debug('Song Ids %r', songs)
    ytmusic.add_playlist_items(playlist_id, songs)


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


def get_everything_playlists(ytmusic: YTMusic) -> list['PlaylistSummary']:
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
        if count < YOUTUBE_PLAYLIST_SIZE:
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
        playlist_cnt = playlist_count(playlist)
        songs = playlist_songs(ytmusic, playlist['playlistId'])
        songs_cnt = len(songs)
        if songs_cnt != playlist_cnt:
            logger.warning(
                'Failed to fetch all songs from playlist %r %r',
                playlist['title'],
                playlist['playlistId'],
            )

        existing_cnt = len(existing_songs)
        existing_songs.update(songs)
        if existing_cnt + songs_cnt != len(existing_songs):
            logger.warning(
                'Dublicate songs from playlist %r %r',
                playlist['title'],
                playlist['playlistId'],
            )
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

    playlists = get_everything_playlists(ytmusic)
    logger.info('Everything Playlists Count: %r', len(playlists))

    existing_songs = get_playlist_songs(ytmusic, playlists)
    logger.info('Songs in all Playlists: %s', f'{len(existing_songs):,}')

    missing_songs = all_songs - existing_songs
    logger.info('Everything playlist is missing %s songs.', f'{len(missing_songs):,}')

    playlist_generator = get_next_playlist(ytmusic, playlists)
    playlist_id, count = next(playlist_generator)
    for chunk in chunk_set(missing_songs):
        count += len(chunk)
        if count <= YOUTUBE_PLAYLIST_SIZE:
            add_songs(ytmusic, playlist_id, chunk)
        else:
            # count remaining songs for playlist
            offset = YOUTUBE_PLAYLIST_SIZE - count
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
