import requests
from requests.structures import CaseInsensitiveDict as CaseInsensitiveDict
from typing import Dict, Optional
from ytmusicapi.auth.browser import is_browser as is_browser
from ytmusicapi.auth.oauth import YTMusicOAuth as YTMusicOAuth, is_custom_oauth as is_custom_oauth, is_oauth as is_oauth
from ytmusicapi.helpers import initialize_headers as initialize_headers

def load_headers_file(auth: str) -> Dict: ...
def prepare_headers(session: requests.Session, proxies: Optional[Dict] = ..., input_dict: Optional[CaseInsensitiveDict] = ...) -> Dict: ...
