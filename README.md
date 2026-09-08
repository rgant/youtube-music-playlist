# YouTube Music Library Radio

Keep a catalogue of the owner's YouTube Music library, and command a Sonos
speaker from it.

## How it works

- The catalogue is a SQLite database of every song in the library.
- `bootstrap` seeds it from the owner's `Everything N` YouTube playlists.
- `refresh` merges the live library into it, and deletes every excluded song.
- Each row carries the YouTube video ID and the Sonos track ID that plays it.
- `harvest` learns a Sonos track ID by reading a queue you filled from a
  playlist.
- `rematch` spends the same evidence again, against songs no harvest reached.
- `queue` sends a random sample of paired songs to the speaker, with no recent
  repeat.

The speaker plays the audio. Nothing here streams it, so no request reaches
Google during playback.

## Risk

`refresh` and `auth` read the library with the owner's browser cookies, through
`ytmusicapi`. They read the library and move no audio, which is the same action
the web app performs. `bootstrap` reads the YouTube Data API v3 over OAuth,
which Google supports.

Google offers no API that streams this audio. The YouTube Data API v3 serves
metadata alone, and YouTube Music has no public API. A design that moves audio
therefore needs a route Google does not support. The Sonos YouTube Music service
is Google's own integration, and playback through it carries no such risk.

## Requirements

The machine that runs these commands needs `uv` and `just`. The other Homebrew
tools in the `Brewfile` serve development: `dprint`, `markdownlint-cli`,
`shellcheck`, and `shfmt`.

