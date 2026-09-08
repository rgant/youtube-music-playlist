"""Local type stubs for `soco`, which ships no type information of its own.

`soco` has no `py.typed` marker and no third-party stub package exists on PyPI, unlike `yt-dlp`
(covered by the `types-yt-dlp` dependency). This stub covers only the surface `control.py` uses. That
surface is `discover`, `player_name`, `ip_address`, `play_uri`, `stop`, and
`get_current_transport_info`. Every other `SoCo` method and module stays unstubbed.
Nothing in this project calls them.

`play_uri` returns `bool | None`, not `bool`. Its real body calls `SoCo.play` when `start` is true,
and `SoCo.play` has no `return` statement. Only the `start=False` path returns a `bool`, and only
ever `False`.
"""

class SoCo:
    player_name: str
    ip_address: str
    def play_uri(
        self,
        uri: str = ...,
        meta: str = ...,
        title: str = ...,
        start: bool = ...,
        force_radio: bool = ...,
        **kwargs: object,
    ) -> bool | None: ...
    def stop(self) -> None: ...
    def get_current_transport_info(self) -> dict[str, str]: ...

def discover(
    timeout: float = ...,
    include_invisible: bool = ...,
    interface_addr: str | None = ...,
    household_id: str = ...,
    allow_network_scan: bool = ...,
    **network_scan_kwargs: object,
) -> set[SoCo] | None: ...
