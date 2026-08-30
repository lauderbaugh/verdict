"""Fuzzy matching. The step that makes noisy extraction safe."""

from __future__ import annotations

import pytest

from verdict.resolve.matcher import (
    TRACK_THRESHOLD,
    base_form,
    best_match,
    normalize,
    similarity,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("San Francisco", "san francisco"),
        ("Sigur Rós", "sigur ros"),          # accents stripped
        ("Don't Look Back", "don t look back"),
        ("  Spaced   Out  ", "spaced out"),
        ("Piñata", "pinata"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("San Francisco - 2011 Remaster", "san francisco"),
        ("San Francisco (2019 Remaster)", "san francisco"),
        ("One Stop (feat. Someone)", "one stop"),
        ("Never Enough (Deluxe Edition)", "never enough"),
        ("Song - Live", "song"),
        ("Plain Title", "plain title"),
    ],
)
def test_base_form_strips_trailing_qualifiers(raw, expected):
    assert base_form(raw) == expected


@pytest.mark.parametrize(
    "prose, spotify",
    [
        ("San Francisco", "San Francisco"),
        ("San Francisco", "San Francisco - 2011 Remaster"),
        ("One Stop", "One Stop (feat. Someone)"),
        ("Venus in the Zinnia", "Venus In The Zinnia"),
        ("Rosas", "Rósas"),
    ],
)
def test_real_variants_match(prose, spotify):
    assert similarity(prose, spotify) >= TRACK_THRESHOLD


@pytest.mark.parametrize(
    "noise, track",
    [
        ("honesty", "Riding That Symbol"),
        ("real", "Real Love"),
        ("San Francisco", "Venus in the Zinnia"),
        ("It truly was an instant friendship.", "Parfait Tirage"),
    ],
)
def test_noise_is_rejected(noise, track):
    """Scare-quoted prose and lyrics must not clear the bar.

    'real' vs 'Real Love' is the important one: a short scare-quoted word
    is a prefix of a real title and would match on a looser rule.
    """
    assert similarity(noise, track) < TRACK_THRESHOLD


def test_distinct_songs_stay_distinct():
    assert similarity("I Ate the Most", "I Ate the Least") < TRACK_THRESHOLD


def test_best_match_returns_the_score():
    hit = best_match("San Francisco", ["Venus in the Zinnia", "San Francisco"])
    assert hit is not None
    name, score = hit
    assert name == "San Francisco" and score == 1.0


def test_best_match_returns_none_below_threshold():
    assert best_match("honesty", ["Riding That Symbol", "One Stop"]) is None


def test_best_match_on_empty_options():
    assert best_match("anything", []) is None


def test_empty_strings_do_not_match_everything():
    assert similarity("", "San Francisco") == 0.0
