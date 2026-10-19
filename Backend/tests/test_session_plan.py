"""Tests for the pure session-plan helpers.

No database, no HTTP, no filesystem: these drive build_session_plan and
baseline_stability directly, so the rules are pinned without any I/O.

The event-key mapping is checked against the REAL collect_* endpoints rather than
a hand-written list, so a change to an endpoint that creates a different
(label, sub_label) cannot silently fall through the plan.
"""

import ast
import pathlib

import pytest

from models.train.session_plan import (
    BAC_BANDS,
    EVENT_KEYS,
    PER_PERSON_QUOTA,
    bac_band_for_value,
    baseline_stability,
    build_session_plan,
    event_key_for_row,
)

ROUTING = pathlib.Path(__file__).resolve().parents[1] / "routes" / "training.py"


class Row:
    def __init__(self, label, sub_label=None, subject_id=None, bac=None,
                 mq3_1_avg=None, mq3_2_avg=None, mq3_3_avg=None, id=None, date=None):
        self.label = label
        self.sub_label = sub_label
        self.subject_id = subject_id
        self.bac = bac
        self.mq3_1_avg = mq3_1_avg
        self.mq3_2_avg = mq3_2_avg
        self.mq3_3_avg = mq3_3_avg
        self.id = id
        self.date = date


class Subject:
    def __init__(self, sid, name):
        self.id = sid
        self.name = name


# ── the event-key mapping, derived from the real endpoints ─────────────────────

def _collect_endpoints():
    """Extract (endpoint, label, sub_label) from the shipped collect_* routes by AST.

    Both row-construction shapes are resolved: the direct
    ``TrainingData(label=..., sub_label=...)`` and the shared
    ``build_pending_row(label=..., sub_label=...)``. Missing either would silently
    skip an endpoint and leave its contract untested.
    """
    src = ROUTING.read_text()
    tree = ast.parse(src)
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        endpoint = None
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if not (isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router"):
                continue
            if dec.args and isinstance(dec.args[0], ast.Constant):
                candidate = str(dec.args[0].value)
                if candidate.startswith("/collect/") and candidate != "/collect/sensor-data":
                    endpoint = candidate
        if endpoint is None:
            continue

        label = sub = None
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            if getattr(call.func, "id", "") not in ("TrainingData", "build_pending_row"):
                continue
            for kw in call.keywords:
                if kw.arg == "label" and isinstance(kw.value, ast.Constant):
                    label = kw.value.value
                elif kw.arg == "sub_label" and isinstance(kw.value, ast.Constant):
                    sub = kw.value.value
        found[endpoint] = (label, sub)
    return found


def test_every_collect_endpoint_maps_to_exactly_one_event_key():
    """Each shipped collect_* endpoint must produce a row the plan recognises.

    A new endpoint, or an endpoint whose sub_label changed, would otherwise be
    invisible to the session plan -- the operator would see a quota that never
    moves and no error.
    """
    endpoints = _collect_endpoints()
    assert len(endpoints) == 7, f"expected the 7 capture endpoints, found {endpoints}"

    for endpoint, (label, sub) in endpoints.items():
        assert label is not None, f"{endpoint}: could not resolve its label"
        key = event_key_for_row(label, sub)
        assert key in EVENT_KEYS, (
            f"{endpoint} creates (label={label}, sub_label={sub!r}) which maps to "
            f"{key!r}, not one of {EVENT_KEYS}")


@pytest.mark.parametrize("label,sub,expected", [
    (0, "clear_air", "clear_air"),
    (0, None, "sober"),
    (0, "drowsy", "fatigue"),
    (0, "yawning", "fatigue"),
    (1, None, "alcohol"),
    (1, "anything", "alcohol"),
    (2, "sanitizer", "sanitizer"),
    (2, "perfume", "perfume"),
    (-1, None, None),        # pending row, not a capture
    (None, None, None),
    (0, "unknown_label", None),
    (2, None, None),          # label 2 with no sub_label is not a known event
])
def test_event_key_mapping(label, sub, expected):
    assert event_key_for_row(label, sub) == expected


def test_drowsy_and_yawning_share_the_fatigue_quota():
    rows = [Row(0, "drowsy", 1), Row(0, "yawning", 1), Row(0, "drowsy", 1)]
    plan = build_session_plan(rows, [Subject(1, "A")])
    assert plan["people"][0]["counts"]["fatigue"] == 3
    assert plan["people"][0]["counts"]["sober"] == 0


# ── BAC bands at the boundaries ───────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (0.0199, "below_near"),
    (0.02, "near"),        # inclusive lower edge
    (0.0499, "near"),
    (0.05, "over"),       # inclusive lower edge
    (0.0, "below_near"),
    (None, None),
    ("not a number", None),
])
def test_bac_band_boundaries(value, expected):
    assert bac_band_for_value(value) == expected


