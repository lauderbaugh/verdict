"""BNM adapter, against two real captured review pages."""

from __future__ import annotations

import json

import pytest

from verdict.feed import FeedItem
from verdict.sources import pitchfork_bnm as bnm
from verdict.verso import StateShapeError

ITEM = FeedItem(link="https://pitchfork.com/reviews/albums/aldous-harding-train-on-the-island/")


def synthetic_page(review: dict) -> str:
    state = {"transformed": {"review": review}}
    return f"<script>window.__PRELOADED_STATE__ = {json.dumps(state)};</script>"


def test_best_new_music_is_emitted(bnm_html):
    result = bnm.parse(ITEM, bnm_html)
    assert len(result.verdicts) == 1
    verdict = result.verdicts[0]
    assert verdict.source == "pitchfork_bnm"
    assert verdict.artist == "Aldous Harding"
    assert verdict.album == "Train on the Island"
    assert verdict.label == "4AD"
    assert verdict.score == 9.0


def test_non_bnm_is_filtered_out(not_bnm_html):
    """Routine, not a defect: no Problem is recorded for an ordinary review."""
    result = bnm.parse(ITEM, not_bnm_html)
    assert result.verdicts == ()
    assert result.problems == ()


def test_score_is_not_thresholded(not_bnm_html, bnm_html):
    """BNM is editorial, not arithmetic.

    The verified 8.0 was not BNM while a 9 was, so no score cutoff could
    reproduce the editorial call.
    """
    from verdict.verso import dig, extract_state

    rejected = dig(extract_state(not_bnm_html), "transformed", "review",
                   "multiReviewHeaderProps", "itemsReviewed", 0, "musicRating")
    assert rejected["score"] == 8 and rejected["isBestNewMusic"] is False
    assert bnm.parse(ITEM, not_bnm_html).verdicts == ()
    assert bnm.parse(ITEM, bnm_html).verdicts[0].score == 9.0


def test_score_is_coerced_to_float(bnm_html):
    """Stored type is inconsistent across pages (`9`, `8.6`, `8`)."""
    stored = json.loads(json.dumps(9))
    assert isinstance(stored, int)
    assert isinstance(bnm.parse(ITEM, bnm_html).verdicts[0].score, float)


def test_track_candidates_are_lifted_from_the_review_body(bnm_html):
    """Without this, every BNM verdict would be a first-track fallback."""
    tracks = bnm.parse(ITEM, bnm_html).verdicts[0].named_tracks
    assert {"I Ate the Most", "San Francisco", "Riding That Symbol"} <= set(tracks)


def test_review_candidates_are_noisier_than_roundup_candidates(bnm_html):
    """A full review quotes lyrics far more than a one-paragraph blurb.

    About half of these are not tracks. That is the accepted trade: the
    cost is recall at resolution, not a wrong track on the playlist.
    """
    tracks = bnm.parse(ITEM, bnm_html).verdicts[0].named_tracks
    assert len(tracks) > 10
    assert "honesty" in tracks  # scare-quoted prose, discarded downstream


def test_multi_album_review_gets_no_candidates():
    """One shared body cannot be attributed to a particular album."""
    page = synthetic_page({
        "body": ["div", ["p", "The standout is “Some Track” here."]],
        "multiReviewHeaderProps": {
            "artistDetails": [{"name": "Band"}],
            "itemsReviewed": [
                {"dangerousHed": "One", "musicRating": {"isBestNewMusic": True, "score": 9}},
                {"dangerousHed": "Two", "musicRating": {"isBestNewMusic": True, "score": 8.6}},
            ],
        },
    })
    result = bnm.parse(ITEM, page)
    assert len(result.verdicts) == 2
    assert all(v.named_tracks == () for v in result.verdicts)


def test_missing_body_is_a_thin_result_not_an_error():
    page = synthetic_page({
        "multiReviewHeaderProps": {
            "artistDetails": [{"name": "Band"}],
            "itemsReviewed": [
                {"dangerousHed": "Record", "musicRating": {"isBestNewMusic": True, "score": 9}},
            ],
        },
    })
    result = bnm.parse(ITEM, page)
    assert result.verdicts[0].named_tracks == ()
    assert result.problems == ()


def test_ad_blocks_in_review_bodies_are_excluded():
    """Review bodies carry the same ad/newsletter block zoo as roundups."""
    page = synthetic_page({
        "body": ["div",
                 ["p", "The single is “Real Track”."],
                 ["native-ad", "“Buy This”"],
                 ["cm-unit", "“Subscribe Now”"],
                 ["inline-newsletter", "“Sign Up”"]],
        "multiReviewHeaderProps": {
            "artistDetails": [{"name": "Band"}],
            "itemsReviewed": [
                {"dangerousHed": "Record", "musicRating": {"isBestNewMusic": True, "score": 9}},
            ],
        },
    })
    assert bnm.parse(ITEM, page).verdicts[0].named_tracks == ("Real Track",)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Aldous Harding: Train on the Island", ("Aldous Harding", "Train on the Island")),
        # Split on the FIRST ': ' only -- album titles can contain colons.
        ("Turnstile: Never Enough: Versions", ("Turnstile", "Never Enough: Versions")),
        ("No separator here", None),
        ("Artist: ", None),
    ],
)
def test_split_name_uses_the_first_separator(name, expected):
    assert bnm._split_name(name) == expected


