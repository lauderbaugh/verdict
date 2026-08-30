"""Pitchfork Best New Music.

~1-3 albums a week. Overlaps the roundup heavily; dedup by Spotify URI
downstream handles the collision, so this adapter makes no attempt to
know what the roundup already found.

The album-reviews feed carries no artist field and no BNM flag, so every
review page has to be fetched and inspected.
"""

from __future__ import annotations

from typing import List, Optional

from verdict.feed import FeedItem
from verdict.models import Verdict
from verdict.sources.base import (
    Candidate,
    DiscoveryResult,
    ParseResult,
    Problem,
    discover_from_feed,
)
from verdict.sources.prose import prose_blocks, track_candidates
from verdict.verso import (  # noqa: F401
    children_of,
    dig,
    extract_state,
    item_reviewed_name,
    StateShapeError,
    strip_html,
)

NAME = "pitchfork_bnm"
FEED_URL = "https://pitchfork.com/feed/feed-album-reviews/rss"

#: Sunday Reviews are retrospectives on old albums and must not reach the
#: playlist. They are excluded at discovery, off the feed `description`,
#: so their pages are never fetched.
#:
#: Verified 2026-08-30 against the live album-reviews feed: exactly one of
#: 30 items was a Sunday Review, and its description opened with this
#: boilerplate verbatim.
#:
#: String matching is a last resort, but there is no structured signal to
#: use instead: `rubric.name` is "Albums" on Sunday Reviews exactly as it
#: is on ordinary reviews, and `documentType` is "review" for both. See
#: tests/test_bnm.py::test_rubric_cannot_distinguish_a_sunday_review.
#: Anchored to the *opening* of the description, not matched anywhere in
#: it. A bare substring test excluded ordinary reviews -- "played the same
#: club every Sunday since 2019", "the most significant album from the
#: past decade of UK rap" -- and because this runs before the page is
#: fetched, such a drop left no trace anywhere. An invisible false
#: positive is the one failure mode this project rules out everywhere
#: else, so the rule now matches only the verified boilerplate opening.
SUNDAY_REVIEW_OPENERS = (
    "each sunday",
    "every sunday",
)

#: A Best New Music record is new by definition. An album this many years
#: older than its review is a retrospective that slipped past the
#: description filter -- Pitchfork can reword boilerplate any week.
RETROSPECTIVE_YEARS = 3


def is_sunday_review(item: FeedItem) -> bool:
    """True if the feed description *opens with* Sunday Review boilerplate."""
    return item.description.lstrip().lower().startswith(SUNDAY_REVIEW_OPENERS)


def skip_reason(item: FeedItem) -> Optional[str]:
    """Why this item will not be fetched, or None if it will be.

    `select` stays a plain predicate; this exists so the caller can write
    a row for every discovery-time drop. Without it a mis-fire here is
    invisible, because the page is never fetched and nothing ever reaches
    `unmatched.ndjson`.
    """
    return "sunday_review" if is_sunday_review(item) else None


def select(item: FeedItem) -> bool:
    """Every album review except Sunday Reviews is worth fetching.

    Whether a review is Best New Music cannot be known from the feed, so
    selection is deliberately broad and the real filter happens in
    `parse`.
    """
    return skip_reason(item) is None


def discover(fetch) -> DiscoveryResult:
    """Album reviews, minus Sunday Reviews, each needing its page fetched.

    Sunday Review drops come back as problems rather than vanishing: the
    filter runs before any fetch, so a mis-fire would leave no other trace.
    """
    return discover_from_feed(fetch, FEED_URL, select, skip_reason=skip_reason)


def _reviewed_items(review: dict) -> list[dict]:
    """The albums covered by this review page.

    `multiReviewHeaderProps/itemsReviewed` is preferred because it is
    uniformly a list, so single- and multi-album reviews take one code
    path. `headerProps/musicRating` is the fallback for pages that lack
    it, and is wrapped to match the same shape.
    """
    header = review.get("multiReviewHeaderProps")
    items = header.get("itemsReviewed") if isinstance(header, dict) else None

    if isinstance(items, list) and items:
        entries = [item for item in items if isinstance(item, dict)]
        if not entries:
            # Present but unreadable is a shape change, not a quiet week.
            raise StateShapeError("itemsReviewed held no objects")
        return entries

    # Wrapped to match the itemsReviewed shape, carrying the header's own
    # title across so the fallback can stand on its own when a page has no
    # ld+json to read the album from.
    header_props = dig(review, "headerProps")
    return [{
        "musicRating": dig(header_props, "musicRating"),
        # headerProps.dangerousHed is raw HTML in every captured review
        # ('<em>Train on the Island</em>'); this is exactly the path that
        # runs when there is no ld+json to read the album from.
        "dangerousHed": strip_html(header_props.get("dangerousHed")),
    }]


