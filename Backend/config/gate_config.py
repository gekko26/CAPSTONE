"""Configuration for the fixed, auditable gate rule layer.

Every threshold and severity used by models/decision.py lives here so the rule table
can be read in one place and reviewed without tracing code.

DECISION MODULE PURITY: models/decision.py must never read ENFORCE_GATE. Gate
enforcement stays OFF; the decision is displayed and logged, never actuated. The flag
is declared here for the future actuation layer only.
"""

# Severity assigned when the face is "fatigue" while the sensors read clear.
FATIGUE_DECISION = "WARN"

# Severity assigned when a required input (rf_class, xgb_class, face_label) is None.
# Whatever this is set to, a missing input must never be able to yield PASS.
MISSING_INPUT_DECISION = "WARN"

# BAC band edges. MUST equal the values in models/train/sensor_models.py; parity is
# enforced by a test rather than by importing sensor_models into decision.py.
BAC_NEAR_LIMIT = 0.02
BAC_OVER_LIMIT = 0.05

# Declared only. decision.py must never import or read this.
ENFORCE_GATE = False

# Reason code -> short human-readable text.
REASON_LABELS = {
    "models_disagree": "Models disagree",
    "sensor_face_mismatch": "Sensors flag alcohol, face looks sober",
    "possible_impaired": "Possible impaired",
    "near_limit": "Near limit",
    "alcohol_confirmed": "Alcohol detected, over limit",
    "bac_unavailable": "BAC unavailable",
    "fatigue": "Fatigue",
    "clear": "Clear",
}

# Severities, ordered. Used to combine independently fired rules.
SEVERITY_ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}

# Class ids predicted by both sensor classifiers.
VALID_CLASSES = (0, 1, 2)

# Face labels produced by MobileNet.
VALID_FACE_LABELS = ("sober", "fatigue", "impaired")

# Inputs that must be present for a decision to ever be PASS. est_bac is
# deliberately absent: a missing BAC estimate is handled by "bac_unavailable" and
# must never block a PASS on a clear reading.
REQUIRED_INPUTS = ("rf_class", "xgb_class", "face_label")


def label_for(code):
    """Human text for a reason code. Unknown codes render the code itself so a new
    rule can never crash a response."""
    return REASON_LABELS.get(code, code)