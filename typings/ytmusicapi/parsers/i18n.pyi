from _typeshed import Incomplete
from typing import Dict, List
from ytmusicapi.navigation import CAROUSEL as CAROUSEL, CAROUSEL_TITLE as CAROUSEL_TITLE, NAVIGATION_BROWSE_ID as NAVIGATION_BROWSE_ID, nav as nav
from ytmusicapi.parsers._utils import i18n as i18n
from ytmusicapi.parsers.browsing import parse_album as parse_album, parse_content_list as parse_content_list, parse_playlist as parse_playlist, parse_related_artist as parse_related_artist, parse_single as parse_single, parse_video as parse_video

class Parser:
    lang: Incomplete
    def __init__(self, language) -> None: ...
    def get_search_result_types(self): ...
    def parse_artist_contents(self, results: List) -> Dict: ...
