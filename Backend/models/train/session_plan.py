"""Pure helpers for the first-collection session plan.

No FastAPI, no database, no cv2, no filesystem. Everything here is a deterministic
function of the rows and subjects handed to it, so the endpoint and the UI show the
same numbers and the tests can drive them without any I/O.

Two responsibilities:

``build_session_plan``
    What is still left to capture, per enrolled person and overall.

``baseline_stability``
    Whether the recent clear-air baseline has stopped moving. This is a WARNING
    signal only. It is deliberately incapable of blocking a capture or a training
    run -- it is surfaced so the operator knows to purge the funnel and let the
    sensors settle, and then decides for themselves.
"""

import math

__all__ = [
    "EVENT_KEYS",
    "PER_PERSON_QUOTA",
    "PER_PERSON_ALCOHOL_BAND_MIN",
    "BAC_BANDS",
    "FATIGUE_SUB_LABELS",
    "BASELINE_MAX_CV",
    "event_key_for_row",
    "bac_band_for_value",
    "build_session_plan",
    "baseline_stability",
]

# The six capture events, in the order an operator should work through them.
# Mapped from rows by (label, sub_label) exactly as the collect_* endpoints build
# them; see test_session_plan.py, which asserts every endpoint's row lands on
# exactly one key.
EVENT_KEYS = ("clear_air", "sober", "fatigue", "alcohol", "sanitizer", "perfume")

# drowsy and yawning are both measurement of the SAME face class (fatigue), so they
# share one quota and one event key.
FATIGUE_SUB_LABELS = ("drowsy", "yawning")

# Placeholders the user can tune to match the session they actually have time for.
# They are intentionally not tied to the training readiness constants: these describe
# what one person should contribute, readiness describes what the dataset needs.
PER_PERSON_QUOTA = {
    "clear_air": 10,
    "sober": 10,
    "fatigue": 8,
    "alcohol": 10,
    "sanitizer": 4,
    "perfume": 4,
}

# Informational: how many alcohol captures one person should ideally contribute per
# BAC band. Not enforced -- bands also need the dataset-level minimum.
PER_PERSON_ALCOHOL_BAND_MIN = 3

# Band edges, read from the backend's own rule module so they cannot drift.
# Imported lazily and defensively: this module is meant to stay dependency-free, and
# a missing gate_config must not stop the plan from rendering.
def _band_edges():
    try:
        from config import gate_config
        return (float(gate_config.BAC_NEAR_LIMIT), float(gate_config.BAC_OVER_LIMIT))
    except Exception:
        # Fall back to the values the module documents. Bands are advisory here.
        return (0.02, 0.05)


BAC_BANDS = ("below_near", "near", "over")

# Placeholder, documented: coefficient of variation above which the baseline is
# treated as unsettled. 0.30 means the spread is more than a third of the mean.
BASELINE_MAX_CV = 0.30


def bac_band_for_value(bac):
    """Classify a BAC reading. Returns a band name, or None when there is no value."""
    if bac is None:
        return None
    try:
        value = float(bac)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    near, over = _band_edges()
    if value >= over:
        return "over"
    if value >= near:
        return "near"
    return "below_near"


def event_key_for_row(label, sub_label):
    """Map a training_data row to exactly one EVENT_KEY, or None if it is not a
    capture event (for example a pending row with label -1, or an unrecognised one)."""
    if label is None:
        return None
    try:
        label = int(label)
    except (TypeError, ValueError):
        return None
    sub = sub_label if isinstance(sub_label, str) else None

    if label == 0:
        if sub == "clear_air":
            return "clear_air"
        if sub in FATIGUE_SUB_LABELS:
            return "fatigue"
        if sub is None:
            return "sober"
        return None
    if label == 1:
        return "alcohol"
    if label == 2:
        if sub == "sanitizer":
            return "sanitizer"
        if sub == "perfume":
            return "perfume"
        return None
    return None


