# Verdict
Read SPEC.md before any work. It contains API shapes and HTML/JSON
structures verified against live responses on 2026-08-30.

When SPEC.md and your training data disagree about the Spotify API or
Pitchfork's page structure, SPEC.md wins. Do not "correct" it from memory.
Verify against the live endpoint if unsure.

## Conventions

`__PRELOADED_STATE__` is read only in `verdict/verso.py`. Adapters go
through it and never touch the blob directly, so a Verso change has one
place to be fixed. Every entry point there raises `StateShapeError` rather
than letting a `KeyError` escape — a run must degrade to a logged row in
`unmatched.ndjson`, never crash.

Prose blocks are chosen by allowlist (`p`), not by denying ad and
newsletter tags. Verified pages carry at least seven such block types and
Verso adds more; a denylist silently leaks their text into track
candidates.

Track candidates are meant to be noisy. Roughly half are lyrics or
scare-quoted prose. Do not filter them heuristically — validation against
the real tracklist is what discards non-tracks, and clever filtering here
only drops real titles.

`log/` holds NDJSON data and is committed; the code that writes it is
`verdict/journal.py`. Tests are offline and fixture-driven — the files in
`tests/fixtures/` are real captured pages, and nothing in the suite
touches the network.

A filter that drops an item before it is fetched must expose *why*, so
the caller can log it. Discovery-time drops leave no row in
`unmatched.ndjson` otherwise, and an invisible false positive is the one
failure mode this project rules out everywhere else.

Prefer real captured fixtures over synthetic state blobs when testing a
parser. Synthetic data is where the bugs hide: it agreed with the code
about `dangerousHed` being plain text and about header casing, and both
were wrong in the real responses.
