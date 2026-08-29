"""The shared source-adapter interface.

Two adapters exist; the second one (`pitchfork_bnm`) is what forced this
shape, per SPEC's instruction to build the roundup completely first and
let the second adapter define the interface rather than guessing it up
front.

Both adapters turned out to fit `select` + `parse`:

- discovery splits into a `FEED_URL` plus a pure `select(item)` predicate,
  so the fetching layer is shared and the selection rules stay testable
  offline;
- `parse` returns a `ParseResult` rather than writing logs itself, so
  adapters stay pure and the caller owns the journal. This matters
  because the two adapters drop items for different reasons: the roundup
  drops malformed segments (a defect worth logging), while BNM drops
  every non-BNM review (routine, not a defect).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from verdict.feed import FeedItem
from verdict.models import Verdict


@dataclass(frozen=True)
class Problem:
    """Something the adapter could not turn into a Verdict.

    Maps onto a row in `unmatched.ndjson`. `artist`/`album` are optional
    because the failure may happen before either could be read.
    """

    reason: str
    source_url: str
    artist: str | None = None
    album: str | None = None


@dataclass(frozen=True)
class ParseResult:
    """Verdicts lifted from one article, plus anything that failed."""

    verdicts: tuple[Verdict, ...] = field(default_factory=tuple)
    problems: tuple[Problem, ...] = field(default_factory=tuple)


@runtime_checkable
class Source(Protocol):
    """One publication's adapter."""

    NAME: str
    FEED_URL: str

    @staticmethod
    def select(item: FeedItem) -> bool:
        """True if this feed item is worth fetching."""

    @staticmethod
    def parse(item: FeedItem, html: str) -> ParseResult:
        """Turn one fetched article into Verdicts.

        Raises `StateShapeError` if the page's internals have moved; the
        caller logs `state_shape_changed` and continues.
        """
