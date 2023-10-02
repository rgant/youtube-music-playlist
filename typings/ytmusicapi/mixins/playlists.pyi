from typing import Any, Literal, NotRequired, Optional, TypedDict

from ytmusicapi.continuations import *
from ytmusicapi.navigation import *
from ytmusicapi.parsers.playlists import *
from ytmusicapi.helpers import sum_total_duration as sum_total_duration, to_int as to_int
from ytmusicapi.parsers.browsing import parse_content_list as parse_content_list, parse_playlist as parse_playlist

from .. import _types
from ._utils import *

Error = dict[str, Any]

Playlist = TypedDict(
    'Playlist',
    {
        'author': NotRequired[_types.Identifier],
        'description': str,
        'duration': str | None,
        'duration_seconds': int,
        'id': str,
        'privacy': Privacy,
        'related': list[Any],
        'thumbnails': list[_types.Thumbnail],
        'title': str,
        'trackCount': int,
        'tracks': list[_types.Song],
        'views': None,
    },
)

Privacy = Literal['PUBLIC', 'PRIVATE', 'UNLISTED']


class PlaylistsMixin:
    def get_playlist(
        self,
        playlistId: str,
        limit: Optional[int] = 100,
        related: bool = False,
        suggestions_limit: int = 0,
    ) -> Playlist: ...
    def create_playlist(
        self,
        title: str,
        description: str,
        privacy_status: Privacy = 'PRIVATE',
        video_ids: Optional[list[str]] = None,
        source_playlist: Optional[str] = None,
    ) -> str | Error: ...
    def edit_playlist(
        self,
        playlistId: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        privacyStatus: Optional[Privacy] = None,
        moveItem: Optional[tuple[str, str]] = None,
        addPlaylistId: Optional[str] = None,
    ) -> str | Error: ...
    def delete_playlist(self, playlistId: str) -> str | Error: ...
    def add_playlist_items(
        self,
        playlistId: str,
        videoIds: Optional[list[str]] = None,
        source_playlist: Optional[str] = None,
        duplicates: bool = False,
    ) -> str | Error: ...
    def remove_playlist_items(self, playlistId: str, videos: list[Playlist]) -> str | Error: ...
