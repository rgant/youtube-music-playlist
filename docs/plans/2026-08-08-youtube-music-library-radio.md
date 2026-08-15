# YouTube Music library radio implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve an endless, randomly ordered MP3 stream of the YouTube Music
library, and play it on the Kitchen Sonos Play:1.

**Architecture:** A long-running HTTP service sends one MP3 response that never
ends. The service picks a song at random, resolves it with `yt-dlp`, and
transcodes it with `ffmpeg`. Sonos treats the response as a radio station, so no
queue limit applies. A SQLite database holds the song list.

**Tech Stack:** Python 3.14, uv, just, `sqlite3`, `yt-dlp`, `ffmpeg`, `soco`,
`ytmusicapi`, YouTube Data API v3.

**Design source:** `docs/specs/2026-08-08-youtube-music-library-radio-design.md`

## Global Constraints

- `requires-python = ">=3.14"`.
- Add and remove dependencies with `uv add` and `uv remove`. Never edit
  dependency lists by hand.
- Ruff sets `line-length = 140` and `quote-style = "double"`.
- Use the standard library `sqlite3` module. Do not add SQLAlchemy. Do not add
  alembic.
- Coverage must stay at or above 90 percent.
- Never write library audio to disk. The owner has no space for it.
- Find the `ffmpeg` path at run time. Do not hardcode a Homebrew prefix.
- The package name is `youtube_music_library_radio`.
- Every linter and formatter runs through `just`.
- Make one commit at the end of the whole plan. Do not commit between tasks.
- Every test must be able to fail. For the main behaviour test of a task,
  replace the code under test with a trivial stub, run the test, and confirm it
  fails. Then restore the code. Record both outputs. A test that passes against
  a stub protects nothing.

## File structure

| Path                                                              | Responsibility                                                                 |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `pyproject.toml`                                                  | Project metadata, dependencies, and every tool setting.                        |
| `justfile`                                                        | Task runner recipes.                                                           |
| `Brewfile`                                                        | Homebrew tools. `ffmpeg` and `uv` run the service. The rest serve development. |
| `src/youtube_music_library_radio/settings.py`                     | Configuration values and their defaults.                                       |
| `src/youtube_music_library_radio/logger.py`                       | Colour log handler. Moves from `common/logger.py`.                             |
| `src/youtube_music_library_radio/catalogue.py`                    | SQLite song store.                                                             |
| `src/youtube_music_library_radio/selector.py`                     | Random choice. Pure.                                                           |
| `src/youtube_music_library_radio/icy.py`                          | ICY metadata blocks. Pure.                                                     |
| `src/youtube_music_library_radio/resolver.py`                     | Video ID to audio URL through `yt-dlp`.                                        |
| `src/youtube_music_library_radio/station.py`                      | Endless MP3 HTTP service.                                                      |
| `src/youtube_music_library_radio/bootstrap.py`                    | First catalogue fill from the `Everything N` playlists.                        |
| `src/youtube_music_library_radio/refresh.py`                      | Library read through `ytmusicapi`.                                             |
| `src/youtube_music_library_radio/control.py`                      | Speaker control through `soco`.                                                |
| `src/youtube_music_library_radio/__main__.py`                     | Command line entry point.                                                      |
| `scripts/install_launchagent.sh`                                  | launchd install and removal.                                                   |
| `scripts/name.robgant.youtube_music_library_radio.plist.template` | launchd job definition.                                                        |

---

### Task 1: Toolchain, package skeleton, and settings

**Files:**

- Create: `pyproject.toml` (replaces the current file), `justfile`, `Brewfile`,
  `src/youtube_music_library_radio/__init__.py`,
  `src/youtube_music_library_radio/settings.py`,
  `src/youtube_music_library_radio/logger.py`, `tests/unit/test_settings.py`
- Delete: `Pipfile`, `Pipfile.lock`, `mypy.ini`, `.pylintrc`,
  `.markdownlint.json`, `common/logger.py`, `common/__init__.py`,
  `cat-watcher-pyproject.toml`, `other-justfile`, `browser.json`
- Keep: `dprint.jsonc`, `.markdownlint.jsonc`, `.markdownlintignore`,
  `.gitattributes`, `.editorconfig`

**Interfaces:**

- Consumes: nothing.
- Produces: `Settings` frozen dataclass with fields `database_path: Path`,
  `speaker_name: str`, `station_host: str`, `station_port: int`,
  `bitrate_kbps: int`, `no_repeat_window: int`, `prune_threshold: int`,
  `metadata_interval: int`, `station_name: str`. Also
  `load_settings() -> Settings` and
  `create_handler() -> logging.StreamHandler[typing.TextIO]`.

**Requirements:**

