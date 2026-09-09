"""Tests for youtube_music_library_radio.render_plists."""

import typing

import pytest

from youtube_music_library_radio import render_plists
from youtube_music_library_radio.settings import Settings

if typing.TYPE_CHECKING:
    from pathlib import Path


def _settings(tmp_path: Path) -> Settings:
    """Build settings whose every value differs from its default, so a crossed placeholder shows."""
    return Settings(
        database_path=tmp_path / "store" / "catalogue.sqlite3",
        speaker_name="Office",
        trust_ratio=0.8,
        missing_threshold=5,
        queue_size=250,
        queue_window_days=14,
        refresh_hour=6,
        refresh_minute=30,
        queue_hour=7,
        queue_minute=45,
        harvest_tolerance=40,
    )


def test_substitutions_name_the_repository_and_the_log_directory(tmp_path: Path) -> None:
    """`refresh` opens `browser.json` against the current directory, so the agent needs the repository.

    launchd writes no log line into a directory that is absent. The log directory sits beside the
    catalogue, so one directory holds every file the agent owns.
    """
    subs = render_plists._substitutions_for("refresh", settings=_settings(tmp_path), repo_dir=tmp_path / "repo")

    assert subs["__REPO_DIR__"] == str(tmp_path / "repo")
    assert subs["__LOG_DIR__"] == str(tmp_path / "store" / "logs")


def test_substitutions_carry_the_refresh_hour_and_minute(tmp_path: Path) -> None:
    """`StartCalendarInterval` is the one place the schedule exists. The command itself reads no clock."""
    subs = render_plists._substitutions_for("refresh", settings=_settings(tmp_path), repo_dir=tmp_path)

    assert subs["__HOUR__"] == "6"
    assert subs["__MINUTE__"] == "30"


def test_substitutions_carry_every_runtime_setting(tmp_path: Path) -> None:
    """The agent inherits no shell, so each setting the render read must reach the plist.

    An absent variable gives the agent a default and raises nothing. The agent then reaches the
    Kitchen speaker and the default catalogue.
    """
    subs = render_plists._substitutions_for("refresh", settings=_settings(tmp_path), repo_dir=tmp_path)

    assert subs["__DATABASE_PATH__"] == str(tmp_path / "store" / "catalogue.sqlite3")
    assert subs["__SPEAKER_NAME__"] == "Office"
    assert subs["__TRUST_RATIO__"] == "0.8"
    assert subs["__MISSING_THRESHOLD__"] == "5"
    assert subs["__QUEUE_SIZE__"] == "250"
    assert subs["__QUEUE_WINDOW_DAYS__"] == "14"
    assert subs["__HARVEST_TOLERANCE__"] == "40"


def test_substitutions_reject_an_unknown_agent(tmp_path: Path) -> None:
    """A name in `_AGENT_NAMES` with no branch here renders nothing, and the install reports success."""
    with pytest.raises(ValueError, match="harvest"):
        _ = render_plists._substitutions_for("harvest", settings=_settings(tmp_path), repo_dir=tmp_path)


def test_substitutions_carry_the_queue_hour_and_minute(tmp_path: Path) -> None:
    """The queue agent reads its schedule from the plist alone, and no command reads a clock."""
    subs = render_plists._substitutions_for("queue", settings=_settings(tmp_path), repo_dir=tmp_path)

    assert subs["__HOUR__"] == "7"
    assert subs["__MINUTE__"] == "45"


def test_the_queue_agent_has_a_template_and_a_name(tmp_path: Path) -> None:
    """The owner asked for a queue that fills itself, so `queue` must reach launchd like `refresh`."""
    written = render_plists.render(output_dir=tmp_path / "agents", settings=_settings(tmp_path))

    assert "queue" in render_plists._AGENT_NAMES
    assert [path.name for path in written].count("com.robgant.library-radio.queue.plist") == 1


