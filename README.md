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

The machine that runs these commands needs `uv` alone. The other Homebrew tools
in the `Brewfile` serve development: `just`, `dprint`, `markdownlint-cli`,
`shellcheck`, and `shfmt`.

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
queue whose length is far from the playlist length. A wrong pool writes wrong
pairings, and nothing later detects one.

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
1,700 times and a different one 6 times. All six name albums that Sonos re-keyed,
so the song is right and the handle is old. A stale handle fails when the speaker
plays it.

`rematch` never changes a song that already carries a pairing. A harvest saw that
song against one playlist, which is the stronger evidence.

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
one of those playlists costs many rounds. This run repeats them on purpose, which
the normal run refuses. It reads no library, because the catalogue already knows
which songs carry no pairing. It never writes into an existing playlist, because
a harvest already ran against those.

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

| Variable                      | Default                                                        | Purpose                                         |
| ----------------------------- | -------------------------------------------------------------- | ----------------------------------------------- |
| `YTM_RADIO_DATABASE_PATH`     | `~/.local/share/youtube-music-library-radio/catalogue.sqlite3` | The catalogue file.                             |
| `YTM_RADIO_SPEAKER_NAME`      | `Kitchen`                                                      | The name of the Sonos speaker.                  |
| `YTM_RADIO_TRUST_RATIO`       | `0.9`                                                          | How much of the catalogue a read must return.   |
| `YTM_RADIO_MISSING_THRESHOLD` | `3`                                                            | How many trusted reads remove an absent song.   |
| `YTM_RADIO_QUEUE_SIZE`        | `500`                                                          | How many songs one Sonos queue holds.           |
| `YTM_RADIO_QUEUE_WINDOW_DAYS` | `7`                                                            | How long a queued song stays out of the next.   |

`YTM_RADIO_QUEUE_WINDOW_DAYS` sets the window `catalogue.queueable_songs`
excludes. A value of zero excludes nothing.

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

## Development

```sh
just check
```

`just check` runs every linter and the test suite. Run `just --list` for the
other recipes.

## License

GPL-3.0-or-later. See `LICENSE`.
