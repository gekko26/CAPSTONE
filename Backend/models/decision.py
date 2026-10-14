"""Fixed, auditable decision layer for the AlcoDetect gate.

Pure function module. It imports nothing from routes, the database, cv2, tensorflow,
joblib or sqlalchemy, and it never reads gate_config.ENFORCE_GATE. The learned fusion
model is retired from the live path; every outcome here comes from an explicit rule.

Design principle
----------------
``est_bac`` alone must NEVER produce a FAIL or a WARN, and must never turn a WARN
into a PASS. It is consulted on exactly one path: the sensors both read breath alcohol
AND the face reads impaired. Everywhere else it is ignored, including for validity
checks, so a NaN or negative estimate on an unrelated path cannot perturb the result.
"""

import math
import numbers
from dataclasses import dataclass

from config import gate_config
from config.gate_config import label_for

# Referenced through the module (gate_config.X) rather than imported by value, so
# the config file stays the single source of truth at call time: editing a constant
# there changes behaviour without touching this file.

__all__ = ["Decision", "decide", "label_for", "decision_to_dict", "missing_inputs"]


@dataclass(frozen=True)
class Decision:
    """Immutable outcome. ``path`` is the reason code of the most severe rule that
    fired, ties broken by rule evaluation order. ``reasons`` lists every rule that
    fired, in evaluation order."""

    status: str
    reasons: tuple
    path: str


# ── Total validators. None of these may raise, whatever they are handed. ──────────

def _is_valid_class(value):
    """A sensor class is an integral 0/1/2. bool is rejected: True is not class 1."""
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        return False
    try:
        return int(value) in gate_config.VALID_CLASSES
    except Exception:
        return False


def _is_valid_face(label):
    if not isinstance(label, str):
        return False
    try:
        return label in gate_config.VALID_FACE_LABELS
    except Exception:
        return False


def _is_valid_bac(value):
    """A BAC estimate must be a real, finite, non-negative number."""
    if value is None or isinstance(value, bool) or not isinstance(value, numbers.Real):
        return False
    try:
        as_float = float(value)
    except Exception:
        return False
    try:
        if math.isnan(as_float) or math.isinf(as_float):
            return False
    except Exception:
        return False
    return as_float >= 0.0


def _is_bac_alcohol(value):
    """Numeric comparison, guarded so a hostile __float__ cannot raise."""
    try:
        return float(value) >= gate_config.BAC_OVER_LIMIT
    except Exception:
        return False


def _is_bac_near(value):
    try:
        return gate_config.BAC_NEAR_LIMIT <= float(value) < gate_config.BAC_OVER_LIMIT
    except Exception:
        return False


# ── The rule table ───────────────────────────────────────────────────────────────

