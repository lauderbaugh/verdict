"""NPR Music's New Music Friday.

The only source here that needs no page fetch. Its show notes list the
week's albums in the RSS `description` itself, so discovery and parsing
are the same request -- no HTML, no state blob, and therefore none of the
fragility `verso.py` exists to quarantine.

NPR names no tracks: it is an audio show, and the notes give albums only.
Every NPR-only album therefore reaches resolution with no named tracks
and falls through to the Last.fm and positional steps.
"""

from __future__ import annotations

import html as html_module
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from verdict.feed import FeedItem, parse_rss
from verdict.models import Verdict
from verdict.sources.base import Candidate, DiscoveryResult, ParseResult, Problem

NAME = "npr_new_music_friday"

#: The NPR Music podcast feed. New Music Friday is one series within it,
#: so the feed carries other shows that must be filtered out.
FEED_URL = "https://feeds.npr.org/510019/podcast.xml"

#: Episodes are identified by title. The series name is stable; the rest
#: of the title varies ("The best albums out Aug. 28", "Record Store Day
#: Black Friday 2025", "A roundup of December albums").
SERIES = re.compile(r"new\s+music\s+friday", re.IGNORECASE)

#: How far back to look. The podcast feed carries 300 items -- roughly
#: two years of episodes -- so unlike the Pitchfork feeds, which span a
#: few days, discovery here must bound itself or every run reprocesses
#: the whole archive. Two weeks covers the weekly cadence plus one
#: missed run; anything older would age out of the 28-day playlist
#: window before it could earn a place.
#:
#: This is why discovery belongs to the source: the same helper serves
#: Pitchfork unbounded and NPR bounded, and only the source knows which
#: it needs.
WITHIN_DAYS = 14

#: Show notes are divided into an editorial tier and a secondary one.
#: Both headings have drifted over the captured range, hence the
#: alternatives.
_SECTION = re.compile(
    r"(The\s+Starting\s+5"
    r"|The\s+Lightning\s+Round"
    r"|Lightning\s+Round\s+Recommendations"
    r"|Artists\s+and\s+albums\s+featured[^:]*)\s*:?",
    re.IGNORECASE,
)

#: Entries are separated by a timestamp, a bullet, a dash, or an "Album
#: No. N" label -- the notes use all four across the captured range, and
#: more than one within a single episode.
_DELIM = re.compile(
    r"\(\d{1,2}:\d{2}\)"
    r"|(?:(?<=['’\")\s:])|^)\s*[-–—•|]\s*"
    r"|Album No\.?\s*\d+"
    r"|Intro\s*&\s*"
)

#: `Artist, 'Album'`, with an optional trailing label in parentheses.
#: Anchored on the quoted album rather than on a separator: the
#: separators vary, the quoting does not. Episodes that leave albums
#: unquoted are not parsed at all -- guessing where an unquoted title
#: begins would put junk on the playlist.
_ENTRY = re.compile(r"^(.{2,70}?),\s*['‘](.{1,80}?)['’]\s*(?:\(([^)]{1,60})\))?\s*$")

#: Everything after these is production credits, which name people.
_TAIL = ("Sample the albums", "Credits:", "Host: ")

#: The editorial tier. Recorded on the Verdict even though nothing reads
#: it yet: it is the signal consensus weighting would need, and it is
#: only available at parse time.
STARTING_5 = "starting_5"
LIGHTNING_ROUND = "lightning_round"


def _strip_html(value: str) -> str:
    return html_module.unescape(re.sub(r"<[^>]+>", "", value or ""))


def is_new_music_friday(item: FeedItem) -> bool:
    return bool(SERIES.search(item.title or ""))


def discover(fetch, now: Optional[datetime] = None) -> DiscoveryResult:
    """Every New Music Friday episode in the feed.

    Needs no page: `needs_page=False` tells the orchestrator the show
    notes on the feed item are the whole story.
    """
    try:
        items = parse_rss(fetch(FEED_URL))
    except Exception as exc:  # noqa: BLE001 - a dead feed must not end the run
        return DiscoveryResult(
            problems=(Problem(reason=f"feed_unavailable: {exc}", source_url=FEED_URL),)
        )

    if not items:
        return DiscoveryResult(
            problems=(Problem(reason="feed_empty", source_url=FEED_URL),)
        )

    episodes = [item for item in items if is_new_music_friday(item)]
    if not episodes:
        # The feed carries other NPR Music shows, so "no episodes" means
        # the series was renamed or dropped, not a quiet week.
        return DiscoveryResult(
            problems=(Problem(reason="series_not_found", source_url=FEED_URL),)
        )

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=WITHIN_DAYS)
    candidates = tuple(
        Candidate(item=item, needs_page=False)
        for item in episodes
        # An undated episode is kept: dropping on missing data would lose
        # a real week, and dedup stops it being added twice.
        if item.published_at is None or item.published_at >= cutoff
    )
    return DiscoveryResult(candidates=candidates)


def _sections(notes: str) -> List[Tuple[Optional[str], str]]:
    """Split show notes into (tier, text) runs."""
    parts = _SECTION.split(notes)
    runs: List[Tuple[Optional[str], str]] = []
    tier: Optional[str] = None
    for index, part in enumerate(parts):
        if part is None:
            continue
        if index % 2 == 1:  # a captured heading
            tier = STARTING_5 if re.search(r"starting", part, re.I) else LIGHTNING_ROUND
            continue
        runs.append((tier, part))
    return runs


def albums(notes: str) -> List[Tuple[Optional[str], str, str]]:
    """(tier, artist, album) for every entry the notes name."""
    for stop in _TAIL:
        cut = notes.find(stop)
        if cut > 0:
            notes = notes[:cut]

    found = []
    for tier, run in _sections(notes):
        for chunk in _DELIM.split(run):
            cleaned = (chunk or "").strip().lstrip("-–—•|").strip().strip(".:").strip()
            match = _ENTRY.match(cleaned)
            if match:
                found.append((tier, match.group(1).strip(), match.group(2).strip()))
    return found


def parse(candidate: Candidate, page: Optional[str] = None) -> ParseResult:
    """Turn one episode's show notes into a Verdict per album.

    `page` is ignored: this source reads the feed item only.
    """
    item = candidate.item
    notes = _strip_html(item.description)
    entries = albums(notes)

    if not entries:
        # Some episodes leave albums unquoted, or list bare artist names
        # with no album at all. Both are unparseable rather than empty,
        # so they go to the bug queue instead of passing as a quiet week.
        return ParseResult(
            problems=(Problem(reason="notes_shape_unreadable", source_url=item.link),)
        )

    verdicts = []
    seen = set()
    for tier, artist, album in entries:
        key = (artist.casefold(), album.casefold())
        if key in seen:
            continue
        seen.add(key)
        verdicts.append(
            Verdict(
                source=NAME,
                artist=artist,
                album=album,
                source_url=item.link,
                published_at=item.published_at,
                label=None,
                # NPR publishes no rating of any kind, and nothing is
                # normalized across publications regardless.
                score=None,
                # Audio show: the notes name albums, never tracks.
                named_tracks=(),
                editorial_tier=tier,
            )
        )
    return ParseResult(verdicts=tuple(verdicts))