def test_the_rendered_queue_plist_sends_the_queue_and_asks_for_a_banner(tmp_path: Path) -> None:
    """Without `--commit` the agent prints a sample and changes nothing, which is a silent no-op."""
    written = render_plists.render(output_dir=tmp_path / "agents", settings=_settings(tmp_path))
    content = next(path for path in written if path.name.endswith("queue.plist")).read_text(encoding="utf-8")

    assert "<string>--commit</string>" in content
    assert "<string>--notify</string>" in content


def test_the_rendered_queue_plist_runs_on_two_days_each_month(tmp_path: Path) -> None:
    """The owner asked for every two weeks. launchd offers a day of the month, and no fortnight."""
    written = render_plists.render(output_dir=tmp_path / "agents", settings=_settings(tmp_path))
    content = next(path for path in written if path.name.endswith("queue.plist")).read_text(encoding="utf-8")

    assert content.count("<key>Day</key>") == 2


def test_render_writes_one_plist_for_each_agent(tmp_path: Path) -> None:
    """The install script bootstraps every name in `_AGENT_NAMES`, so each one needs a file."""
    written = render_plists.render(output_dir=tmp_path / "agents", settings=_settings(tmp_path))

    assert [path.name for path in written] == [f"com.robgant.library-radio.{agent}.plist" for agent in render_plists._AGENT_NAMES]
    assert all(path.exists() for path in written)


def test_render_leaves_no_placeholder_in_the_output(tmp_path: Path) -> None:
    """A plist that keeps `__REPO_DIR__` names a program that is absent, and launchd reports that alone."""
    written = render_plists.render(output_dir=tmp_path / "agents", settings=_settings(tmp_path))

    for path in written:
        assert render_plists._PLACEHOLDER_RE.search(path.read_text(encoding="utf-8")) is None


def test_render_creates_the_log_directory(tmp_path: Path) -> None:
    """An agent whose `StandardOutPath` names an absent directory never starts."""
    _ = render_plists.render(output_dir=tmp_path / "agents", settings=_settings(tmp_path))

    assert (tmp_path / "store" / "logs").is_dir()


def test_render_raises_for_a_placeholder_no_substitution_names(tmp_path: Path) -> None:
    """A typo in a template placeholder must stop the render. A half-rendered plist reaches launchd."""
    templates = tmp_path / "templates"
    templates.mkdir()
    _ = (templates / "com.robgant.library-radio.refresh.plist.template").write_text("__NOT_A_KEY__", encoding="utf-8")

    with pytest.raises(RuntimeError, match="__NOT_A_KEY__"):
        _ = render_plists.render(output_dir=tmp_path / "agents", settings=_settings(tmp_path), template_dir=templates)


def test_main_renders_into_the_named_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The install script names `~/Library/LaunchAgents`, and a test must never write there."""
    monkeypatch.setenv("YTM_RADIO_DATABASE_PATH", str(tmp_path / "store" / "catalogue.sqlite3"))
    output = tmp_path / "agents"

    assert render_plists.main(["--output", str(output)]) == 0
    assert (output / "com.robgant.library-radio.refresh.plist").exists()


def test_the_rendered_plist_names_the_browser_headers_file(tmp_path: Path) -> None:
    """`refresh` resolves `browser.json` against the current directory, and `WorkingDirectory` sets that.

    The plist names the file in full as well, so it describes every file the agent reads.
    """
    written = render_plists.render(output_dir=tmp_path / "agents", settings=_settings(tmp_path))
    content = written[0].read_text(encoding="utf-8")

    assert "<string>--headers</string>" in content
    assert f"<string>{render_plists._repo_dir()}/browser.json</string>" in content


def test_the_rendered_plist_asks_for_a_failure_banner() -> None:
    """A failed run reaches a launchd log nobody reads, so the agent asks for a banner.

    A terminal run omits `--notify`, because it already shows the fault on the screen.
    """
    content = (render_plists._repo_dir() / "scripts" / "plists" / "com.robgant.library-radio.refresh.plist.template").read_text(
        encoding="utf-8"
    )

    assert "<string>--notify</string>" in content
