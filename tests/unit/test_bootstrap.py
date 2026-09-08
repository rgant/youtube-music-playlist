"""Tests for youtube_music_library_radio.bootstrap.

Most tests pass a fake fetch function instead of a mock of `urllib`. That fake answers each call
from a payload recorded under `tests/fixtures`, or from a small dict built in the test. Two tests,
`test_fetch_bytes_error_names_the_endpoint_and_never_the_token` and
`test_http_get_json_reports_a_non_object_body_as_a_response_fault`, monkeypatch
`urllib.request.urlopen` instead, because `_fetch_bytes` is the one call in the module with no
`fetch` injection seam of its own. No test reaches the network. No test reads `oauth.json` or
`client_secret.apps.googleusercontent.com.json`.

`_MALFORMED_RESPONSES` holds one API response per field this module validates. Two tests read that
table: one calls the reader directly, and one calls `main`. The second one belongs here, beside the
table it reads. A copy of the table in `test_main.py` rots as soon as this module validates one
more field.
"""

import dataclasses
import json
import logging
import typing
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from youtube_music_library_radio.__main__ import main
from youtube_music_library_radio.bootstrap import (
    CredentialError,
    _fetch_bytes,
    _http_get_json,
    _load_json_object,
    _paginate,
    _require_dict,
    _require_str,
    access_token,
    bootstrap,
    everything_playlist_ids,
    playlist_video_ids,
)
from youtube_music_library_radio.catalogue import count_songs, open_catalogue

if typing.TYPE_CHECKING:
    import argparse
    import sqlite3

    from youtube_music_library_radio.bootstrap import _Fetch
    from youtube_music_library_radio.jsonshape import JSON

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_TOKEN = "fake-access-token"  # noqa: S105 -- a literal for tests, never a real credential
_PLAYLISTS_URL = "https://www.googleapis.com/youtube/v3/playlists"
_PLAYLIST_ITEMS_URL = "https://www.googleapis.com/youtube/v3/playlistItems"

# One well-formed page for each endpoint. A case in `_MALFORMED_RESPONSES` replaces the one page it
# breaks, so the field named in that case is the only field that can fail.
_GOOD_PLAYLISTS_PAGE: dict[str, JSON] = {"items": [{"id": "PL_A", "snippet": {"title": "Everything 1"}}]}
_GOOD_ITEMS_PAGE: dict[str, JSON] = {"items": [{"contentDetails": {"videoId": "VIDEO_1"}}]}


@dataclasses.dataclass(frozen=True)
class _MalformedResponse:
    """One API response of the wrong shape, and the endpoint and the field the error must name."""

    playlists_page: dict[str, JSON]
    items_page: dict[str, JSON]
    endpoint: str
    field: str


def _playlists_fault(page: dict[str, JSON], field: str) -> _MalformedResponse:
    """Build a case that breaks the playlists page and leaves the playlist items page well formed."""
    return _MalformedResponse(playlists_page=page, items_page=_GOOD_ITEMS_PAGE, endpoint=_PLAYLISTS_URL, field=field)


def _items_fault(page: dict[str, JSON], field: str) -> _MalformedResponse:
    """Build a case that breaks the playlist items page and leaves the playlists page well formed."""
    return _MalformedResponse(playlists_page=_GOOD_PLAYLISTS_PAGE, items_page=page, endpoint=_PLAYLIST_ITEMS_URL, field=field)


