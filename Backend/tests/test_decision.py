"""Tests for the fixed decision layer (models/decision.py).

Pure: no database, no files, no real models. Every case exercises the rule table
directly. The one exception is the bare-import test, which runs in a subprocess.
"""

import itertools
import math
import subprocess
import sys

import pytest

from config import gate_config
from models.decision import (
    Decision,
    decide,
    decision_to_dict,
    label_for,
    missing_inputs,
)

NAN = float("nan")


# ── Rule 1: disagreement ────────────────────────────────────────────────────────

def test_rule1_disagreement_rf_alcohol_xgb_clear():
    d = decide(rf_class=1, xgb_class=0, est_bac=None, face_label="sober")
    assert (d.status, d.reasons, d.path) == ("WARN", ("models_disagree",), "models_disagree")


def test_rule1_disagreement_rf_clear_xgb_alcohol():
    d = decide(rf_class=2, xgb_class=1, est_bac=None, face_label="impaired")
    assert d.status == "WARN"
    assert "models_disagree" in d.reasons
    assert d.path == "models_disagree"


def test_rule1_both_alcohol_is_not_disagreement():
    d = decide(rf_class=1, xgb_class=1, est_bac=0.09, face_label="impaired")
    assert "models_disagree" not in d.reasons


def test_rule1_both_clear_is_not_disagreement():
    d = decide(rf_class=0, xgb_class=2, est_bac=None, face_label="sober")
    assert "models_disagree" not in d.reasons
    assert d.status == "PASS"


# ── Rule 2a: sensors read alcohol, face does not ───────────────────────────────

@pytest.mark.parametrize("face", ["sober", "fatigue"])
def test_rule2a_sensor_face_mismatch(face):
    for bac in (0.0, 0.03, 0.09, 0.4):
        d = decide(rf_class=1, xgb_class=1, est_bac=bac, face_label=face)
        assert d.status == "WARN", (face, bac)
        assert d.reasons == ("sensor_face_mismatch",)
        assert d.path == "sensor_face_mismatch"


def test_rule2a_ignores_a_huge_bac():
    """A sober face cannot be overruled by BAC on this path."""
    d = decide(rf_class=1, xgb_class=1, est_bac=0.40, face_label="sober")
    assert d.status == "WARN"
    assert "alcohol_confirmed" not in d.reasons


# ── Rule 2b: sensors read alcohol AND face impaired ────────────────────────────

def test_rule2b_bac_over_limit_fails():
    d = decide(rf_class=1, xgb_class=1, est_bac=0.05, face_label="impaired")
    assert (d.status, d.reasons, d.path) == ("FAIL", ("alcohol_confirmed",), "alcohol_confirmed")


def test_rule2b_bac_near_limit_warns():
    d = decide(rf_class=1, xgb_class=1, est_bac=0.0499, face_label="impaired")
    assert (d.status, d.reasons, d.path) == ("WARN", ("near_limit",), "near_limit")


def test_rule2b_bac_at_near_limit_edge():
    d = decide(rf_class=1, xgb_class=1, est_bac=gate_config.BAC_NEAR_LIMIT, face_label="impaired")
    assert d.reasons == ("near_limit",)


def test_rule2b_bac_below_near_limit_possible_impaired():
    d = decide(rf_class=1, xgb_class=1, est_bac=0.0199, face_label="impaired")
    assert (d.status, d.reasons, d.path) == ("WARN", ("possible_impaired",), "possible_impaired")


def test_rule2b_bac_unavailable():
    d = decide(rf_class=1, xgb_class=1, est_bac=None, face_label="impaired")
    assert (d.status, d.reasons, d.path) == ("WARN", ("bac_unavailable",), "bac_unavailable")


def test_rule2b_invalid_bac_is_reported_as_invalid_not_ranked():
    for bad in (NAN, -1.0, -0.0001):
        d = decide(rf_class=1, xgb_class=1, est_bac=bad, face_label="impaired")
        assert d.status == "WARN", bad
        assert d.reasons == ("invalid_est_bac",), bad


