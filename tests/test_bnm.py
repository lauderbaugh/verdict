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
        ("  Each Sunday, Pitchfork takes an in-depth look...", True),
        ("Aldous Harding returns with her finest record yet.", False),
        ("", False),
        # Ordinary reviews that a bare substring test wrongly excluded.
        # The drop happened before the page was fetched, so it left no
        # trace anywhere -- an invisible false positive.
        ("The band has played the same club every Sunday since 2019.", False),
        ("A tender record about the most significant album from the past "
         "decade of UK rap.", False),
    ],
)
def test_sunday_reviews_are_excluded_at_discovery(description, is_sunday):
    """Matched on the *opening* of the description, not anywhere in it."""
    item = FeedItem(link="https://pitchfork.com/reviews/albums/x/", description=description)
    assert bnm.is_sunday_review(item) is is_sunday
    assert bnm.select(item) is not is_sunday


def test_discovery_drops_are_loggable():
    """A drop that leaves no row is the one failure mode ruled out elsewhere."""
    sunday = FeedItem(link="x", description="Each Sunday, Pitchfork takes...")
    ordinary = FeedItem(link="x", description="A fine new record.")
    assert bnm.skip_reason(sunday) == "sunday_review"
    assert bnm.skip_reason(ordinary) is None


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


# --- fixes from adversarial QA --------------------------------------------


def test_header_props_hed_is_html_in_every_real_review(
    bnm_html, not_bnm_html, sunday_html
):
    """The field the fallback reads is raw HTML, not text.

    `dangerous` is Condé Nast's own marker for that. Synthetic test data
    used a plain string, which is why this went unnoticed.
    """
    from verdict.verso import dig, extract_state

    for page in (bnm_html, not_bnm_html, sunday_html):
        hed = dig(extract_state(page), "transformed", "review", "headerProps",
                  "dangerousHed")
        assert hed.startswith("<em>")


def test_fallback_album_is_cleaned_of_markup():
    """Otherwise '<em>Train on the Island</em>' reaches the search query."""
    page = synthetic_page({
        "headerProps": {
            "musicRating": {"isBestNewMusic": True, "score": 9},
            "artists": [{"name": "Aldous Harding"}],
            "dangerousHed": "<em>Train on the Island</em>",
        }
    })
    verdict = bnm.parse(ITEM, page).verdicts[0]
    assert verdict.album == "Train on the Island"


def test_entities_are_unescaped():
    """`J Mascis Live at CBGB&#39;s` turns up in the wild."""
    page = synthetic_page({
        "multiReviewHeaderProps": {
            "artistDetails": [{"name": "J Mascis"}],
            "itemsReviewed": [{
                "dangerousHed": "J Mascis Live at CBGB&#39;s",
                "musicRating": {"isBestNewMusic": True, "score": 9},
            }],
        }
    })
    assert bnm.parse(ITEM, page).verdicts[0].album == "J Mascis Live at CBGB's"


def test_multi_album_review_pairs_artists_by_position():
    """itemsReviewed carries no artist field, so position is all there is."""
    page = synthetic_page({
        "multiReviewHeaderProps": {
            "artistDetails": [{"name": "Artist One"}, {"name": "Artist Two"}],
            "itemsReviewed": [
                {"dangerousHed": "Record One",
                 "musicRating": {"isBestNewMusic": True, "score": 9}},
                {"dangerousHed": "Record Two",
                 "musicRating": {"isBestNewMusic": True, "score": 9}},
            ],
        }
    })
    pairs = [(v.artist, v.album) for v in bnm.parse(ITEM, page).verdicts]
    assert pairs == [("Artist One", "Record One"), ("Artist Two", "Record Two")]


def test_one_artist_covers_every_album_on_the_page():
    page = synthetic_page({
        "multiReviewHeaderProps": {
            "artistDetails": [{"name": "Only Artist"}],
            "itemsReviewed": [
                {"dangerousHed": "One",
                 "musicRating": {"isBestNewMusic": True, "score": 9}},
                {"dangerousHed": "Two",
                 "musicRating": {"isBestNewMusic": True, "score": 9}},
            ],
        }
    })
    assert {v.artist for v in bnm.parse(ITEM, page).verdicts} == {"Only Artist"}


def test_ambiguous_artist_counts_become_a_problem_not_a_guess():
    page = synthetic_page({
        "multiReviewHeaderProps": {
            "artistDetails": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
            "itemsReviewed": [
                {"dangerousHed": "One",
                 "musicRating": {"isBestNewMusic": True, "score": 9}},
                {"dangerousHed": "Two",
                 "musicRating": {"isBestNewMusic": True, "score": 9}},
            ],
        }
    })
    result = bnm.parse(ITEM, page)
    assert result.verdicts == ()
    assert {p.reason for p in result.problems} == {"artist_album_unparsed"}


def test_retrospective_that_slips_discovery_is_caught_at_parse():
    """Backstop for reworded boilerplate.

    A Best New Music record is new by definition, so a decade-old album on
    this path is a retrospective. It goes to the bug queue, not the
    playlist.
    """
    from datetime import datetime, timezone

    page = synthetic_page({
        "multiReviewHeaderProps": {
            "artistDetails": [{"name": "Paradis"}],
            "itemsReviewed": [{
                "dangerousHed": "Recto Verso", "releaseYear": "2016",
                "musicRating": {"isBestNewMusic": True, "score": 8.5},
            }],
        }
    })
    item = FeedItem(link="x", description="Reworded boilerplate.",
                    published_at=datetime(2026, 8, 23, tzinfo=timezone.utc))
    result = bnm.parse(item, page)
    assert result.verdicts == ()
    assert result.problems[0].reason == "suspected_retrospective"


def test_a_current_release_is_not_flagged_retrospective():
    from datetime import datetime, timezone

    page = synthetic_page({
        "multiReviewHeaderProps": {
            "artistDetails": [{"name": "Aldous Harding"}],
            "itemsReviewed": [{
                "dangerousHed": "Train on the Island", "releaseYear": "2026",
                "musicRating": {"isBestNewMusic": True, "score": 9},
            }],
        }
    })
    item = FeedItem(link="x", published_at=datetime(2026, 8, 23, tzinfo=timezone.utc))
    assert len(bnm.parse(item, page).verdicts) == 1


def test_unreadable_items_reviewed_is_a_shape_change_not_a_quiet_week():
    """An empty ParseResult would read as 'no BNM this week'."""
    from verdict.verso import StateShapeError

    page = synthetic_page({
        "multiReviewHeaderProps": {"artistDetails": [{"name": "A"}],
                                   "itemsReviewed": ["not-a-dict"]},
        "headerProps": {"musicRating": {"isBestNewMusic": True, "score": 9}},
    })
    with pytest.raises(StateShapeError):
        bnm.parse(ITEM, page)