- Port every tool table from `cat-watcher-pyproject.toml`: `[tool.ruff]`,
  `[tool.pylint]`, `[tool.mypy]`, `[tool.pytest.ini_options]`,
  `[tool.coverage]`, `[tool.deptry]`, `[tool.pyproject-fmt]`,
  `[tool.basedpyright]`.
- Drop the cat-watcher rules for alembic, djlint, and the ML stack. This project
  has none of them.
- Set `[tool.basedpyright] venvPath = "."` and `venv = ".venv"`.
- Set `[tool.mypy] mypy_path = ["src"]` and `files = ["src", "tests"]`.
- Set the build backend to `hatchling`.
- Read every setting from an environment variable with the prefix `YTM_RADIO_`.
- Default `no_repeat_window` to 2000. Default `prune_threshold` to 3. Default
  `metadata_interval` to 16000. Default `bitrate_kbps` to 128. Default
  `station_port` to 8900. Default `station_name` to `My Library Radio`.
- Default `database_path` to
  `~/.local/share/youtube-music-library-radio/catalogue.sqlite3`.
- Keep the colour formatter behaviour in `logger.py` without change.
- Write a `Brewfile` that names `ffmpeg`, `uv`, `just`, `dprint`,
  `markdownlint-cli`, `shellcheck`, and `shfmt`. Every name is a verified
  formula.
- Mark `ffmpeg` and `uv` with a comment as the runtime tools. The Mac Mini needs
  those two alone.
- Do not add `actionlint`. This project has no GitHub Actions workflow.
- Add a `just brew` recipe that runs `brew bundle`. Add a `just brew-check`
  recipe that runs `brew bundle check`.
- The `justfile` must call `dprint` and `markdownlint` for `format` and `lint`.
  `dprint.jsonc` already excludes `pyproject.toml` from its toml plugin because
  `pyproject-fmt` owns that file.
- Default `station_host` to an empty string. An empty value tells `control` to
  detect the address that reaches the speaker.

**Steps:**

- [x] **Step 1:** Delete the files in the Delete list above.
- [x] **Step 2:** Write `pyproject.toml` with project metadata and every ported
      tool table.
- [x] **Step 3:** Add runtime dependencies. Run `uv add soco ytmusicapi yt-dlp`.
- [x] **Step 4:** Add development dependencies. Run

  ```shell
  uv add --dev basedpyright deptry mypy pylint pylint-per-file-ignores pylint-pytest pylint-venv pyproject-fmt pytest pytest-cov pytest-randomly ruff
  ```

- [x] **Step 5:** Write the `Brewfile`. Run `brew bundle check`. Expect a report
      that every tool is installed.
- [x] **Step 6:** Write `justfile` with grouped recipes. Provide `format`,
      `lint`, `test`, `test-cov`, `check`, `sync`, `brew`, `brew-check`,
      `outdated`, and `clean`. Make `default` run `check`.
- [x] **Step 7:** Create the package directory and move the logger into it.
- [x] **Step 8:** Write the failing tests in `tests/unit/test_settings.py`. Name
      them `test_defaults_match_the_documented_values`,
      `test_environment_overrides_a_default`, and
      `test_database_path_expands_the_home_directory`. Assert each default value
      listed above. Assert that `YTM_RADIO_STATION_PORT` changes `station_port`.
      Assert that the default `database_path` contains no tilde.
- [x] **Step 9:** Run `uv run pytest tests/unit/test_settings.py -v`. Expect
      failure because `settings` does not exist.
- [x] **Step 10:** Write `settings.py` to make the tests pass.
- [x] **Step 11:** Run `uv run pytest tests/unit/test_settings.py -v`. Expect
      three passes.
- [x] **Step 12:** Run `just format` then `just lint`. Fix every finding.

**Acceptance criteria:**

- `just lint` reports no error.
- `just test` passes.
- `just brew-check` reports no absent tool.
- `uv run python -c "import youtube_music_library_radio"` succeeds.
- No file named `Pipfile`, `Pipfile.lock`, `mypy.ini`, or `.pylintrc` remains.

---

### Task 2: `selector`

**Files:**

- Create: `src/youtube_music_library_radio/selector.py`,
  `tests/unit/test_selector.py`

**Interfaces:**

- Consumes: nothing.
- Produces: `pick(candidates: Sequence[Song], rng: random.Random) -> Song`.

**Requirements:**

- `pick` takes an explicit `random.Random` so tests can seed it. Do not call the
  module level `random` functions.
- If `candidates` is empty, raise `ValueError`.
- Import `Song` from `catalogue`. Task 3 defines it. Declare the import now and
  let the test fail until Task 3 lands.

