# Verdict

Weekly job that reads album recommendations from music critics, extracts the
tracks named in each writeup, and maintains a rolling 4-week Spotify playlist.

The core unit is a **verdict**: one publication's opinion on one album. Source
adapters emit verdicts; everything downstream is source-agnostic.

Everything in the Pitchfork section below was verified against live responses on
2026-08-30. Do not re-derive it. Do not substitute remembered API shapes.

## Critical: Spotify API changed in February 2026

Training data is almost certainly pre-change. Verify against
https://developer.spotify.com/documentation/web-api before writing code.

| Old (dead) | New |
|---|---|
| `POST /playlists/{id}/tracks` | `POST /playlists/{id}/items` |
| `GET /playlists/{id}/tracks` | `GET /playlists/{id}/items` |
| `DELETE /playlists/{id}/tracks` | `DELETE /playlists/{id}/items` |
| `POST /users/{id}/playlists` | `POST /me/playlists` |
| `GET /artists/{id}/top-tracks` | removed, no replacement |
| `GET /albums` (several) | removed, fetch one at a time |

Playlist field `tracks` renamed to `items`; nested `tracks.tracks.track` →
`items.items.item`. Search `limit` caps at 10 (default 5). `popularity` removed
from track, album, and artist objects.

Do not use a client library unless you confirm it was updated after February 2026.
Raw HTTP is safer.

## Architecture

```
sources/       one adapter per publication, emits Verdict objects
resolve/       Verdict -> Spotify album -> validated track URIs
playlist/      rolling window management
log/           NDJSON history
```

`Verdict`: `source`, `artist`, `album`, `label`, `source_url`, `published_at`,
`score` (optional, source-native — do NOT normalize across publications),
`named_tracks` (list, often empty).

Two adapters planned. Build `pitchfork_roundup` first and completely; add
`pitchfork_bnm` second and let the second one force the shared interface.

## Adapter 1: pitchfork_roundup (primary)

Pitchfork's weekly "N New Albums You Should Listen to Now" roundup. ~13 albums per
week, human-curated, includes records that never receive a full review.

### Discovery

Feed: `https://pitchfork.com/feed/feed-news/rss` (confirmed 200, RSS 2.0).

Match items whose `<link>` contains `albums-you-should-listen-to-now`. Match on the
slug, NOT the title — the feed carries unrelated "Listen to ..." news posts that
match on title text, and the album count in the title varies week to week.

Take `pubDate` from the feed item; the article body has no reliable date.

### Parsing

Article pages are Condé Nast Verso. Extract the state blob:

```python
i = html.index('__PRELOADED_STATE__'); j = html.index('{', i)
data, _ = json.JSONDecoder().raw_decode(html[j:])
body = data['transformed']['article']['body']
```

Use `raw_decode`, not a regex. The blob is ~600KB and contains `</script>` inside
string values; a non-greedy regex truncates it.

`body` is a nested-list AST (not HTML): `[tag, *children]` where children are
strings, nested lists, or dicts of attributes. Flattener:

```python
def flat(x):
    if isinstance(x, str): return x
    if isinstance(x, list):
        return ''.join(flat(i) for i in x[1:] if not isinstance(i, dict))
    return ''
```

The `x[1:]` slice is required — index 0 is the tag name and including it produces
artifacts like `emThis Mirror Weighs a Ton`.

### Segmentation

`h2` blocks delimit albums. Everything from an `h2` until the next `h2` belongs to
that album. Discard `inline-embed`, `native-ad`, `cm-unit`, `hr`.

Header shape: `['h2', 'Interpol: ', ['em', 'This Mirror Weighs a Ton'], ' [Partisan]']`

- artist = leading string, strip trailing `': '`
- album = the `em` child
- label = bracketed trailing string (optional, log it)

This is fully structured. Do not parse artist out of URL slugs.

### Track candidates

Track names appear in curly quotes (U+201C / U+201D) in `p` blocks. Album titles
are italicized, never quoted — so `em` content is never a track candidate.

```python
re.findall(r'[\u201c]([^\u201d]{1,60})[\u201d]', text)
```

Straight-quote matching returns nothing. Condé Nast uses typographic quotes.

Candidates are NOISY — verified output includes song lyrics and descriptive
phrases alongside real track names. This is expected and fine: validation against
the real tracklist discards non-tracks. Strip trailing punctuation (Pitchfork puts
commas and periods inside the quotes) before matching.

Roughly 4 of 13 albums name no tracks at all. Fallback required.

## Adapter 2: pitchfork_bnm (secondary)

Best New Music, ~1-3 albums/week. Overlaps the roundup; dedup by URI handles it.

Discovery via `https://pitchfork.com/feed/feed-album-reviews/rss` (~28 items/week,
covers about a week). Feed has no artist field and no BNM flag, so each review page
must be fetched.

