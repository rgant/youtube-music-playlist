from typing import Optional

from requests.sessions import Session
from ytmusicapi.auth.oauth import YTMusicOAuth


def setup(filepath: Optional[str] = None, headers_raw: Optional[str] = None) -> str: ...
def setup_oauth(
    filepath: Optional[str] = None,
    session: Optional[Session] = None,
    proxies: Optional[dict[str, str]] = None,
    open_browser: bool = False,
) -> YTMusicOAuth: ...