To keep Task 2 independent, define `Song` in `catalogue.py` as the first action
of this task. Task 3 then adds the database functions around it.

**Steps:**

- [x] **Step 1:** Create `catalogue.py`. Define only the frozen dataclass `Song`
      with fields `video_id: str`, `title: str`, `artist: str`,
      `failure_count: int`, `last_success: datetime | None`,
      `last_played: datetime | None`.
- [x] **Step 2:** Write the failing tests in `tests/unit/test_selector.py`. Name
      them `test_pick_returns_a_member_of_the_candidates`,
      `test_pick_is_deterministic_for_a_seeded_generator`, and
      `test_pick_rejects_an_empty_sequence`. Assert membership. Assert that two
      generators with the same seed return the same song. Assert `ValueError`
      for an empty sequence.
- [x] **Step 3:** Run `uv run pytest tests/unit/test_selector.py -v`. Expect
      failure because `selector` does not exist.
- [x] **Step 4:** Write `selector.py` to make the tests pass.
- [x] **Step 5:** Run `uv run pytest tests/unit/test_selector.py -v`. Expect
      three passes.
- [x] **Step 6:** Run `just format` then `just lint`. Fix every finding.

**Acceptance criteria:**

- `pick` performs no input and no output.
- `selector.py` imports nothing beyond the standard library and
  `catalogue.Song`.

---

### Task 3: `catalogue`

**Files:**

- Modify: `src/youtube_music_library_radio/catalogue.py`
- Create: `tests/unit/test_catalogue.py`,
  `tests/integration/test_catalogue_concurrency.py`

**Interfaces:**

- Consumes: `Song` from Task 2.
- Produces:
  - `open_catalogue(path: Path) -> sqlite3.Connection`
  - `merge_songs(conn: sqlite3.Connection, songs: Iterable[Song]) -> int`
  - `delete_songs(conn: sqlite3.Connection, video_ids: Iterable[str]) -> int`
  - `candidate_songs(conn: sqlite3.Connection, window: int) -> list[Song]`
  - `record_success(conn: sqlite3.Connection, video_id: str, title: str, artist:
    str) -> None`
  - `record_failure(conn: sqlite3.Connection, video_id: str, prune_threshold:
    int) -> bool`
  - `count_songs(conn: sqlite3.Connection) -> int`

**Requirements:**

- `open_catalogue` creates the parent directory, creates the table with
  `CREATE TABLE IF NOT EXISTS`, sets `journal_mode=WAL`, and sets `user_version`
  to 1.
- The table key is `video_id`.
- `merge_songs` inserts new rows and returns the number of rows added. An
  existing row keeps its `failure_count`, `last_success`, and `last_played`. An
  existing row takes a non-empty incoming title or artist.
- `candidate_songs` returns every song except the most recently played songs,
  where `window` names how many to exclude. A song with no `last_played` value
  always counts as a candidate.
- If `window` is at least the row count, `candidate_songs` returns every song.
  The station must never receive an empty list while rows exist.
- `record_success` sets `last_success` and `last_played`, and resets
  `failure_count` to zero. It fills an empty title or artist. A stored non-empty
  title or artist stays, the same guard `merge_songs` applies. `resolver` writes
  `Unknown` for a song that returns neither an artist nor an uploader. That
  value must never replace a real artist.
- `record_failure` raises `failure_count` by one. If the new count reaches
  `prune_threshold`, it deletes the row and returns `True`. Otherwise it returns
  `False`.

**Steps:**

- [x] **Step 1:** Write the failing tests in `tests/unit/test_catalogue.py`. Use
      a `tmp_path` database, not a double. Name them
      `test_open_creates_the_table_and_sets_wal`,
      `test_merge_adds_new_songs_and_reports_the_count`,
      `test_merge_keeps_counts_on_an_existing_row`,
      `test_merge_fills_an_empty_title_from_the_incoming_row`,
      `test_candidates_exclude_the_recently_played`,
      `test_candidates_return_every_song_when_the_window_covers_the_table`,
      `test_candidates_include_a_song_that_never_played`,
      `test_success_resets_the_failure_count`,
      `test_failure_raises_the_count_below_the_threshold`, and
      `test_failure_deletes_the_row_at_the_threshold`.
- [x] **Step 2:** Run `uv run pytest tests/unit/test_catalogue.py -v`. Expect
      failure because the functions do not exist.
- [x] **Step 3:** Write the database functions in `catalogue.py` to make the
      tests pass.
- [x] **Step 4:** Run `uv run pytest tests/unit/test_catalogue.py -v`. Expect
      every test to pass.