@pytest.mark.parametrize(
    "description, is_sunday",
    [
        ("Every Sunday, we revisit a significant album from the past.", True),
        ("each week we look back at a significant album from the past", True),
        ("Aldous Harding returns with her finest record yet.", False),
        ("", False),
    ],
)
def test_sunday_reviews_are_excluded_at_discovery(description, is_sunday):
    """Retrospectives on old albums must never reach the playlist."""
    item = FeedItem(link="https://pitchfork.com/reviews/albums/x/", description=description)
    assert bnm.is_sunday_review(item) is is_sunday
    assert bnm.select(item) is not is_sunday


def test_multi_album_review_uses_per_item_fields():
    """itemsReviewed is uniformly a list, so one code path covers both.

    ld+json carries only one name, so multi-album pages read artist and
    album from the blob's unambiguous per-item fields instead.
    """
    page = synthetic_page({
        "multiReviewHeaderProps": {
            "artistDetails": [{"name": "Some Band"}],
            "itemsReviewed": [
                {"dangerousHed": "First: Record", "publisher": "Label A",
                 "musicRating": {"isBestNewMusic": True, "score": 8.6}},
                {"dangerousHed": "Second Record", "publisher": "Label B",
                 "musicRating": {"isBestNewMusic": False, "score": 7}},
            ],
        }
    })
    result = bnm.parse(ITEM, page)
    assert len(result.verdicts) == 1
    assert result.verdicts[0].artist == "Some Band"
    assert result.verdicts[0].album == "First: Record"
    assert result.verdicts[0].score == 8.6


def test_falls_back_to_header_props_when_items_absent():
    page = synthetic_page({
        "headerProps": {
            "musicRating": {"isBestNewMusic": True, "score": 8.6},
            "artists": [{"name": "Fallback Band"}],
            "dangerousHed": "Only Record",
        }
    })
    result = bnm.parse(ITEM, page)
    assert len(result.verdicts) == 1
    assert result.verdicts[0].artist == "Fallback Band"
    assert result.verdicts[0].album == "Only Record"
    assert result.verdicts[0].score == 8.6


def test_missing_review_node_raises_state_shape_error():
    """The caller logs `state_shape_changed` and keeps the run alive."""
    with pytest.raises(StateShapeError):
        bnm.parse(ITEM, synthetic_page({}))


def test_recirculation_modules_are_never_consulted(bnm_html):
    """`isBestNewMusic` and `score` appear many times per page.

    In recirc modules `score` is a relevance float (e.g. 0.5373776035),
    so the adapter walks to the node instead of grepping the blob.
    """
    assert bnm_html.count("isBestNewMusic") > 1
    assert bnm.parse(ITEM, bnm_html).verdicts[0].score == 9.0


# --- Sunday Reviews -------------------------------------------------------
#
# Retrospectives on old albums. Verified against a real one: Paradis,
# "Recto Verso" (a 2016 record reviewed on 2026-08-23).


def test_real_sunday_review_is_excluded_at_discovery():
    """The exact description carried by the live feed on 2026-08-30."""
    item = FeedItem(
        link="https://pitchfork.com/reviews/albums/paradis-recto-verso/",
        description=(
            "Each Sunday, Pitchfork takes an in-depth look at a significant album "
            "from the past, and any record not in our archives is eligible. This "
            "week, we revisit a 2016 album from France that reinvented the "
            "decade’s electro-pop as an idyllic Mediterranean dream."
        ),
    )
    assert bnm.is_sunday_review(item) is True
    assert bnm.select(item) is False


def test_rubric_cannot_distinguish_a_sunday_review(sunday_html, bnm_html, not_bnm_html):
    """Why the exclusion is string matching and not a structured field.

    A structured signal would be preferable, but there is not one: the
    Sunday Review reports the same `rubric.name` and `documentType` as
    both ordinary reviews. If a future Verso change gives Sunday Reviews
    their own rubric, this test fails and the exclusion should move.
    """
    from verdict.verso import dig, extract_state

    rubrics = {
        name: dig(extract_state(html), "transformed", "review", "headerProps",
                  "rubric", "name")
        for name, html in (
            ("sunday", sunday_html), ("bnm", bnm_html), ("ordinary", not_bnm_html)
        )
    }
    assert rubrics == {"sunday": "Albums", "bnm": "Albums", "ordinary": "Albums"}


def test_sunday_review_reviews_an_old_album(sunday_html):
    """What makes it ineligible: the record is a decade old."""
    from verdict.verso import dig, extract_state

    entry = dig(extract_state(sunday_html), "transformed", "review",
                "multiReviewHeaderProps", "itemsReviewed", 0)
    assert entry["releaseYear"] == "2016"


def test_sunday_review_would_be_dropped_by_the_bnm_filter_too(sunday_html):
    """Belt and braces: it is not flagged Best New Music either.

    This is a second line of defence, not the primary one -- a Sunday
    Review carrying the flag would still need the discovery filter.
    """
    assert bnm.parse(ITEM, sunday_html).verdicts == ()