# One malformed API response per field `bootstrap` validates. Each case stands for a change of the
# response shape that a `typing.cast` turns into a `KeyError` or into a value of the wrong type.
_MALFORMED_RESPONSES = (
    pytest.param(_playlists_fault({"items": [{"id": "PL_A"}]}, "snippet"), id="snippet-absent"),
    pytest.param(_playlists_fault({"items": [{"id": "PL_A", "snippet": "Everything 1"}]}, "snippet"), id="snippet-not-an-object"),
    pytest.param(_playlists_fault({"items": [{"id": "PL_A", "snippet": {}}]}, "title"), id="title-absent"),
    pytest.param(_playlists_fault({"items": [{"id": "PL_A", "snippet": {"title": 1}}]}, "title"), id="title-not-a-string"),
    pytest.param(_playlists_fault({"items": [{"snippet": {"title": "Everything 1"}}]}, "id"), id="id-absent"),
    pytest.param(_playlists_fault({"items": [{"id": 1, "snippet": {"title": "Everything 1"}}]}, "id"), id="id-not-a-string"),
    pytest.param(_playlists_fault({"items": "PL_A"}, "items"), id="items-not-a-list"),
    pytest.param(_playlists_fault({"items": ["PL_A"]}, "items"), id="an-items-entry-not-an-object"),
    pytest.param(_playlists_fault({"items": [], "nextPageToken": 2}, "nextPageToken"), id="page-token-not-a-string"),
    pytest.param(_items_fault({"items": [{"id": "ITEM_1"}]}, "contentDetails"), id="content-details-absent"),
    pytest.param(_items_fault({"items": [{"contentDetails": "VIDEO_1"}]}, "contentDetails"), id="content-details-not-an-object"),
    pytest.param(_items_fault({"items": [{"contentDetails": {}}]}, "videoId"), id="video-id-absent"),
    pytest.param(_items_fault({"items": [{"contentDetails": {"videoId": 1}}]}, "videoId"), id="video-id-not-a-string"),
)


def _load_fixture(name: str) -> dict[str, JSON]:
    """Load one recorded JSON payload from tests/fixtures."""
    return typing.cast("dict[str, JSON]", json.loads((_FIXTURES / name).read_text(encoding="utf-8")))


def _fetch_pages(playlists_page: dict[str, JSON], items_page: dict[str, JSON]) -> _Fetch:
    """Build a fake fetch that answers the playlists endpoint with one page and the items endpoint with another.

    A page with no `nextPageToken` ends the paging, so each endpoint answers one request.
    """

    def _fetch(url: str, headers: dict[str, str]) -> dict[str, JSON]:
        del headers
        if url.startswith(_PLAYLISTS_URL):
            return playlists_page
        return items_page

    return _fetch


def _fetch_case(case: _MalformedResponse) -> _Fetch:
    """Build a fake fetch that answers each endpoint with the page `case` holds for it."""
    return _fetch_pages(case.playlists_page, case.items_page)


class _FakeResponse:
    """A stand-in for the `http.client.HTTPResponse` that `urllib.request.urlopen` returns.

    Carries the one body `_fetch_bytes` reads. `urllib` is a boundary this project does not own, so
    a test that needs a response body builds one here instead of reaching the network.
    """

    def __init__(self, body: bytes) -> None:
        self._body: bytes = body

    def __enter__(self) -> typing.Self:
        """Return this response, as `http.client.HTTPResponse` does."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close nothing. This fake holds no socket."""
        del args

    def read(self) -> bytes:
        """Return the body this fake was built with."""
        return self._body


def test_only_everything_playlists_survive_the_filter() -> None:
    """`everything_playlist_ids` keeps a title that starts with "Everything " and drops a bare-prefix look-alike.

    The fixture carries `EverythingElse` and `Liked Music` alongside two real `Everything N`
    playlists. A filter on the bare word `Everything` lets `EverythingElse` through.
    """
    page_one = _load_fixture("playlists_page.json")
    calls: list[str] = []

    def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, JSON]:
        calls.append(url)
        assert headers["Authorization"] == f"Bearer {_TOKEN}"
        if len(calls) == 1:
            return page_one
        return {"items": []}  # ends paging after the one recorded page

    ids = everything_playlist_ids(_TOKEN, fetch=fake_fetch)

    assert ids == ["PL_EVERYTHING_1", "PL_EVERYTHING_2"]


def test_paging_follows_the_next_page_token() -> None:
    """`_paginate` follows `nextPageToken` and yields the items of every page.

    Page one is the recorded fixture, which carries `nextPageToken: "PLAYLISTS_PAGE_2"`. Page two
    is a second, distinct payload the fake serves only when it sees that token in the request URL.
    A stub that serves page one alone leaves `PL_EVERYTHING_3` out of the result.
    """
    page_one = _load_fixture("playlists_page.json")
    page_two: dict[str, JSON] = {"items": [{"id": "PL_EVERYTHING_3", "snippet": {"title": "Everything 3"}}]}

    def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, JSON]:
        del headers
        if "pageToken=PLAYLISTS_PAGE_2" in url:
            return page_two
        return page_one

    items = list(_paginate(_PLAYLISTS_URL, {"part": "snippet", "mine": "true"}, _TOKEN, fake_fetch))

    ids = [item["id"] for item in items]
    assert "PL_EVERYTHING_1" in ids  # from page one
    assert "PL_EVERYTHING_3" in ids  # from page two