- [x] **Step 5:** Write the failing test in
      `tests/integration/test_catalogue_concurrency.py`. Name it
      `test_a_failure_write_and_a_merge_write_both_survive`. Open two
      connections to one database file. Record a failure on the first
      connection. Merge new songs on the second connection. Assert that the
      failure count and the new rows both persist.
- [x] **Step 6:** Run
      `uv run pytest tests/integration/test_catalogue_concurrency.py -v`. Expect
      failure or a lock error before WAL mode is correct.
- [x] **Step 7:** Correct the connection settings until the test passes.
- [x] **Step 8:** Run
      `uv run pytest tests/integration/test_catalogue_concurrency.py -v`. Expect
      a pass.
- [x] **Step 9:** Run `just format` then `just lint`. Fix every finding.

**Acceptance criteria:**

- The concurrency test passes without a retry loop in the test.
- `catalogue.py` imports no third-party package.

---

### Task 4: `icy`

**Files:**

- Create: `src/youtube_music_library_radio/icy.py`, `tests/unit/test_icy.py`

**Interfaces:**

- Consumes: nothing.
- Produces: `metadata_block(title: str) -> bytes` and `EMPTY_BLOCK: bytes`.

**Requirements:**

- A block starts with one length byte. The byte holds the payload length divided
  by 16.
- The payload reads `StreamTitle='<title>';` and pads with null bytes to a
  multiple of 16.
- `EMPTY_BLOCK` is a single zero byte. Sonos reads it as "no change".
- A title that contains a single quote must not break the payload. Remove the
  quote or replace it.
- The observed Sonos behaviour splits `Artist - Title` into the artist field and
  the title field. Callers pass that form. `metadata_block` does not build it.

**Steps:**

- [x] **Step 1:** Write the failing tests in `tests/unit/test_icy.py`. Name them
      `test_block_length_byte_matches_the_payload`,
      `test_payload_pads_to_a_multiple_of_sixteen`,
      `test_payload_carries_the_title`, `test_empty_block_is_one_zero_byte`, and
      `test_a_quote_in_the_title_does_not_break_the_payload`. Assert the first
      byte equals the remaining length divided by 16. Assert the remaining
      length divides by 16. Assert the payload contains the title. Assert
      `EMPTY_BLOCK` equals a single zero byte. Assert a title with an apostrophe
      still yields a well-formed block.
- [x] **Step 2:** Run `uv run pytest tests/unit/test_icy.py -v`. Expect failure
      because `icy` does not exist.
- [x] **Step 3:** Write `icy.py` to make the tests pass.
- [x] **Step 4:** Run `uv run pytest tests/unit/test_icy.py -v`. Expect five
      passes.
- [x] **Step 5:** Run `just format` then `just lint`. Fix every finding.

**Acceptance criteria:**

- `icy.py` performs no input and no output.

---

### Task 5: `resolver`

**Files:**

- Create: `src/youtube_music_library_radio/resolver.py`,
  `tests/integration/test_resolver.py`

**Interfaces:**

- Consumes: nothing.
- Produces:
  - `Resolved` frozen dataclass with fields `video_id: str`, `title: str`,
    `artist: str`, `audio_url: str`
  - `ResolutionError(Exception)`
  - `resolve(video_id: str) -> Resolved`

**Requirements:**

- Call `yt-dlp` as a library. Use the options `quiet`, `no_warnings`,
  `skip_download`, and `format="bestaudio"`.
- Build the watch URL from `https://music.youtube.com/watch?v=` and the video
  ID.
- Read the artist from the `artist` field. If that field is absent, read
  `uploader`. If both are absent, use `Unknown`.
- Wrap every `yt-dlp` exception in `ResolutionError`. Never let a `yt-dlp`
  exception escape.
- Do not retry inside `resolve`. The caller decides what a failure means.
- Mark the integration test with a registered pytest marker named `network`.
  `[tool.pytest.ini_options] markers` must declare it.

**Steps:**

- [x] **Step 1:** Add the `network` marker to
      `[tool.pytest.ini_options] markers` in `pyproject.toml`.
- [x] **Step 2:** Write the failing tests in
      `tests/integration/test_resolver.py`. Name them
      `test_resolve_returns_an_audio_url_for_a_known_song` and
      `test_resolve_raises_for_an_unknown_video_id`. Use the known-good ID
      `YEUZmtb_AT8`. Assert the resolved artist is `Nirvana` and the title
      starts with `Territorial Pissings`, not merely that a URL is non-empty. A
      test that checks only for a non-empty string passes against a stub. Use an
      invalid ID for the failure case. Mark both with `network`.
- [x] **Step 3:** Run `uv run pytest tests/integration/test_resolver.py -v`.
      Expect failure because `resolver` does not exist.
- [x] **Step 4:** Write `resolver.py` to make the tests pass.
- [x] **Step 5:** Run `uv run pytest tests/integration/test_resolver.py -v`.
      Expect two passes.
