# Genre sources: world, jazz, classical

Investigated 2026-09-02. No adapter code. Fixtures in `tests/fixtures/bandcamp_*`.

## The short answer

**One viable candidate: Bandcamp Daily.** It covers jazz and experimental
well, electronic and hip-hop incidentally, and classical and world barely.

The dedicated genre press is largely unreachable. Of nine specialist outlets
checked, four block this project at the Cloudflare edge *even from a
residential IP*, two are JavaScript-rendered with nothing in the HTML, two
publish no feed or review index, and one has a working feed carrying the wrong
kind of content.

So the honest position on the original request: **jazz is partly served, world
is not served at all**, and classical is reachable but with a caveat about what
a "track" means on a classical record — see the correction below.

## What was checked

| Outlet | Genre | Result |
|---|---|---|
| **Bandcamp Daily** | broad | **working feed, 36 items/15 days** |
| All About Jazz | jazz | Cloudflare 403 |
| Jazzwise | jazz | Cloudflare 403 |
| Gramophone | classical | Cloudflare 403 |
| Presto Music | classical | Cloudflare 403 |
| DownBeat | jazz | JS-rendered index, no links in HTML |
| classical-music.com (BBC) | classical | **readable — see the correction below** |
| Songlines | world | no feed, `/reviews` 404 |
| Afropop | world | no feed, `/reviews` 404 |
| The Wire | experimental | feed works, but features and mixes, not reviews |

### The Cloudflare wall is stricter here than Paste

Four outlets return Cloudflare's "Attention Required!" challenge page to the
`verdict/0.1` User-Agent **from a home connection**. Paste at least serves a
laptop; these do not serve us at all.

That is worth stating plainly because it changes what an allowlist request would
mean. With Paste we are asking to be let through from a datacentre. With these
we would be asking to be let through at all, which is a larger ask of outlets
that have evidently chosen to refuse unknown clients.

### JS-rendered is a hard stop for this project

DownBeat's `/reviews` is a month dropdown; all three archive URL shapes return
the same 71KB page. classical-music.com returns 1.4MB containing no
`wp-content`, no state blob, no `ld+json`, no dates and almost no links.

Both would need a headless browser. That is a different class of dependency
from anything here — the whole pipeline is standard-library HTTP — and it is
not worth taking on for one source.

## Bandcamp Daily

**Feed**: `https://daily.bandcamp.com/feed`, 36 items covering 2026-08-17 to
2026-09-01. Served by nginx, **not Cloudflare**, so the datacentre block that
stops Paste should not apply. Worth confirming from a runner before building.

**Album of the Day is a genuine daily editorial pick.** 12 of the 36 items,
weekdays, one album each. Titles parse cleanly:

```
Floating Points, “Mere Mortals”
Henry Threadgill, Vijay Iyer & Dafnis Prieto, “Fifteen”
L’Rain, “fata morgana”
```

12 of 12 parsed with a single `Artist, “Album”` pattern — the same shape as
NPR's show notes, and unusually clean for a headline.

**The page carries structured metadata**, better than anything except NPR:

```json
{
  "headline":       "Floating Points, “Mere Mortals”",
  "articleSection": "Album of the Day",
  "genre":          "https://daily.bandcamp.com/genres/soundtrack",
  "datePublished":  "2026-09-01T13:44:42Z"
}
```

`articleSection` is the editorial tier, structurally. `genre` is a per-album
genre URL, which no existing source provides and which would make
genre-balancing possible later.

**Tracks are named in curly quotes**, so `prose.py` works unchanged. Eight
candidates on the sampled review, including real titles ("Opening of the Jar",
"Movement 2 – Hope") alongside the album name repeated — the usual noise
profile.

**Other columns**: `best-jazz` and `best-experimental` exist as monthly
roundups. There is no `best-world` and no classical column; both 404.

### Coverage against agreement

Of the 12 Album of the Day picks, **1 overlaps** what our sources already put on
the playlist (Ok Cowgirl, *Rhinestone Cowgirl*). The other 11 are new:

```
Floating Points — Mere Mortals
Henry Threadgill, Vijay Iyer & Dafnis Prieto — Fifteen
Tyondai Braxton — Splayed Werks
Lusine — Melting Days
Marci — Mask Lady and Late Night Girl
...
```

So it is a **coverage source, not an agreement source** — the same shape as
Paste. But unlike Paste it reaches records the existing four would never see:
the Threadgill/Iyer trio is exactly the jazz the current sources miss.

**Volume**: ~5 albums/week at 2-4 tracks = **10-20 tracks/week**, almost none
overlapping. That is the largest single increase any candidate would add, and
it lands on a playlist already carrying four sources.

## Recommendation

Bandcamp Daily is worth building **if** the goal is breadth. It is well
structured, cheap to parse, on infrastructure that should not block a runner,
and it reaches genres the current four do not.

Two reasons to wait:

1. **It does not answer the original question.** World and classical remain
   unserved, and nothing found here changes that. If the goal was those two
   specifically, this is a consolation prize.
2. **Volume.** Four sources already produce 25-30 albums a week. Adding 5 more
   with near-zero overlap makes the cap question urgent rather than theoretical.

If world and classical matter more than breadth, the realistic routes are an
allowlist request to Songlines or Gramophone, or accepting that the specialist
press is not machine-readable and leaving those genres out.


## Correction, 2026-09-03

Alex sent four URLs after reading the above. Two of them showed I was wrong.

