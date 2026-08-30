"""Fixtures are real captured Pitchfork pages. Tests never touch the network."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from verdict.feed import FeedItem

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def roundup_html() -> str:
    return _load("roundup_2026-08-28.html")


@pytest.fixture(scope="session")
def bnm_html() -> str:
    return _load("review_bnm.html")


@pytest.fixture(scope="session")
def not_bnm_html() -> str:
    return _load("review_not_bnm.html")


@pytest.fixture(scope="session")
def sunday_html() -> str:
    """A real Sunday Review: Paradis, "Recto Verso", published 2026-08-23."""
    return _load("review_sunday.html")


@pytest.fixture
def roundup_item() -> FeedItem:
    return FeedItem(
        link="https://pitchfork.com/news/13-new-albums-you-should-listen-to-now/",
        title="13 New Albums You Should Listen to Now",
        published_at=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def review_item() -> FeedItem:
    return FeedItem(
        link="https://pitchfork.com/reviews/albums/example/",
        title="Example Review",
        published_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
    )