`uv` downloads its own Python, because `pyproject.toml` sets
`python-preference = "only-managed"`. A Homebrew Python does not serve this
project. See
[Sonos discovery and the macOS firewall](#sonos-discovery-and-the-macos-firewall).

## Setup

Do these steps in order.

### 1. Install the Homebrew tools

```sh
brew bundle
```

Run `brew bundle check` to verify that no tool is absent.

### 2. Create the Python environment

```sh
uv sync
```

### 3. Write the OAuth token file

`bootstrap` reads the YouTube Data API over OAuth. It needs two files in the
repository root.

- `client_secret.apps.googleusercontent.com.json`. Download it from the Google
  Cloud console. Create an OAuth client ID of the type "TVs and Limited Input
  devices", because `ytmusicapi` uses the device-code flow. Save the download
  under that exact name.
- `oauth.json`. The command below writes it.

```sh
uv run ytmusicapi oauth
```

The command asks for a client ID and a client secret. Read both values from
`client_secret.apps.googleusercontent.com.json`. Then follow the device-code
instructions in the browser.

### 4. Write the browser headers file

`ytmusicapi` cannot read the library over OAuth. Every authenticated OAuth call
returns HTTP 400 against this account. See
[sigma67/ytmusicapi#813](https://github.com/sigma67/ytmusicapi/issues/813).
Browser cookies are the only route left.

```sh
uv run library-radio auth
```

Do these steps before you run the command. It reads the clipboard.

1. Open `https://music.youtube.com` in Chrome. Sign in.
2. Press Cmd-Option-I to open DevTools. Choose the Network tab.
3. Type `browse` in the filter box.
4. Click "Library" in the app, so the page makes a request.
5. Find a POST to `https://music.youtube.com/youtubei/v1/browse`.
6. Right-click that request. Choose Copy, then "Copy as cURL".
7. Run `uv run library-radio auth`.

A page load does not work. `ytmusicapi` needs the `cookie` header and the
`x-goog-authuser` header, and only a signed-in API call carries both. If the
capture holds neither, the command names which one is absent, prints these
steps, and writes nothing.

The clipboard is the default because a terminal cannot carry this text. One line
of a Chrome capture runs past 2000 characters. A terminal accepts about 1024 on
one line. A paste stalls there, and Ctrl-D does nothing.

To read a file instead, use `--from-file PATH`. To read a pipe, use
`--from-file -`. `auth` accepts a raw header block as well, for a browser that
offers one.

The command writes the new headers to `browser.json.new`, reads 25 songs, and
reports the count. Then it moves that file onto `browser.json`.

CAUTION: the capture holds live Google account cookies. Anyone who reads them
can open your account without a password. Keep the capture on the clipboard, or
in a file you delete. Never put it into a chat, an issue, or a message.

That read is the point of the command. It proves the new cookies work. If the
read returns no songs, the command names the fault and returns a non-zero value.
It then deletes `browser.json.new` and leaves the existing `browser.json` in
place. A failed `auth` never destroys a credential that still works.

`--headers` names another path. `refresh` takes the same option with the same
default, so change both together.

### 5. Fill the catalogue

`bootstrap` reads the `Everything N` YouTube playlists for their song IDs. It
reads `oauth.json` and `client_secret.apps.googleusercontent.com.json` from
step 3.

```sh
uv run library-radio bootstrap
uv run library-radio refresh
```

`bootstrap` seeds an empty catalogue one time. `refresh` adds the library songs
that the playlists never held. Run `refresh` again at any time to add new songs.
`refresh` reads the whole library, which takes a few minutes.

## Commands

| Command                   | Purpose                                         |
| ------------------------- | ----------------------------------------------- |
| `library-radio bootstrap` | Fill an empty catalogue from the playlists.     |
| `library-radio auth`      | Write `browser.json` and verify it.             |
| `library-radio playlists` | Put every uncovered library song in a playlist. |
| `library-radio harvest`   | Pair the Sonos queue against one playlist.      |
| `library-radio rematch`   | Pair songs against tracks the catalogue holds.  |
| `library-radio queue`     | Send a random sample of songs to the speaker.   |
| `library-radio refresh`   | Merge the live library into the catalogue.      |
| `library-radio stop`      | Stop the speaker.                               |
| `library-radio status`    | Report the row count and the transport state.   |

Each command returns 0 after success. Each command returns a non-zero value
after a failure.

`status` reads the row count from the catalogue, which is always available. It
then reads the transport state, which needs the speaker on the LAN. If discovery
fails, `status` still reports the row count and returns a non-zero value.

## The Sonos queue

The catalogue is the record. Each row holds one library song, its YouTube video
ID, and the Sonos track ID that plays it. The queue builder reads that row and
sends nothing to Google.

### Harvest a playlist

A Sonos track ID exists nowhere but a Sonos queue. So the catalogue learns one
by reading a queue you filled from a playlist.

1. In the Sonos app, clear the queue.
2. Add one `Everything N` playlist to the queue.
3. Run the harvest. Name that playlist on the command line.

```sh
uv run library-radio harvest --playlist "Everything 32"
```

The command reads the queue, matches each entry against that playlist's songs,
and writes the pairing. It logs every entry it left unplaced, which is usually a
song you removed from the library.

One playlist at a time is what makes the match safe. A 500-song pool holds no
repeated title, so no entry is ambiguous. Against the whole library the same
rules collide.

The command refuses a playlist the catalogue does not know. It also refuses a
queue whose length sits more than `YTM_RADIO_HARVEST_TOLERANCE` from the
playlist length. A wrong pool writes wrong pairings, and nothing later detects
one.

### Pair from what the catalogue already saw

Every harvest records each queue entry in `sonos_tracks`, paired or not. That
table therefore describes songs that no harvest reached. `rematch` spends it.

```sh
uv run library-radio rematch
```

That prints the count and writes nothing. To write, add `--commit`.

```sh
uv run library-radio rematch --commit
```

It compares title, artist, and album across the whole library, which `harvest`
refuses to do. One rule makes the wider pool safe. The key must name exactly one
song and exactly one free track. A key that fits two songs, or two tracks, pairs
nothing.

Against the 1,752 pairs three real harvests made, the rule chose the same track
1,700 times and a different one 6 times. All six name albums that Sonos
re-keyed, so the song is right and the handle is old. A stale handle fails when
the speaker plays it.

`rematch` never changes a song that already carries a pairing. A harvest saw
that song against one playlist, which is the stronger evidence.

### Send a queue

```sh
uv run library-radio queue
```

That prints the sample and leaves the speaker alone. To send it, add `--commit`.

```sh
uv run library-radio queue --commit
```

The command chooses `YTM_RADIO_QUEUE_SIZE` songs at random from those that carry
a Sonos track ID and did not reach the queue inside
`YTM_RADIO_QUEUE_WINDOW_DAYS`. It clears the speaker's queue, sends each track,
and records the time against every song it sent.

Each track carries its title, artist, and album, so the Sonos app names it. The
speaker plays a track with no metadata, and it shows the raw URI instead.

It refuses to send when too few songs qualify. A short queue hides how much of
your library the speaker cannot reach. The fix is another harvest, or `rematch`.

## Removed songs

`refresh` removes a song the library lost. It is careful about it, because a
short library read looks exactly like a mass removal.

- A read is trusted when it returns at least `YTM_RADIO_TRUST_RATIO` of the
  stored row count.
- An untrusted read changes no count, deletes no row, and returns a non-zero
  value.
- A trusted read raises `missing_count` on every song it did not return, and
  clears that count on every song it did.
- A song goes once `missing_count` reaches `YTM_RADIO_MISSING_THRESHOLD`.

So a removal needs several trusted reads in a row to agree. A song you delete on
purpose leaves after that many refreshes. A transient fault costs a wait.

## Playlists

The `Everything N` playlists carry the library into the Sonos queue. A song no
playlist holds cannot reach the speaker.

```sh
uv run library-radio playlists
```

That run reads, prints the plan, and changes nothing. To write, add `--commit`:

```sh
uv run library-radio playlists --commit
```

The dry run is the default because a write reaches your Google account.

The command fills the free slots of an existing playlist first. Then it creates
new playlists that continue the ordinal run.

`--unpaired` changes what it gathers. It puts every song that carries no Sonos
pairing into new playlists, so one harvest of each reaches them all.

```sh
uv run library-radio playlists --unpaired --commit
```

Those songs already sit in a playlist, about 25 in each, so a harvest of every
one of those playlists costs many rounds. This run repeats them on purpose,
which the normal run refuses. It reads no library, because the catalogue already
knows which songs carry no pairing. It never writes into an existing playlist,
because a harvest already ran against those.

### How it survives a short read

`ytmusicapi` returns fewer songs than a playlist claims often enough to matter.
A short set looks like "these songs are in no playlist", so a run against it
adds songs the playlists already hold. One such run put 275 songs into
`Everything 32` that `Everything 1` to `Everything 8` already held.

The catalogue answers that. Every run adds what it read to the `playlist_songs`
table and deletes nothing. The generator works from that accumulated set, not
from one read. A playlist that returns 2 songs today still contributes the 500
an earlier run saw.

The run stops only when the accumulated set is smaller than a playlist claims.
That means songs exist which no run has ever seen. Run the command again, and
the store fills the gap.

The union over-states membership if you delete a song from a playlist by hand.
That song then waits, and no run puts it back. Over-statement makes a song wait.
Under-statement writes a duplicate, so the store leans this way on purpose.

These faults stop the run the same way:

- The accumulated set holds no `Everything N` for some N below the highest.
- The library read returns nothing, which means the credential expired.
- A planned write names a song a playlist already holds.
- A write reports success, and a re-read does not confirm it.

### What it does not do

The command removes nothing. It never deletes a duplicate, a stale entry, or a
playlist.

A song in two playlists costs nothing downstream. The Sonos harvest keys on the
track ID, so a repeated song reaches the database one time.

`library-radio playlists` logs a warning that names how many songs sit in more
than one playlist. Read `docs/plans/2026-08-15-playlist-generator.md` for the
measurement.

## Expired browser cookies

Browser cookies expire. `browser.json` stops working some months after you write
it.

YouTube answers an expired session with an empty library and no error. The
library read returns zero songs and raises nothing. A silent zero reads as "the
library holds no new songs". It means "the credential is dead".

`refresh` and `auth` both refuse an empty read. Each one raises, names
`browser.json`, and returns a non-zero value. The message says that the read
succeeded and returned nothing, which is a different fault from an absent file.

`library-radio auth` is the fix:

```sh
uv run library-radio auth
```

The command writes new cookies and verifies them against the library in one
step. If the verification fails, it leaves the existing `browser.json` in place.
You are never worse off than before you ran it.

A library that truly holds no songs also raises. This account holds about 21,000
songs, so that case cannot happen here.

## Excluded songs

`refresh` excludes a song whose library title holds `radio edit` or `censored`
as a whole word. It deletes an excluded song from the catalogue.

A deletion is necessary, not a tidy-up. `bootstrap` reads the `Everything N`
playlists, which carry video IDs and no titles, so it seeds every song before
any title exists. A skip alone leaves the row in place, and a player plays it.

The match reads whole words, so `The Uncensored Mix` stays. A plain substring
match deletes it.

`refresh` logs one line for each excluded song. The line names the pattern, the
video ID, the artist, and the title. Read that list after a run. It is the one
place an over-matching pattern shows itself, and a title such as
`Censored by the BBC` matches the rule for the wrong reason.

To change the rule, edit `_EXCLUDED_PATTERNS` in
`src/youtube_music_library_radio/refresh.py`.

A deletion is final. The catalogue holds no history.

To recover from a wrong pattern, first correct the pattern. Then run `bootstrap`
and `refresh` again. `bootstrap` restores a song that the `Everything N`
playlists hold. `refresh` restores a song that the library holds. A restored row
carries no Sonos pairing, so a harvest must run again for it.

Copy the database before the first run with a new pattern. See
[The catalogue on another machine](#the-catalogue-on-another-machine).

## Settings

Every setting reads an environment variable with the `YTM_RADIO_` prefix.

| Variable                      | Default                                                        | Purpose                                                    |
| ----------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------- |
| `YTM_RADIO_DATABASE_PATH`     | `~/.local/share/youtube-music-library-radio/catalogue.sqlite3` | The catalogue file.                                        |
| `YTM_RADIO_SPEAKER_NAME`      | `Kitchen`                                                      | The name of the Sonos speaker.                             |
| `YTM_RADIO_TRUST_RATIO`       | `0.9`                                                          | How much of the catalogue a read must return.              |
| `YTM_RADIO_MISSING_THRESHOLD` | `3`                                                            | How many trusted reads remove an absent song.              |
| `YTM_RADIO_QUEUE_SIZE`        | `500`                                                          | How many songs one Sonos queue holds.                      |
| `YTM_RADIO_QUEUE_WINDOW_DAYS` | `7`                                                            | How long a queued song stays out of the next.              |
| `YTM_RADIO_REFRESH_HOUR`      | `4`                                                            | The hour the LaunchAgent runs `refresh`.                   |
| `YTM_RADIO_REFRESH_MINUTE`    | `0`                                                            | The minute the LaunchAgent runs `refresh`.                 |
| `YTM_RADIO_HARVEST_TOLERANCE` | `25`                                                           | How far the queue length can sit from the playlist length. |

`YTM_RADIO_QUEUE_WINDOW_DAYS` sets the window `catalogue.queueable_songs`
excludes. A value of zero excludes nothing.

`YTM_RADIO_REFRESH_HOUR` and `YTM_RADIO_REFRESH_MINUTE` reach launchd alone. No
command reads a clock. See
[Deploying to the Mac mini](#deploying-to-the-mac-mini).

## The Everything N playlists

The account holds the playlists `Everything 1` through `Everything 33`. They are
the residue of an earlier design. `bootstrap` reads them one time, for the song
IDs they hold.

Nothing writes to those playlists now. They go stale by design. The catalogue is
the source of truth.

## The catalogue on another machine

The catalogue holds derived data. To set up a second machine, run the setup
steps there. `bootstrap` rebuilds every row from the playlists, and `refresh`
adds the library songs. Copy the credential files across, because git ignores
them:

- `client_secret.apps.googleusercontent.com.json`
- `oauth.json`
- `browser.json`

The machine that builds the queue owns the catalogue. It writes `last_queued`
for every song it sends. A copy on a second machine goes stale at once. Move a
catalogue in one direction only, at setup time.

To move a catalogue, write a single-file snapshot and copy that file:

```sh
sqlite3 ~/.local/share/youtube-music-library-radio/catalogue.sqlite3 \
  "VACUUM INTO '/tmp/catalogue-snapshot.sqlite3'"
```

Never copy `catalogue.sqlite3` with `cp`, `scp`, or `rsync` while a player runs.
WAL journal mode holds recent writes in a separate `-wal` file. A copy of the
main file alone loses those writes, and it still reports a plausible row count.
`VACUUM INTO` writes one consistent file with no `-wal` companion.

## Deploying to the Mac mini

The mini runs the schedule. The laptop stays the development machine. Each later
deploy is `just deploy-update`, run on the mini.

`refresh` runs as a user-level LaunchAgent, and not as a LaunchDaemon. The
catalogue path starts with `~`, and macOS grants local network access per user.
A root daemon has neither. So the mini needs auto-login and a session that stays
open.

### First-boot procedure

Prerequisites:

- A user account with auto-login and a GUI session that stays open.
- Sleep turned off, so the agent fires at its hour.
- [Homebrew](https://brew.sh/) installed.
- The Sonos speaker on the same network as the mini.

Each command below runs on the mini, except the `scp` lines.

The clone uses the HTTPS URL, because the repository is public. The mini needs
no GitHub key, and it never pushes.

```sh
git clone https://github.com/rgant/youtube-music-playlist.git \
  ~/Programming/youtube-music-library-radio
cd ~/Programming/youtube-music-library-radio
brew install uv just    # the two tools the runtime needs
uv sync                 # Python 3.14 and the dependencies
```

Do not run `brew bundle` on the mini. It also installs `dprint` and
`markdownlint-cli`, which serve development alone. Homebrew ships no bottle for
an Intel Mac, so each one builds from source. `markdownlint-cli` pulls a `node`
build of more than an hour.

`uv sync` downloads a managed interpreter, because `pyproject.toml` sets
`python-preference = "only-managed"`. Read
[Sonos discovery and the macOS firewall](#sonos-discovery-and-the-macos-firewall)
for the reason.

Next copy the credential files from the laptop. Git ignores each one, so the
clone carries none of them.

```sh
scp oauth.json client_secret.apps.googleusercontent.com.json browser.json \
  <mini>:Programming/youtube-music-library-radio/
```

CAUTION: `browser.json` holds live Google account cookies. Copy it over your own
network. Never put it into a chat, an issue, or a message.

Then move the catalogue. Follow
[The catalogue on another machine](#the-catalogue-on-another-machine). Write the
snapshot on the laptop, copy that file, and put it at the path
`YTM_RADIO_DATABASE_PATH` names.

Make the directory on the mini before the copy. `scp` refuses a target directory
that is absent.

```sh
mkdir -p ~/.local/share/youtube-music-library-radio
```

Archive the laptop catalogue after the move. One machine owns it. Two copies
disagree about `last_queued`, `missing_count`, and `playlist_songs`, and both
clear the same Sonos queue.

Last, verify the deploy and start the agent.

```sh
uv run library-radio status    # the row count proves the catalogue moved
just install-agents            # render the plist, then bootstrap it
just agents-status             # confirm that launchd holds the label
just agents-kick               # run refresh now, then read the log
just agents-notify-test        # show one banner, then grant permission
```

`status` reports the row count first, then the transport state. If it names the
speaker, the firewall already accepts the interpreter. If it reports
`Discovery found: none`, read
[Sonos discovery and the macOS firewall](#sonos-discovery-and-the-macos-firewall).

Answer the notification prompt that `just agents-notify-test` raises. macOS asks
one time, and it draws the dialog on the screen of the mini. An `ssh` session
shows nothing, so watch that screen over screen sharing. An unanswered prompt
swallows every later banner, and a banner is the one sign of a failed refresh.

### Sonos discovery and the macOS firewall

`soco` finds the speaker with SSDP, which is multicast UDP. The macOS
application firewall accepts the reply only when the running interpreter meets
both of these conditions:

- The binary carries a code signature.
- The binary sits on the firewall allow list.

Either one alone fails. The failure looks like
`RuntimeError: no speaker named 'Kitchen' found. Discovery found: none`, and
`curl http://<speaker>:1400/status/zp` still answers `200`. The firewall gates
incoming connections alone.

`pyproject.toml` sets `python-preference = "only-managed"` for the first
condition. Homebrew builds Python from source on a Mac that no bottle covers,
and a local build signs nothing. That build is also a 32 KB stub beside a
separate dylib, and a signature on the pair does not satisfy the firewall. A
managed interpreter is one self-contained file that an ad-hoc signature covers.

Meet the second condition once, on the mini:

```sh
PY="$(readlink .venv/bin/python)"
codesign --force --sign - "${PY}"
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add "${PY}"
sudo /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp "${PY}"
uv run library-radio status
```

`uv python upgrade` writes a new unsigned binary, so discovery can stop again.
macOS can raise its own dialog for the new binary on the screen of the mini.
Answer that dialog. If discovery still fails, run the commands above again.

`refresh` never reads the speaker, so the schedule survives a lapse. `queue`,
`harvest`, `stop`, and `status` all stop.

### What the agent runs

`just install-agents` fills the templates under `scripts/plists/` from the
current settings and writes the result to `~/Library/LaunchAgents`. The label is
`com.robgant.library-radio.refresh`. It runs `.venv/bin/library-radio refresh`
at `YTM_RADIO_REFRESH_HOUR:REFRESH_MINUTE` each day.

launchd gives the agent no shell. The render writes every value in
[Settings](#settings) into the plist, so the agent and a terminal run act on the
same values. After you change a setting, run `just install-agents` again.

The plist names an absolute path for `--headers`, so it describes every file the
agent reads. After you move the repository, run `just install-agents` again.

The plist passes `--notify` as well. After a failure the command shows a macOS
banner titled `library-radio refresh failed`, with the fault as its body. A
terminal run omits the flag, because it prints the fault already.

The agent writes its output to `refresh.stdout.log` and `refresh.stderr.log`, in
the `logs` directory beside the catalogue.

### Operations

`<catalogue>` below is the directory that holds `catalogue.sqlite3`.

| Need                     | Command                                        |
| ------------------------ | ---------------------------------------------- |
| Deploy a new commit      | `just deploy-update`                           |
| Report the loaded agents | `just agents-status`                           |
| Run `refresh` now        | `just agents-kick`                             |
| Test the failure banner  | `just agents-notify-test`                      |
| Read the log             | `tail -f <catalogue>/logs/refresh.stderr.log`  |
| Stop every agent         | `bash scripts/install_launchagents.sh --stop`  |
| Start every agent        | `bash scripts/install_launchagents.sh --start` |
| Remove every agent       | `just uninstall-agents`                        |
| Send a Sonos queue       | `uv run library-radio queue --commit`          |

Run the script directly for `--stop` and `--start`. A deploy that moves the
catalogue must stop every agent first, and not write underneath a run.

### What the schedule does not cover

`queue --commit` is not an agent. It clears the Sonos queue first, so a
scheduled run stops a song the speaker plays. Send a queue by hand.

The failure banner needs a person at the mini. Nothing sends mail, and nothing
raises an alarm on another machine. Browser cookies expire after some months,
and `queue` keeps working from the paired songs. So a missed banner shows itself
as "no new songs" alone. If a month passes with no new song, read
`refresh.stderr.log`. See [Expired browser cookies](#expired-browser-cookies).

`auth` needs Chrome and the clipboard. A headless mini has neither. Run `auth`
on the laptop and copy the new `browser.json`. Or write the cURL capture to a
file, copy that file, and run `library-radio auth --from-file PATH` on the mini.

## Development

```sh
just check
```

`just check` runs every linter and the test suite. Run `just --list` for the
other recipes.

## License

GPL-3.0-or-later. See `LICENSE`.
