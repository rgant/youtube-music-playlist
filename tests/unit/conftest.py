"""Shared fixtures for every test under tests/unit."""

import contextlib
import typing

import pytest

from youtube_music_library_radio.catalogue import open_catalogue

if typing.TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(name="conn")
def fixture_conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a catalogue on a real temporary database, and close it after the test passes or fails.

    A test that closes its own connection on the last line leaks that connection when an assertion
    above it fails. `filterwarnings = error` then turns the `ResourceWarning` of the garbage
    collector into a second failure, in a later and unrelated test. The failure list then names
    tests that are correct, and the reader debugs the wrong one. This fixture closes the connection
    on the failing path too.
    """
    with contextlib.closing(open_catalogue(tmp_path / "catalogue.sqlite3")) as conn:
        yield conn


@pytest.fixture(autouse=True)
def _block_real_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail a test that reaches the real `soco.discover`, instead of blocking on real multicast.

    This applies to every unit test, and not to `test_control.py` alone. A future test can call
    `find_speaker` with no `discover_fn` of its own. Without this fixture, that test touches the
    network for five seconds per call, and it never fails fast. This project allows no network in a
    unit test.
    """

    def _blocked(*_args: object, **_kwargs: object) -> None:
        message = "a unit test reached the real soco.discover; pass discover_fn instead"
        raise AssertionError(message)

    monkeypatch.setattr("youtube_music_library_radio.control.discover", _blocked)
