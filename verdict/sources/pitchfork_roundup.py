"""Pitchfork's weekly "N New Albums You Should Listen to Now" roundup.

~13 albums a week, human-curated, and it includes records that never get
a full review -- which is why SPEC makes it the primary adapter.
"""

from __future__ import annotations

import re

from verdict.feed import FeedItem
from verdict.models import Verdict
from verdict.sources.base import ParseResult, Problem
from verdict.verso import (
    children_of,
    dig,
    extract_state,
    first_child_tagged,
    flat,
    tag_of,
)

NAME = "pitchfork_roundup"
FEED_URL = "https://pitchfork.com/feed/feed-news/rss"

#: Roundups are identified by URL slug, never by title. The news feed
#: carries unrelated "Listen to ..." posts that match on title text, and
#: the album count in the title changes week to week.
SLUG = "albums-you-should-listen-to-now"

#: Only `p` blocks carry prose. This is an allowlist rather than the
#: denylist SPEC sketches: the verified fixture also contains `ad`,
#: `journey-inline-newsletter` and `inline-newsletter` blocks between the
#: `h2`s, which that denylist misses. Allowlisting means a Verso block
#: type we have never seen cannot silently inject text into candidates.
PROSE_TAGS = frozenset({"p"})

#: Track names appear in curly quotes (U+201C/U+201D). Straight-quote
#: matching returns nothing -- Condé Nast uses typographic quotes.
#: Album titles are italicised and never quoted, so `em` content is
#: never a track candidate.
_TRACK_RE = re.compile("“([^”]{1,60})”")

#: Trailing label in the header, e.g. `[Partisan]`.
_LABEL_RE = re.compile(r"\[([^\[\]]+)\]\s*$")

#: Pitchfork puts commas and full stops *inside* the quotes. Stripped
#: from the right only: a leading apostrophe can be part of the title,
#: as in “’Til the End”.
_TRAILING_PUNCT = " ,.;:!?—-’'"


def select(item: FeedItem) -> bool:
    """True for roundup articles, matched on slug rather than title."""
    return SLUG in item.link


def _segment(body: list) -> list[tuple[list, list]]:
    """Split the body AST into (header, prose blocks) pairs.

    `h2` blocks delimit albums: everything from one `h2` until the next
    belongs to that album. Content before the first `h2` is the article
    intro and is discarded.
    """
    segments: list[tuple[list, list]] = []
    for node in children_of(body):
        tag = tag_of(node)
        if tag == "h2":
            segments.append((node, []))
        elif segments and tag in PROSE_TAGS:
            segments[-1][1].append(node)
    return segments


def _parse_header(header: list) -> tuple[str, str | None, str | None]:
    """Read artist, album and label out of an `h2`.

    Header shape is fully structured, e.g.::

        ['h2', 'Interpol: ', ['em', 'This Mirror Weighs a Ton'], ' [Partisan]']

    so the artist is never parsed out of a URL slug.
    """
    kids = children_of(header)

    leading = next((k for k in kids if isinstance(k, str)), "")
    artist = leading.strip().removesuffix(":").strip()

    em = first_child_tagged(header, "em")
    album = flat(em).strip() or None

    # The label sits in a trailing top-level string, after the `em`.
    tail = "".join(k for k in kids if isinstance(k, str))
    label_match = _LABEL_RE.search(tail.strip())
    label = label_match.group(1).strip() if label_match else None

    return artist, album, label


def _track_candidates(prose: list) -> tuple[str, ...]:
    """Quoted phrases from an album's prose, de-duplicated in order.

    These are NOISY on purpose. Verified output mixes song lyrics and
    descriptive phrases in with real track names, and that is fine:
    validation against the real tracklist discards non-tracks, which is
    what makes false positives structurally hard. Filtering cleverly
    here would only risk dropping real titles.
    """
    text = " ".join(flat(block) for block in prose)
    seen: dict[str, None] = {}
    for raw in _TRACK_RE.findall(text):
        name = raw.strip().rstrip(_TRAILING_PUNCT).strip()
        if name:
            seen.setdefault(name, None)
    return tuple(seen)


def parse(item: FeedItem, html: str) -> ParseResult:
    """Turn one roundup article into a Verdict per album."""
    state = extract_state(html)
    body = dig(state, "transformed", "article", "body")

    verdicts: list[Verdict] = []
    problems: list[Problem] = []

    for header, prose in _segment(body):
        artist, album, label = _parse_header(header)

        if not artist or not album:
            # A header we cannot read is a real defect, not a routine
            # skip -- send it to the bug queue with what we did get.
            problems.append(
                Problem(
                    reason="header_unparsed",
                    source_url=item.link,
                    artist=artist or None,
                    album=album,
                )
            )
            continue

        verdicts.append(
            Verdict(
                source=NAME,
                artist=artist,
                album=album,
                source_url=item.link,
                published_at=item.published_at,
                label=label,
                score=None,  # the roundup does not score
                named_tracks=_track_candidates(prose),
            )
        )

    return ParseResult(verdicts=tuple(verdicts), problems=tuple(problems))
