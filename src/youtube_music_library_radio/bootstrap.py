"""Fill an empty catalogue for the first time, from the owner's "Everything N" YouTube playlists.

The owner's account holds every library song's video ID, across 33 playlists named `Everything 1`
through `Everything 33`. The YouTube Data API cannot read the YouTube Music library, so those
playlists are the only route to that list. One full read costs about 354 quota units.

This module runs one time, to seed the catalogue. Nothing writes to those playlists after that
read, so they go stale by design.

Each `Song` this module builds carries an empty title and an empty artist. `merge_songs` protects a
stored non-empty value against an empty incoming one. `catalogue.record_success` fills the title and
the artist after the first successful play.
"""

import json
import logging
import typing
import urllib.error
import urllib.parse
import urllib.request

from youtube_music_library_radio.catalogue import Song, merge_songs

if typing.TYPE_CHECKING:
    import http.client
    import sqlite3
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from youtube_music_library_radio.jsonshape import JSON

_logger = logging.getLogger(__name__)

_PLAYLISTS_URL = "https://www.googleapis.com/youtube/v3/playlists"
_PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
_PAGE_SIZE = 50

# The trailing space is the point. A bare "Everything" prefix also matches "EverythingElse".
# The owner's playlists are "Everything 1" through "Everything 33".
_EVERYTHING_PREFIX = "Everything "

# One JSON fetch of one URL, as `_paginate` and every public function below take it.
# `_http_get_json` is the one this module ships. A test passes its own, so no test reaches the
# network.
type _Fetch = Callable[[str, dict[str, str]], dict[str, JSON]]


class CredentialError(TypeError):
    """A credential file holds the wrong shape: a missing field, or a field of the wrong type.

    Subclasses `TypeError` because ruff TRY004 asks for a `TypeError` after a failed type check. A
    dedicated class lets the command line report this fault to the owner as operator error. A catch
    of the builtin `TypeError` also catches a programming error in any caller, and reports that bug
    to the owner as operator error too.

    Never raised for an API response. A response that changes shape is not a credential fault, and
    a message about `oauth.json` sends the owner to a file that is correct. The API path raises
    `RuntimeError`, which `__main__._EXPECTED_FAILURES` also names.
    """


def _fetch_bytes(request: urllib.request.Request) -> bytes:
    """Send `request` and return the raw response body.

    Raises `RuntimeError` naming the request's method and URL after any transport failure. The
    message never repeats the request's headers. A caller can carry a bearer token there, and a
    token must never reach a log or an exception message.
    """
    try:
        # Each caller justifies its own URL. `_http_get_json` builds one from the fixed
        # `https://www.googleapis.com` constants below. `access_token` checks that its URL uses
        # `https` before it reaches here. Neither URL is unchecked user input.
        response = typing.cast("http.client.HTTPResponse", urllib.request.urlopen(request))  # noqa: S310
        with response:
            return response.read()
    except urllib.error.URLError as exc:
        message = f"{request.get_method()} {request.full_url} failed"
        raise RuntimeError(message) from exc


def _http_get_json(url: str, headers: dict[str, str]) -> dict[str, JSON]:
    """Fetch `url` with `headers` and parse the JSON response body.

    Raises `RuntimeError` naming `url` when the body is not JSON, or when its top-level value is not
    an object. The bearer token travels in `headers`, so `url` names an endpoint and never a secret.
    """
    # `url` is always built from the fixed `https://www.googleapis.com` constants this project
    # controls: `_PLAYLISTS_URL` and `_PLAYLIST_ITEMS_URL`, below.
    request = urllib.request.Request(url, headers=headers)  # noqa: S310
    body = _fetch_bytes(request)
    return _parse_json_object(body, source=url, error=RuntimeError)


def _parse_json_object(raw: bytes, *, source: Path | str, error: type[Exception]) -> dict[str, JSON]:
    """Parse `raw` as JSON and return its top-level object.

    Raises `RuntimeError` naming `source` when `raw` is not valid JSON. Raises `error` naming
    `source` when `raw` parses but its top-level value is not an object. `source` is a file path or
    a URL, never a secret, so it is safe to name in full.

    `error` has no default. Read `_require_str` for the reason.
    """
    try:
        # `json.loads` returns `Any`. `JSON` names every value it can return, so this cast asserts
        # nothing that a document can break. The `isinstance` below is the check that can fail.
        parsed = typing.cast("JSON", json.loads(raw))
    except json.JSONDecodeError as exc:
        message = f"{source}: could not parse as JSON"
        raise RuntimeError(message) from exc
    if not isinstance(parsed, dict):
        message = f"{source}: top-level JSON value must be an object"
        raise error(message)
    return parsed


