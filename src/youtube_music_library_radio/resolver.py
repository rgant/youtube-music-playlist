"""Turn a stored video ID into a playable audio URL, via `yt-dlp`.

`yt-dlp` fails often against this catalogue: deleted videos, region locks, age restrictions. This
module wraps every `yt-dlp` exception in `ResolutionError`. The caller then skips one song and the
station keeps playing. An unhandled exception ends the stream instead.

The wrapper says whether a retry can help. `PermanentResolutionError` names a video that is gone,
and `TransientResolutionError` names an attempt that can succeed later. YouTube answers a valid
request with HTTP 403 at a rate that no request option changes, and the same video plays on the
next try. See <https://github.com/yt-dlp/yt-dlp/issues/17395>, which records that behaviour with no
root cause and no fix.

That split protects the catalogue. `station` deletes a song after `prune_threshold` failures, so a
transient fault that counts against a song erodes a library of working songs. An unrecognised
message is therefore transient, because the safe default is the one that keeps the row.
"""

import dataclasses
import typing

import yt_dlp

if typing.TYPE_CHECKING:
    from collections.abc import Mapping

_WATCH_URL_PREFIX = "https://music.youtube.com/watch?v="


@dataclasses.dataclass(frozen=True)
class Resolved:
    """A video ID resolved to a playable audio stream."""

    video_id: str
    title: str
    artist: str
    audio_url: str


class ResolutionError(Exception):
    """`yt-dlp` cannot resolve a video ID to a playable audio stream."""


class PermanentResolutionError(ResolutionError):
    """The video is gone, private, or unplayable. A retry cannot help, so the row can go."""


class TransientResolutionError(ResolutionError):
    """The attempt failed for a reason that can clear. A retry can help, so the row must stay."""


# A yt-dlp message that holds one of these names a video that no retry recovers. Compare in lower
# case. Every other message is transient, which keeps the row in the catalogue.
_PERMANENT_MARKERS = (
    "video unavailable",
    "private video",
    "has been removed",
    "drm protected",
    "members-only",
    "account associated with this video has been terminated",
    "who has blocked it",
)


def _classify(video_id: str, cause: BaseException) -> ResolutionError:
    """Build the error for a failed resolve of `video_id`. `cause` decides which class it gets."""
    text = str(cause).lower()
    if any(marker in text for marker in _PERMANENT_MARKERS):
        return PermanentResolutionError(f"could not resolve video_id {video_id!r}: the video is gone")
    return TransientResolutionError(f"could not resolve video_id {video_id!r}: the attempt failed and can succeed later")


def resolve(video_id: str) -> Resolved:
    """Resolve `video_id` to its title, artist, and a playable audio URL.

    Raises `PermanentResolutionError` for a video that is gone, private, or DRM protected. Raises
    `TransientResolutionError` for every other failure. That covers a 403, a bot check, a transport
    failure, and a response with no title or no playable URL.

    This function does not retry. The caller decides how many attempts one song gets.
    """
    try:
        watch_url = _WATCH_URL_PREFIX + video_id
        # The installed yt-dlp stubs type this field `str | None`. The runtime code of yt-dlp
        # treats it as a bool. The cast corrects the stub's type, not ours.
        skip_download = True
        with yt_dlp.YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
                "skip_download": typing.cast("str | None", typing.cast("object", skip_download)),
                "format": "bestaudio",
            }
        ) as ydl:
            info = ydl.extract_info(watch_url, download=False)
        return _to_resolved(video_id, info)
    except ResolutionError:
        # `_to_resolved` already classified this one. A second wrap hides that verdict behind the
        # text of the wrapper.
        raise
    except Exception as exc:
        raise _classify(video_id, exc) from exc


def _to_resolved(video_id: str, info: Mapping[str, object]) -> Resolved:
    """Convert a yt-dlp info mapping into a `Resolved`.

    Raises `TransientResolutionError` for an absent or empty `title` or `url`. A song with no URL
    cannot play. An absent title is a failed resolve, and not `"Unknown"`. A later attempt can
    return a complete info dict, so neither case deletes the row.

    Reads `artist` first, then `uploader`, then `"Unknown"`. An absent artist alone never skips a
    song.
    """
    title = info.get("title")
    if not title:
        message = f"video_id {video_id!r}: yt-dlp returned no title"
        raise TransientResolutionError(message)

    audio_url = info.get("url")
    if not audio_url:
        message = f"video_id {video_id!r}: yt-dlp returned no audio url"
        raise TransientResolutionError(message)

    artist = info.get("artist") or info.get("uploader") or "Unknown"
    return Resolved(
        video_id=video_id,
        title=typing.cast("str", title),
        artist=typing.cast("str", artist),
        audio_url=typing.cast("str", audio_url),
    )
