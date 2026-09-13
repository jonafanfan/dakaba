"""Tests for the two remaining guidance signals: dead-space tilt, and the model's placement hint.

There used to be a third signal here, `_compute_placement`'s `reason` — why the standing marker
landed where it did. The marker (and the geometry behind it) was removed: the app's only
positioning guidance is now a single free-text sentence from the vision model
(`placement_hint`), so there is no marker to explain any more. The dead-space check is unrelated
to placement and reuses the same saliency map computation used to feed it.
"""
import pathlib

import numpy as np
import pytest

from scene_analysis import _detect_dead_space


# ── dead space: aim off the empty third ──────────────────────────────────────

def band(top=0.0, mid=0.0, bottom=0.0, h=120, w=160):
    """Saliency map with independently set thirds."""
    s = np.zeros((h, w), dtype=np.float32)
    s[: h // 3] = top
    s[h // 3 : 2 * h // 3] = mid
    s[2 * h // 3 :] = bottom
    return s


def test_blank_top_says_aim_lower():
    """Empty ceiling or featureless sky above -> tilt down to cut it out."""
    result = _detect_dead_space(band(top=0.02, mid=0.9, bottom=0.8))
    assert result["direction"] == "down"
    assert "lower" in result["reason"]


def test_blank_bottom_says_aim_higher():
    result = _detect_dead_space(band(top=0.8, mid=0.9, bottom=0.02))
    assert result["direction"] == "up"
    assert "higher" in result["reason"]


def test_an_evenly_interesting_frame_is_left_alone():
    assert _detect_dead_space(band(top=0.7, mid=0.8, bottom=0.75))["direction"] == "ok"


def test_a_mildly_quieter_third_is_not_dead():
    """The threshold must not fire on ordinary variation, or it nags on every scan.

    Top carries 60% of the rest's interest — quieter, but not dead. Only well under half counts.
    """
    assert _detect_dead_space(band(top=0.48, mid=0.8, bottom=0.8))["direction"] == "ok"


def test_a_flat_map_is_not_judged():
    """On a uniform map the thirds are all noise, so the reliability gate must hold here too."""
    assert _detect_dead_space(np.full((120, 160), 0.5, dtype=np.float32))["direction"] == "ok"


def test_a_near_flat_map_is_not_judged_either():
    """The case a perfectly uniform map does not actually test.

    On a uniform map the ratio comparison already returns "ok" on its own, so it proves nothing
    about the reliability gate. Here the bands differ by a wide *ratio* (0.001 vs 0.01) but a
    trivial absolute amount, so the ratio test alone would confidently call the top dead. Only the
    gate stops it, and without one the app would nag about dead space on featureless scenes.
    """
    faint = band(top=0.001, mid=0.01, bottom=0.01)
    assert faint.std() < 0.010, "must be under the gate for this test to mean anything"
    assert _detect_dead_space(faint)["direction"] == "ok"


def test_the_deader_of_two_quiet_bands_wins():
    """When both outer thirds are quiet, the emptier one decides which way to aim.

    Top (0.02) is quiet enough to trip the threshold on its own, but the bottom (0.01) is emptier
    still — so the advice must be to aim up and cut the floor, not down. Without the comparison
    against the opposite band, whichever branch is written first would always win.
    """
    result = _detect_dead_space(band(top=0.02, mid=0.9, bottom=0.01))
    assert result["direction"] == "up"
    assert "higher" in result["reason"]


def test_ok_carries_no_reason_text():
    """The client shows reason verbatim, so 'ok' must not produce a cue."""
    assert _detect_dead_space(band(top=0.7, mid=0.8, bottom=0.75))["reason"] == ""


def test_only_one_direction_can_win():
    """Both thirds quiet relative to a busy middle: pick the deader one, never both."""
    result = _detect_dead_space(band(top=0.02, mid=0.9, bottom=0.10))
    assert result["direction"] == "down", "top is emptier, so the top is what to cut"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        np.zeros((0, 0), dtype=np.float32),
        np.zeros((10, 10, 3), dtype=np.float32),
        np.zeros((5,), dtype=np.float32),
        np.zeros((2, 10), dtype=np.float32),      # too short to have thirds
    ],
    ids=["none", "empty", "3d", "1d", "two-rows"],
)
def test_malformed_input_degrades_to_ok(bad):
    assert _detect_dead_space(bad) == {"direction": "ok", "reason": ""}


def test_direction_is_always_from_the_closed_set():
    rng = np.random.default_rng(11)
    for _ in range(40):
        s = band(rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(0, 1))
        assert _detect_dead_space(s)["direction"] in ("up", "down", "ok")


# ── placement_hint: the model's standing sentence ────────────────────────────
#
# The app's only positioning guidance, now that there is no marker: one plain-English sentence,
# e.g. "Stand next to the drawer". Free text rather than a closed set, and shown verbatim above
# the shutter — hence the validation.

from scene_analysis import _clean_hint


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Stand in front of the blue door", "Stand in front of the blue door"),
        ("Stand just behind the low wall.", "Stand just behind the low wall"),   # trailing stop
        ("  Stand   beside  the window  ", "Stand beside the window"),           # collapsed space
        ("Stand\nlevel with the bench", "Stand level with the bench"),           # newline
    ],
)
def test_usable_hints_are_kept(raw, expected):
    assert _clean_hint(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", 42, [], {}, ".", "x" * 61],
    ids=["none", "empty", "spaces", "int", "list", "dict", "just-a-stop", "too-long"],
)
def test_unusable_hints_become_empty(raw):
    """The client renders this verbatim above the shutter, so anything doubtful must vanish.
    A sentence that overflows the panel is worse than no sentence at all."""
    assert _clean_hint(raw) == ""


def test_the_length_cap_is_a_boundary_not_a_guess():
    assert _clean_hint("x" * 60) == "x" * 60
    assert _clean_hint("x" * 61) == ""


# ── client/engine agreement on the filter set ────────────────────────────────

def _page():
    return (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(
        encoding="utf-8")


def test_the_client_offers_exactly_the_filters_the_engine_can_pick():
    """Two lists, in two languages, that must stay identical.

    The engine validates `filter` against VALID_FILTERS; the client maps that name to a CSS string.
    A name the client does not know renders the photo unfiltered with no error at either end — and
    now that the picker is built from the client's map, it would also be missing from the strip.
    Silent on both sides, which is exactly why it is worth pinning.
    """
    import re

    from scene_analysis import VALID_FILTERS

    block = re.search(r"const FILTER_CSS = \{(.*?)\n    \};", _page(), re.S)
    assert block, "FILTER_CSS not found in the page"
    client_filters = re.findall(r"'([^']+)':\s*'", block.group(1))
    assert client_filters == VALID_FILTERS, (
        "client offers %s, engine can pick %s" % (client_filters, VALID_FILTERS)
    )


def test_the_picker_includes_an_off_switch():
    """Turning the filter off must be as reachable as turning it on, now that the strip is the only
    place that choice lives — the model's pick is applied by default."""
    page = _page()
    assert "const NO_FILTER = 'Original';" in page
    assert "[NO_FILTER, ...Object.keys(FILTER_CSS)]" in page, (
        "the strip must be built from the filter map, not a hand-written list that can drift"
    )
