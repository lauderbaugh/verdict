"""RSS discovery.

RSS is one of the two durable interfaces (SPEC: "Failure handling"), so
parsing stays deliberately boring: stdlib only, no feed library.

Fetching is kept out of this module so that discovery logic is testable
without touching the network.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime


@dataclass(frozen=True)
class FeedItem:
    """One `<item>` from a publication's RSS feed."""

    link: str
    title: str = ""
    description: str = ""
    published_at: datetime | None = None


def _text(item: ET.Element, tag: str) -> str:
    value = item.findtext(tag)
    return value.strip() if value else ""


def _published(item: ET.Element) -> datetime | None:
    """Parse `pubDate`. The feed is authoritative for dates.

    Article bodies carry no reliable date (SPEC: "Discovery"), so a feed
    item with an unparseable pubDate yields None rather than a guess.
    """
    raw = _text(item, "pubDate")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def parse_rss(xml_text: str) -> list[FeedItem]:
    """Parse an RSS 2.0 document into FeedItems, skipping link-less entries."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"feed did not parse: {exc}") from exc

    items = []
    for node in root.iter("item"):
        link = _text(node, "link")
        if not link:
            continue
        items.append(
            FeedItem(
                link=link,
                title=_text(node, "title"),
                description=_text(node, "description"),
                published_at=_published(node),
            )
        )
    return items
