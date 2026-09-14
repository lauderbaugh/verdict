"""Bandcamp Daily's Album of the Day.

The first source here that is neither a weekly roundup nor a rated
review. Album of the Day is exactly one editorial pick per weekday, so
there is no tier to read and no score to record -- being chosen *is* the
verdict.

Discovery is the site-wide feed, which carries every section: Lists,
Features, Label Profile, the monthly "Best Jazz" and "Best Ambient"
columns, and Album of the Day. Only Album of the Day is taken. The
monthly genre columns look tempting -- they are the closest thing to
jazz and classical coverage this pipeline can reach -- but each one
covers a dozen records in a single article with no per-album structure,
which is a different parsing problem and a different question about what
"recommended" means. Left alone for now.

The prose follows the same house convention as Pitchfork and Paste:
track names in curly quotes, album titles italicised. `prose.py` needs
no changes.

One thing deliberately not read: every Album of the Day page embeds a
Bandcamp player carrying the album's *complete* tracklist as escaped
JSON. It would be easy to lift and it would be wrong. A full tracklist
is a manifest, not a recommendation -- the same reason Stereogum's
release list is not a source (SPEC: "Agreement must be editorial, not
mere presence"). Only the tracks the writer actually named in prose
count as named.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from verdict.models import Verdict
from verdict.sources.base import (
    Candidate,
    DiscoveryResult,
    ParseResult,
    Problem,
    discover_from_feed,
)
from verdict.sources.prose import track_candidates
from verdict.verso import ld_json_docs, strip_html

NAME = "bandcamp_daily"

#: The site-wide feed. There is no Album of the Day-only feed; the
#: section is filtered out of this one.
FEED_URL = "https://daily.bandcamp.com/feed"

#: Album of the Day lives under its own path segment. The feed also
#: tags each item with a `<category>`, and the two agreed on all 36
#: captured items -- but `feed.py` does not read categories and the URL
#: is the same signal without a schema change.
#:
#: Matched as a full prefix rather than a bare path segment, so the
#: filter also pins the host. Discovery decides what gets fetched, and a
#: substring test would have let `https://elsewhere.example/
#: album-of-the-day/x` through if the feed ever carried it.
SECTION = "https://daily.bandcamp.com/album-of-the-day/"

#: The feed holds 36 items spanning about two weeks, so it is nearly
#: self-bounding. The window is kept anyway for the same reason NPR has
#: one: a source must not reprocess whatever the publisher decides to
#: leave in the feed. Two weeks covers the weekly cadence plus one
#: missed run.
WITHIN_DAYS = 14

#: `Artist, "Album"` -- the same shape NPR's show notes use, with
#: typographic double quotes instead of single ones.
#:
#: The artist part is non-greedy but the comma is not the anchor: the
#: quote is. Three of twelve captured headlines name collaborations that
#: contain their own commas ("Henry Threadgill, Vijay Iyer & Dafnis
#: Prieto"), and one album title does ("Happier Together, Filthier Than
#: Ever: The Best Of"). Anchoring on the quotes gets both right;
#: splitting on the first or last comma gets one of them wrong.
#: The artist is length-bounded for the same reason NPR's entry pattern
#: is: a lazy `.+?` in front of a literal makes a non-matching headline
#: quadratic in its length, and no real credit runs to 120 characters.
_HEADLINE = re.compile(r'^(?P<artist>.{1,120}?)\s*,\s*[“"](?P<album>.{1,200})[”"]\s*$')

#: The article body, from the opening `<article>` to the `<article-end>`
#: marker Bandcamp puts before the footer. Scoped rather than taking
#: every `<p>` on the page: the captured page happens to contain exactly
#: four, all of them prose, and that is luck rather than a guarantee.
_BODY = re.compile(r"<article\b.*?<article-end\b", re.DOTALL)

_PARAGRAPH = re.compile(r"<p\b[^>]*>(.*?)</p>", re.DOTALL)


def _is_album_of_the_day(item) -> bool:
    return (item.link or "").startswith(SECTION)


def discover(fetch, now: Optional[datetime] = None) -> DiscoveryResult:
    """Album of the Day items from the feed, bounded to the recent window.

    Every candidate needs its page. The feed's own `description` is a
    one-line teaser -- no tracks, no label, not even the album on its
    own -- so unlike NPR there is nothing to parse without fetching.
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=WITHIN_DAYS)

    def select(item) -> bool:
        if not _is_album_of_the_day(item):
            return False
        # An undated item is kept rather than dropped: losing a real
        # pick is worse than reprocessing one, and dedup catches the
        # repeat.
        return item.published_at is None or item.published_at >= cutoff

    return discover_from_feed(fetch, FEED_URL, select=select, needs_page=True)


def _article(page: str) -> Optional[dict]:
    """The `ld+json` Article node, if the page carries one."""
    for doc in ld_json_docs(page):
        if doc.get("@type") == "Article":
            return doc
    return None


def _genre(doc: dict) -> Optional[str]:
    """The single genre Bandcamp files the album under.

    `keywords` is the display form ("Soundtrack"); `genre` is a URL
    ending in the same slug. Preferring the former keeps the value
    readable in the log, and the latter is a fallback rather than a
    parse of the URL's shape.
    """
    keywords = doc.get("keywords")
    if isinstance(keywords, str) and keywords.strip():
        return keywords.split(",")[0].strip()
    url = doc.get("genre")
    if isinstance(url, str) and "/" in url:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return slug.replace("-", " ").title() or None
    return None


def track_candidates_from(page: Optional[str]):
    """Curly-quoted track candidates from the review body."""
    if not page:
        return ()
    body = _BODY.search(page)
    text = body.group(0) if body else page
    paragraphs = _PARAGRAPH.findall(text)
    # Track names are frequently wrapped in a link to the track on
    # Bandcamp -- `“<a href="...">Nuits Sonores</a>”` -- so the markup
    # comes out before the quotes are matched, not after.
    return track_candidates([["p", strip_html(p)] for p in paragraphs])


def parse(candidate: Candidate, page: Optional[str]) -> ParseResult:
    """Turn one Album of the Day into a Verdict."""
    item = candidate.item
    doc = _article(page or "") or {}

    # The `ld+json` headline first, the feed title second. They matched
    # on every captured item; both are kept because the feed title costs
    # nothing and covers a page whose `ld+json` has moved.
    headline = ""
    for value in (doc.get("headline"), item.title):
        if isinstance(value, str) and value.strip():
            headline = strip_html(value)
            break

    match = _HEADLINE.match(headline)
    if not match:
        # Nothing safe to guess at: without the quotes there is no
        # telling where a collaboration credit ends and a title begins.
        return ParseResult(
            problems=(
                Problem(reason="headline_unparsed", source_url=item.link),
            )
        )

    return ParseResult(
        verdicts=(
            Verdict(
                source=NAME,
                artist=match.group("artist").strip(),
                album=match.group("album").strip(),
                source_url=item.link,
                published_at=item.published_at,
                # Not published. The page names the label only inside
                # the embedded player's JSON, which is not read here.
                label=None,
                # Album of the Day carries no score, and inventing one
                # to stand for "chosen" would be exactly the
                # normalisation SPEC rules out.
                score=None,
                named_tracks=track_candidates_from(page),
                # One pick per weekday and no ranking within it. The
                # section name is the tier.
                editorial_tier="album_of_the_day",
                genre=_genre(doc),
            ),
        )
    )
