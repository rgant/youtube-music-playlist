"""Tests for youtube_music_library_radio.refresh.

Every test passes a fake client object instead of a mock of `ytmusicapi`. That fake is a small
class. Its `get_library_songs` method returns a payload recorded under tests/fixtures, or a payload
built inline for one edge case.

One test, `test_malformed_headers_file_content_raises_a_named_error`, uses the real
`ytmusicapi.YTMusic` client on purpose. A malformed headers file fails inside the `YTMusic`
constructor, before any request goes out.

No test reaches the network or reads a real headers file.
"""

import json
import logging
import typing
from pathlib import Path

import pytest
from ytmusicapi.exceptions import YTMusicUserError

from testdoubles import FakeLibraryClient
from youtube_music_library_radio.catalogue import Song, count_songs, merge_songs, songs_by_video_id
from youtube_music_library_radio.refresh import _EXCLUDED_PATTERNS, ExcludedSong, _compile_matchers, library_songs, refresh

if typing.TYPE_CHECKING:
    import sqlite3

    from youtube_music_library_radio.jsonshape import JSON

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load_fixture(name: str) -> list[dict[str, JSON]]:
    """Load one recorded JSON payload from tests/fixtures."""
    return typing.cast("list[dict[str, JSON]]", json.loads((_FIXTURES / name).read_text(encoding="utf-8")))


_FakeClient = FakeLibraryClient


def _headers_file(tmp_path: Path) -> Path:
    """Write a placeholder headers file. Its content is never read: every test injects a fake client."""
    headers_path = tmp_path / "browser.json"
    _ = headers_path.write_text("{}", encoding="utf-8")
    return headers_path


def _stored(conn: sqlite3.Connection) -> list[Song]:
    """Read every stored song back, through the accessor `harvest` uses."""
    rows = typing.cast("list[sqlite3.Row]", conn.execute("SELECT video_id FROM songs").fetchall())
    ids = {typing.cast("str", row["video_id"]) for row in rows}
    return songs_by_video_id(conn, ids)

def _seeded_song(video_id: str) -> Song:
    """Build the row `bootstrap` seeds: a video ID with an empty title and an empty artist.

    The `Everything N` playlists return video IDs and no titles, so every seeded row starts this
    way. No title-based rule can hold at that time, which is why `refresh` must delete the row.
    """
    return Song(video_id=video_id, title="", artist="")


def _catalogue_ids(conn: sqlite3.Connection) -> set[str]:
    """Return the `video_id` of every row in the catalogue."""
    return {song.video_id for song in _stored(conn)}


def test_payload_becomes_songs(tmp_path: Path) -> None:
    """`library_songs` builds one `Song` per entry, from the video ID, the title, and the first artist."""
    payload = _load_fixture("library_songs_page.json")
    headers_path = _headers_file(tmp_path)

    scan = library_songs(headers_path, client_factory=lambda _auth: _FakeClient(payload))

    by_id = {song.video_id: song for song in scan.songs}
    assert by_id["LIB_VIDEO_AAAAAAAAAA"].title == "Alright Already"
    assert by_id["LIB_VIDEO_AAAAAAAAAA"].artist == "Fitz and The Tantrums"
    assert by_id["LIB_VIDEO_BBBBBBBBBB"].artist == "Bob's Burgers"  # first of several artists
    assert by_id["LIB_VIDEO_DDDDDDDDDD"].artist == ""  # an empty artists list is realistic, not an error


def test_radio_edit_titles_drop_out(tmp_path: Path) -> None:
    """`library_songs` keeps a "radio edit" title out of `songs`, and reports it in `excluded`."""
    payload = _load_fixture("library_songs_page.json")
    headers_path = _headers_file(tmp_path)

    scan = library_songs(headers_path, client_factory=lambda _auth: _FakeClient(payload))

    video_ids = {song.video_id for song in scan.songs}
    assert "LIB_VIDEO_CCCCCCCCCC" not in video_ids  # "Baditude (Radio Edit)"
    assert len(scan.songs) == len(payload) - 1  # the one radio-edit entry drops out, and no other
    assert scan.excluded == (
        ExcludedSong(
            video_id="LIB_VIDEO_CCCCCCCCCC",
            title="Baditude (Radio Edit)",
            artist="The Silencers",
            pattern="radio edit",
        ),
    )


