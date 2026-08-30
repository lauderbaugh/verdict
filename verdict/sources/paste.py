"""Paste album reviews.

Same WordPress theme as AV Club -- `wp-theme-pastemagazine`, since Paste
owns it -- so the conventions carry over: the album is italicised in the
`<h1>`, tracks are named in curly quotes, and the `ld+json` is generic
Yoast output with no `Review` node worth reading.

Discovery is an HTML index rather than a feed. Paste has no working
section feed, and the index is richer than one would be: it carries the
article URL, the title with the album italicised, the byline and the
date, all without a page fetch.
"""

from __future__ import annotations

import html as html_module
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from verdict.feed import FeedItem
from verdict.models import Verdict
from verdict.sources.base import Candidate, DiscoveryResult, ParseResult, Problem
from verdict.sources.prose import track_candidates
from verdict.verso import strip_html

NAME = "paste"

#: The reviews index. Paginated `?page=1..61`, reverse-chronological.
INDEX_URL = "https://www.pastemagazine.com/articles/music/reviews"

#: Paste publishes 22-28 reviews a month and the index holds 108 entries
#: -- about four months. Unbounded discovery would reprocess the archive
#: every run, the same trap NPR's feed set. Two weeks covers the weekly
#: cadence plus one missed run.
WITHIN_DAYS = 14

#: One index row: URL, title with the album italicised, byline, date.
_ROW = re.compile(
    r'<a class="auto cell copy-container[^"]*" href="([^"]+)">\s*'
    r'<b class="title">(.*?)</b>\s*'
    r'<b class="byline">(.*?)</b>\s*'
    r'<b class="time">(.*?)</b>',
    re.DOTALL,
)

_DATE = re.compile(
    r"((?:January|February|March|April|May|June|July|August|September"
    r"|October|November|December)\s+\d{1,2},\s+20\d\d)"
)

#: The album, italicised in the headline. `<em>` and `<i>` are used
#: interchangeably, exactly as on AV Club and Pitchfork.
_ITALIC = re.compile(r"<(?:em|i)>(.*?)</(?:em|i)>", re.DOTALL)

_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL)

#: Paste kept its letter grade, unlike AV Club, which dropped grades
#: entirely after the same company acquired it. Page only -- the index
#: does not carry it.
_RATING = re.compile(r'<div class="rating">\s*([A-F][+-]?)\s*</div>', re.IGNORECASE)

#: `/music/<artist-slug>/<article-slug>`. The slug is the *release*
#: credit rather than a canonical artist, which is what Spotify wants:
#: an AV Club retrospective on Smog carried a breadcrumb reading "Bill
#: Callahan", and searching Spotify for that would miss the record.
_ARTIST_SLUG = re.compile(r"/music/([a-z0-9][a-z0-9-]*)/")


def _artist_from(url: str, headline: str) -> Optional[str]:
    """The artist, from the URL slug, cased from the headline where possible.

    The slug is authoritative for *which* artist; it is just lowercased
    and hyphenated. Where the headline contains the same name, its
    casing is borrowed so the logs read correctly -- "KATSEYE" rather
    than "Katseye". Resolution does not care either way, since matching
    casefolds.
    """
    match = _ARTIST_SLUG.search(url)
    if not match:
        return None
    words = match.group(1).replace("-", " ").strip()
    if not words:
        return None
    spelled = re.search(re.escape(words).replace(r"\ ", r"[\s'’.-]+"), headline, re.I)
    return spelled.group(0) if spelled else words.title()


def _published(text: str) -> Optional[datetime]:
    match = _DATE.search(text or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def index_rows(page: str) -> List[FeedItem]:
    """Every dated review on one index page."""
    rows = []
    for url, title, _byline, when in _ROW.findall(page or ""):
        published = _published(html_module.unescape(strip_html(when)))
        rows.append(
            FeedItem(
                link=url.strip(),
                # Kept as markup: the italics are what identify the album.
                title=re.sub(r"\s+", " ", title).strip(),
                published_at=published,
            )
        )
    return rows


def discover(fetch, now: Optional[datetime] = None) -> DiscoveryResult:
    """Reviews from the index, bounded to the recent window.

    Every candidate needs its page. The index gives artist and album for
    ~89% of rows, but not the letter grade and not the prose, and the
    prose is where the named tracks are -- which is the whole reason
    this source is worth more than a listing.
    """
    try:
        page = fetch(INDEX_URL)
    except Exception as exc:  # noqa: BLE001 - a dead index must not end the run
        return DiscoveryResult(
            problems=(Problem(reason=f"index_unavailable: {exc}", source_url=INDEX_URL),)
        )

    rows = index_rows(page)
    if not rows:
        # A 200 with no rows means the index markup moved, not a quiet week.
        return DiscoveryResult(
            problems=(Problem(reason="index_shape_changed", source_url=INDEX_URL),)
        )

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=WITHIN_DAYS)
    candidates = tuple(
        Candidate(item=row, needs_page=True)
        for row in rows
        # An undated row is kept: dropping on missing data loses a real
        # review, and dedup stops it being added twice.
        if row.published_at is None or row.published_at >= cutoff
    )
    return DiscoveryResult(candidates=candidates)


def parse(candidate: Candidate, page: Optional[str]) -> ParseResult:
    """Turn one review into a Verdict.

    Falls back to the index headline for the album when the page's own
    `<h1>` cannot be read, since discovery already captured it.
    """
    item = candidate.item

    # The page's own headline first, the index row second. Discovery
    # already captured the index title, and the two disagree often
    # enough to be worth trying both: a page whose h1 drops the italics
    # is still recoverable from the row that pointed at it.
    sources = []
    if page:
        match = _H1.search(page)
        if match:
            sources.append(match.group(1))
    if item.title:
        sources.append(item.title)

    album, headline_markup = None, (sources[0] if sources else "")
    for markup in sources:
        italics = _ITALIC.search(markup)
        if italics:
            album = strip_html(italics.group(1)) or None
            if album:
                headline_markup = markup
                break

    headline = strip_html(headline_markup)
    artist = _artist_from(item.link, headline)

    if not artist or not album:
        # ~11% of index rows name the album without italicising it, and
        # the page does not always italicise either. Nothing to guess at
        # safely, so it goes to the bug queue.
        return ParseResult(
            problems=(
                Problem(
                    reason="artist_album_unparsed",
                    source_url=item.link,
                    artist=artist,
                    album=album,
                ),
            )
        )

    grade = None
    if page:
        rating = _RATING.search(page)
        if rating:
            # Source-native and never normalized: a Paste "B-" stays "B-".
            grade = rating.group(1).upper()

    return ParseResult(
        verdicts=(
            Verdict(
                source=NAME,
                artist=artist,
                album=album,
                source_url=item.link,
                published_at=item.published_at,
                label=None,
                score=grade,
                named_tracks=track_candidates_from(page),
                # No "best of" or recommended distinction exists on a
                # Paste review; every review is just a review.
                editorial_tier=None,
            ),
        )
    )


def track_candidates_from(page: Optional[str]):
    """Curly-quoted track candidates from the review body.

    The same house convention Pitchfork uses, so `prose.py` needs no
    changes. Paste italicises album titles rather than quoting them, so
    `em` content is never a candidate here either.
    """
    if not page:
        return ()
    body = re.search(
        r'<article[^>]*id="article-detail-container".*?</article>', page, re.DOTALL
    )
    text = body.group(0) if body else page
    paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", text, re.DOTALL)
    return track_candidates([["p", strip_html(p)] for p in paragraphs])