- [x] **Step 6:** Run `just format` then `just lint`. Fix every finding.

**Acceptance criteria:**

- No `yt-dlp` exception type appears outside `resolver.py`.
- The test suite passes when the network is absent, because `-m "not network"`
  deselects these tests.

---

### Task 6: `station`

**Files:**

- Create: `src/youtube_music_library_radio/station.py`,
  `tests/integration/test_station.py`, `tests/fixtures/make_tone.py`

**Interfaces:**

- Consumes: `Settings` (Task 1), `pick` (Task 2), `candidate_songs` /
  `record_success` / `record_failure` (Task 3), `metadata_block` / `EMPTY_BLOCK`
  (Task 4), `Resolved` / `ResolutionError` / `resolve` (Task 5).
- Produces:
  - `find_ffmpeg() -> str`
  - `transcode(ffmpeg: str, audio_url: str, bitrate_kbps: int) -> subprocess.Popen[bytes]`
  - `build_server(settings: Settings, conn: sqlite3.Connection, resolve_fn:
    Callable[[str], Resolved]) -> ThreadingHTTPServer`

**Requirements:**

- `build_server` takes `resolve_fn` so tests can pass a fake. Do not import
  `resolve` inside the handler.
- `find_ffmpeg` uses `shutil.which`. If `ffmpeg` is absent, raise `RuntimeError`
  that names the absent program.
- The handler answers `GET` with status 200 and `Content-Type: audio/mpeg`.
- If the request carries `Icy-MetaData: 1`, send the `icy-metaint` header and
  interleave metadata. Otherwise send audio only.
- Send a metadata block only when the song changes. Send `EMPTY_BLOCK` at every
  other interval.
- Count the audio bytes since the last metadata block. Never let a metadata
  block land at the wrong offset. Read at most the remaining bytes before the
  next interval.
- On `ResolutionError`, call `record_failure` and continue with the next song.
  Do not end the response.
- If `ffmpeg` writes no bytes, or ends early, continue with the next song. Do
  not end the response.
- Call `record_success` only after the song yields audio. If it yields no bytes,
  call `record_failure` instead. `record_success` clears the failure count, so
  calling it before playback lets a song that never plays oscillate between zero
  and one. Such a song never reaches the prune threshold, and the station picks
  it again forever.
- Bound consecutive resolution failures within one response. A run of failures
  is evidence of one systemic fault, not of many individually dead songs. Above
  a small threshold, stop pruning and pause before the next attempt. A network
  outage must never drain the catalogue.
- Bound consecutive songs that yield no audio. Above a small threshold, pause.
  Above a larger one, end the response, so a thread cannot outlive the client
  that asked for it.
- Always `kill` and then `wait` on the `ffmpeg` process. A leaked process is a
  defect.
- Treat `BrokenPipeError` and `ConnectionResetError` as a normal client
  disconnect. Do not log them as errors. Sonos opens a probe connection and
  closes it.
- If the table holds no songs, `build_server` raises `RuntimeError` before it
  binds a port.

**Steps:**

- [x] **Step 1:** Write `tests/fixtures/make_tone.py`. It generates a short MP3
      file with `ffmpeg` for the tests to serve. Add `tests/fixtures` to
      `[tool.pytest.ini_options] pythonpath`.
- [x] **Step 2:** Write the failing tests in
      `tests/integration/test_station.py`. Use a fake `resolve_fn` that returns
      a local file path, not the network. Name them
      `test_response_carries_an_audio_content_type`,
      `test_metaint_header_appears_only_when_requested`,
      `test_stream_continues_after_a_resolution_failure`,
      `test_a_failed_song_records_a_failure`,
      `test_no_ffmpeg_process_survives_a_client_disconnect`, and
      `test_build_server_refuses_an_empty_catalogue`. For
      `test_stream_continues_after_a_resolution_failure`, make the fake resolver
      fail on its first call and succeed after. Assert that audio bytes still
      arrive. A test that only checks the response stayed open passes against a
      server that sends nothing.
- [x] **Step 3:** Run `uv run pytest tests/integration/test_station.py -v`.
      Expect failure because `station` does not exist.
- [x] **Step 4:** Write `station.py` to make the tests pass.
- [x] **Step 5:** Run `uv run pytest tests/integration/test_station.py -v`.
      Expect every test to pass.
- [x] **Step 6:** Run `just format` then `just lint`. Fix every finding.

**Acceptance criteria:**

- The tests need no network.
- The disconnect test asserts a child process count of zero after the client
  closes.

---

### Task 7: `bootstrap`

**Files:**

