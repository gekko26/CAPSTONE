# metrics_store.py
# Persists real training metrics to models/saved/metrics.json so the
# frontend can display actual results instead of hardcoded numbers.
import json
import os
import threading
import time

SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../saved")
METRICS_PATH = os.path.join(SAVE_DIR, "metrics.json")
_lock = threading.Lock()


def save_metrics(section, data):
    """Merges `data` into metrics.json under the given model section."""
    os.makedirs(SAVE_DIR, exist_ok=True)
    with _lock:
        existing = {}
        if os.path.exists(METRICS_PATH):
            try:
                with open(METRICS_PATH) as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                existing = {}
        existing[section] = {**data, "trained_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = METRICS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(existing, f, indent=2)
        os.replace(tmp, METRICS_PATH)


def load_metrics():
    if not os.path.exists(METRICS_PATH):
        return {}
    try:
        with open(METRICS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def describe(entry):
    """Consumer-side guard for a per-model metrics entry.

    After per-model isolation in sensor_models.train(), a single estimator can fail
    while the others succeed. That failure is recorded as
    ``{"error": "...", "samples": n}`` — an entry with NO "accuracy" key. Any
    consumer that does `entry["accuracy"]` blindly renders "undefined%" or throws.

    Returns ``(ok, payload_or_error)``:
      ok=True  -> (True, entry)  the normal metrics dict
      ok=False -> (False, reason) a displayable explanation
    """
    if entry is None:
        return False, "not trained"
    if not isinstance(entry, dict):
        return False, f"malformed metrics entry ({type(entry).__name__})"
    if entry.get("error"):
        return False, str(entry["error"])
    if entry.get("accuracy") is None:
        return False, "trained but no accuracy recorded"
    return True, entry


def is_failed(entry):
    """True when the entry carries no usable accuracy (missing, None, or an error)."""
    ok, _ = describe(entry)
    return not ok