def _load_json_object(path: Path) -> dict[str, JSON]:
    """Read `path` and parse it as a JSON object. Raises `RuntimeError` naming `path` if it cannot.

    Reads a credential file alone, so a wrong top-level value is a `CredentialError`.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        message = f"{path}: could not read the file"
        raise RuntimeError(message) from exc
    return _parse_json_object(raw, source=path, error=CredentialError)


def _field_error(source: Path | str, field: str, error: type[Exception]) -> Exception:
    """Build the error a failed field check raises. `source` and `field` name the fault.

    Every field check below reports the same thing: `source` carries no usable `field`. They share
    one message, and `error` keeps the two fault classes apart. A caller that reads a credential
    file passes `CredentialError`. A caller that reads an API response passes `RuntimeError`,
    because a changed response shape is no fault of the credential.

    The message never holds the field's value. `source` often holds credentials. A value of the
    wrong type can still carry a secret fragment inside it.
    """
    return error(f"{source}: missing or invalid required field {field!r}")


def _require_object(value: JSON, field: str, *, source: Path | str, error: type[Exception]) -> dict[str, JSON]:
    """Return `value` as a JSON object. Raises `error` naming `source` and `field` for any other type.

    `error` has no default anywhere in this module. A default is how one fault becomes the other. An
    omitted argument then decides which fault the owner reads about, and sends them to the wrong
    file.
    """
    if not isinstance(value, dict):
        raise _field_error(source, field, error)
    return value


def _require_str(data: dict[str, JSON], field: str, *, source: Path | str, error: type[Exception]) -> str:
    """Return `data[field]` as a `str`. Raises the same as `_require_object`, for the same reason."""
    value = data.get(field)
    if not isinstance(value, str):
        raise _field_error(source, field, error)
    return value


def _require_dict(data: dict[str, JSON], field: str, *, source: Path | str, error: type[Exception]) -> dict[str, JSON]:
    """Return `data[field]` as a JSON object. Raises the same as `_require_object`, for the same reason."""
    return _require_object(data.get(field), field, source=source, error=error)


def access_token(oauth_path: Path, client_secret_path: Path) -> str:
    """Exchange the refresh token stored at `oauth_path` for a fresh access token.

    Reads the token endpoint and the app's client ID and secret from `client_secret_path`, in the
    format the Google Cloud console exports for an installed app. Never logs the refresh token or
    the access token this returns.
    """
    oauth_data = _load_json_object(oauth_path)
    refresh_token = _require_str(oauth_data, "refresh_token", source=oauth_path, error=CredentialError)

    client_secret_data = _load_json_object(client_secret_path)
    installed = _require_dict(client_secret_data, "installed", source=client_secret_path, error=CredentialError)
    client_id = _require_str(installed, "client_id", source=client_secret_path, error=CredentialError)
    client_secret = _require_str(installed, "client_secret", source=client_secret_path, error=CredentialError)
    token_uri = _require_str(installed, "token_uri", source=client_secret_path, error=CredentialError)

    # `token_uri` names an endpoint, not a secret, so it is safe to name in full below.
    if urllib.parse.urlparse(token_uri).scheme != "https":
        message = f"{client_secret_path}: installed.token_uri must be an https URL, got {token_uri!r}"
        raise RuntimeError(message)

    form = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("ascii")
    # The check above holds `token_uri` to an `https` scheme. This URL comes from a credential
    # file, not from a constant, so it needs that check at run time.
    request = urllib.request.Request(  # noqa: S310
        token_uri,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    body = _fetch_bytes(request)

    # A revoked or wrong refresh token makes the token endpoint answer with no `access_token`
    # field. An absent field here is a credential fault, not a changed API shape.
    token_data = _parse_json_object(body, source=token_uri, error=CredentialError)
    return _require_str(token_data, "access_token", source=token_uri, error=CredentialError)


def _response_items(payload: dict[str, JSON], *, source: str) -> list[dict[str, JSON]]:
    """Return the `items` of one response page.

    An absent `items` gives an empty page, which ends the paging. Raises `RuntimeError` naming
    `source` and `items` for a present `items` that is not a list. Raises the same error for an
    entry of that list that is not an object.
    """
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise _field_error(source, "items", RuntimeError)
    return [_require_object(item, "items", source=source, error=RuntimeError) for item in items]


def _paginate(base_url: str, params: dict[str, str], token: str, fetch: _Fetch) -> Iterator[dict[str, JSON]]:
    """Yield every item across every page of `base_url`. `nextPageToken` names the next page.

    If a response repeats the `nextPageToken` that fetched the current page, this raises
    `RuntimeError` naming `base_url`. A malformed endpoint can loop, and this stops the loop.
    Raises the same error for a `nextPageToken` that is not a string.
    """
    headers = {"Authorization": f"Bearer {token}"}
    page_token: str | None = None
    while True:
        query = dict(params)
        if page_token is not None:
            query["pageToken"] = page_token
        url = f"{base_url}?{urllib.parse.urlencode(query)}"
        payload = fetch(url, headers)

        yield from _response_items(payload, source=base_url)

        next_token = payload.get("nextPageToken")
        if next_token is None:
            return
        if not isinstance(next_token, str):
            raise _field_error(base_url, "nextPageToken", RuntimeError)
        if next_token == page_token:
            message = f"{base_url} returned the same nextPageToken twice in a row; stopping instead of paging forever"
            raise RuntimeError(message)
        page_token = next_token


def everything_playlist_ids(token: str, *, fetch: _Fetch = _http_get_json) -> list[str]:
    """Return the playlistId of every playlist whose title starts with "Everything " (word and space).

    The owner's playlists are named `Everything 1` through `Everything 33`. A title such as
    `EverythingElse` does not start with `Everything ` and is excluded.

    Raises `RuntimeError` naming the endpoint and the field for an item with no `snippet`, no
    `snippet.title`, or no `id`. It raises the same error for any one of those of the wrong type.
    """
    params = {"part": "snippet", "mine": "true", "maxResults": str(_PAGE_SIZE)}
    playlist_ids: list[str] = []
    for item in _paginate(_PLAYLISTS_URL, params, token, fetch):
        snippet = _require_dict(item, "snippet", source=_PLAYLISTS_URL, error=RuntimeError)
        title = _require_str(snippet, "title", source=_PLAYLISTS_URL, error=RuntimeError)
        if title.startswith(_EVERYTHING_PREFIX):
            playlist_ids.append(_require_str(item, "id", source=_PLAYLISTS_URL, error=RuntimeError))
    return playlist_ids


def playlist_video_ids(token: str, playlist_id: str, *, fetch: _Fetch = _http_get_json) -> list[str]:
    """Return the video ID of every item in `playlist_id`.

    Raises `RuntimeError` naming the endpoint and the field for an item with no `contentDetails` or
    no `contentDetails.videoId`, and for either one of the wrong type.
    """
    params = {"part": "contentDetails", "maxResults": str(_PAGE_SIZE), "playlistId": playlist_id}
    video_ids: list[str] = []
    for item in _paginate(_PLAYLIST_ITEMS_URL, params, token, fetch):
        content_details = _require_dict(item, "contentDetails", source=_PLAYLIST_ITEMS_URL, error=RuntimeError)
        video_ids.append(_require_str(content_details, "videoId", source=_PLAYLIST_ITEMS_URL, error=RuntimeError))
    return video_ids


def bootstrap(conn: sqlite3.Connection, token: str, *, fetch: _Fetch = _http_get_json) -> int:
    """Read every Everything playlist and merge its video IDs into the catalogue. Return the rows added.

    Each merged `Song` carries an empty title and an empty artist. `catalogue.record_success` fills
    the title and the artist after the first successful play.
    """
    playlist_ids = everything_playlist_ids(token, fetch=fetch)
    _logger.info("bootstrap: found %d Everything playlists", len(playlist_ids))

    songs: list[Song] = []
    for playlist_id in playlist_ids:
        video_ids = playlist_video_ids(token, playlist_id, fetch=fetch)
        _logger.info("bootstrap: playlist %s holds %d video ids", playlist_id, len(video_ids))
        songs.extend(
            Song(video_id=video_id, title="", artist="", failure_count=0, last_success=None, last_played=None) for video_id in video_ids
        )

    added = merge_songs(conn, songs)
    _logger.info("bootstrap: merged %d video ids, added %d new rows", len(songs), added)
    return added
