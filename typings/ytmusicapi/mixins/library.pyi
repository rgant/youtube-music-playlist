from typing import Dict, List, Literal, NotRequired, Optional, TypedDict

from ytmusicapi.continuations import *
from ytmusicapi.parsers.browsing import *
from ytmusicapi.parsers.library import *
from .. import _types
from ._utils import *


Order = Literal['a_to_z', 'z_to_a', 'recently_added']

PlaylistSummary = TypedDict(
    'Playlist',
    {
        'author': NotRequired[list[_types.Identifier]],
        'count': NotRequired[str],
        'description': str,
        'playlistId': str,
        'thumbnails': list[_types.Thumbnail],
        'title': str,
    },
)


class LibraryMixin:
    def get_library_playlists(self, limit: Optional[int] = 25) -> List[PlaylistSummary]: ...
    def get_library_songs(
        self,
        limit: Optional[int] = 25,
        validate_responses: bool = False,
        order: Optional[Order] = None,
    ) -> List[_types.Song]: ...
    def get_library_albums(self, limit: int = ..., order: str = ...) -> List[Dict]: ...
    def get_library_artists(self, limit: int = ..., order: str = ...) -> List[Dict]: ...
    def get_library_subscriptions(self, limit: int = ..., order: str = ...) -> List[Dict]: ...
    def get_liked_songs(self, limit: int = ...) -> Dict: ...
    def get_history(self) -> List[Dict]: ...
    def add_history_item(self, song): ...
    def remove_history_items(self, feedbackTokens: List[str]) -> Dict: ...
    def rate_song(self, videoId: str, rating: str = ...) -> Dict: ...
    def edit_song_library_status(self, feedbackTokens: List[str] = ...) -> Dict: ...
    def rate_playlist(self, playlistId: str, rating: str = ...) -> Dict: ...
    def subscribe_artists(self, channelIds: List[str]) -> Dict: ...
    def unsubscribe_artists(self, channelIds: List[str]) -> Dict: ...
