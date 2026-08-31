"""Stereogum corroboration. Evidence about other people's albums."""

from __future__ import annotations

from datetime import date

import pytest

from verdict.corroborate import (
    MIN_LIST_ENTRIES,
    Corroboration,
    apply,
    fetch_week,
    parse_page,
)
from verdict.journal import Journal
from verdict.models import Verdict

AOTW_FEED = """<rss version="2.0"><channel><item>
  <title>Album Of The Week: Maripool Rotten Luck</title>
  <link>https://stereogum.com/aotw</link>
  <pubDate>Tue, 25 Aug 2026 12:00:00 +0000</pubDate>
</item></channel></rss>"""


@pytest.fixture(scope="module")
def page():
    with open("tests/fixtures/stereogum_aotw.html", encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def week(page):
    return parse_page(page)


def verdict(album, artist="Someone"):
    return Verdict(source="s", artist=artist, album=album, source_url="u")


# --- parsing --------------------------------------------------------------

def test_the_editorial_pick_is_split_from_the_title(week):
    """The RSS title leaves no separator; the page's state has the markup."""
    assert week.editorial == ("Maripool", "Rotten Luck")


def test_the_weekly_list_is_read(week):
    assert week.entry_count == 126


def test_a_page_without_state_is_empty():
    assert not parse_page("<html>nothing here</html>")


def test_malformed_state_is_empty():
    assert not parse_page('<script id="__NEXT_DATA__" type="x">{oh no</script>')


# --- the two signals are different claims ---------------------------------

def test_presence_on_the_list_is_not_editorial_agreement(week):
    """The finding that motivates keeping them apart.

    Stereogum listed 12 of Pitchfork's 13 that week and picked none of
    them. A comprehensive release list mostly proves the record came out.
    """
    from verdict.feed import FeedItem
    from verdict.sources import pitchfork_roundup as roundup
    from verdict.sources.base import Candidate

    with open("tests/fixtures/roundup_2026-08-28.html", encoding="utf-8") as handle:
        verdicts = roundup.parse(Candidate(FeedItem(link="x")), handle.read()).verdicts

    stamped = [apply(v, week) for v in verdicts]
    assert sum(1 for v in stamped if v.corroborated_by_list) == 12
    assert sum(1 for v in stamped if v.corroborated_editorially) == 0


def test_the_editorial_pick_is_flagged(week):
    stamped = apply(verdict("Rotten Luck", "Maripool"), week)
    assert stamped.corroborated_editorially is True


def test_an_uncovered_album_gets_neither(week):
    stamped = apply(verdict("A Record Nobody Mentioned"), week)
    assert stamped.corroborated_by_list is False
    assert stamped.corroborated_editorially is False


def test_short_titles_do_not_match_by_accident(week):
    """Containment stops being evidence below a few characters."""
    assert apply(verdict("Up"), week).corroborated_by_list is False
    assert apply(verdict("A"), week).corroborated_by_list is False


def test_matching_is_word_bounded(week):
    """A title must not match inside a longer word."""
    assert week.lists("Chrom") is False


def test_empty_corroboration_leaves_a_verdict_untouched():
    original = verdict("Anything")
    assert apply(original, Corroboration()) is original


# --- degradation ----------------------------------------------------------

def fetcher(mapping, fail=None):
    def fetch(url):
        if fail and url in fail:
            raise OSError("boom")
        return mapping[url]
    return fetch


def test_a_dead_feed_degrades_and_logs(tmp_path):
    """Corroboration is evidence about someone else's albums.

    Losing it costs a log field and nothing else, so it must never be
    able to fail a run.
    """
    def dead(url):
        raise OSError("no route to host")

    journal = Journal(tmp_path)
    result = fetch_week(dead, journal, date(2026, 8, 30))
    assert not result
    import json
    rows = [json.loads(line) for line in (tmp_path / "unmatched.ndjson").read_text().splitlines()]
    assert rows[0]["reason"].startswith("corroboration_unavailable")


def test_an_unfetchable_page_degrades(tmp_path):
    from verdict.corroborate import FEED_URL

    journal = Journal(tmp_path)
    result = fetch_week(fetcher({FEED_URL: AOTW_FEED}, fail={"https://stereogum.com/aotw"}),
                        journal, date(2026, 8, 30))
    assert not result


def synthetic_page(entries):
    """A Next.js page carrying the pick and a list of `entries` albums."""
    import json as _json

    blocks = [{"innerHTML": "Other albums of note out this week:"
                            + "".join(f"• Band{i}'s Record{i}" for i in range(entries))}]
    state = {"props": {"pageProps": {"post": {
        "title": "Album Of The Week: Maripool <em>Rotten Luck</em>",
        "contentBlocks": {"blocks": blocks}}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{_json.dumps(state)}</script>'


def test_a_truncated_list_keeps_the_pick_and_logs(tmp_path):
    """The site runs a paywall; it may behave differently from a runner.

    The editorial pick survives truncation because it comes from the page
    title, which a paywall does not hide. The list is dropped rather than
    trusted, so a paywalled week cannot read as "nobody else covered it".
    """
    import json

    from verdict.corroborate import FEED_URL

    journal = Journal(tmp_path)
    result = fetch_week(
        fetcher({FEED_URL: AOTW_FEED,
                 "https://stereogum.com/aotw": synthetic_page(3)}),
        journal, date(2026, 8, 30),
    )
    assert result.editorial == ("Maripool", "Rotten Luck")
    assert result.listed_blob == ""
    assert result.lists("Record1") is False
    rows = [json.loads(l) for l in (tmp_path / "unmatched.ndjson").read_text().splitlines()]
    assert any("corroboration_list_truncated" in r["reason"] for r in rows)


def test_a_full_list_is_not_treated_as_truncated(tmp_path):
    from verdict.corroborate import FEED_URL

    result = fetch_week(
        fetcher({FEED_URL: AOTW_FEED,
                 "https://stereogum.com/aotw": synthetic_page(MIN_LIST_ENTRIES + 5)}),
        Journal(tmp_path), date(2026, 8, 30),
    )
    assert result.entry_count == MIN_LIST_ENTRIES + 5
    assert result.lists("Record1") is True


def test_the_happy_path_returns_both(tmp_path, page):
    from verdict.corroborate import FEED_URL

    result = fetch_week(
        fetcher({FEED_URL: AOTW_FEED, "https://stereogum.com/aotw": page}),
        Journal(tmp_path), date(2026, 8, 30),
    )
    assert result.editorial == ("Maripool", "Rotten Luck")
    assert result.entry_count == 126


def test_the_feed_url_avoids_the_redirect():
    """Stereogum 308s the slashed form.

    urllib did not follow 308 before Python 3.11, and the first live run
    logged `corroboration_unavailable: HTTP Error 308: Permanent
    Redirect`. Requesting the already-redirected URL sidesteps the
    version question entirely.
    """
    from verdict.corroborate import FEED_URL

    assert not FEED_URL.endswith("/")