def _mean(values):
    return sum(values) / float(len(values))


def _population_std(values, mean):
    if len(values) < 2:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / float(len(values))
    return math.sqrt(var)


def baseline_stability(clear_air_rows, n=6, max_cv=BASELINE_MAX_CV, avg_attrs=None):
    """Assess whether the recent clear-air baseline has settled.

    ``clear_air_rows`` is any iterable of row-like objects exposing the three MQ3
    average columns. Only the LAST ``n`` rows that actually carry sensor data are
    used, because the most recent readings are the ones that describe the sensors
    right now.

    Returns ``{"status", "cv_per_channel", "n_used", "message", "dead_channels"}``.
    ``status`` is one of ``stable`` / ``unstable`` / ``insufficient_data``.

    A channel whose mean is exactly zero is reported as a possible dead sensor --
    the coefficient of variation of a constant is 0 and would otherwise look like
    the most stable channel in the set, which is precisely backwards.

    This never raises and never blocks anything; it is advisory.
    """
    if avg_attrs is None:
        avg_attrs = ("mq3_1_avg", "mq3_2_avg", "mq3_3_avg")

    usable = []
    for row in clear_air_rows or []:
        values = []
        ok = True
        for attr in avg_attrs:
            value = getattr(row, attr, None)
            if value is None:
                ok = False
                break
            try:
                value = float(value)
            except (TypeError, ValueError):
                ok = False
                break
            if value != value:  # NaN
                ok = False
                break
            values.append(value)
        if ok:
            usable.append(values)

    usable = usable[-int(n):] if n else usable
    n_used = len(usable)

    if n_used < 3:
        return {
            "status": "insufficient_data",
            "cv_per_channel": {},
            "n_used": n_used,
            "dead_channels": [],
            "message": (f"Baseline stability unknown: only {n_used} clear-air row(s) with "
                        f"sensor data; at least 3 are needed"),
        }

    cv_per_channel = {}
    dead_channels = []
    for index, attr in enumerate(avg_attrs):
        series = [row[index] for row in usable]
        mean = _mean(series)
        if mean == 0.0:
            cv_per_channel[attr] = None
            dead_channels.append(attr)
        else:
            cv_per_channel[attr] = _population_std(series, mean) / mean

    if dead_channels:
        return {
            "status": "unstable",
            "cv_per_channel": cv_per_channel,
            "n_used": n_used,
            "dead_channels": dead_channels,
            "message": ("Baseline unstable: channel(s) reading zero "
                        f"({', '.join(dead_channels)}) look disconnected. Check the "
                        "sensor wiring before capturing"),
        }

    worst = max(cv for cv in cv_per_channel.values() if cv is not None)
    if worst > max_cv:
        return {
            "status": "unstable",
            "cv_per_channel": cv_per_channel,
            "n_used": n_used,
            "dead_channels": [],
            "message": ("Baseline unstable: purge the funnel and wait for the sensors "
                        "to settle between captures"),
        }

    return {
        "status": "stable",
        "cv_per_channel": cv_per_channel,
        "n_used": n_used,
        "dead_channels": [],
        "message": f"Baseline stable across the last {n_used} clear-air rows",
    }


def _rows_by_subject(rows):
    """Group event-keyed rows by subject_id, and collect the untagged leftovers."""
    per_subject = {}
    untagged = 0
    for row in rows or []:
        key = event_key_for_row(getattr(row, "label", None), getattr(row, "sub_label", None))
        if key is None:
            continue
        subject_id = getattr(row, "subject_id", None)
        if subject_id is None:
            untagged += 1
            continue
        per_subject.setdefault(subject_id, []).append((key, row))
    return per_subject, untagged


