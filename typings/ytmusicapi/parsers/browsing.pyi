from .songs import *
from ._utils import *

def parse_mixed_content(rows): ...
def parse_content_list(results, parse_func, key=...): ...
def parse_album(result): ...
def parse_single(result): ...
def parse_song(result): ...
def parse_song_flat(data): ...
def parse_video(result): ...
def parse_playlist(data): ...
def parse_related_artist(data): ...
def parse_watch_playlist(data): ...