def test_paging_raises_when_the_next_page_token_repeats() -> None:
    """`_paginate` raises `RuntimeError` naming the endpoint instead of looping forever.

    A malformed or looping endpoint can answer every request with the same `nextPageToken`. Without
    a guard, the helper requests that token forever and never returns to its caller.
    """
    looping_page: dict[str, JSON] = {"items": [{"id": "A"}], "nextPageToken": "SAME_TOKEN"}

    def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, JSON]:
        del url, headers
        return looping_page

    generator = _paginate(_PLAYLISTS_URL, {}, _TOKEN, fake_fetch)

    with pytest.raises(RuntimeError, match=_PLAYLISTS_URL):
        _ = list(generator)


def test_playlist_items_yield_video_ids() -> None:
    """`bootstrap` builds one catalogue row for each video ID this function returns.

    A dropped item leaves that song out of the seeded catalogue.
    """
    page = _load_fixture("playlist_items_page.json")

    def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, JSON]:
        del url, headers
        return page

    video_ids = playlist_video_ids(_TOKEN, "PL_EVERYTHING_1", fetch=fake_fetch)

    assert video_ids == ["VIDEO_AAAAAAAAAAA", "VIDEO_BBBBBBBBBBB", "VIDEO_CCCCCCCCCCC"]


def test_bootstrap_adds_one_row_for_each_video_id(tmp_path: Path) -> None:
    """`bootstrap` reads every Everything playlist and merges each one.

    A stop after the first playlist seeds a catalogue that holds part of the library and reports
    success.
    """
    conn = open_catalogue(tmp_path / "catalogue.sqlite3")
    playlist_videos = {
        "PL_A": ["VIDEO_1", "VIDEO_2"],
        "PL_B": ["VIDEO_3", "VIDEO_4", "VIDEO_5"],
    }
    playlists_page: dict[str, JSON] = {
        "items": [
            {"id": "PL_A", "snippet": {"title": "Everything 1"}},
            {"id": "PL_B", "snippet": {"title": "Everything 2"}},
        ]
    }

    def fake_fetch(url: str, headers: dict[str, str]) -> dict[str, JSON]:
        del headers
        if url.startswith(_PLAYLISTS_URL):
            return playlists_page
        for playlist_id, video_ids in playlist_videos.items():
            if f"playlistId={playlist_id}" in url:
                return {"items": [{"contentDetails": {"videoId": video_id}} for video_id in video_ids]}
        raise AssertionError(url)

    added = bootstrap(conn, _TOKEN, fetch=fake_fetch)

    assert added == 5
    assert count_songs(conn) == 5
    conn.close()


