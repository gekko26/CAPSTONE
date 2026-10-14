"""Tests for the pure identity-matching logic (no DeepFace / cv2 / TF / SQLAlchemy)."""

import math

import pytest

from models.train.identity_matching import (
    IdentitySmoother, confidence_for_distance, decide_identity,
)

THRESHOLD = 0.32
MARGIN = 0.08


# ── decide_identity ───────────────────────────────────────────────────────────

def test_no_candidates_is_unknown():
    assert decide_identity([], THRESHOLD, MARGIN)[:2] == (None, "no_candidates")


def test_single_close_match_wins():
    assert decide_identity([("a", 0.10)], THRESHOLD, MARGIN)[:2] == ("a", "matched")


def test_single_far_match_is_unknown():
    assert decide_identity([("a", 0.90)], THRESHOLD, MARGIN)[:2] == (None, "above_threshold")


def test_two_people_clear_gap_best_wins():
    name, reason, _ = decide_identity([("a", 0.10), ("b", 0.45)], THRESHOLD, MARGIN)
    assert (name, reason) == ("a", "matched")


def test_two_people_within_margin_is_ambiguous():
    name, reason, _ = decide_identity([("a", 0.30), ("b", 0.32)], THRESHOLD, MARGIN)
    assert (name, reason) == (None, "ambiguous")


def test_same_person_many_photos_uses_minimum_and_is_not_ambiguous():
    """Four photos of one person plus a rival must NOT read as ambiguous.

    The person's own photos aggregate to their minimum, so they are judged against
    each other exactly once — never counted as rivals of themselves.
    """
    candidates = [("a", 0.40), ("a", 0.11), ("a", 0.30), ("a", 0.12), ("b", 0.20)]
    name, reason, info = decide_identity(candidates, THRESHOLD, MARGIN)
    assert name == "a"
    assert reason == "matched"
    assert info["best_distance"] == pytest.approx(0.11)
    assert info["usable_people"] == 2


def test_one_person_gallery_applies_threshold_only():
    """A single enrolled person can never be ambiguous — only the threshold decides."""
    for d in (0.05, 0.20, 0.31):
        name, reason, _ = decide_identity([("solo", d)], THRESHOLD, MARGIN)
        assert (name, reason) == ("solo", "matched"), d
    name, reason, _ = decide_identity([("solo", 0.33)], THRESHOLD, MARGIN)
    assert (name, reason) == (None, "above_threshold")


def test_boundary_at_threshold_is_accepted():
    """Exactly at the threshold is still a match; one step above is not."""
    assert decide_identity([("a", THRESHOLD)], THRESHOLD, MARGIN)[:2] == ("a", "matched")
    assert decide_identity([("a", THRESHOLD + 1e-9)], THRESHOLD, MARGIN)[:2] == \
        (None, "above_threshold")


def test_boundary_at_margin_is_accepted_as_distinguishable():
    """The rule is `gap < margin` -> ambiguous, so gap == margin is ACCEPTED.

    A gap of exactly the margin means the two people are separated by at least the
    required amount, so the winner is reported. Anything strictly below the margin
    is refused.
    """
    a = 0.20
    at_margin = a + MARGIN
    assert decide_identity([("a", a), ("b", at_margin)], THRESHOLD, MARGIN)[:2] == \
        ("a", "matched")
    just_under = a + MARGIN - 1e-6
    assert decide_identity([("a", a), ("b", just_under)], THRESHOLD, MARGIN)[:2] == \
        (None, "ambiguous")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, None, "x"])
def test_unusable_distances_are_ignored(bad):
    name, reason, _ = decide_identity([("a", bad)], THRESHOLD, MARGIN)
    assert (name, reason) == (None, "no_candidates")


def test_unusable_distances_mixed_with_a_good_one():
    name, _, _ = decide_identity([("a", float("nan")), ("a", 0.11)], THRESHOLD, MARGIN)
    assert name == "a"


def test_none_person_is_ignored():
    assert decide_identity([(None, 0.1), ("", 0.1)], THRESHOLD, MARGIN)[:2] == \
        (None, "no_candidates")


def test_malformed_pairs_do_not_raise():
    assert decide_identity([("a",), "junk", None], THRESHOLD, MARGIN)[:2] == \
        (None, "no_candidates")


# ── properties ────────────────────────────────────────────────────────────────

def test_property_adding_a_worse_candidate_never_changes_the_winner():
    base = [("a", 0.10), ("b", 0.40)]
    winner, _, _ = decide_identity(base, THRESHOLD, MARGIN)
    for extra in (("c", 0.45), ("d", 0.60), ("e", 0.9)):
        w2, _, _ = decide_identity(base + [extra], THRESHOLD, MARGIN)
        assert w2 == winner