def test_rule2b_infinite_bac_is_invalid():
    d = decide(rf_class=1, xgb_class=1, est_bac=float("inf"), face_label="impaired")
    assert d.reasons == ("invalid_est_bac",)


# ── Rule 3: sensors clear ──────────────────────────────────────────────────────

@pytest.mark.parametrize("cls", [0, 2])
def test_rule3a_clear_passes(cls):
    d = decide(rf_class=cls, xgb_class=cls, est_bac=None, face_label="sober")
    assert (d.status, d.reasons, d.path) == ("PASS", ("clear",), "clear")


def test_rule3b_clear_sensors_but_impaired_face_warns():
    d = decide(rf_class=0, xgb_class=0, est_bac=None, face_label="impaired")
    assert (d.status, d.reasons, d.path) == ("WARN", ("possible_impaired",), "possible_impaired")


def test_rule3b_clear_sensors_ignore_bac_even_when_over_limit():
    d = decide(rf_class=0, xgb_class=0, est_bac=0.40, face_label="impaired")
    assert d.status == "WARN"
    assert "alcohol_confirmed" not in d.reasons
    assert d.reasons == ("possible_impaired",)


def test_rule3c_clear_sensors_fatigue_face():
    d = decide(rf_class=0, xgb_class=0, est_bac=None, face_label="fatigue")
    assert (d.status, d.reasons, d.path) == ("WARN", ("fatigue",), "fatigue")


# ── Rule 4: missing inputs ─────────────────────────────────────────────────────

def test_rule4_missing_rf_class():
    d = decide(rf_class=None, xgb_class=0, est_bac=None, face_label="sober")
    assert d.status == gate_config.MISSING_INPUT_DECISION
    assert d.reasons == ("missing_rf_class",)
    assert d.path == "missing_rf_class"


def test_rule4_missing_xgb_class():
    d = decide(rf_class=0, xgb_class=None, est_bac=None, face_label="sober")
    assert d.status == gate_config.MISSING_INPUT_DECISION
    assert d.reasons == ("missing_xgb_class",)


def test_rule4_missing_face_label_never_passes():
    for rf, xgb in itertools.product([0, 1, 2], [0, 1, 2]):
        d = decide(rf_class=rf, xgb_class=xgb, est_bac=0.09, face_label=None)
        assert d.status != "PASS", (rf, xgb)
        assert "missing_face_label" in d.reasons


def test_rule4_rule2a_does_not_apply_when_face_is_none():
    """Both classifiers read alcohol but the face is unavailable: only the missing
    rule fires, never sensor_face_mismatch."""
    d = decide(rf_class=1, xgb_class=1, est_bac=0.09, face_label=None)
    assert d.reasons == ("missing_face_label",)
    assert "sensor_face_mismatch" not in d.reasons


def test_rule4_all_missing_lists_every_name():
    d = decide()
    assert d.reasons == ("missing_rf_class", "missing_xgb_class", "missing_face_label")
    assert missing_inputs(d) == ["rf_class", "xgb_class", "face_label"]


def test_rule4_missing_inputs_take_severity_over_pass():
    """A clear-looking sensor pair with a missing face cannot reach PASS."""
    d = decide(rf_class=0, xgb_class=0, est_bac=None, face_label=None)
    assert d.status == gate_config.MISSING_INPUT_DECISION


# ── Rule 5: invalid values ─────────────────────────────────────────────────────

def test_rule5_invalid_rf_class():
    d = decide(rf_class=7, xgb_class=0, est_bac=None, face_label="sober")
    assert d.status == "WARN"
    assert "invalid_rf_class" in d.reasons


def test_rule5_invalid_xgb_class():
    d = decide(rf_class=0, xgb_class=7, est_bac=None, face_label="sober")
    assert "invalid_xgb_class" in d.reasons