def build_session_plan(rows, subjects, targets=None, quotas=None):
    """Per-person and overall capture plan.

    ``rows``      training_data rows (any objects with label/sub_label/subject_id/bac)
    ``subjects``  objects exposing ``id`` and ``name``
    ``targets``   readiness mapping; defaults to the backend's own constants
    ``quotas``    per-person quota mapping; defaults to PER_PERSON_QUOTA

    Deterministic: the same inputs always produce the same output, including the
    ``next_suggested`` tie-break (earliest key in EVENT_KEYS order wins).
    """
    if quotas is None:
        quotas = PER_PERSON_QUOTA

    per_subject_rows, untagged = _rows_by_subject(rows)

    people = []
    for subject in sorted(subjects or [], key=lambda s: getattr(s, "id", 0) or 0):
        subject_id = getattr(subject, "id", None)
        name = getattr(subject, "name", None)
        entries = per_subject_rows.get(subject_id, [])

        counts = {key: 0 for key in EVENT_KEYS}
        bands = {band: 0 for band in BAC_BANDS}
        for key, row in entries:
            counts[key] += 1
            if key == "alcohol":
                band = bac_band_for_value(getattr(row, "bac", None))
                if band:
                    bands[band] += 1

        remaining = {}
        for key in EVENT_KEYS:
            quota = quotas.get(key, 0)
            # Never negative: over-quota is finished work, not negative debt.
            remaining[key] = max(0, quota - counts[key])

        total_quota = sum(quotas.get(k, 0) for k in EVENT_KEYS)
        total_done = sum(min(counts[k], quotas.get(k, 0)) for k in EVENT_KEYS)
        fraction = (total_done / total_quota) if total_quota else 0.0

        # Largest remaining share wins; ties break to the earliest EVENT_KEY so the
        # suggestion is stable between calls.
        best_key = None
        best_shortfall = -1.0
        for key in EVENT_KEYS:
            quota = quotas.get(key, 0)
            if quota <= 0:
                continue
            shortfall = remaining[key] / float(quota)
            if remaining[key] == 0:
                continue
            if shortfall > best_shortfall:
                best_shortfall = shortfall
                best_key = key

        people.append({
            "subject_id": subject_id,
            "name": name,
            "counts": counts,
            "remaining": remaining,
            "completion_fraction": round(fraction, 4),
            "next_suggested": best_key,
            "alcohol_bands": bands,
        })

    # Overall sensor-class counts, from the SAME row set, including untagged rows:
    # an untagged capture is still a real capture for dataset purposes.
    overall_counts = {key: 0 for key in EVENT_KEYS}
    overall_bands = {band: 0 for band in BAC_BANDS}
    for row in rows or []:
        key = event_key_for_row(getattr(row, "label", None), getattr(row, "sub_label", None))
        if key is None:
            continue
        overall_counts[key] += 1
        if key == "alcohol":
            band = bac_band_for_value(getattr(row, "bac", None))
            if band:
                overall_bands[band] += 1

    class_counts = {
        0: overall_counts["clear_air"] + overall_counts["sober"] + overall_counts["fatigue"],
        1: overall_counts["alcohol"],
        2: overall_counts["sanitizer"] + overall_counts["perfume"],
    }

    if targets is None:
        targets = {
            "sensor_min_rows_per_class": 30,
            "sensor_target_rows_per_class": 60,
            "min_subjects": 3,
            "target_subjects": 5,
            "bac_band_min_rows": 8,
            "bac_band_min_subjects": 2,
        }

    return {
        "people": people,
        "overall": {
            "event_counts": overall_counts,
            "rows_per_sensor_class": {str(k): v for k, v in class_counts.items()},
            "rows_per_sensor_class_min": targets.get("sensor_min_rows_per_class"),
            "rows_per_sensor_class_target": targets.get("sensor_target_rows_per_class"),
            "untagged_rows": untagged,
            "alcohol_bands": overall_bands,
            "subjects_enrolled": len(people or []),
            "min_subjects": targets.get("min_subjects"),
            "target_subjects": targets.get("target_subjects"),
        },
        "quotas": dict(quotas),
        "event_keys": list(EVENT_KEYS),
    }