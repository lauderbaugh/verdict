# verdict

A small app that gathers suggested albums and tracks from music critics and
maintains a Spotify playlist of the last four weeks.

Read [SPEC.md](SPEC.md) before making changes. It records API shapes and page
structures verified against live responses, and it wins over recollection.

## How it works

Once a week: read the critics, pick tracks, update a rolling playlist.

```
sources/   RSS discovery + article parsing  ->  Verdict
resolve/   Verdict -> Spotify album -> validated track URIs
playlist/  rolling 28-day window
log/       NDJSON history, and the bug queue
```

A **Verdict** is one publication's opinion on one album: artist, album, label,
source URL, an optional source-native `score`, and any tracks the writeup named.
Scores are never normalized across publications.

### Sources

`docs/second-source-candidates.md` surveys others.

- **`pitchfork_roundup`** — the weekly "N New Albums You Should Listen to Now",
  ~13 albums a week. The primary source; it covers records that never get a full
  review.
- **`pitchfork_bnm`** — Best New Music, ~1-3 a week. Filtered on the editorial
  flag, never on score: a verified 8.0 was not Best New Music while 8.6 and 9
  were.
- **`npr_new_music_friday`** — ~10 albums a week, read straight from the podcast
  show notes with no page fetch. Names no tracks, so its albums always reach the
  fallback chain.

Stereogum is fetched once a week but is **not** a source: it contributes no
tracks, only evidence about albums the sources already found. Two separate
signals land on each track in the log — whether the album appeared on
Stereogum'"'"'s comprehensive weekly list (mostly a calendar fact) and whether it
was Stereogum'"'"'s own Album Of The Week (real agreement). Nothing consumes either
yet.

### Selecting tracks

Two to four tracks per album, chosen in this order:

1. **Named** — tracks the critic named, kept only if they match the real
   tracklist. Capped at 4, in the order the writeup named them.
2. **Title track** — the track sharing the album's name, if there is one. Above
   play counts on purpose: the artist naming the record after a track beats a
   number inferred from listening.
3. **Last.fm** — fill to 2 by play count. Skipped entirely when the critic named
   enough or the title track already closed the gap.
4. **Positional** — if Last.fm is unavailable or the album is too sparsely
   played. Prefers tracks 2 and 4, never defaults to track 1, and skips anything
   under 90 seconds when a longer track is available.

All three fallback rungs skip tracks whose titles label them interludes or
skits. Tracks a critic named explicitly are never filtered — writing about an
interlude is a deliberate pick.

Quoted candidates are deliberately noisy — roughly half are lyrics or scare
quotes. They are only ever *filtered* against the real tracklist, never trusted,
so a false positive has to coincide with an actual track on the actual album.

### The rolling window

There is no database. State lives in the playlist: each item carries `added_at`,
which is all the 28-day age-out needs. A track is skipped only if it is in the
playlist now or was added in the trailing 28 days — the log is a history, not a
permanent blocklist.

A run that resolves nothing writes nothing. Age-out is skipped too, so a broken
week cannot quietly drain the playlist.

## Logs

Append-only NDJSON, committed by the weekly job.

| File | Holds |
|---|---|
| `log/additions.ndjson` | every track added, with how it was chosen — `named` / `lastfm` / `positional`, its match confidence or play count, and the rule that placed it |
| `log/removals.ndjson` | URIs aged out of the window |
| `log/skips.ndjson` | tracks the interlude filter kept out of the fallback rungs — not failures, the filter working |
| `log/unmatched.ndjson` | the bug queue: albums that did not resolve, pages whose structure moved, feeds that came back empty |

`unmatched.ndjson` is where the project tells you it is broken. Nothing fails
silently into it.

## Setup

One-time, on your machine. Needs a Spotify app with
`http://127.0.0.1:8888/callback` registered as a redirect URI.

```bash
export SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=...
python3 bootstrap.py          # authorise once; prints a refresh token + playlist id
export SPOTIFY_REFRESH_TOKEN=...
python3 probe_write_path.py   # confirm the write path on a scratch playlist
```

Then add repository secrets: `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`,
`SPOTIFY_REFRESH_TOKEN`, `SPOTIFY_PLAYLIST_ID`, and `LOG_COMMIT_PAT` (a
fine-grained PAT with contents write — logs are committed with it rather than
`GITHUB_TOKEN`, so the push counts as repository activity).

`LASTFM_API_KEY` is optional. Without it, step 2 above is skipped and albums fall
through to track position.

Run the weekly workflow by hand from the Actions tab before trusting the cron.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Entirely offline — nothing in the suite touches the network. The fixtures in
`tests/fixtures/` are real captured pages, and they are the regression net for
the one fragile dependency in the project: Pitchfork's `__PRELOADED_STATE__`
blob, which has no stability contract.
