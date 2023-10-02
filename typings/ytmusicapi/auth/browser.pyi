from ytmusicapi.helpers import *
from _typeshed import Incomplete
from requests.structures import CaseInsensitiveDict as CaseInsensitiveDict

path: Incomplete

def is_browser(headers: CaseInsensitiveDict) -> bool: ...
def setup_browser(filepath: Incomplete | None = ..., headers_raw: Incomplete | None = ...): ...
