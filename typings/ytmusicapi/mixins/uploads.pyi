from ytmusicapi.helpers import *
from ytmusicapi.navigation import *
import requests
from ._utils import prepare_order_params as prepare_order_params, validate_order_parameter as validate_order_parameter
from typing import Dict, List, Union
from ytmusicapi.continuations import get_continuations as get_continuations
from ytmusicapi.parsers.albums import parse_album_header as parse_album_header
from ytmusicapi.parsers.library import get_library_contents as get_library_contents, parse_library_albums as parse_library_albums, parse_library_artists as parse_library_artists
from ytmusicapi.parsers.uploads import parse_uploaded_items as parse_uploaded_items

class UploadsMixin:
    def get_library_upload_songs(self, limit: int = ..., order: str = ...) -> List[Dict]: ...
    def get_library_upload_albums(self, limit: int = ..., order: str = ...) -> List[Dict]: ...
    def get_library_upload_artists(self, limit: int = ..., order: str = ...) -> List[Dict]: ...
    def get_library_upload_artist(self, browseId: str, limit: int = ...) -> List[Dict]: ...
    def get_library_upload_album(self, browseId: str) -> Dict: ...
    def upload_song(self, filepath: str) -> Union[str, requests.Response]: ...
    def delete_upload_entity(self, entityId: str) -> Union[str, Dict]: ...
