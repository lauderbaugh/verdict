# Second source candidates

Investigated 2026-08-31. No adapter code written. Fixtures in
`tests/fixtures/{paste,stereogum,npr}_*`.

All three are **alive**, unlike AV Club. Recency was checked by paginating and
confirming current-year content; feeds were judged on item counts rather than
status codes. Both traps from the AV Club pass.

## Summary

| | Cadence | Overlap with Pitchfork | Adds | Albums/wk | Tracks/wk at 2-4 |
|---|---|---|---|---|---|
| **Paste** reviews | 22-28/mo (~5-7/wk) | **0 of 13** | ~5-7 | 5-7 | 10-28 |
| **Stereogum** Album Of The Week | exactly 1/wk | n/a (single pick) | 1 | 1 | **2-4** |
| **Stereogum** weekly album list | 126/wk | **12 of 13 (92%)** | ~114 | 126 | 250-500 |
| **Stereogum** 5 Best Songs | 5 songs/wk | n/a (tracks) | 5 tracks | — | 5 |
| **NPR** New Music Friday | 10/wk (5+5) | **7 of 13 (54%)** | 3 | 10 | 20-40 |
| NPR, Starting 5 only | 5/wk | 5 of 13 | ~0-1 | 5 | 10-20 |

Overlap measured against the captured Pitchfork roundup for the week of
2026-08-28.

## 1. Paste

**Alive, and the highest-volume reviewer.** Newest review 2026-08-30, the day
before this investigation. 22-28/month across 2026-04 to 2026-08, index
paginated to `?page=61`, reverse-chronological confirmed.

**Stack is identical to AV Club** — `wp-theme-pastemagazine`, same WordPress
theme, same `img.pastemagazine.com` asset host. Paste owns AV Club, and the AV
Club fixtures came from this same theme.

Carries over from the AV Club investigation:

- `<h1>` italicises the album (`<em>` or `<i>` interchangeably), same convention
  as Pitchfork.
- Tracks named in **curly quotes**, so `verdict/sources/prose.py` works unchanged.
- Yoast `ld+json` is useless — `WebPage`, `BreadcrumbList`, `WebSite`, no
  `Review` node.
- Section feeds soft-404 the same way.

What differs, and it matters:

- Paste has **no breadcrumb artist**. AV Club's `Music / Reviews / <Artist>`
  block is absent. The **URL carries the artist slug** instead:
  `/music/ty-segall/ty-segall-chrome-review`, present on 108 of 108 entries.
- The **index is rich enough to skip most page fetches**: each row has the URL,
  the title with the album in `<em>`, byline and date. 96 of 108 rows carry an
  italicised album, so ~89% of verdicts could be built from the index alone,
  fetching pages only for the rest and for track candidates.

No score of any kind. `score` would be `None`. No Best New Music equivalent.

Track candidates run 6-10 per review, cleaner than AV Club's 24-43.

**Coverage vs agreement:** zero overlap with the Pitchfork roundup that week.
Paste covered KATSEYE, Ty Segall, Saul Williams, Lambchop against a disjoint
Pitchfork 13. Across Paste's whole 5-month index page, **none** of the Pitchfork
13 artists appear. Aldous Harding does — the Pitchfork Best New Music fixture —
so overlap exists but is rare and skews to higher-profile records.

Paste is a **coverage** source, not an agreement source.

## 2. Stereogum

**Next.js, not WordPress-rendered.** `__NEXT_DATA__` carries a structured post
object, closer to Verso than to Paste. Feeds are well-formed, no undeclared
namespace.

Stereogum offers three distinct things, worth separating.

### Album Of The Week — the genuine editorial pick

`/category/franchises/album-of-the-week/feed/`, 40 items, **exactly weekly**
without a gap from 2025-11-11 to 2026-08-25.

The closest analogue to Best New Music found anywhere: one named pick per week,
chosen editorially, with no score to threshold on. Satisfies the SPEC constraint
that selection be editorial rather than arithmetic.

Artist and album separate cleanly from the structured title:

```
Album Of The Week: Maripool <em>Rotten Luck</em>
```

Prefix, artist, italicised album — the italics convention again. The RSS title
strips the markup (`Album Of The Week: Maripool Rotten Luck`, no separator at
all), so the page's `__NEXT_DATA__` is needed rather than the feed alone.