def test_fetch_bytes_error_names_the_endpoint_and_never_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_fetch_bytes` names the method and URL on a transport failure, and never a header value.

    A future edit that logs `request.headers` for a debug session leaks a bearer token. This test
    uses a recognizable sentinel token. A message that echoes it therefore fails the last assertion,
    and never passes by coincidence.
    """
    sentinel_token = "SENTINEL-TOKEN-DO-NOT-LEAK"  # noqa: S105 -- a marker, never a real credential

    def fake_urlopen(*args: object, **kwargs: object) -> bytes:
        del args, kwargs
        reason = "connection refused"
        raise urllib.error.URLError(reason)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    request = urllib.request.Request(_PLAYLISTS_URL, headers={"Authorization": f"Bearer {sentinel_token}"})

    with pytest.raises(RuntimeError) as exc_info:
        _ = _fetch_bytes(request)

    message = str(exc_info.value)
    assert _PLAYLISTS_URL in message
    assert "GET" in message
    assert sentinel_token not in message


def test_access_token_rejects_a_non_https_token_uri(tmp_path: Path) -> None:
    """The token request carries the client secret and the refresh token in its body.

    An `http` `token_uri` sends the secret and the token across the network in clear text.
    """
    oauth_path = tmp_path / "oauth.json"
    _ = oauth_path.write_text(json.dumps({"refresh_token": "a-refresh-token"}), encoding="utf-8")
    client_secret_path = tmp_path / "client_secret.json"
    _ = client_secret_path.write_text(
        json.dumps({"installed": {"client_id": "an-id", "client_secret": "a-secret", "token_uri": "http://evil.example/token"}}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="https"):
        _ = access_token(oauth_path, client_secret_path)


def test_load_json_object_parses_a_well_formed_file(tmp_path: Path) -> None:
    """`access_token` reads `oauth.json` and the client secret file through this function.

    If it rejects a correct file, `bootstrap` gets no token and reads no playlist.
    """
    path = tmp_path / "oauth.json"
    _ = path.write_text(json.dumps({"refresh_token": "a-refresh-token"}), encoding="utf-8")

    data = _load_json_object(path)

    assert data == {"refresh_token": "a-refresh-token"}


def test_load_json_object_raises_when_the_top_level_value_is_not_an_object(tmp_path: Path) -> None:
    """`main` reports a `CredentialError` as one log line that names the file.

    A list that reaches the field checks raises `AttributeError` and gives the owner a traceback.
    """
    path = tmp_path / "oauth.json"
    _ = path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    with pytest.raises(CredentialError, match="must be an object"):
        _ = _load_json_object(path)


def test_require_str_returns_a_present_string_field() -> None:
    """`access_token` reads every credential field through `_require_str`.

    A check that rejects a correct value stops `bootstrap` before it reads one playlist.
    """
    value = _require_str({"refresh_token": "a-refresh-token"}, "refresh_token", source="test", error=CredentialError)

    assert value == "a-refresh-token"


def test_require_str_raises_naming_the_file_and_field_when_missing(tmp_path: Path) -> None:
    """The owner keeps `oauth.json` and the client secret file side by side.

    A message without the path and the field name leaves the owner to guess which file to correct.
    """
    path = tmp_path / "oauth.json"
    _ = path.write_text(json.dumps({}), encoding="utf-8")
    data = _load_json_object(path)

    with pytest.raises(CredentialError, match="refresh_token") as exc_info:
        _ = _require_str(data, "refresh_token", source=path, error=CredentialError)

    assert str(path) in str(exc_info.value)


def test_require_str_raises_when_the_field_is_not_a_string(tmp_path: Path) -> None:
    """A field of the wrong type is as fatal as an absent field.

    Without this check, a wrong `client_id` reaches the token request. The later error then names
    `token_uri` and not the credential file that holds the fault.
    """
    path = tmp_path / "client_secret.json"
    _ = path.write_text(json.dumps({"client_id": 12345}), encoding="utf-8")
    data = _load_json_object(path)

    with pytest.raises(CredentialError, match="client_id"):
        _ = _require_str(data, "client_id", source=path, error=CredentialError)


def test_require_str_error_never_echoes_the_offending_value(tmp_path: Path) -> None:
    """`_require_str`'s error never echoes a wrongly-typed value, which can still hold a secret fragment."""
    path = tmp_path / "client_secret.json"
    sentinel = "SENTINEL-VALUE-DO-NOT-LEAK"
    _ = path.write_text(json.dumps({"client_secret": [sentinel]}), encoding="utf-8")
    data = _load_json_object(path)

    with pytest.raises(CredentialError) as exc_info:
        _ = _require_str(data, "client_secret", source=path, error=CredentialError)

    assert sentinel not in str(exc_info.value)


def test_require_dict_raises_when_the_field_is_not_an_object(tmp_path: Path) -> None:
    """`access_token` reads `client_id`, `client_secret`, and `token_uri` out of `installed`.

    A string `installed` raises `AttributeError` on the next field read and gives the owner a
    traceback.
    """
    path = tmp_path / "client_secret.json"
    _ = path.write_text(json.dumps({"installed": "not-an-object"}), encoding="utf-8")
    data = _load_json_object(path)

    with pytest.raises(CredentialError, match="installed"):
        _ = _require_dict(data, "installed", source=path, error=CredentialError)


def test_http_get_json_parses_an_object_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_http_get_json` is the default `fetch` for `everything_playlist_ids`, `playlist_video_ids`, and `bootstrap`.

    Every test of them injects a fake fetch, so a direct call is the one way to prove the shipped
    reader parses a real body.
    """

    def fake_urlopen(*args: object, **kwargs: object) -> _FakeResponse:
        del args, kwargs
        return _FakeResponse(b'{"items": [], "nextPageToken": "PAGE_2"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    payload = _http_get_json(_PLAYLISTS_URL, {"Authorization": f"Bearer {_TOKEN}"})

    assert payload == {"items": [], "nextPageToken": "PAGE_2"}


def test_http_get_json_reports_a_non_object_body_as_a_response_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_http_get_json` raises `RuntimeError` naming the URL when the body is not a JSON object.

    A response body is not a credential. A `CredentialError` here sends the owner to `oauth.json`,
    which is a file with nothing wrong in it.
    """

    def fake_urlopen(*args: object, **kwargs: object) -> _FakeResponse:
        del args, kwargs
        return _FakeResponse(b'["not", "an", "object"]')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="must be an object") as caught:
        _ = _http_get_json(_PLAYLISTS_URL, {"Authorization": f"Bearer {_TOKEN}"})

    assert _PLAYLISTS_URL in str(caught.value)


