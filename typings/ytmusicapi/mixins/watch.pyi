from ytmusicapi.parsers.watch import *
from typing import Dict, List, Union
from ytmusicapi.continuations import get_continuations as get_continuations
from ytmusicapi.parsers.playlists import validate_playlist_id as validate_playlist_id

class WatchMixin:
    def get_watch_playlist(self, videoId: str = ..., playlistId: str = ..., limit: int = ..., radio: bool = ..., shuffle: bool = ...) -> Dict[str, Union[List[Dict]]]: ...
