"""Bandcamp Daily's monthly "Best Jazz on Bandcamp" column.

The HTML twin of `pitchfork_roundup`: one article, a dozen albums, an
`h3` per album delimiting the prose that belongs to it. The roundup
segments a Verso AST and this segments markup, but the shape of the
problem -- and of the header, `Artist` then the album italicised -- is
the same one, which is why the header parsing here reads like that one.

**Why this is a source and Stereogum is not.** The distinction SPEC
draws is selection versus enumeration. Stereogum's weekly list names
everything that came out; being on it proves a record exists. This
column names twelve records out of a month of jazz releases, and a
writer chose those twelve. That is the same act as the Pitchfork
roundup, and it is editorial.

The same rule cuts the other way inside the article. Most months close
with an "Other Albums of Note" list -- records mentioned in passing
rather than given their own entry. Those are explicitly the ones that
did not make the cut, so they are dropped, and the heading terminates
the album list. Its level is not stable: an `h2` in the August and July
2026 articles, an `h3` in June and May, which is why it is matched on
its text rather than its tag.

Named tracks are sparse here -- four of twelve August entries quoted a
title -- so most albums from this source reach the fallback chain. That
is fine for jazz, where the positional rungs pick discrete tunes. It
would not be fine for the classical column, whose records are movements.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

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

NAME = "bandcamp_best_jazz"

#: Shared with `bandcamp_daily`: one site-wide feed carrying every
#: section. This adapter takes a different one.
FEED_URL = "https://daily.bandcamp.com/feed"

#: Matched as a full prefix rather than a path substring, so the filter
#: also pins the host. Discovery decides what gets fetched.
SECTION = "https://daily.bandcamp.com/best-jazz/"

#: The column is monthly, published on the 1st for the month just gone.
#: Two weeks covers it landing in the next weekly run plus one missed
#: run. It will be rediscovered on the following run too; dedup is what
#: stops the second one adding anything.
WITHIN_DAYS = 14

#: The article body. `<article-end>` is Bandcamp's own marker for where
#: the prose stops and the footer begins.
_BODY = re.compile(r"<article\b.*?<article-end\b", re.DOTALL)

#: One album's heading. Split on, not matched -- the prose that follows
#: belongs to the heading before it.
_ENTRY = re.compile(r"<h3[^>]*>(.*?)</h3>", re.DOTALL)

#: Any heading that could close the album list. Both the level and the
#: inner markup vary: `<h2>Other Albums of Note:</h2>` in the August and
#: July 2026 articles, `<h3><strong>Other Albums of Note:</strong></h3>`
#: in June and May. So headings are matched structurally and the *text*
#: is what decides -- a regex against the raw markup gets three months
#: right and one wrong, which is the worst possible outcome.
_HEADING = re.compile(r"<h[23][^>]*>(.*?)</h[23]>", re.DOTALL)
_LEFTOVERS_TEXT = "other albums of note"

#: `Artist<br><a ...><em>Album</em></a>`. The `<br>` separates the two
#: in all 12 of 12 captured August headings.
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)

_ITALIC = re.compile(r"<(?:em|i)>(.*?)</(?:em|i)>", re.DOTALL)
_PARAGRAPH = re.compile(r"<p\b[^>]*>(.*?)</p>", re.DOTALL)


def _is_best_jazz(item) -> bool:
    return (item.link or "").startswith(SECTION)


def discover(fetch, now: Optional[datetime] = None) -> DiscoveryResult:
    """The month's column, if it landed inside the window."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=WITHIN_DAYS)

    def select(item) -> bool:
        if not _is_best_jazz(item):
            return False
        return item.published_at is None or item.published_at >= cutoff

    return discover_from_feed(fetch, FEED_URL, select=select, needs_page=True)


def _article(page: str) -> dict:
    for doc in ld_json_docs(page):
        if doc.get("@type") == "Article":
            return doc
    return {}


def segments(page: str) -> List[Tuple[str, str]]:
    """`(heading, prose)` for each album, in the order the writer put them.

    Everything from an `h3` until the next one belongs to that album,
    exactly as `h2` works in the Pitchfork roundup. The body is cut at
    the "Other Albums of Note" heading first -- without that the last
    album's prose swallows the whole courtesy list, and picks up its
    quoted titles as if the writer had named them.
    """
    body = _BODY.search(page or "")
    text = body.group(0) if body else (page or "")
    for heading in _HEADING.finditer(text):
        if strip_html(heading.group(1)).lower().startswith(_LEFTOVERS_TEXT):
            text = text[: heading.start()]
            break

    parts = _ENTRY.split(text)
    # split() yields [preamble, heading, prose, heading, prose, ...].
    return [(parts[i], parts[i + 1] if i + 1 < len(parts) else "")
            for i in range(1, len(parts), 2)]


def _artist_and_album(heading: str) -> Tuple[Optional[str], Optional[str]]:
    """The credit before the `<br>`, and the album italicised after it."""
    italics = _ITALIC.search(heading)
    album = strip_html(italics.group(1)) if italics else None

    before = _BREAK.split(heading, maxsplit=1)[0]
    artist = strip_html(before)
    # An entry whose credit trails off ("Ben Goldberg…") is prose, not a
    # name; the ellipsis belongs to the sentence the heading is making.
    artist = artist.rstrip("…. ").strip() or None
    return artist, (album or None)


def parse(candidate: Candidate, page: Optional[str]) -> ParseResult:
    """Every album in the column, plus the entries that are not albums."""
    item = candidate.item
    doc = _article(page or "")
    keywords = doc.get("keywords")
    genre = keywords.split(",")[0].strip() if isinstance(keywords, str) else None

    verdicts, problems = [], []
    for heading, prose in segments(page or ""):
        artist, album = _artist_and_album(heading)
        if not artist or not album:
            # Some months carry an entry that is a remark rather than a
            # record -- August's "Ben Goldberg… released eight albums in
            # August. Yes, eight." There is nothing to resolve, and a
            # silent skip would leave no trace of a real editorial
            # mention we chose not to act on.
            problems.append(
                Problem(
                    reason="entry_names_no_album",
                    source_url=item.link,
                    artist=artist,
                    album=album,
                )
            )
            continue

        paragraphs = _PARAGRAPH.findall(prose)
        verdicts.append(
            Verdict(
                source=NAME,
                artist=artist,
                album=album,
                source_url=item.link,
                # Every album in the column shares the article's date.
                published_at=item.published_at,
                label=None,
                # A monthly best-of carries no score, and being chosen is
                # the verdict.
                score=None,
                named_tracks=track_candidates(
                    [["p", strip_html(p)] for p in paragraphs]
                ),
                editorial_tier="best_jazz",
                genre=genre,
            )
        )

    if not verdicts:
        # A 200 with no albums means the column's markup moved, not a
        # month with no jazz in it.
        problems.append(
            Problem(reason="article_shape_changed", source_url=item.link)
        )
    return ParseResult(verdicts=tuple(verdicts), problems=tuple(problems))
