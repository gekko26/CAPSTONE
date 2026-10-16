"""Indicator client — pushes display state to the ESP32.

HARD RULES this module enforces
------------------------------
* Never raises into a caller. Every public function swallows everything and logs a
  message that contains NO secret. A dead ESP32, a slow ESP32 or a missing token must
  not be able to change a /predict/full response, status code or timing budget.
* Never logs the token. The token is only ever sent in a request header.
* Runs OFF the request path: every call is dispatched on a short-lived daemon thread
  and returns immediately.
* Makes NO network call at all when indicators are disabled or the token is unset.

Threading note: one daemon thread per call is deliberate. The volume of traffic is
tiny (a few events per gate interaction), and a shared queue would need its own
supervision. A thread that cannot be created is caught and logged like anything else.
"""

import logging
import os
import threading

import requests
from dotenv import load_dotenv

from config.indicators import (
    ESP32_DEVICE_TOKEN,
    INDICATOR_TIMEOUT_S,
    indicators_enabled,
    token_configured,
)

load_dotenv()

# Resolved once at import, the same way routes/sensor.py does it, so the whole module
# agrees on one address. Tests patch this attribute directly.
ESP32_URL = os.getenv("ESP32_URL", "http://192.168.69.2").rstrip("/")

logger = logging.getLogger("AlcoDetect.Indicator")

# Header name shared with the firmware. The value never appears in a log line.
TOKEN_HEADER = "X-Device-Token"

# Verdict statuses the firmware accepts, mirroring decision.py's PASS/WARN/FAIL.
VERDICTS = ("pass", "warn", "fail")

# States the backend may set that are NOT verdicts.
PROGRESS_STATES = ("person_detected", "idle")


# ── Person-present debounce ───────────────────────────────────────────────────
# Held at module scope so it survives across /camera/analyze calls, which is what makes
# the debounce meaningful. Touched only by the single-threaded request handler and by
# the debounce tests; a plain dict is enough and needs no lock or dependency.
_person_state = {
    "present": False,
    "present_streak": 0,
    "absent_streak": 0,
    "last_announced": None,   # monotonic seconds of the last person announcement
}


def reset_person_state() -> None:
    """Test helper: forget the debounce history."""
    _person_state.update({
        "present": False, "present_streak": 0, "absent_streak": 0,
        "last_announced": None,
    })


def _post_state(state, run_id=None, volume=None) -> None:
    """Send one indicator command. Blocking, short timeout, never raises."""
    if not indicators_enabled():
        return
    if not token_configured():
        # Fail closed and say so once per call site, without the value.
        logger.debug("indicator call skipped: ESP32_DEVICE_TOKEN is not set")
        return

    payload = {"state": state}
    if run_id is not None:
        payload["run_id"] = run_id
    if volume is not None:
        payload["volume"] = volume

    try:
        resp = requests.post(
            f"{ESP32_URL}/indicator",
            json=payload,
            headers={TOKEN_HEADER: ESP32_DEVICE_TOKEN},
            timeout=INDICATOR_TIMEOUT_S,
        )
        # The body may echo the indicator state; log only the status code so no device
        # detail and certainly no token can reach the log.
        if resp.status_code != 200:
            logger.warning("indicator %s rejected by device (HTTP %s)",
                           state, resp.status_code)
    except requests.exceptions.RequestException as exc:
        # The class name is safe to log; the message could contain a URL.
        logger.warning("indicator %s not delivered: %s", state, type(exc).__name__)
    except Exception as exc:                       # noqa: BLE001 - must never escape
        logger.warning("indicator %s failed unexpectedly: %s",
                       state, type(exc).__name__)


def _dispatch(state, run_id=None, volume=None) -> None:
    """Fire and forget. Returns immediately; the thread does the work."""
    if not indicators_enabled():
        return
    try:
        t = threading.Thread(
            target=_post_state, args=(state, run_id, volume),
            daemon=True, name="indicator",
        )
        t.start()
    except Exception as exc:                       # noqa: BLE001
        logger.warning("could not dispatch indicator %s: %s",
                       state, type(exc).__name__)


# ── Public API ───────────────────────────────────────────────────────────────

def person_detected(now_monotonic=None) -> bool:
    """Tell the device a person is in front of the camera.

    Debounced: only fires after INDICATOR_PERSON_FRAMES consecutive positive analyses,
    and never more often than INDICATOR_PERSON_COOLDOWN_S.

    Returns True when an announcement was actually dispatched (used by tests).
    """
    from config.indicators import (
        INDICATOR_PERSON_COOLDOWN_S,
        INDICATOR_PERSON_FRAMES,
    )
    import time
    if now_monotonic is None:
        now_monotonic = time.monotonic()

    _person_state["present"] = True
    _person_state["absent_streak"] = 0
    _person_state["present_streak"] += 1

    if _person_state["present_streak"] < INDICATOR_PERSON_FRAMES:
        return False

    last = _person_state["last_announced"]
    if last is not None and (now_monotonic - last) < INDICATOR_PERSON_COOLDOWN_S:
        return False

    _person_state["last_announced"] = now_monotonic
    _dispatch("person_detected")
    return True


def person_cleared(now_monotonic=None) -> bool:
    """Tell the device nobody is there any more, so a blue LED cannot lie forever.

    Debounced by INDICATOR_PERSON_CLEAR_FRAMES consecutive negative analyses.
    """
    from config.indicators import INDICATOR_PERSON_CLEAR_FRAMES
    _person_state["present"] = False
    _person_state["present_streak"] = 0
    _person_state["absent_streak"] += 1
    if _person_state["absent_streak"] < INDICATOR_PERSON_CLEAR_FRAMES:
        return False
    _dispatch("idle")
    return True


def verdict(status, run_id) -> None:
    """Show the decision. Display only — it never feeds back into the decision.

    ``status`` is the decision string ("PASS"/"WARN"/"FAIL"); anything else is
    ignored so a stray value can never produce a colour.
    """
    if not indicators_enabled():
        return
    normalised = str(status).strip().lower()
    if normalised not in VERDICTS:
        logger.warning("indicator verdict ignored: unrecognised status")
        return
    if run_id is None:
        # Without a run id the firmware would refuse it anyway; do not bother the
        # device, and make the misconfiguration obvious in the log.
        logger.warning("indicator verdict not sent: no run id available")
        return
    _dispatch(normalised, run_id=int(run_id))


def bench(state, volume=None) -> None:
    """Drive a state directly for the bench. Bypasses debounce, keeps the token."""
    _dispatch(state, run_id=None, volume=volume)