def test_missing_headers_file_raises_a_named_error(tmp_path: Path) -> None:
    """If the headers file does not exist, `library_songs` raises and names the command that writes it."""
    headers_path = tmp_path / "does-not-exist.json"

    with pytest.raises(RuntimeError, match="uv run library-radio auth"):
        _ = library_songs(headers_path)


def test_refresh_reports_the_added_row_count(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """`refresh` merges the library payload into the catalogue and reports the rows added."""
    payload = _load_fixture("library_songs_page.json")
    headers_path = _headers_file(tmp_path)

    result = refresh(conn, headers_path, client_factory=lambda _auth: _FakeClient(payload))

    assert result.added == 3  # every entry except the radio edit
    assert count_songs(conn) == 3
    assert result.deleted == 0  # the catalogue held no row for the radio edit


def test_refresh_deletes_a_seeded_row_the_library_title_excludes(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """`refresh` deletes a row `bootstrap` seeded, once the library gives that row an excluded title.

    This is the whole defect. `bootstrap` seeds a video ID with an empty title, so the exclusion
    cannot hold at that time and the row enters the catalogue. A skip at merge time leaves that row
    in place, and a player plays it. Only a delete removes it.
    """
    _ = merge_songs(conn, [_seeded_song("SEEDED_RADIO_EDIT"), _seeded_song("SEEDED_KEEPER")])
    payload: list[dict[str, JSON]] = [
        {"videoId": "SEEDED_RADIO_EDIT", "title": "Baditude (Radio Edit)", "artists": [{"name": "The Silencers"}]},
        {"videoId": "SEEDED_KEEPER", "title": "Alright Already", "artists": [{"name": "Fitz and The Tantrums"}]},
    ]
    headers_path = _headers_file(tmp_path)

    result = refresh(conn, headers_path, client_factory=lambda _auth: _FakeClient(payload))

    assert _catalogue_ids(conn) == {"SEEDED_KEEPER"}
    assert result.deleted == 1
    assert result.added == 0  # both rows were already there


@pytest.mark.parametrize(
    ("title", "pattern"),
    [
        ("Baditude (Radio Edit)", "radio edit"),
        ("baditude - radio edit", "radio edit"),
        ("Baditude (RADIO EDIT)", "radio edit"),
        ("Radio Edit", "radio edit"),  # the pattern is the whole title
        ("Rap God (Censored)", "censored"),
        ("Rap God - censored version", "censored"),
        ("Rap God - CENSORED Version", "censored"),
        ("Censored", "censored"),  # the pattern is the whole title
    ],
)
def test_every_pattern_deletes_a_seeded_row_whatever_the_case(conn: sqlite3.Connection, tmp_path: Path, title: str, pattern: str) -> None:
    """Each pattern of `_EXCLUDED_PATTERNS` deletes a seeded row, and the match ignores case.

    A pattern stands at the start of a title, in the middle, at the end, or alone as the whole
    title. Each of those is a whole word, so each one excludes the song.
    """
    _ = merge_songs(conn, [_seeded_song("SEEDED_MATCH"), _seeded_song("SEEDED_KEEPER")])
    payload: list[dict[str, JSON]] = [
        {"videoId": "SEEDED_MATCH", "title": title, "artists": [{"name": "Some Band"}]},
        {"videoId": "SEEDED_KEEPER", "title": "A Plain Title", "artists": [{"name": "Some Band"}]},
    ]
    headers_path = _headers_file(tmp_path)

    result = refresh(conn, headers_path, client_factory=lambda _auth: _FakeClient(payload))

    assert _catalogue_ids(conn) == {"SEEDED_KEEPER"}
    assert result.deleted == 1
    assert [song.pattern for song in result.excluded] == [pattern]


@pytest.mark.parametrize(
    "title",
    [
        "The Uncensored Mix",
        "uncensored",
        "Greatest Hits, Uncensored",
        "Radio Editions",
        "Radioedit",
    ],
)
def test_a_longer_word_that_holds_a_pattern_survives(conn: sqlite3.Connection, tmp_path: Path, title: str) -> None:
    """A pattern inside a longer word excludes nothing, so an uncensored cut stays in the catalogue.

    `uncensored` holds `censored`, and an uncensored cut is the full song. That song is the one the
    owner wants. A word boundary on each end of the pattern is what keeps it.
    """
    _ = merge_songs(conn, [_seeded_song("SEEDED_LONGER_WORD")])
    payload: list[dict[str, JSON]] = [
        {"videoId": "SEEDED_LONGER_WORD", "title": title, "artists": [{"name": "Some Band"}]},
    ]
    headers_path = _headers_file(tmp_path)

    result = refresh(conn, headers_path, client_factory=lambda _auth: _FakeClient(payload))

    assert _catalogue_ids(conn) == {"SEEDED_LONGER_WORD"}
    assert not result.excluded
    assert result.deleted == 0


@pytest.mark.parametrize(
    "pattern",
    [
        "",  # `\b\b` matches every title that holds a word character. A refresh empties the catalogue.
        "(explicit)",  # A bracket sits at each edge, so the matcher matches no title at all.
        "#1 hit",  # Punctuation sits at the start alone.
        "radio edit ",  # A trailing space. It is easy to add by hand and impossible to see.
    ],
)
def test_a_pattern_with_a_non_alphanumeric_edge_stops_the_module(pattern: str) -> None:
    r"""`_compile_matchers` raises `ValueError` for a pattern that does not start and end with a letter or a digit.

    The empty pattern is the dangerous one: `\b\b` matches almost every title, and `refresh` deletes
    every row it matches. Each other pattern matches no title at all. Neither fault reports itself
    without this check, so the module must refuse to load instead.

    The message names the pattern, because the owner has to find it in `_EXCLUDED_PATTERNS`.
    """
    with pytest.raises(ValueError, match="must start and end with a letter or a digit") as caught:
        _ = _compile_matchers((pattern,))

    assert repr(pattern) in str(caught.value)


def test_every_project_pattern_compiles_to_one_matcher() -> None:
    """`_compile_matchers` accepts every pattern of `_EXCLUDED_PATTERNS` and keeps the order given.

    The validation must reject a bad pattern and pass a good one. A rule that also rejects
    `radio edit`, which holds a space, breaks the whole feature.
    """
    matchers = _compile_matchers(_EXCLUDED_PATTERNS)

    assert [pattern for pattern, _matcher in matchers] == list(_EXCLUDED_PATTERNS)


def test_punctuation_inside_a_pattern_stays_a_literal() -> None:
    """`_compile_matchers` escapes each pattern, so a `.` inside one matches a dot and no other character.

    No pattern in `_EXCLUDED_PATTERNS` holds a regex metacharacter today, so no title-level
    test covers `re.escape`. This one does: without `re.escape` the matcher for `c.o` also matches
    `cao`, and `refresh` then deletes songs the owner never named.
    """
    _pattern, matcher = _compile_matchers(("c.o",))[0]

    assert matcher.search("track c.o here") is not None
    assert matcher.search("track cao here") is None


def test_refresh_lists_every_excluded_song_with_its_artist_and_pattern(
    conn: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`refresh` logs one line per excluded song, with the pattern, the video ID, the artist, and the title.

    The owner reads this listing to confirm that a pattern matches the right songs. A line without
    the artist and the title cannot tell a real song from a wrong match.
    """
    payload: list[dict[str, JSON]] = [
        {"videoId": "LIB_VIDEO_CENSORED", "title": "Rap God (Censored)", "artists": [{"name": "Eminem"}]},
    ]
    headers_path = _headers_file(tmp_path)

    with caplog.at_level(logging.INFO):
        result = refresh(conn, headers_path, client_factory=lambda _auth: _FakeClient(payload))

    assert result.excluded == (
        ExcludedSong(video_id="LIB_VIDEO_CENSORED", title="Rap God (Censored)", artist="Eminem", pattern="censored"),
    )
    listing = [message for message in caplog.messages if "LIB_VIDEO_CENSORED" in message]
    assert len(listing) == 1
    assert "censored" in listing[0]
    assert "Eminem" in listing[0]
    assert "Rap God (Censored)" in listing[0]


def test_refresh_counts_an_excluded_song_the_catalogue_never_held(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """`refresh` reports an exclusion with no matching row as excluded, and deletes nothing.

    A song the catalogue never held is the common case after the first run. The row is already gone,
    and the pattern still matches the library title on every run after that.
    """
    payload: list[dict[str, JSON]] = [
        {"videoId": "LIB_VIDEO_NEW_RADIO_EDIT", "title": "New Song (Radio Edit)", "artists": [{"name": "Some Band"}]},
    ]
    headers_path = _headers_file(tmp_path)

    result = refresh(conn, headers_path, client_factory=lambda _auth: _FakeClient(payload))

    assert len(result.excluded) == 1
    assert result.deleted == 0
    assert count_songs(conn) == 0


def test_entries_without_a_usable_video_id_are_dropped(tmp_path: Path) -> None:
    """An entry with no `videoId`, or an empty one, cannot be played and must not enter the catalogue."""
    payload: list[dict[str, JSON]] = [
        {"title": "Ghost Track", "artists": [{"name": "Nobody"}]},
        {"videoId": "", "title": "Empty Id", "artists": [{"name": "Nobody"}]},
        {"videoId": "LIB_VIDEO_REAL", "title": "Real Track", "artists": [{"name": "Somebody"}]},
    ]
    headers_path = _headers_file(tmp_path)

    scan = library_songs(headers_path, client_factory=lambda _auth: _FakeClient(payload))

    assert [song.video_id for song in scan.songs] == ["LIB_VIDEO_REAL"]


def test_malformed_title_and_artist_fields_default_to_empty_strings(tmp_path: Path) -> None:
    """A missing or wrongly-typed `title` or `artists` field degrades to "" instead of dropping the entry."""
    payload: list[dict[str, JSON]] = [
        {"videoId": "LIB_VIDEO_NO_TITLE", "artists": [{"name": "Somebody"}]},
        {"videoId": "LIB_VIDEO_BAD_TITLE_TYPE", "title": 12345, "artists": [{"name": "Somebody"}]},
        {"videoId": "LIB_VIDEO_ARTISTS_NOT_A_LIST", "title": "T", "artists": "not-a-list"},
        {"videoId": "LIB_VIDEO_ARTIST_NOT_A_DICT", "title": "T", "artists": ["not-a-dict"]},
        {"videoId": "LIB_VIDEO_ARTIST_MISSING_NAME", "title": "T", "artists": [{"id": "no-name-field"}]},
        {"videoId": "LIB_VIDEO_NO_ARTISTS_KEY", "title": "T"},
    ]
    headers_path = _headers_file(tmp_path)

    scan = library_songs(headers_path, client_factory=lambda _auth: _FakeClient(payload))

    by_id = {song.video_id: song for song in scan.songs}
    assert by_id["LIB_VIDEO_NO_TITLE"].title == ""
    assert by_id["LIB_VIDEO_BAD_TITLE_TYPE"].title == ""
    assert by_id["LIB_VIDEO_ARTISTS_NOT_A_LIST"].artist == ""
    assert by_id["LIB_VIDEO_ARTIST_NOT_A_DICT"].artist == ""
    assert by_id["LIB_VIDEO_ARTIST_MISSING_NAME"].artist == ""
    assert by_id["LIB_VIDEO_NO_ARTISTS_KEY"].artist == ""


def test_a_wrongly_typed_title_warns_and_names_the_video_id(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A `title` of another type logs one warning that names the video ID. An absent `title` logs none.

    Both entries degrade to an empty title, so the warning is the only signal that the library
    response changed shape. An absent title is normal, and a warning for it is noise.
    """
    payload: list[dict[str, JSON]] = [
        {"videoId": "LIB_VIDEO_BAD_TITLE_TYPE", "title": 12345, "artists": [{"name": "Somebody"}]},
        {"videoId": "LIB_VIDEO_NO_TITLE", "artists": [{"name": "Somebody"}]},
    ]
    headers_path = _headers_file(tmp_path)

    with caplog.at_level(logging.WARNING):
        scan = library_songs(headers_path, client_factory=lambda _auth: _FakeClient(payload))

    assert [song.title for song in scan.songs] == ["", ""]
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "LIB_VIDEO_BAD_TITLE_TYPE" in warnings[0]
    assert "title" in warnings[0]


def test_rejected_headers_file_raises_a_named_error(tmp_path: Path) -> None:
    """`library_songs` wraps a `YTMusicError` the client raises with the same named, actionable error."""
    headers_path = _headers_file(tmp_path)

    def rejecting_factory(auth: str) -> _FakeClient:
        del auth
        message = "Your cookie is missing the required value __Secure-3PAPISID"
        raise YTMusicUserError(message)

    with pytest.raises(RuntimeError, match="uv run library-radio auth"):
        _ = library_songs(headers_path, client_factory=rejecting_factory)


def test_malformed_headers_file_content_raises_a_named_error(tmp_path: Path) -> None:
    """`library_songs` raises the same named error when the file exists but is not valid JSON.

    This uses the real `ytmusicapi.YTMusic` client, and not a fake. `ytmusicapi` rejects a malformed
    auth file inside its own constructor, before any request goes out. This test therefore stays
    local and free of the network, and it drives the real error path of the module.
    """
    headers_path = tmp_path / "not-json.json"
    _ = headers_path.write_text("this is not JSON", encoding="utf-8")

    with pytest.raises(RuntimeError, match="uv run library-radio auth"):
        _ = library_songs(headers_path)


def test_an_empty_library_read_raises(tmp_path: Path) -> None:
    """If the read returns no songs, `library_songs` raises and names the path and the fix.

    An expired browser cookie reads as an empty library and no error, so this is the only signal of
    a dead credential. Without this the caller sees a successful read of zero songs.
    """
    headers_path = _headers_file(tmp_path)

    with pytest.raises(RuntimeError, match="returned no songs") as caught:
        _ = library_songs(headers_path, client_factory=lambda _auth: _FakeClient([]))

    assert str(headers_path) in str(caught.value)
    assert "uv run library-radio auth" in str(caught.value)


def test_the_empty_read_error_and_the_missing_file_error_name_different_faults(tmp_path: Path) -> None:
    """The two credential errors share a type and a fix, and each message names its own fault.

    Both faults send the owner to the same command, so they share one error shape. A message that
    reports "missing or rejected" for a file that was read and accepted sends the owner to the
    wrong place. This test fails if either error reuses the message of the other.
    """
    present_path = _headers_file(tmp_path)
    absent_path = tmp_path / "does-not-exist.json"

    with pytest.raises(RuntimeError) as empty:
        _ = library_songs(present_path, client_factory=lambda _auth: _FakeClient([]))
    with pytest.raises(RuntimeError) as missing:
        _ = library_songs(absent_path)

    assert "the library read succeeded and returned no songs" in str(empty.value)
    assert "missing" not in str(empty.value)
    assert "missing or rejected browser headers file" in str(missing.value)
    assert "returned no songs" not in str(missing.value)


def test_refresh_writes_nothing_when_the_library_reads_empty(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """`refresh` propagates the empty-library failure and leaves every catalogue row in place.

    The read comes before the merge and before the delete, so a dead credential deletes nothing.
    A `refresh` that caught this error and reported "added 0 rows" is the defect this test locks
    out.
    """
    _ = merge_songs(conn, [_seeded_song("SEEDED_KEEPER")])
    headers_path = _headers_file(tmp_path)

    with pytest.raises(RuntimeError, match="returned no songs"):
        _ = refresh(conn, headers_path, client_factory=lambda _auth: _FakeClient([]))

    assert _catalogue_ids(conn) == {"SEEDED_KEEPER"}


def test_library_songs_asks_for_the_limit_it_is_given(tmp_path: Path) -> None:
    """`library_songs` passes `limit` to the client, so a caller can ask for a small read.

    `refresh` needs every song. `auth` only needs proof that the credential works, and a full read
    of about 19,000 songs takes minutes. One parameter serves both.
    """
    client = _FakeClient([{"videoId": "LIB_A", "title": "A Plain Title", "artists": [{"name": "Some Band"}]}])
    headers_path = _headers_file(tmp_path)

    _ = library_songs(headers_path, client_factory=lambda _auth: client, limit=25)

    assert client.limits == [25]


def test_library_songs_reads_the_whole_library_by_default(tmp_path: Path) -> None:
    """With no limit, the read covers the whole library, which is what `refresh` needs."""
    client = _FakeClient([{"videoId": "LIB_A", "title": "A Plain Title", "artists": [{"name": "Some Band"}]}])
    headers_path = _headers_file(tmp_path)

    _ = library_songs(headers_path, client_factory=lambda _auth: client)

    assert client.limits == [25_000]


def test_library_songs_reads_the_album_and_the_duration(tmp_path: Path) -> None:
    """The Sonos match needs both. They arrive in the same library payload as the title."""
    entry: dict[str, JSON] = {
        "videoId": "WITHALBUM",
        "title": "A Title",
        "artists": [{"name": "A Band"}],
        "album": {"name": "An Album", "id": "MPREb_x"},
        "duration_seconds": 213,
    }

    scan = library_songs(_headers_file(tmp_path), client_factory=lambda _auth: _FakeClient([entry]))

    assert (scan.songs[0].album, scan.songs[0].duration_seconds) == ("An Album", 213)


def test_library_songs_degrades_an_absent_album_and_duration(tmp_path: Path) -> None:
    """An absent album or duration is normal, and it must not drop the song.

    `merge_songs` keeps a stored value against an empty incoming one, so a degraded read never
    erases an album an earlier read stored.
    """
    entry: dict[str, JSON] = {"videoId": "BARE", "title": "A Title", "artists": [{"name": "A Band"}]}

    scan = library_songs(_headers_file(tmp_path), client_factory=lambda _auth: _FakeClient([entry]))

    assert (scan.songs[0].album, scan.songs[0].duration_seconds) == ("", 0)


def test_library_songs_degrades_a_malformed_album_and_duration(tmp_path: Path) -> None:
    """A value of the wrong type is a change in the library response, and it must not raise."""
    entry: dict[str, JSON] = {
        "videoId": "ODD",
        "title": "A Title",
        "artists": [{"name": "A Band"}],
        "album": "a string, not an object",
        "duration_seconds": "3:33",
    }

    scan = library_songs(_headers_file(tmp_path), client_factory=lambda _auth: _FakeClient([entry]))

    assert (scan.songs[0].album, scan.songs[0].duration_seconds) == ("", 0)


def test_refresh_marks_a_song_the_read_returned(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A trusted read sets `last_seen` and clears `missing_count` on every song it held."""
    _ = merge_songs(conn, [_seeded_song("KEPT")])

    _ = refresh(conn, _headers_file(tmp_path), client_factory=lambda _auth: _FakeClient([_entry("KEPT")]))

    song = next(s for s in _stored(conn) if s.video_id == "KEPT")
    assert song.missing_count == 0
    assert song.last_seen is not None


def test_refresh_counts_a_song_the_read_did_not_return(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """One absent read is evidence. It raises the count and deletes nothing."""
    _ = merge_songs(conn, [_seeded_song(f"V{index}") for index in range(10)])

    result = refresh(conn, _headers_file(tmp_path), client_factory=lambda _auth: _FakeClient([_entry(f"V{i}") for i in range(9)]))

    assert result.removed == 0
    assert _catalogue_ids(conn) == {f"V{index}" for index in range(10)}
    assert next(s for s in _stored(conn) if s.video_id == "V9").missing_count == 1


def test_refresh_removes_a_song_after_enough_trusted_reads(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A song the library really lost goes after `missing_threshold` trusted reads agree."""
    _ = merge_songs(conn, [_seeded_song(f"V{index}") for index in range(10)])
    payload = [_entry(f"V{index}") for index in range(9)]

    removed = [
        refresh(conn, _headers_file(tmp_path), client_factory=lambda _auth: _FakeClient(payload), missing_threshold=3).removed
        for _ in range(3)
    ]

    assert removed == [0, 0, 1]
    assert "V9" not in _catalogue_ids(conn)


def test_refresh_refuses_a_read_that_lost_too_many_songs(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A read holding far fewer songs than the catalogue is a short read, not a mass deletion.

    This is the fault that makes an automatic removal dangerous. The rule must stop the run before
    any count moves, so a bad read costs nothing at all.
    """
    _ = merge_songs(conn, [_seeded_song(f"V{index}") for index in range(10)])
    half = [_entry(f"V{index}") for index in range(5)]

    with pytest.raises(RuntimeError, match=r"returned 5 songs.*holds 10"):
        _ = refresh(conn, _headers_file(tmp_path), client_factory=lambda _auth: _FakeClient(half))

    assert _catalogue_ids(conn) == {f"V{index}" for index in range(10)}
    assert all(song.missing_count == 0 for song in _stored(conn))


def test_refresh_trusts_a_read_of_an_empty_catalogue(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """The first run has nothing to compare against, so the trust ratio must not block it."""
    result = refresh(conn, _headers_file(tmp_path), client_factory=lambda _auth: _FakeClient([_entry("FIRST")]))

    assert result.added == 1
    assert _catalogue_ids(conn) == {"FIRST"}


def test_refresh_clears_the_count_when_a_song_returns(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A song that comes back starts again from zero, so two old absences never add to a later one."""
    _ = merge_songs(conn, [_seeded_song(f"V{index}") for index in range(10)])
    short = [_entry(f"V{index}") for index in range(9)]
    full = [_entry(f"V{index}") for index in range(10)]

    _ = refresh(conn, _headers_file(tmp_path), client_factory=lambda _auth: _FakeClient(short), missing_threshold=3)
    _ = refresh(conn, _headers_file(tmp_path), client_factory=lambda _auth: _FakeClient(short), missing_threshold=3)
    _ = refresh(conn, _headers_file(tmp_path), client_factory=lambda _auth: _FakeClient(full), missing_threshold=3)

    assert next(s for s in _stored(conn) if s.video_id == "V9").missing_count == 0


def _entry(video_id: str) -> dict[str, JSON]:
    """Build one `get_library_songs` entry in the shape the live response uses."""
    return {"videoId": video_id, "title": f"Title {video_id}", "artists": [{"name": "A Band"}]}


class _PagingClient:
    """A stand-in for `ytmusicapi.YTMusic` whose plain read drops songs, as the live one does.

    The live `get_library_songs` pages the library, and it silently returns a short list. The same
    call with `validate_responses` set re-reads a short page. This fake shows that difference.
    """

    def __init__(self, complete: list[dict[str, JSON]], short: list[dict[str, JSON]]) -> None:
        self._complete: list[dict[str, JSON]] = complete
        self._short: list[dict[str, JSON]] = short

    def get_library_songs(self, limit: int, *, validate_responses: bool = False) -> list[dict[str, JSON]]:
        """Return every song only when the caller asks for a validated read."""
        _ = limit
        return self._complete if validate_responses else self._short


def test_library_songs_reads_the_whole_library(tmp_path: Path) -> None:
    """A plain read drops about a quarter of this library, and `refresh` then counts songs as absent."""
    complete = [_entry("V1"), _entry("V2"), _entry("V3")]
    client = _PagingClient(complete, complete[:1])

    scan = library_songs(_headers_file(tmp_path), client_factory=lambda _auth: client)

    assert [song.video_id for song in scan.songs] == ["V1", "V2", "V3"]