- Create: `src/youtube_music_library_radio/bootstrap.py`,
  `tests/unit/test_bootstrap.py`

**Interfaces:**

- Consumes: `Song` and `merge_songs` (Task 3).
- Produces:
  - `access_token(oauth_path: Path, client_secret_path: Path) -> str`
  - `everything_playlist_ids(token: str) -> list[str]`
  - `playlist_video_ids(token: str, playlist_id: str) -> list[str]`
  - `bootstrap(conn: sqlite3.Connection, token: str) -> int`

**Requirements:**

- Read playlists from `https://www.googleapis.com/youtube/v3/playlists` with
  `part=snippet`, `mine=true`, and `maxResults=50`. Follow `nextPageToken`.
- Keep only playlists whose title starts with the word `Everything` and a space.
  The owner's playlists are named `Everything 1` through `Everything 33`.
- Read items from `https://www.googleapis.com/youtube/v3/playlistItems` with
  `part=contentDetails` and `maxResults=50`. Follow `nextPageToken`.
- Build a `Song` for each video ID with an empty title and an empty artist. The
  station fills both after the first successful resolve.
- `bootstrap` returns the number of rows added.
- `access_token` exchanges the stored refresh token. It returns the access token
  string.
- Use `urllib.request` from the standard library. Do not add an HTTP dependency.
- Test the pure parts against recorded JSON payloads held in `tests/fixtures`.
  Do not call the network in a unit test.

**Steps:**

- [x] **Step 1:** Save two small recorded JSON payloads under `tests/fixtures`:
      one playlists page with a `nextPageToken`, one playlist items page.
- [x] **Step 2:** Write the failing tests in `tests/unit/test_bootstrap.py`.
      Name them `test_only_everything_playlists_survive_the_filter`,
      `test_paging_follows_the_next_page_token`,
      `test_playlist_items_yield_video_ids`, and
      `test_bootstrap_adds_one_row_for_each_video_id`. Pass a fake fetch
      function rather than a mock of `urllib`.
- [x] **Step 3:** Run `uv run pytest tests/unit/test_bootstrap.py -v`. Expect
      failure because `bootstrap` does not exist.
- [x] **Step 4:** Write `bootstrap.py` to make the tests pass. Give the paging
      helper an injectable fetch function.
- [x] **Step 5:** Run `uv run pytest tests/unit/test_bootstrap.py -v`. Expect
      four passes.
- [x] **Step 6:** Run `just format` then `just lint`. Fix every finding.

**Acceptance criteria:**

- The unit tests need no network and no credential.
- The quota cost of one full run stays near 354 units, as the design records.

---

### Task 8: `refresh`

**Files:**

- Create: `src/youtube_music_library_radio/refresh.py`,
  `tests/unit/test_refresh.py`

**Interfaces:**

- Consumes: `Song`, `merge_songs`, and `delete_songs` (Task 3).
- Produces:
  - `ExcludedSong` with `video_id`, `title`, `artist`, and `pattern`
  - `LibraryScan` with `songs` and `excluded`
  - `RefreshResult` with `added`, `excluded`, and `deleted`
  - `library_songs(headers_path: Path) -> LibraryScan`
  - `refresh(conn: sqlite3.Connection, headers_path: Path) -> RefreshResult`

**Requirements:**

- Authenticate `ytmusicapi` with browser headers, not OAuth. OAuth returns HTTP
  400 for every authenticated call. See
  <https://github.com/sigma67/ytmusicapi/issues/813>.
- The owner creates the headers file with `uv run ytmusicapi browser`. Document
  that command in the README.
- Read the library with `get_library_songs`. Pass a limit above the library
  size.
- Exclude a song whose title holds `radio edit` or `censored` as a whole word.
  Compare without case. A whole-word match keeps `The Uncensored Mix`, which a
  substring match deletes.
- Delete an excluded song from the catalogue. A skip alone does nothing, because
  `bootstrap` already inserted that video ID. The `Everything N` playlists
  return video IDs with no titles, so this rule can only run against a library
  title.
- Log one line for each excluded song. Name the pattern, the video ID, the
  artist, and the title. The owner reads this list to find a pattern that
  matches too much.
- Build a `Song` with the video ID, the title, and the first artist name.
- `refresh` returns the rows added, the songs excluded, and the rows deleted.
- If the headers file is absent or rejected, raise a clear error that names the
  command to recreate it.
- Test the transform from an API payload to `Song` values against a recorded
  payload. Do not call the network.

**Steps:**

- [x] **Step 1:** Save a small recorded `get_library_songs` payload under
      `tests/fixtures`.
