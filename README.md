# verdict

A small app that gathers suggested albums and tracks from various critics and maintains a Spotify playlist for the last few weeks.

Read [SPEC.md](SPEC.md) before making changes. It records API shapes and
page structures verified against live responses, and it wins over recollection.

## Status

Feature complete, but **nothing has ever run against a real Spotify account**.
The write path is built to the documented request bodies and covered by offline
tests; `probe_write_path.py` is what confirms it for real.

| Component | State |
|---|---|
| `verdict/verso.py` — Condé Nast state-blob reader | built |
| `verdict/feed.py` — RSS discovery | built |
| `verdict/sources/` — roundup + Best New Music | built |
| `verdict/spotify.py` — raw-HTTP client, read and write | built, writes unverified |
| `verdict/resolve/` — album lookup and track validation | built |
| `verdict/playlist/window.py` — rolling 4-week window | built |
| `verdict/journal.py` — NDJSON history | built |
| `verdict/run.py` — weekly orchestrator | built |
| `bootstrap.py` — one-time handshake | built, needs running |
| `.github/workflows/` — CI + weekly cron | built, needs secrets |

## Setup

One-time, on your machine. Requires a Spotify app with
`http://127.0.0.1:8888/callback` registered as a redirect URI.

```bash
export SPOTIFY_CLIENT_ID=...
export SPOTIFY_CLIENT_SECRET=...

python3 bootstrap.py          # authorise once; prints a refresh token + playlist id
export SPOTIFY_REFRESH_TOKEN=...
python3 probe_write_path.py   # confirm the write path on a scratch playlist
```

`probe_write_path.py` exists because the February 2026 rename moved the `DELETE`
body key from `tracks` to `items`. A body inferred from the old endpoint is
accepted and removes nothing — a silent no-op. Delete the probe once it passes.

Then add five repository secrets: `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`,
`SPOTIFY_REFRESH_TOKEN`, `SPOTIFY_PLAYLIST_ID`, and `LOG_COMMIT_PAT` — a
fine-grained PAT with contents write. The logs are committed with the PAT rather
than `GITHUB_TOKEN`: scheduled workflows auto-disable after 60 days of repository
inactivity, and whether bot commits reset that timer is undocumented.

Run it by hand from the Actions tab before trusting the cron.

## Tests

Fixture-driven and fully offline; nothing here touches the network.

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

The fixtures in `tests/fixtures/` are real captured Pitchfork pages. They are the
regression net for the one fragile dependency in the project — the
`__PRELOADED_STATE__` blob, which has no stability contract.
