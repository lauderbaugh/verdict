# verdict

A small app that gathers suggested albums and tracks from various critics and maintains a Spotify playlist for the last few weeks.

Read [SPEC.md](SPEC.md) before making changes. It records API shapes and
page structures verified against live responses, and it wins over recollection.

## Status

Sources and resolution. The rolling playlist window and deployment are not built
yet, and nothing has run against a real Spotify account.

| Component | State |
|---|---|
| `verdict/verso.py` — Condé Nast state-blob reader | built |
| `verdict/feed.py` — RSS discovery | built |
| `verdict/sources/pitchfork_roundup.py` | built |
| `verdict/sources/pitchfork_bnm.py` | built |
| `verdict/journal.py` — NDJSON history | `unmatched` only |
| `verdict/spotify.py` — raw-HTTP client, read paths | built |
| `verdict/resolve/` — album lookup and track validation | built |
| `playlist/` — rolling 4-week window | not started |
| `bootstrap.py`, GitHub Actions workflow | not started |

## Tests

Fixture-driven and fully offline; nothing here touches the network.

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

The fixtures in `tests/fixtures/` are real captured Pitchfork pages. They are the
regression net for the one fragile dependency in the project — the
`__PRELOADED_STATE__` blob, which has no stability contract.