- [x] **Step 2:** Write the failing tests in `tests/unit/test_refresh.py`. Name
      them `test_payload_becomes_songs`, `test_radio_edit_titles_drop_out`,
      `test_missing_headers_file_raises_a_named_error`, and
      `test_refresh_reports_the_added_row_count`. Pass a fake client object
      rather than a mock of `ytmusicapi`.
- [x] **Step 3:** Run `uv run pytest tests/unit/test_refresh.py -v`. Expect
      failure because `refresh` does not exist.
- [x] **Step 4:** Write `refresh.py` to make the tests pass. Separate the
      transform from the client call so the transform stays testable.
- [x] **Step 5:** Run `uv run pytest tests/unit/test_refresh.py -v`. Expect four
      passes.
- [x] **Step 6:** Run `just format` then `just lint`. Fix every finding.

**Acceptance criteria:**

- The unit tests need no network and no cookie.
- A failure in `refresh` never reaches the station process.

---

### Task 9: `control`

**Files:**

- Create: `src/youtube_music_library_radio/control.py`,
  `tests/unit/test_control.py`

**Interfaces:**

- Consumes: `Settings` (Task 1).
- Produces:
  - `station_url(settings: Settings) -> str`
  - `lan_address(speaker_ip: str) -> str`
  - `find_speaker(name: str) -> SoCo`
  - `start(speaker: SoCo, url: str, station_name: str) -> None`
  - `stop(speaker: SoCo) -> None`
  - `transport_state(speaker: SoCo) -> str`

**Requirements:**

- `start` calls `play_uri` with `force_radio=True`. Sonos then rewrites the
  address to `x-rincon-mp3radio://`.
- `lan_address` opens a UDP socket toward the speaker and reads the local
  address. This finds the interface that reaches the speaker. Do not use the
  host name.
- If `settings.station_host` is empty, `station_url` calls `lan_address`.
  Otherwise it uses the configured value. The speaker fetches the stream over
  the network, so `localhost` never works.
- `find_speaker` discovers by name. If no speaker matches, raise an error that
  names the speakers it found.
- There is no watchdog. The station is a passive server, and the speaker starts
  playback. A radio station serves bytes to whoever connects. It holds no view
  on whether a speaker must play.
- The reason is not simplicity alone. A `STOPPED` transport cannot say whether a
  person stopped the speaker or the stream broke. Both look the same from the
  network. Every rule written against that state either fights the owner or goes
  silent for good.
- The owner saves the station URL as a Sonos favourite and presses play. Sonos
  S1 keeps the custom radio URL entry that S2 removed.
- After a reboot, the owner presses play again. This is how every internet radio
  station behaves.
- Change the `speaker_name` default in `settings.py` to `Kitchen`, the name of
  the owner's speaker. The current empty string was inferred, not chosen.
- Add validation to `Settings`. Reject a `no_repeat_window` below zero, a
  `prune_threshold` below one, a `metadata_interval` below one, a `bitrate_kbps`
  below one, and a `station_port` outside 1 to 65535. Name the setting and the
  environment variable in the error. `_env_int` calls `int()` and returns the
  result, so nothing rejects a negative value today. A negative window silences
  the radio. A negative interval plays static.
- Test `station_url` and `find_speaker` with a fake speaker object. Do not touch
  the network in a unit test.
- The speaker itself gets a manual test in Task 10.

**Steps:**

- [x] **Step 1:** Write the failing tests in `tests/unit/test_control.py`. Name
      them `test_station_url_uses_the_settings_host_and_port`,
      `test_settings_reject_a_negative_no_repeat_window`, and
      `test_find_speaker_names_the_alternatives_when_it_fails`. Use a fake
      speaker class that records calls, not a mock library.
- [x] **Step 2:** Run `uv run pytest tests/unit/test_control.py -v`. Expect
      failure because `control` does not exist.
- [x] **Step 3:** Write `control.py` to make the tests pass.
- [x] **Step 4:** Run `uv run pytest tests/unit/test_control.py -v`. Expect
      every test to pass.
- [x] **Step 5:** Run `just format` then `just lint`. Fix every finding.

**Acceptance criteria:**

- No unit test opens a network socket.

---

### Task 10: Command line, deployment, and documentation

**Files:**

- Create: `src/youtube_music_library_radio/__main__.py`,
  `scripts/install_launchagent.sh`,
  `scripts/name.robgant.youtube_music_library_radio.plist.template`,
  `tests/unit/test_main.py`
- Modify: `pyproject.toml`, `README.md`
- Delete: `library_playlist.py`, `sonos_enqueue.py`

**Interfaces:**

- Consumes: every module from Tasks 1 to 9.
- Produces: `main(argv: Sequence[str] | None = None) -> int` and the console
  script `library-radio`.

**Requirements:**

- Provide the subcommands `serve`, `bootstrap`, `refresh`, `play`, `stop`, and
  `status`.