def test_rule5_unknown_face_label():
    d = decide(rf_class=0, xgb_class=0, est_bac=None, face_label="bogus")
    assert (d.status, d.reasons, d.path) == ("WARN", ("invalid_face_label",), "invalid_face_label")


def test_rule5_bool_is_not_a_valid_class():
    d = decide(rf_class=True, xgb_class=0, est_bac=None, face_label="sober")
    assert "invalid_rf_class" in d.reasons


def test_rule5_invalid_value_never_reaches_pass():
    for rf, xgb, face in [(7, 7, "sober"), (0, "x", "sober"), (0, 0, 5), (0, 0, "SOBER")]:
        d = decide(rf_class=rf, xgb_class=xgb, est_bac=None, face_label=face)
        assert d.status != "PASS", (rf, xgb, face)


# ── Exhaustive product + invariants ────────────────────────────────────────────

CLASSES = [0, 1, 2, None, 7]
FACES = ["sober", "fatigue", "impaired", "bogus", None]
BACS = [None, -1, NAN, 0.0, 0.0199, 0.02, 0.0499, 0.05, 0.12]

PRODUCT = list(itertools.product(CLASSES, CLASSES, BACS, FACES))


def test_product_size_is_large_enough():
    assert len(PRODUCT) == 5 * 5 * 9 * 5


def test_exhaustive_product_never_raises_and_always_valid():
    for rf, xgb, bac, face in PRODUCT:
        d = decide(rf_class=rf, xgb_class=xgb, est_bac=bac, face_label=face)
        assert isinstance(d, Decision)
        assert d.status in ("PASS", "WARN", "FAIL")
        assert isinstance(d.reasons, tuple) and d.reasons
        assert d.path in d.reasons


def test_bac_alone_invariant():
    """Outside the single BAC-sensitive path, the result must not depend on est_bac
    at all — including for NaN and negative values."""
    checked = 0
    for rf, xgb, bac, face in PRODUCT:
        if rf == 1 and xgb == 1 and face == "impaired":
            continue
        baseline = decide(rf_class=rf, xgb_class=xgb, est_bac=bac, face_label=face)
        for other in BACS:
            got = decide(rf_class=rf, xgb_class=xgb, est_bac=other, face_label=face)
            assert (got.status, got.reasons, got.path) == \
                   (baseline.status, baseline.reasons, baseline.path), \
                   (rf, xgb, face, bac, other)
        checked += 1
    assert checked > 0


def test_fail_requires_alcohol_sensors_and_impaired_face():
    for rf, xgb, bac, face in PRODUCT:
        d = decide(rf_class=rf, xgb_class=xgb, est_bac=bac, face_label=face)
        if d.status == "FAIL":
            assert rf == 1 and xgb == 1 and face == "impaired", (rf, xgb, bac, face)
            assert bac is not None and bac >= gate_config.BAC_OVER_LIMIT, (rf, xgb, bac, face)


def test_failsafe_missing_input_never_passes():
    for rf, xgb, bac, face in PRODUCT:
        d = decide(rf_class=rf, xgb_class=xgb, est_bac=bac, face_label=face)
        if rf is None or xgb is None or face is None:
            assert d.status != "PASS", (rf, xgb, bac, face)


def test_monotonicity_on_the_alcohol_path():
    """Raising est_bac with an impaired face must never lower severity."""
    order = ["PASS", "WARN", "FAIL"]
    ladder = [0.0, 0.0199, gate_config.BAC_NEAR_LIMIT, 0.0499, gate_config.BAC_OVER_LIMIT, 0.4]
    severities = [order.index(decide(1, 1, b, "impaired").status) for b in ladder]
    assert severities == sorted(severities), list(zip(ladder, severities))


def test_most_severe_wins_and_ties_break_on_evaluation_order():
    # disagreement (rule 1) outranks a clear-reading rule (rule 3) on a tie
    d = decide(rf_class=0, xgb_class=1, est_bac=None, face_label="sober")
    assert d.status == "WARN"
    assert d.path == "models_disagree"
    assert d.reasons == ("models_disagree",)


