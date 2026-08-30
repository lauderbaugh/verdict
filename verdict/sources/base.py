"""The shared source-adapter interface.

Shaped by two real sources that work differently, rather than by one
imagined interface.

Pitchfork discovers from an RSS feed, filters by URL slug or editorial
flag, then fetches and parses an HTML page per item. NPR discovers from
an RSS feed too, but the show notes in the feed's own `description`
carry everything needed -- artist, album, editorial tier -- so there is
no page to fetch at all.

The first cut of this module assumed every source parsed an RSS feed the
same way and that the orchestrator would do it on their behalf. NPR broke
that: it needs the same feed parsed into a different shape, and needs no
second request. So **discovery belongs to the source**. The orchestrator
supplies a fetcher and receives candidates; it no longer knows what a
feed is, which source has one, or whether a page fetch follows.

Adapters stay pure. They return problems rather than writing logs,
because the two sources drop items for different reasons: Pitchfork
drops non-roundup articles routinely and Sunday Reviews deliberately,
while NPR drops episodes whose show notes are in a shape it cannot read
-- which is a defect worth a row in the bug queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol, Tuple, runtime_checkable

from verdict.feed import FeedItem
from verdict.models import Verdict

#: Fetches a URL and returns its body. Injected so every source is
#: testable without touching the network.
Fetcher = Callable[[str], str]


@dataclass(frozen=True)
class Problem:
    """Something an adapter could not turn into a Verdict.

    Maps onto a row in `unmatched.ndjson`. `artist`/`album` are optional
    because the failure may happen before either could be read.
    """

    reason: str
    source_url: str
    artist: Optional[str] = None
    album: Optional[str] = None


@dataclass(frozen=True)
class Candidate:
    """One item a source wants to turn into verdicts.

    `needs_page` is what NPR forced into existence: a source that can
    answer from the feed alone should not be made to fetch a page it does
    not read.
    """

    item: FeedItem
    needs_page: bool = True

    @property
    def url(self) -> str:
        return self.item.link


@dataclass(frozen=True)
class DiscoveryResult:
    """What a source found worth looking at, and what it dropped.

    Drops are returned rather than silently skipped: a filter that runs
    before any page is fetched leaves no other trace.
    """

    candidates: Tuple[Candidate, ...] = field(default_factory=tuple)
    problems: Tuple[Problem, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ParseResult:
    """Verdicts from one candidate, plus anything that failed."""

    verdicts: Tuple[Verdict, ...] = field(default_factory=tuple)
    problems: Tuple[Problem, ...] = field(default_factory=tuple)


def discover_from_feed(
    fetch: Fetcher,
    feed_url: str,
    select,
    skip_reason=None,
    needs_page: bool = True,
) -> DiscoveryResult:
    """Feed-based discovery, for the sources that work that way.

    A helper the source *calls*, not something the orchestrator does on
    its behalf -- that distinction is the point. NPR uses this too but
    with `needs_page=False`, and a source that had no feed at all would
    simply not call it.
    """
    from verdict.feed import parse_rss

    try:
        items = parse_rss(fetch(feed_url))
    except Exception as exc:  # noqa: BLE001 - a dead feed must not end the run
        return DiscoveryResult(
            problems=(Problem(reason=f"feed_unavailable: {exc}", source_url=feed_url),)
        )

    if not items:
        # A feed answering 200 with nothing is broken, not quiet.
        return DiscoveryResult(
            problems=(Problem(reason="feed_empty", source_url=feed_url),)
        )

    candidates, problems = [], []
    for item in items:
        reason = skip_reason(item) if skip_reason else None
        if reason:
            problems.append(Problem(reason=reason, source_url=item.link))
            continue
        if select(item):
            candidates.append(Candidate(item=item, needs_page=needs_page))
    return DiscoveryResult(candidates=tuple(candidates), problems=tuple(problems))


@runtime_checkable
class Source(Protocol):
    """One publication's adapter."""

    NAME: str

    @staticmethod
    def discover(fetch: Fetcher) -> DiscoveryResult:
        """Everything this source wants to look at this run.

        Owns its own feed URL and its own selection rules. Must not
        raise: an unreachable or unparseable feed is a Problem.
        """

    @staticmethod
    def parse(candidate: Candidate, page: Optional[str]) -> ParseResult:
        """Turn one candidate into Verdicts.

        `page` is the fetched body, or None when the candidate said it
        needed no page. May raise `StateShapeError` if a page's internals
        have moved; the caller logs `state_shape_changed` and continues.
        """