Per review page, from the same state blob:

- `transformed/review/multiReviewHeaderProps/itemsReviewed[]` — prefer this; it is
  uniformly a list and handles single- and multi-album reviews with one code path.
  Fall back to `transformed/review/headerProps/musicRating` only if absent.
- each item's `musicRating` has `isBestNewMusic`, `isBestNewReissue`, `score`
- `score` type is inconsistent (`9`, `8.6`, `8`) — coerce to float on read

Do NOT grep the blob for `isBestNewMusic` or `score`. Both keys appear many times
per page in recirculation modules for unrelated albums, and `score` in those
modules is a relevance float (e.g. `0.5373776035`), not a review score. Parse and
walk to the node.

Artist/album from `ld+json` `itemReviewed.name`, formatted `"Artist: Album"`.
Split on the FIRST `': '` only — album titles can contain colons.

Filter to `isBestNewMusic == True`. Do not threshold on score: verified that an 8.0
was not BNM while BNM records scored 8.6 and 9. BNM is editorial, not arithmetic.

Sunday Reviews (retrospectives on old albums) appear in this feed and must be
excluded; their `description` opens with boilerplate about revisiting a significant
album from the past.

Verified 2026-08-30 against the live feed (Paradis, *Recto Verso*, a 2016 record
reviewed 2026-08-23). Exactly 1 of 30 feed items was a Sunday Review, and its
description began "Each Sunday, Pitchfork takes an in-depth look at a significant
album from the past". Note "Each Sunday", not "Every Sunday".

There is no structured signal to use instead: Sunday Reviews report the same
`rubric.name` ("Albums") and the same `documentType` ("review") as ordinary
reviews. Match the description text, and revisit this if a rubric ever appears.

## Resolution

`GET /search?type=album&q=artist:X album:Y`, then `GET /albums/{id}/tracks`.

Keep only quoted candidates that fuzzy-match a real track name. Non-matches are
discarded, which makes false positives structurally hard.

If a verdict names no valid track, take the album's first track only. Do not take
the whole album — at ~13 albums/week over a 4-week window, full albums would mean
a 500-track playlist.

Expect resolution failures on reissues, deluxe editions, self-titled albums,
non-ASCII artist names, and releases absent from Spotify. Use a similarity
threshold; log near-misses rather than guessing.

## Rolling window

Playlist items carry `added_at`. Each run: read `GET /playlists/{id}/items`, remove
anything older than 28 days, append new tracks. No database — state lives in the
playlist.

Dedup is scoped, NOT permanent. Skip a URI only if it is currently in the playlist
or appears in the trailing 28 days of `log/additions.ndjson`. A record
re-recommended months later, or picked up by a second source, becomes eligible
again — `additions.ndjson` is a history, not a blocklist.

## Logs (NDJSON, append-only, one object per line)

```
log/additions.ndjson   source, track, artist, album, uri, source_url, score, run_date, match_confidence
log/removals.ndjson    uri, aged_out_date
log/unmatched.ndjson   source, artist, album, source_url, reason
```

NDJSON not a JSON array: appends stay pure, diffs stay clean, no parsing to write.
Volume is low enough that size never matters. `unmatched.ndjson` is the bug queue.

## Failure handling

`__PRELOADED_STATE__` is an internal Verso detail with no stability contract. If
the blob is missing or a key path is absent, write to `unmatched.ndjson` with
reason `state_shape_changed` and skip that item. Never crash the run, and never let
the playlist silently stop updating.

RSS and `ld+json` are the durable interfaces; the state blob is the fragile one.

Rate-limit politely. The feed reports 100 requests per window.

## Auth

Single-user. Owner authorizes once; playlist is public and friends follow the link.
Do NOT build multi-user OAuth — Spotify Dev Mode caps at 25 manually added Premium
users, and extended quota review routinely rejects hobby projects.

Authorization code flow WITH client secret, not PKCE. PKCE rotates the refresh
token per use, which would mean writing a new secret back to the repo each run.

Ship a local `bootstrap.py` for the one-time handshake that prints the refresh
token. Scopes: `playlist-modify-public`, `playlist-read-private`.

## Deployment

GitHub Actions, weekly cron, public repo, free tier.

Secrets: `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN`,
`SPOTIFY_PLAYLIST_ID`, plus a fine-grained PAT for log commits.

Commit logs with the PAT, not `GITHUB_TOKEN`. Scheduled workflows auto-disable
after 60 days of repo inactivity, and whether bot commits reset that timer is
undocumented and has changed before. A PAT push is unambiguously user activity.

## Non-goals

Per-user playlists. Genre filtering. Normalizing scores across publications.
Anything using audio features or popularity, which no longer exist.

## Later

Feed `media:thumbnail` gives album art up to 3600px. Composite a collage and push
via `PUT /playlists/{id}/images`.