The site has a paywall (`paywallSettings`, "This column is only available to
subscribers"), but the AOTW body was fully present in `contentBlocks` — 13
blocks, untruncated. Worth re-checking before building, since a metered paywall
may behave differently from a datacentre IP.

**Volume: 1 album/week, 2-4 tracks.** The cheapest possible addition.

### The weekly album list — an agreement signal, not a source

Each Album Of The Week post contains a block titled *"Other albums of note out
this week"*: **126 entries**, one week's releases, formatted `Artist's Album`.

It overlaps the Pitchfork roundup at **12 of 13 (92%)**. The only miss is Liim.

At 126 albums/week this is far too large to treat as a source — 250-500 tracks a
week on its own. But that is the wrong use. As a **corroboration signal** it is
close to ideal: one cheap fetch per week answers "did Stereogum also flag this
album?" for almost every Pitchfork pick, adding no tracks at all.

This is the most useful finding of the pass, and what makes the
consensus-weighted design in SPEC.md practical.

### The 5 Best Songs Of The Week

`/category/franchises/the-5-best-songs-of-the-week/feed/`, 40 items, every
Friday, newest 2026-08-28.

Names **tracks directly**, which would skip prose extraction — but less cleanly
than hoped. YouTube embed titles are inconsistent (`Sour Widows - Lucky Star
(Official Video)`, `Classic Traffic - "My Rage" (Official Music Video)`, and one
that is just `Swarm` with no artist). The reliable extraction is the prose
paragraph per song, which curly-quotes the track and names the artist.

It is also a different shape from the rest of the pipeline: `Verdict` is artist +
album, these are songs. Using it means resolving track → album or widening the
model. Noted, not recommended now.

## 3. NPR Music

**Structurally the strongest, and the only one needing no page fetch at all.**

`/sections/music/` now 404s, and the topic feed (`feeds.npr.org/1039/rss.xml`,
10 items) is general music journalism, not reviews.

The useful feed is the **NPR Music podcast feed**,
`feeds.npr.org/510019/podcast.xml` — 300 items, of which **80 are New Music
Friday**, weekly without gaps, newest 2026-08-28.

Show notes parse straight out of the RSS `description`:

```
The Starting 5
(03:37) Album No. 1 - Mastodon, 'Marrow Deep'
(08:56) Album No. 2 - Mike D 5D, 'Thank You'
...
The Lightning Round
- Billy Strings, 'So Much For Goodbyes'
- Elephant Stone, 'ASHA'
```

Format is `Artist, 'Album'` — comma-separated, album single-quoted. Unambiguous,
unlike Stereogum's title or AV Club's prose headlines. **No page fetch, no HTML
parsing, no state blob**, so no fragile dependency of the kind `verso.py` exists
to quarantine.

**It has a built-in editorial tier**: "The Starting 5" versus "The Lightning
Round". Editorial, not a threshold — the same kind of signal as Best New Music,
and it maps naturally onto how many tracks an album deserves.

**NPR does both coverage and agreement.** 7 of 13 overlap with Pitchfork (54%),
and it adds 3 the roundup missed (Elephant Stone *ASHA*, Tiny Habits *Keepers*,
Saul Williams *Leap Life*). It also caught **Liim**, the one album Stereogum's
126-entry list missed.

No track names — it is an audio show. Track candidates would be empty, so every
NPR-only album falls to the Last.fm/positional chain. NPR adds albums cheaply but
contributes no named tracks.

**Volume: 10/week for the full list, 5/week for the Starting 5 alone.**

## Recommendation

Not building anything this pass. If asked to rank:

1. **NPR New Music Friday** — best structure by a distance, no HTML parsing, a
   real editorial tier, and it both overlaps (7/13) and adds (3). The trade is no
   named tracks.
2. **Stereogum Album Of The Week** — the only true Best New Music analogue found,
   1 album/week for 2-4 tracks. Its weekly list is separately valuable as a 92%
   corroboration signal costing no playlist space.
3. **Paste** — highest volume and best pure coverage, and much of the parsing is
   already understood from the AV Club fixtures. But 0/13 overlap contributes
   nothing to agreement, and 10-28 tracks/week is the largest volume increase.

Playlist impact, for the cap decision:

- Pitchfork today: ~13 albums/wk, roughly 26-40 tracks over a 4-week window of
  ~104-160.
- NPR Starting 5: +5 albums/wk, ~4 already overlapping, so perhaps +2-8
  tracks/wk net.
- Stereogum AOTW: +1 album/wk, +2-4 tracks.
- Paste: +5-7 albums/wk, almost none overlapping, so +10-28 tracks/wk. This is
  the one that would meaningfully lengthen the playlist.

## Fixtures captured

| File | Contents |
|---|---|
| `paste_reviews_index.html` | 108 dated entries, Apr-Aug 2026 |
| `paste_review.html` | Ty Segall *Chrome*, h1 italics, quoted tracks |
| `stereogum_aotw_feed.xml` | 40 weekly picks, 2025-11 to 2026-08 |
| `stereogum_aotw.html` | `__NEXT_DATA__`, structured title, the 126-album list |
| `stereogum_5best_feed.xml` | 40 weekly song posts |
| `stereogum_5best.html` | embed titles and per-song prose |
| `npr_music_podcast_feed.xml` | 300 items, 80 New Music Friday |
| `npr_music_topics_feed.xml` | the general music topic feed, for contrast |
