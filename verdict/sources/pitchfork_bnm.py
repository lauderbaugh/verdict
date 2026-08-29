"""Pitchfork Best New Music.

~1-3 albums a week. Overlaps the roundup heavily; dedup by Spotify URI
downstream handles the collision, so this adapter makes no attempt to
know what the roundup already found.

The album-reviews feed carries no artist field and no BNM flag, so every
review page has to be fetched and inspected.
"""

from __future__ import annotations

from verdict.feed import FeedItem
from verdict.models import Verdict
from verdict.sources.base import ParseResult, Problem
from verdict.sources.prose import prose_blocks, track_candidates
from verdict.verso import children_of, dig, extract_state, item_reviewed_name

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
SUNDAY_REVIEW_MARKERS = (
    "each sunday",
    "every sunday",
    "significant album from the past",
)


def is_sunday_review(item: FeedItem) -> bool:
    """True if the feed description opens with Sunday Review boilerplate."""
    description = item.description.lower()
    return any(marker in description for marker in SUNDAY_REVIEW_MARKERS)


def select(item: FeedItem) -> bool:
    """Every album review except Sunday Reviews is worth fetching.

    Whether a review is Best New Music cannot be known from the feed, so
    selection is deliberately broad and the real filter happens in
    `parse`.
    """
    return not is_sunday_review(item)


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
        return [item for item in items if isinstance(item, dict)]

    # Wrapped to match the itemsReviewed shape, carrying the header's own
    # title across so the fallback can stand on its own when a page has no
    # ld+json to read the album from.
    header_props = dig(review, "headerProps")
    return [{
        "musicRating": dig(header_props, "musicRating"),
        "dangerousHed": header_props.get("dangerousHed"),
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


def _artist_album(review: dict, entry: dict, html: str, single: bool):
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

    album = (entry.get("dangerousHed") or "").strip() or None

    # Reached via the headerProps fallback too, where multiReviewHeaderProps
    # may be absent entirely -- so this must not assume it exists.
    header = review.get("multiReviewHeaderProps")
    artists = (header or {}).get("artistDetails") or []
    artist = None
    if isinstance(artists, list) and artists and isinstance(artists[0], dict):
        artist = (artists[0].get("name") or "").strip() or None

    if not artist:
        names = review.get("headerProps", {}).get("artists") or []
        if isinstance(names, list) and names and isinstance(names[0], dict):
            artist = (names[0].get("name") or "").strip() or None

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


def parse(item: FeedItem, html: str) -> ParseResult:
    """Turn one review page into a Verdict per Best New Music album."""
    state = extract_state(html)
    review = dig(state, "transformed", "review")
    entries = _reviewed_items(review)

    verdicts: list[Verdict] = []
    problems: list[Problem] = []
    single = len(entries) == 1
    candidates = _body_candidates(review, single)

    for entry in entries:
        rating = entry.get("musicRating")
        if not isinstance(rating, dict):
            problems.append(Problem(reason="rating_missing", source_url=item.link))
            continue

        # Editorial, not arithmetic: an 8.0 was verified as not BNM while
        # BNM records scored 8.6 and 9, so score is never thresholded.
        if not rating.get("isBestNewMusic"):
            continue

        names = _artist_album(review, entry, html, single)
        if names is None:
            problems.append(Problem(reason="artist_album_unparsed", source_url=item.link))
            continue

        artist, album = names
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
