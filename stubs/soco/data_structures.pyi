"""Local type stubs for `soco.data_structures`, which ships no type information of its own.

`queue.py` builds the item it adds to the Sonos queue, because `soco.SoCo.add_uri_to_queue` sends an
item with an empty title. This stub covers only that surface: one resource and one music track.

`DidlMusicTrack` takes `creator` and `album` through `**kwargs` in the real class, and it stores each
one as an attribute of the same name. They are declared here, because a test reads them back.
"""

class DidlResource:
    uri: str
    protocol_info: str
    def __init__(self, uri: str, protocol_info: str, **kwargs: object) -> None: ...

class DidlMusicTrack:
    title: str
    parent_id: str
    item_id: str
    creator: str
    album: str
    desc: str
    resources: list[DidlResource]
    def __init__(
        self,
        title: str,
        parent_id: str,
        item_id: str,
        restricted: bool = ...,
        resources: list[DidlResource] | None = ...,
        desc: str = ...,
        **kwargs: object,
    ) -> None: ...