def test_alcohol_bands_are_counted_per_person_and_overall():
    rows = [
        Row(1, None, 1, bac=0.01), Row(1, None, 1, bac=0.0199),   # below_near x2
        Row(1, None, 1, bac=0.02), Row(1, None, 1, bac=0.0499),   # near x2
        Row(1, None, 1, bac=0.05), Row(1, None, 1, bac=0.07),     # over x2
        Row(1, None, 2, bac=0.03),                                # other person
        Row(1, None, None, bac=0.03),                             # untagged
    ]
    plan = build_session_plan(rows, [Subject(1, "A"), Subject(2, "B")])
    a, b = plan["people"]
    assert a["alcohol_bands"] == {"below_near": 2, "near": 2, "over": 2}
    assert b["alcohol_bands"] == {"below_near": 0, "near": 1, "over": 0}
    # near: 0.02 + 0.0499 (subject 1) + 0.03 (subject 2) + 0.03 (untagged) = 4
    assert plan["overall"]["alcohol_bands"] == {"below_near": 2, "near": 4, "over": 2}


# ── build_session_plan behaviour ──────────────────────────────────────────────

def test_remaining_is_never_negative():
    """Over-quota is finished work, not negative debt that would re-suggest it."""
    rows = [Row(0, "clear_air", 1) for _ in range(25)]     # quota is 10
    plan = build_session_plan(rows, [Subject(1, "A")])
    person = plan["people"][0]
    assert person["counts"]["clear_air"] == 25
    assert person["remaining"]["clear_air"] == 0
    for key in EVENT_KEYS:
        assert person["remaining"][key] >= 0, f"{key} went negative"
    assert person["next_suggested"] != "clear_air"


def test_next_suggested_picks_the_largest_remaining_share():
    #    remaining/quota
    # sober      10 -> 1.0   (0 done of 10)
    # clear_air  10 -> 0.8   (2 done of 10)
    # alcohol    10 -> 0.7   (3 done of 10)
    rows = ([Row(0, "clear_air", 1)] * 2 + [Row(1, None, 1)] * 3)
    plan = build_session_plan(rows, [Subject(1, "A")])
    assert plan["people"][0]["next_suggested"] == "sober"


def test_next_suggested_tie_breaks_to_the_earliest_event_key():
    """A tie must be deterministic, otherwise the chip flickers between events."""
    # Two keys left with an identical remaining share; the earlier key must win.
    quotas = {"clear_air": 10, "sober": 10}
    rows = [Row(0, "clear_air", 1), Row(0, None, 1)]     # 9/10 remaining each
    plan = build_session_plan(rows, [Subject(1, "A")], quotas=quotas)
    assert plan["people"][0]["next_suggested"] == "clear_air"

    # With nothing captured at all EVERY key ties at 1.0, and the whole map is stable.
    empty = build_session_plan([], [Subject(1, "A")])
    assert empty["people"][0]["next_suggested"] == EVENT_KEYS[0]
    # ...and repeating it gives the same answer.
    assert build_session_plan([], [Subject(1, "A")])["people"][0]["next_suggested"] \
        == empty["people"][0]["next_suggested"]


def test_next_suggested_prefers_a_higher_share_over_an_earlier_key():
    """Tie-break is only consulted on a genuine tie, not as a blanket preference."""
    rows = [Row(0, "clear_air", 1)] * 10        # clear_air finished (0.0 share)
    plan = build_session_plan(rows, [Subject(1, "A")])
    assert plan["people"][0]["next_suggested"] != "clear_air"


def test_two_subjects_are_independent():
    rows = [Row(0, "clear_air", 1)] * 10          # A: clear_air done
    plan = build_session_plan(rows, [Subject(1, "A"), Subject(2, "B")])
    a, b = plan["people"]
    assert a["remaining"]["clear_air"] == 0
    assert b["remaining"]["clear_air"] == PER_PERSON_QUOTA["clear_air"]
    assert b["next_suggested"] == "clear_air"


def test_rows_without_a_subject_count_only_as_untagged():
    rows = [Row(0, "clear_air", None) for _ in range(4)]
    plan = build_session_plan(rows, [Subject(1, "A")])
    assert plan["overall"]["untagged_rows"] == 4
    assert plan["people"][0]["counts"]["clear_air"] == 0, "untagged rows leaked into a person"
    # but they DO count toward the dataset sensor-class total
    assert plan["overall"]["rows_per_sensor_class"]["0"] == 4