### classical-music.com is readable. I looked for the wrong thing.

I reported "no dates or links". Both claims were false, and both for the same
reason: I searched with patterns that assumed absolute URLs and schema.org
dates.

- **Links.** The index carries 28 unique review URLs, written *relative without
  a leading slash* — `href="reviews/chamber/review-adorations-isidore-quartet"`.
  My regex was anchored on `/reviews/`, so it matched none of them and I
  concluded the page was JS-rendered.
- **Dates.** Alex was right that they are there and hidden. Every review page
  carries `<time datetime="2026-08-04T13:32:39.000Z">`, and a matching
  `<meta property="article:modified_time">`. There is no `datePublished` in
  `ld+json`, which is the only thing I checked.

`robots.txt` disallows `GPTBot` outright but allows `*` everywhere except
`/search`, previews and an Apple News path. Fetching `/reviews/` is permitted.

**What still argues against building it.** Sampling six reviews gave dates
spread across March to August 2026 — the index is a curated grid, not a
reverse-chronological listing, so a 14-day window would yield perhaps two to
four albums a fortnight from 28 fetches. And the deeper problem is the
selection chain: a classical release is movements, and the fallback rungs would
pick movements 2 and 4 of a symphony out of order. That is a real design
question, not a parsing one, and it should be answered before an adapter exists
rather than after.

### Jazzwise and Elsewhere are both behind Cloudflare

- `jazzwise.com/pages/album-reviews` — 403, "Attention Required! | Cloudflare".
  Same result as the section index. Confirms rather than changes the finding.
- `elsewhere.co.nz/music/` — 403, "Just a moment...". This is the interstitial
  JS challenge rather than a flat block, which is a slightly softer refusal, but
  it needs a browser to clear and there is no feed behind it either.

### musicreviewworld.com is not a critic source

`/music/` returns 200 and 70 article links, but:

- `/feed/` and `/music/feed/` both return the site's HTML, not RSS — a soft-404
  of exactly the kind worth checking for.
- The index carries no dates at all. Dates exist only on the article pages.
- The listing mixes single reviews and artist biographies with SEO listicles —
  "best AI singing voice generators", "best AI tools for social media" — sitting
  directly alongside the music writing.

That last point is the disqualifying one. This project's premise is that a named
human made a judgement worth acting on, and a listing that treats album reviews
and affiliate-driven AI tool roundups as the same kind of content does not
support that premise. Not recommended.

## Spotify playlists as a source — closed, 2026-09-03

Asked whether well-reviewed Spotify playlists could stand in for the outlets we
cannot read. Probed against the project's own credentials rather than reasoned
about:

| What | Result |
|---|---|
| Spotify-owned editorial (`State of Jazz`, `African Heat`) | **404** — not available to this app |
| Someone else's public playlist, metadata | readable: name, owner, followers, `snapshot_id` |
| Someone else's public playlist, **`/items`** | **403 Forbidden** |
| Our own playlist, same endpoint | 65 items, fine |

`public: true` makes no difference. We can see that a playlist exists and how
many people follow it, and nothing about what is on it.

Spotify's embed pages do render playlist contents server-side, so a workaround
exists. Not taken: it routes around an access control Spotify deliberately
imposed, using the credentials the entire pipeline depends on. Losing the app
costs Verdict, not just this feature.

Worth recording what a search *does* return, since the question was partly how
to find these at all. Not the publications — fans transcribing them:

```
Reviewed in The Wire Magazine 511 | September 2026   by a private user, 81 followers
Songlines Top of the World 1999-2024 [2250]          by a private user
Jazzwise's 100 Jazz Albums That Shook The World      by a private user
```

Zero Spotify-owned playlists surfaced, which is its own confirmation. And even
with the 403 lifted, one reader's transcription of a magazine is not the
magazine: Verdict's premise is a named critic's judgement, and a second-hand
copy cannot be checked against the original.

## Bandcamp Daily's genre columns — the actual answer

The gap was one section over from the source already built. Bandcamp Daily runs
monthly genre columns beside Album of the Day, structured far more rigidly than
the prose the pipeline already handles:

```html
<h3>Henry Threadgill's Zooid<br><a href="..."><em>Cut You Where You Was</em></a></h3>
```

Recency, checked 2026-09-03:

| Column | Latest | Verdict |
|---|---|---|
| `best-jazz` | August 2026 | current and monthly — **built** |
| `best-field-recordings` | August 2026 | current |
| `best-contemporary-classical` | July 2026 | alive but intermittent |
| `best-experimental` | May 2026 | lapsing |
| `best-folk` | November 2023 | dead |

### What the genre field measured

A census over the live Album of the Day window, nine albums: 3 Alternative,
1 each of Electronic, Pop, Jazz, Ambient, Rock, Experimental. **Zero World, zero
Classical.** So Album of the Day alone does not close the gap — measured rather
than assumed, which is what the field was added for.

World remains genuinely unserved. Bandcamp carries a `world` genre tag but runs
no column behind it, and `best-folk`, the nearest neighbour, died in 2023.

## Outcome

`bandcamp_daily` and `bandcamp_best_jazz` were built. `best-contemporary-classical`
is the obvious next one and has identical markup, but is blocked on the same
question as classical-music.com: a classical record is movements, and the
fallback rungs would pick movements 2 and 4 of a symphony, out of order, and
call that a recommendation. classical-music.com is left open for the same
reason. Spotify playlists and the rest are closed.
