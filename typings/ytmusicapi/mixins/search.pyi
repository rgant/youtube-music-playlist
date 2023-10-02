from ytmusicapi.parsers.search import *
from typing import Dict, List, Union
from ytmusicapi.continuations import get_continuations as get_continuations

class SearchMixin:
    def search(self, query: str, filter: str = ..., scope: str = ..., limit: int = ..., ignore_spelling: bool = ...) -> List[Dict]: ...
    def get_search_suggestions(self, query: str, detailed_runs: bool = ...) -> Union[List[str], List[Dict]]: ...