def test_completion_fraction_and_sensor_class_totals():
    rows = ([Row(0, "clear_air", 1)] * 10 + [Row(0, None, 1)] * 5
            + [Row(1, None, 1)] * 3 + [Row(2, "sanitizer", 1)])
    plan = build_session_plan(rows, [Subject(1, "A")])
    person = plan["people"][0]
    total_quota = sum(PER_PERSON_QUOTA.values())
    done = sum(min(person["counts"][k], PER_PERSON_QUOTA[k]) for k in EVENT_KEYS)
    assert person["completion_fraction"] == round(done / total_quota, 4)
    classes = plan["overall"]["rows_per_sensor_class"]
    assert classes["0"] == 15          # clear_air + sober
    assert classes["1"] == 3           # alcohol
    assert classes["2"] == 1           # sanitizer


def test_empty_inputs_are_safe():
    plan = build_session_plan([], [])
    assert plan["people"] == []
    assert plan["overall"]["untagged_rows"] == 0
    assert plan["overall"]["subjects_enrolled"] == 0


# ── baseline_stability ────────────────────────────────────────────────────────

def _clear(vals, sid=None):
    return [Row(0, "clear_air", sid, mq3_1_avg=v, mq3_2_avg=v, mq3_3_avg=v)
            for v in vals]


def test_tight_values_are_stable():
    result = baseline_stability(_clear([63, 66, 69, 64, 67, 65]))
    assert result["status"] == "stable"
    assert result["n_used"] == 6
    assert result["dead_channels"] == []


def test_the_observed_bimodal_pattern_is_unstable():
    """The exact pattern reported: a low cluster mixed with a high one."""
    rows = _clear([63, 69, 66]) + _clear([326, 722, 110])
    result = baseline_stability(rows)
    assert result["status"] == "unstable"
    assert result["message"] == ("Baseline unstable: purge the funnel and wait for the "
                                 "sensors to settle between captures")
    assert max(v for v in result["cv_per_channel"].values() if v is not None) > 0.30


def test_fewer_than_three_rows_is_insufficient_data():
    assert baseline_stability(_clear([50, 51]))["status"] == "insufficient_data"
    assert baseline_stability([])["status"] == "insufficient_data"


def test_a_channel_stuck_at_zero_is_flagged_as_a_dead_sensor():
    """A constant zero has CV 0 and would otherwise look like the STABLE channel."""
    rows = [Row(0, "clear_air", mq3_1_avg=0.0, mq3_2_avg=50, mq3_3_avg=50) for _ in range(4)]
    result = baseline_stability(rows)
    assert result["status"] == "unstable"
    assert result["dead_channels"] == ["mq3_1_avg"]
    assert "look disconnected" in result["message"]


def test_rows_without_sensor_data_are_ignored_not_counted():
    rows = [Row(0, "clear_air", mq3_1_avg=None, mq3_2_avg=None, mq3_3_avg=None),
            Row(0, "clear_air", mq3_1_avg=0.0, mq3_2_avg=0.0, mq3_3_avg=0.0),
            Row(0, "clear_air", mq3_1_avg=0.0, mq3_2_avg=0.0, mq3_3_avg=0.0),
            Row(0, "clear_air", mq3_1_avg=0.0, mq3_2_avg=0.0, mq3_3_avg=0.0)]
    result = baseline_stability(rows)
    assert result["n_used"] == 3, "a row with NULL averages must not count as usable"


def test_only_the_last_n_clear_air_rows_are_used():
    """An early bad block must not condemn a now-settled baseline."""
    # The early block must clear BASELINE_MAX_CV on its own, otherwise "stable" below
    # proves nothing about *which* end of the list was read.
    bad = _clear([150, 1400, 220, 1600, 300, 1850])
    good = _clear([63, 66, 69, 64, 67, 65])
    assert baseline_stability(bad, n=6)["status"] == "unstable", "fixture must be genuinely unstable"
    assert baseline_stability(good, n=6)["status"] == "stable"
    assert baseline_stability(bad + good, n=6)["status"] == "stable"
    assert baseline_stability(bad + good, n=12)["status"] == "unstable"


def test_baseline_is_advisory_and_never_raises_on_junk():
    for rows in ([None], [object()], [Row(0, "clear_air", mq3_1_avg="x")]):
        out = baseline_stability(rows)
        assert out["status"] in ("stable", "unstable", "insufficient_data")
    assert BAC_BANDS == ("below_near", "near", "over")