def test_decision_is_frozen():
    d = decide(rf_class=0, xgb_class=0, est_bac=None, face_label="sober")
    with pytest.raises(Exception):
        d.status = "FAIL"


# ── Config-driven severity ─────────────────────────────────────────────────────

def test_fatigue_decision_config_is_followed(monkeypatch):
    assert gate_config.FATIGUE_DECISION == "WARN"
    assert decide(0, 0, None, "fatigue").status == "WARN"
    monkeypatch.setattr(gate_config, "FATIGUE_DECISION", "FAIL")
    assert decide(0, 0, None, "fatigue").status == "FAIL"


def test_missing_input_decision_config_is_followed(monkeypatch):
    assert gate_config.MISSING_INPUT_DECISION == "WARN"
    assert decide(None, 0, None, "sober").status == "WARN"
    monkeypatch.setattr(gate_config, "MISSING_INPUT_DECISION", "FAIL")
    assert decide(None, 0, None, "sober").status == "FAIL"
    # and it still outranks a PASS-producing rule set
    assert decide(0, 0, None, None).status == "FAIL"


def test_enforce_gate_is_declared_but_false():
    assert gate_config.ENFORCE_GATE is False


def test_decision_module_never_reads_enforce_gate():
    """Flipping ENFORCE_GATE must not change a single result."""
    before = [decide(rf, xgb, b, f) for rf, xgb, b, f in PRODUCT]
    original = gate_config.ENFORCE_GATE
    try:
        gate_config.ENFORCE_GATE = True
        after = [decide(rf, xgb, b, f) for rf, xgb, b, f in PRODUCT]
    finally:
        gate_config.ENFORCE_GATE = original
    assert [(d.status, d.reasons, d.path) for d in before] == \
           [(d.status, d.reasons, d.path) for d in after]


def test_source_never_references_enforce_gate():
    """AST-level check: no name or attribute in decision.py can reach ENFORCE_GATE.

    A raw text grep would false-positive on the module docstring, which mentions
    the flag to document that it must stay unused.
    """
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "models" / "decision.py"
    tree = ast.parse(src.read_text())
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "ENFORCE_GATE":
            offenders.append(node.lineno)
        if isinstance(node, ast.Attribute) and node.attr == "ENFORCE_GATE":
            offenders.append(node.lineno)
    assert offenders == [], f"decision.py references ENFORCE_GATE at lines {offenders}"


# ── Constants parity ───────────────────────────────────────────────────────────

def test_bac_constants_match_sensor_models():
    from models.train import sensor_models
    assert gate_config.BAC_NEAR_LIMIT == sensor_models.BAC_NEAR_LIMIT
    assert gate_config.BAC_OVER_LIMIT == sensor_models.BAC_OVER_LIMIT


# ── Serialisation helpers ──────────────────────────────────────────────────────

def test_decision_to_dict_shape():
    d = decide(rf_class=0, xgb_class=0, est_bac=None, face_label="sober")
    out = decision_to_dict(d)
    assert set(out) == {"status", "reasons", "labels", "path"}
    assert out["status"] == "PASS"
    assert out["labels"] == ["Clear"]
    assert out["path"] == "clear"


def test_label_for_known_and_unknown_codes():
    assert label_for("models_disagree") == "Models disagree"
    assert label_for("totally_new_code") == "totally_new_code"


# ── Bare-import purity ─────────────────────────────────────────────────────────

def test_bare_import_pulls_no_heavy_dependencies():
    """Importing models.decision must not load tensorflow, cv2, sqlalchemy or joblib."""
    code = (
        "import sys;"
        "sys.path.insert(0, '.');"
        "import models.decision;"
        "bad = [m for m in ('tensorflow','cv2','sqlalchemy','joblib','keras') "
        "if m in sys.modules];"
        "print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"decision.py pulled in: {out.stdout.strip()}"