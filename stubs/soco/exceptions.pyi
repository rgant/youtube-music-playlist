"""Local type stub for `soco.exceptions`, which ships no type information of its own.

This covers the one exception `queue.py` catches. A speaker refuses a track now and then, and
`add_to_queue` raises `SoCoUPnPException` with `error_code` "800".

`error_code` and `error_description` are strings that `SoCoUPnPException.__init__` assigns.
`error_description` is empty in every refusal measured here, so a log line names the song.
"""

class SoCoException(Exception): ...

class SoCoUPnPException(SoCoException):
    message: str
    error_code: str
    error_description: str
    error_xml: str
    def __init__(self, message: str, error_code: str, error_xml: str, error_description: str = ...) -> None: ...