def _score(rating: dict) -> float | None:
    """Coerce the review score to float.

    The stored type is inconsistent across pages (`9`, `8.6`, `8`), and
    both verified fixtures happened to hold ints.
    """
    raw = rating.get("score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _split_name(name: str) -> tuple[str, str] | None:
    """Split `"Artist: Album"`, on the FIRST `': '` only.

    Album titles can themselves contain colons, so a greedy split would
    truncate them.
    """
    artist, separator, album = name.partition(": ")
    if not separator:
        return None
    artist, album = artist.strip(), album.strip()
    return (artist, album) if artist and album else None


def _artists(review: dict) -> List[str]:
    """Credited artists, in the order the page lists them."""
    header = review.get("multiReviewHeaderProps") or {}
    details = header.get("artistDetails") or []
    names = [
        (a.get("name") or "").strip()
        for a in details
        if isinstance(a, dict) and (a.get("name") or "").strip()
    ]
    if names:
        return names
    fallback = (review.get("headerProps") or {}).get("artists") or []
    return [
        (a.get("name") or "").strip()
        for a in fallback
        if isinstance(a, dict) and (a.get("name") or "").strip()
    ]


def _artist_for(artists: List[str], index: int, total: int) -> Optional[str]:
    """The artist credited with the album at `index`.

    `itemsReviewed` carries no artist field, so a multi-album review has
    to be attributed by position. One artist covers every album on the
    page; equal counts are paired by index. Anything else is ambiguous
    and becomes a Problem rather than a guess -- and a wrong pairing
    still degrades safely, since scoring takes the weaker of the artist
    and album similarity and drops it below threshold.
    """
    if len(artists) == 1:
        return artists[0]
    if len(artists) == total and total > 0:
        return artists[index]
    return None


def _artist_album(review: dict, entry: dict, html: str, single: bool,
                  index: int, total: int):
    """Resolve artist and album for one reviewed item.

    `ld+json` is preferred, per SPEC's rule that `ld+json` and RSS are the
    durable interfaces while the state blob is the fragile one. It only
    carries one name though, so multi-album reviews fall back to the
    blob's per-item fields, which are unambiguous anyway.
    """
    if single:
        name = item_reviewed_name(html)
        if name:
            split = _split_name(name)
            if split:
                return split

    album = strip_html(entry.get("dangerousHed")) or None
    artist = _artist_for(_artists(review), index, total)
    return (artist, album) if artist and album else None


def _body_candidates(review: dict, single: bool) -> tuple[str, ...]:
    """Quoted track candidates from the review body.

    Full reviews quote far more freely than a one-paragraph roundup
    blurb -- a verified review yielded 25 candidates against the
    roundup's one or two per album -- so the hit rate here is lower.
    That costs recall, not accuracy: tracklist validation discards
    whatever is not a real title, and the alternative is falling back to
    the album's first track for every Best New Music record.

    Multi-album reviews get nothing. One shared body cannot be
    attributed to a particular album, and guessing risks pinning one
    record's track onto another.
    """
    if not single:
        return ()
    body = review.get("body")
    if not isinstance(body, list):
        return ()  # no body is a thin result, not a broken page
    return track_candidates(prose_blocks(children_of(body)))


def _is_retrospective(entry: dict, item: FeedItem) -> bool:
    """True if the album long predates the review that covers it."""
    if item.published_at is None:
        return False
    try:
        release_year = int(str(entry.get("releaseYear") or "").strip())
    except ValueError:
        return False
    return item.published_at.year - release_year > RETROSPECTIVE_YEARS


def parse(candidate: Candidate, page: str) -> ParseResult:
    """Turn one review page into a Verdict per Best New Music album."""
    item = candidate.item
    html = page
    state = extract_state(page)
    review = dig(state, "transformed", "review")
    entries = _reviewed_items(review)

    verdicts: list[Verdict] = []
    problems: list[Problem] = []
    single = len(entries) == 1
    candidates = _body_candidates(review, single)

    for index, entry in enumerate(entries):
        rating = entry.get("musicRating")
        if not isinstance(rating, dict):
            problems.append(Problem(reason="rating_missing", source_url=item.link))
            continue

        # Editorial, not arithmetic: an 8.0 was verified as not BNM while
        # BNM records scored 8.6 and 9, so score is never thresholded.
        if not rating.get("isBestNewMusic"):
            continue

        names = _artist_album(review, entry, html, single, index, len(entries))
        if names is None:
            problems.append(Problem(reason="artist_album_unparsed", source_url=item.link))
            continue

        artist, album = names

        # Backstop for a Sunday Review whose boilerplate was reworded and
        # slipped past discovery. A Best New Music record is new, so a much
        # older one on this path is a retrospective; send it to the bug
        # queue rather than onto the playlist.
        if _is_retrospective(entry, item):
            problems.append(
                Problem(
                    reason="suspected_retrospective",
                    source_url=item.link,
                    artist=artist,
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
                label=(entry.get("publisher") or "").strip() or None,
                score=_score(rating),
                named_tracks=candidates,
            )
        )

    return ParseResult(verdicts=tuple(verdicts), problems=tuple(problems))
