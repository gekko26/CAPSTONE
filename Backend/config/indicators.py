"""Indicator settings (deployment LEDs + voice cues).

Separate from gate_config.py on purpose: gate_config holds decision rules, and
indicators must never be able to influence a decision. Nothing in this file is
imported by models/decision.py.

INDICATORS_ENABLED defaults to False. With it off, the backend makes NO network call
to the ESP32 at all — not a failed one, none — so a half-wired bench cannot produce
spurious traffic. The hardware may not exist yet and everything else still works.
"""

import os

# ── Master switch. OFF by default. ───────────────────────────────────────────
INDICATORS_ENABLED = os.getenv("INDICATORS_ENABLED", "0").strip().lower() in (
    "1", "true", "yes", "on",
)

# Shared token for POST /indicator. Read from the environment ONLY — never a literal,
# never a file in the repo, never logged. Unset => the bench endpoint answers 503 and
# every indicator call is a no-op.
ESP32_DEVICE_TOKEN = os.getenv("ESP32_DEVICE_TOKEN", "") or ""

# ── Network ─────────────────────────────────────────────────────────────────
INDICATOR_TIMEOUT_S = float(os.getenv("INDICATOR_TIMEOUT_S", "1.0"))
# How many consecutive "person in front of the camera" analyses before announcing.
INDICATOR_PERSON_FRAMES = int(os.getenv("INDICATOR_PERSON_FRAMES", "3"))
# Minimum gap between two person announcements, so someone standing there is not
# greeted over and over.
INDICATOR_PERSON_COOLDOWN_S = float(os.getenv("INDICATOR_PERSON_COOLDOWN_S", "10"))
# Consecutive "nobody there" analyses before the indicator is put back to idle.
INDICATOR_PERSON_CLEAR_FRAMES = int(os.getenv("INDICATOR_PERSON_CLEAR_FRAMES", "5"))

# ── Bench (Settings -> Sensors -> Indicators) ────────────────────────────────
# States the bench may drive directly. Verdicts are deliberately absent: the bench
# cannot paint a PASS, because the firmware would refuse a verdict without a matching
# run id while ANALYZING anyway.
INDICATOR_TEST_STATES = [
    "person_detected",
    "sampling",
    "analyzing",
    "warn",
    "fail",
    "off",
]

# Voice volume for the bench only. 0..30 on the device.
INDICATOR_TEST_VOLUME = int(os.getenv("INDICATOR_TEST_VOLUME", "18"))


def indicators_enabled() -> bool:
    return INDICATORS_ENABLED


def token_configured() -> bool:
    """True only when a non-empty token is present in the environment.

    The value itself is never returned, logged or echoed anywhere.
    """
    return bool(ESP32_DEVICE_TOKEN)