- `serve` starts the station and nothing else. It runs no thread that watches
  the speaker. `bootstrap` fills an empty catalogue. `refresh` merges the
  library. `play` points the speaker at the station. `stop` stops the speaker.
  `status` reports the row count and the transport state.
- Return 0 on success. Return a non-zero value on failure.
- Register the console script `library-radio` under `[project.scripts]`.
- The plist template holds a placeholder for the install path. The install
  script fills it.
- Set `KeepAlive` so launchd restarts the service after a crash.
- The install script accepts `--start`, `--stop`, and no argument. Follow the
  cat-watcher pattern.
- The README must state the `brew bundle` step, the `uv run ytmusicapi browser`
  step, the bootstrap step, and the launchd install step.
- The README must record that the Mac Mini needs `ffmpeg` and `uv` alone. The
  other Homebrew tools serve development.
- The README must record that the `Everything N` playlists now go stale by
  design.
- Rewrite the README title and description for radio, not playlists.

**Steps:**

- [x] **Step 1:** Delete `library_playlist.py` and `sonos_enqueue.py`.
- [x] **Step 2:** Write the failing tests in `tests/unit/test_main.py`. Name
      them `test_unknown_subcommand_returns_non_zero`,
      `test_status_reports_the_row_count`, and
      `test_each_subcommand_is_registered`. Pass fake handlers so no test
      touches the speaker or the network.
- [x] **Step 3:** Run `uv run pytest tests/unit/test_main.py -v`. Expect failure
      because `__main__` does not exist.
- [x] **Step 4:** Write `__main__.py` to make the tests pass.
- [x] **Step 5:** Run `uv run pytest tests/unit/test_main.py -v`. Expect every
      test to pass.
- [x] **Step 6:** Add `[project.scripts]` to `pyproject.toml`. Run `uv sync`.
      Run `uv run library-radio --help`. Expect the subcommand list.
- [x] **Step 7:** Write the plist template and the install script.
- [x] **Step 8:** Rewrite `README.md`.
- [x] **Step 9:** Run `just format` then `just lint`. Fix every finding.

**Acceptance criteria:**

- `uv run library-radio --help` lists every subcommand.
- `shellcheck scripts/install_launchagent.sh` reports no error.
- The README names every setup step in order.

---

### Task 11: Whole system verification

No new code. This task proves the system against the real speaker and the real
account.

**Steps:**

- [x] **Step 1:** Run `just brew-check` then `just check`. Expect no absent
      tool. Expect format, lint, and test to pass.
- [x] **Step 2:** Run `uv run pytest --cov -m "not network"`. Expect coverage at
      or above 90 percent.
- [x] **Step 3:** Run `uv run pytest -m network -v`. Expect the resolver tests
      to pass.
- [ ] **Step 4:** Run `uv run library-radio bootstrap`. Expect a row count near
      16,054.
- [x] **Step 5:** Run `uv run library-radio status`. Expect the row count and a
      transport state.
- [x] **Step 6:** Set the Kitchen speaker volume to 0. Run
      `uv run library-radio serve` in one terminal. Run
      `uv run library-radio play` in another.
- [ ] **Step 6b:** Record whether Sonos restarts the stream by itself. Kill the
      station process while the speaker plays, then start it again. Watch
      whether the speaker reconnects without help. This decides nothing. It
      tells the owner whether a reboot needs one press of play.
- [x] **Step 7:** Watch for five minutes. Expect the state to stay `PLAYING`.
      Expect the Sonos app to show a song title and an artist name. Expect the
      title to change between songs.
- [x] **Step 8:** Confirm that no `ffmpeg` process survives after
      `uv run library-radio stop`.
- [ ] **Step 9:** Install the launchd agent. Reboot or reload the agent. Confirm
      the service returns.
- [ ] **Step 10:** Raise the volume and listen. Confirm the audio is correct.
- [ ] **Step 11:** Ask the owner to rename the GitHub repository to
      `youtube-music-library-radio`. GitHub redirects the old URL, so the git
      remote needs no change.
- [ ] **Step 12:** Report the results. The owner reviews every change and makes
      one commit.

**Acceptance criteria:**

- The speaker plays library songs in random order for five minutes without a
  stop.
- The Sonos app shows the current song title and artist.
- No `ffmpeg` process leaks.
- `just check` passes.

---

## Notes for the implementer

- The design document records the evidence behind every architectural choice.
  Read it before Task 6.
- Prototypes of the station, the ICY encoder, and the speaker control exist in
  the session scratchpad. They prove the approach. They are not production code.
  Do not copy them without review.
- The owner reviews every change and runs git. Do not stage. Do not commit.