def test_property_lowering_the_threshold_never_turns_unknown_into_a_name():
    candidates = [("a", 0.50)]
    seen = set()
    for t in (0.10, 0.30, 0.49, 0.51, 0.9, 2.0):
        name, _, _ = decide_identity(candidates, t, MARGIN)
        seen.add(name is not None)
    # monotone: once it becomes a name it must never go back to unknown
    order = [0.10, 0.30, 0.49, 0.51, 0.9, 2.0]
    flags = [decide_identity(candidates, t, MARGIN)[0] is not None for t in order]
    assert flags == sorted(flags), flags


def test_confidence_is_monotonic_and_bounded():
    assert confidence_for_distance(0.0, THRESHOLD) == 1.0
    assert confidence_for_distance(THRESHOLD, THRESHOLD) == 0.0
    assert confidence_for_distance(THRESHOLD * 2, THRESHOLD) == 0.0
    mid = confidence_for_distance(THRESHOLD / 2, THRESHOLD)
    assert 0.0 < mid < 1.0
    assert confidence_for_distance(0.1, THRESHOLD) > confidence_for_distance(0.2, THRESHOLD)


def test_confidence_handles_junk():
    assert confidence_for_distance(float("nan"), THRESHOLD) == 0.0
    assert confidence_for_distance(None, THRESHOLD) == 0.0
    assert confidence_for_distance(0.1, 0) == 0.0


# ── IdentitySmoother ──────────────────────────────────────────────────────────

def _feed(smoother, seq, start=0):
    out = []
    for i, (name, reason) in enumerate(seq, start=start):
        out.append(smoother.update(i, name, reason))
    return out


def test_stable_name_only_after_n_consecutive_frames():
    s = IdentitySmoother(consecutive=3)
    out = _feed(s, [("a", None)] * 3)
    assert [o["identified"] for o in out] == [False, False, True]
    assert out[-1]["name"] == "a"


def test_single_frame_never_identifies():
    s = IdentitySmoother(consecutive=3)
    out = _feed(s, [("a", None)] * 2)
    assert not any(o["identified"] for o in out)


def test_flicker_between_two_names_stays_unknown():
    s = IdentitySmoother(consecutive=3)
    out = _feed(s, [("a", None), ("b", None), ("a", None), ("b", None),
                    ("a", None), ("b", None), ("a", None), ("b", None)])
    assert not any(o["identified"] for o in out)


def test_unknown_frame_breaks_the_run():
    s = IdentitySmoother(consecutive=3)
    out = _feed(s, [("a", None), ("a", None), (None, "unknown"),
                    ("a", None), ("a", None)])
    assert not any(o["identified"] for o in out)


def test_two_no_face_frames_clear_the_state():
    s = IdentitySmoother(consecutive=3)
    _feed(s, [("a", None), ("a", None), ("a", None)])
    assert s.update(3, None, "no_face")["identified"] is False
    s.update(4, None, "no_face")
    # state was cleared, so recovery must earn 3 frames from scratch again
    out = _feed(s, [("a", None), ("a", None), ("a", None)], start=5)
    assert [o["identified"] for o in out] == [False, False, True]


def test_one_no_face_frame_is_tolerated_as_grace_but_still_breaks_the_run():
    s = IdentitySmoother(consecutive=3)
    out = _feed(s, [("a", None), ("a", None), (None, "too_small"), ("a", None)])
    assert not any(o["identified"] for o in out)
    assert out[-1]["run"] == 1


def test_too_small_also_counts_as_a_break():
    s = IdentitySmoother(consecutive=3)
    out = _feed(s, [("a", None), (None, "too_small"), (None, "too_small")])
    breaks = out[1:]
    assert all(o["reason"] == "too_small" for o in breaks)
    assert all(o["name"] is None and o["run"] == 0 for o in breaks)
    assert not any(o["identified"] for o in out)


def test_switching_name_requires_the_full_run_again():
    s = IdentitySmoother(consecutive=3)
    _feed(s, [("a", None)] * 3)
    out = _feed(s, [("b", None), ("b", None), ("b", None)], start=3)
    assert [o["identified"] for o in out] == [False, False, True]
    assert out[-1]["name"] == "b"


def test_never_identifies_a_face_that_is_not_there():
    s = IdentitySmoother(consecutive=3)
    out = _feed(s, [(None, "no_face")] * 10)
    assert not any(o["identified"] for o in out)
    assert all(o["name"] is None for o in out)


def test_reset_clears_everything():
    s = IdentitySmoother(consecutive=3)
    _feed(s, [("a", None)] * 3)
    s.reset()
    assert s.update(99, "a", None)["identified"] is False


def test_deterministic_across_runs():
    seq = [("a", None), ("a", None), ("a", None), (None, "no_face"),
           ("b", None), ("b", None), ("b", None), ("b", None)]
    runs = []
    for _ in range(3):
        s = IdentitySmoother(consecutive=3)
        runs.append([o["identified"] for o in _feed(s, seq)])
    assert runs[0] == runs[1] == runs[2]


def test_window_is_defined_relative_to_consecutive():
    s = IdentitySmoother(consecutive=4)
    assert s.window == 8
    out = _feed(s, [("a", None)] * 4)
    assert out[-1]["identified"] is True