@pytest.mark.parametrize("case", _MALFORMED_RESPONSES)
def test_a_malformed_api_response_names_the_endpoint_and_the_field(conn: sqlite3.Connection, case: _MalformedResponse) -> None:
    """`bootstrap` raises `RuntimeError` naming the endpoint and the field for each malformed response.

    Every case of `_MALFORMED_RESPONSES` reaches one field this module checks. A `typing.cast` in
    place of that check raises `KeyError` for an absent field, and returns a value of the wrong type
    for a present one. Neither is a `RuntimeError`, so this test fails on each.

    `RuntimeError` is the narrowest type that fits. `CredentialError` subclasses `TypeError`, so a
    case that reported a credential fault fails here too.
    """
    with pytest.raises(RuntimeError) as caught:
        _ = bootstrap(conn, _TOKEN, fetch=_fetch_case(case))

    message = str(caught.value)
    assert case.endpoint in message
    assert f"required field {case.field!r}" in message


@pytest.mark.parametrize("case", _MALFORMED_RESPONSES)
def test_main_reports_a_malformed_api_response_as_operator_error(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture, case: _MalformedResponse
) -> None:
    """`main` turns each malformed response into one log line and a non-zero code, and not a traceback.

    The real `bootstrap` and the real checks run here. The fetch alone is a fake. A `typing.cast` in
    place of a check raises `KeyError`. `__main__._EXPECTED_FAILURES` does not name `KeyError`, so
    the call then raises out of `main` and the test errors.
    """

    def _handle(args: argparse.Namespace) -> int:
        del args
        return bootstrap(conn, _TOKEN, fetch=_fetch_case(case))

    with caplog.at_level(logging.ERROR):
        code = main(["bootstrap"], handlers={"bootstrap": _handle})

    assert code != 0
    assert case.endpoint in caplog.text
    assert f"required field {case.field!r}" in caplog.text


def test_a_credential_fault_and_a_response_fault_raise_different_types(tmp_path: Path) -> None:
    """A credential file with a missing field raises `CredentialError`. A malformed response raises `RuntimeError`.

    The two faults send the owner to two different places, so they must keep two different types.
    `CredentialError` subclasses `TypeError` and stands apart from `RuntimeError` today. If one type
    ever becomes a subclass of the other, each assertion below fails. That change is the one that
    lets the two faults merge again.
    """
    oauth_path = tmp_path / "oauth.json"
    _ = oauth_path.write_text(json.dumps({"token_type": "Bearer"}), encoding="utf-8")

    with pytest.raises(CredentialError) as credential:
        _ = _require_str(_load_json_object(oauth_path), "refresh_token", source=oauth_path, error=CredentialError)
    with pytest.raises(RuntimeError) as response:
        _ = everything_playlist_ids(_TOKEN, fetch=_fetch_pages({"items": [{"id": "PL_A"}]}, _GOOD_ITEMS_PAGE))

    assert not isinstance(credential.value, RuntimeError)
    assert not isinstance(response.value, CredentialError)
