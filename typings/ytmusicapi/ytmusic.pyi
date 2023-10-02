from typing import Optional, Self, Type, TypedDict

from requests.structures import CaseInsensitiveDict

from ytmusicapi.helpers import *
from ytmusicapi.auth.headers import load_headers_file as load_headers_file, prepare_headers as prepare_headers
from ytmusicapi.auth.oauth import YTMusicOAuth as YTMusicOAuth, is_oauth as is_oauth
from ytmusicapi.mixins.browsing import BrowsingMixin as BrowsingMixin
from ytmusicapi.mixins.explore import ExploreMixin as ExploreMixin
from ytmusicapi.mixins.library import LibraryMixin as LibraryMixin
from ytmusicapi.mixins.playlists import PlaylistsMixin as PlaylistsMixin
from ytmusicapi.mixins.search import SearchMixin as SearchMixin
from ytmusicapi.mixins.uploads import UploadsMixin as UploadsMixin
from ytmusicapi.mixins.watch import WatchMixin as WatchMixin
from ytmusicapi.parsers.i18n import Parser as Parser


Context = TypedDict(
    'Context',
    {
        'context': dict[str, str],
        'user': dict[str, str],
    },
)


class YTMusic(BrowsingMixin, SearchMixin, WatchMixin, ExploreMixin, LibraryMixin, PlaylistsMixin, UploadsMixin):
    auth: Optional[str]
    input_dict: Optional[CaseInsensitiveDict[str]]
    is_oauth_auth: bool
    proxies: Optional[dict[str, str]]
    cookies: dict[str, str]
    headers: dict[str, str]
    context: Context
    language: str
    lang: str
    parser: Parser
    is_browser_auth: bool
    sapisid: str
    def __init__(
        self,
        auth: Optional[str] = None,
        user: Optional[str] = None,
        requests_session: bool = True,
        proxies: Optional[dict[str, str]] = None,
        language: str = 'en',
        location: str = '',
    ) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        execType: None = None,
        execValue: None = None,
        trackback: None = None,
    ) -> None: ...
