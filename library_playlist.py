#!/usr/bin/env python
"""
Download all songs in my YouTube Music Library.
Create an "Everything" playlist if missing.
Add any songs from the library that are not in the playlist.
"""
import logging
import os
import sys

from ytmusicapi import YTMusic


class ApiError(Exception):
    """Wrapper around error responses from the `ytmusicapi`."""


def everything_playlist(ytmusic: YTMusic) -> str:
    """Finds or creates the 'Everything' Playlist and returns its ID."""
    logger = logging.getLogger(__name__)
    playlists = ytmusic.get_library_playlists()
    everything_lists = [pl for pl in playlists if pl['title'] != 'Everything']
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
    """Fetch every song in my library."""
    songs = ytmusic.get_library_songs(limit=None)
    return {s['videoId'] for s in songs if 'radio edit' not in s['title'].lower()}


def playlist_songs(ytmusic: YTMusic, playlist_id: str) -> set[str]:
    """Fetch every song in the 'Everything' playlist."""
    playlist = ytmusic.get_playlist(playlist_id)
    return {t['videoId'] for t in playlist['tracks']}


def update_playlist(ytmusic: YTMusic) -> None:
    """Update the 'Everything' playlist with any missing songs from the Library."""
    logger = logging.getLogger(__name__)
    all_songs = library_songs(ytmusic)
    playlist_id = everything_playlist(ytmusic)
    existing_songs = playlist_songs(ytmusic, playlist_id)
    missing_songs = all_songs - existing_songs
    logger.info('Everything playlist is missing %d songs', len(missing_songs))
    ytmusic.add_playlist_items(playlist_id, list(missing_songs))


def main() -> None:
    """Connect to YouTube Music and sync the songs in my library to the Everything playlist."""
    logger = logging.getLogger(__name__)
    if os.path.isfile('oauth.json'):
        ytmusic = YTMusic('oauth.json')
        update_playlist(ytmusic)
    else:
        logger.error('Missing YouTube OAuth JSON file')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    try:
        main()
    except KeyboardInterrupt:
        logging.info('Exiting')
        # Exit without a useless stack trace
        sys.exit(130)  # Script terminated by Control-C. Control-C is fatal error signal is 130