def decide(rf_class=None, xgb_class=None, est_bac=None, face_label=None):
    """Evaluate every rule independently and combine them.

    ``None`` means "unavailable". It is never replaced by a substitute class, so a
    missing model can never masquerade as a clear reading.
    """
    fired = []

    def fire(code):
        fired.append(code)

    rf_ok = rf_class is not None
    xgb_ok = xgb_class is not None
    face_ok = face_label is not None

    # Rule 4 first in *detection* but last in evaluation order, so a concrete rule
    # such as sensor_face_mismatch wins the `path` tie-break over missing_*.
    # --- Rule 1: DISAGREEMENT -------------------------------------------------
    rf_is_alcohol = _is_valid_class(rf_class) and int(rf_class) == 1
    xgb_is_alcohol = _is_valid_class(xgb_class) and int(xgb_class) == 1
    if rf_ok and xgb_ok and rf_is_alcohol != xgb_is_alcohol:
        fire("models_disagree")

    # --- Rule 2: SENSOR ALCOHOL (both classifiers read breath alcohol) --------
    if rf_is_alcohol and xgb_is_alcohol:
        if face_ok and _is_valid_face(face_label) and face_label in ("sober", "fatigue"):
            # est_bac is IGNORED here: a sober or fatigued face cannot be overruled
            # by a BAC estimate.
            fire("sensor_face_mismatch")
        elif face_ok and _is_valid_face(face_label) and face_label == "impaired":
            # The one and only path where est_bac may influence the outcome.
            if est_bac is None:
                fire("bac_unavailable")
            elif not _is_valid_bac(est_bac):
                fire("invalid_est_bac")
            elif _is_bac_alcohol(est_bac):
                fire("alcohol_confirmed")
            elif _is_bac_near(est_bac):
                fire("near_limit")
            else:
                fire("possible_impaired")

    # --- Rule 3: SENSOR CLEAR (neither classifier read breath alcohol) ---------
    rf_clear = _is_valid_class(rf_class) and int(rf_class) in (0, 2)
    xgb_clear = _is_valid_class(xgb_class) and int(xgb_class) in (0, 2)
    if rf_clear and xgb_clear:
        if face_ok and _is_valid_face(face_label):
            if face_label == "sober":
                fire("clear")
            elif face_label == "impaired":
                fire("possible_impaired")
            elif face_label == "fatigue":
                fire("fatigue")

    # --- Rule 4: MISSING INPUTS ----------------------------------------------
    for name in gate_config.REQUIRED_INPUTS:
        value = {"rf_class": rf_class, "xgb_class": xgb_class,
                 "face_label": face_label}[name]
        if value is None:
            fire(f"missing_{name}")

    # --- Rule 5: INVALID VALUES ----------------------------------------------
    # est_bac validity is checked only inside rule 2b, so an unusable estimate can
    # never add a WARN on any other path.
    if rf_ok and not _is_valid_class(rf_class):
        fire("invalid_rf_class")
    if xgb_ok and not _is_valid_class(xgb_class):
        fire("invalid_xgb_class")
    if face_ok and not _is_valid_face(face_label):
        fire("invalid_face_label")

    if not fired:
        # Unreachable via the public signature, but a decision must always exist.
        return Decision(status="WARN", reasons=("missing_rf_class",), path="missing_rf_class")

    status = "PASS"
    for code in fired:
        code_status = _status_for(code)
        if gate_config.SEVERITY_ORDER[code_status] > gate_config.SEVERITY_ORDER[status]:
            status = code_status
    # Fail-safe clamp: a run that is missing a required input must never be able to
    # finish at PASS. Only applied when a missing_* rule actually fired, so a fully
    # available clear reading still passes.
    if any(code.startswith("missing_") for code in fired):
        if gate_config.SEVERITY_ORDER[gate_config.MISSING_INPUT_DECISION] > \
                gate_config.SEVERITY_ORDER[status]:
            status = gate_config.MISSING_INPUT_DECISION

    # path = reason of the most severe rule that fired; ties break on first fired.
    def rank(code):
        return (-gate_config.SEVERITY_ORDER[_status_for(code)], fired.index(code))

    path = min(fired, key=rank)
    return Decision(status=status, reasons=tuple(fired), path=path)


_CODE_STATUS = {
    "clear": "PASS",
    "sensor_face_mismatch": "WARN",
    "possible_impaired": "WARN",
    "near_limit": "WARN",
    "bac_unavailable": "WARN",
    "models_disagree": "WARN",
    "invalid_rf_class": "WARN",
    "invalid_xgb_class": "WARN",
    "invalid_face_label": "WARN",
    "invalid_est_bac": "WARN",
    "alcohol_confirmed": "FAIL",
}


def _status_for(code):
    """Severity a single reason code contributes."""
    if code == "fatigue":
        return gate_config.FATIGUE_DECISION
    if code.startswith("missing_"):
        return gate_config.MISSING_INPUT_DECISION
    try:
        return _CODE_STATUS[code]
    except KeyError:
        return "WARN"


def decision_to_dict(decision):
    """JSON-ready form. ``labels`` is the human text for every fired reason."""
    return {
        "status": decision.status,
        "reasons": list(decision.reasons),
        "labels": [label_for(code) for code in decision.reasons],
        "path": decision.path,
    }


def missing_inputs(decision):
    """Names of the required inputs that were unavailable, in REQUIRED_INPUTS order."""
    codes = set(decision.reasons)
    return [name for name in gate_config.REQUIRED_INPUTS if f"missing_{name}" in codes]