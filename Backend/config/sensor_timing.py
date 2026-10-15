"""Single source of truth for the ESP32 sensor-sampling timing on the web side.

The firmware (alcodetect_esp32) runs the same sequence:

    PREROLL  0 .. 3000 ms   relays ON, no sampling (airflow settles first)
    SAMPLING 3000 .. 8000   relays ON, the 5 s MQ3 window (unchanged)
    POSTROLL after window   relays ON for 2000 ms while the reading settles
    IDLE                   relays OFF

Three copies of these numbers exist by design — the firmware, this module and
Frontend/src/config/timing.js — and GET /sensor/timing reports whether the firmware
actually agrees, so drift is detectable rather than silent.

These values are NOT mode-dependent: training and deployment run the identical
sequence, because airflow changes what the MQ3 sensors read.
"""

# ── the sequence itself ──────────────────────────────────────────────────────
SENSOR_PREROLL_MS = 3000    # relays on before the first sample
SENSOR_WINDOW_MS = 5000     # unchanged MQ3 sampling window (50 samples @ 100 ms)
SENSOR_POSTROLL_MS = 2000   # relays on after the last sample

# Network + processing margin added to "data is ready to read".
# Justification from the values that were hard-coded before this change:
#   * Deployment.jsx waited 6000 ms after /trigger while the window was 5000 ms,
#     i.e. a 1000 ms allowance — exactly the margin kept here, because the ESP32
#     POSTs its window to the backend and the backend must still be inserting.
#   * Training.jsx re-armed on a 6000 ms completion timer for the same reason.
SENSOR_SETTLE_MARGIN_MS = 1000

# ── derived ──────────────────────────────────────────────────────────────────
# How long after /trigger the sampled window is expected to be readable.
SENSOR_READY_AFTER_MS = SENSOR_PREROLL_MS + SENSOR_WINDOW_MS + SENSOR_SETTLE_MARGIN_MS

# How long the ESP32 refuses a new /trigger (whole sequence, fans included).
SENSOR_BUSY_UNTIL_MS = SENSOR_PREROLL_MS + SENSOR_WINDOW_MS + SENSOR_POSTROLL_MS


def as_dict():
    """Flat dict for GET /sensor/timing."""
    return {
        "preroll_ms": SENSOR_PREROLL_MS,
        "buffer_ms": SENSOR_WINDOW_MS,
        "postroll_ms": SENSOR_POSTROLL_MS,
        "settle_margin_ms": SENSOR_SETTLE_MARGIN_MS,
        "ready_after_ms": SENSOR_READY_AFTER_MS,
        "busy_until_ms": SENSOR_BUSY_UNTIL_MS,
    }
