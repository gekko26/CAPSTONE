# Deployment trigger stability

## Why

`Deployment.jsx` used to start the ESP32 sequence on a **single** `/camera/analyze`
response with `is_close === true`. A passer-by could be "close" for one frame, start
the trigger, and leave while the backend waited `ready_after_ms` before hitting
`/predict/full`. The result was a run with no stable subject.

The page now requires the **same face** to be present, close, and stable in both
position and size for `N` consecutive **fresh** analyses before it starts the
sequence. Timing (3 s pre-roll / 5 s window / 2 s post-roll), training behaviour, the
OFF status of gate enforcement, and all decision/model logic are unchanged.

Stability is computed client-side (`Frontend/src/config/deploymentGate.js`, pure and
React-free). The backend only adds two honesty fields to `/camera/analyze`:

* `cached` — `false` for a fresh analysis, `true` for the cached fallback;
* `face_bbox` — the server's 640×360-normalised bbox (may be `null` when there is no
  face).

Fail-safe rule: any response that does not carry `cached === false` (busy/error
bodies, the cached fallback, a missing flag) resets the gate and never counts.

## PROVISIONAL constants

All constants live in `Frontend/src/config/deploymentGate.js`. They are marked
`PROVISIONAL` and `Backend/config/gate_config.py` is untouched — these are UI
trigger-stability values, not decision thresholds.

| Constant | Value | What it controls | How to calibrate it |
|---|---|---|---|
| `STABLE_FRAMES_N` | 3 | Consecutive fresh, in-band analyses required | At the standing point, record ≥20 approach trials (plus deliberate pass-bys). Lower N only if real subjects trigger fast enough; raise N if a pass-by still reaches the sequence. 3 at 1.25 Hz ≈ 2.4 s of presence. |
| `WIDTH_TOL_FRACTION` | 0.20 | Per-frame `face_width` deviation from the median of the last N counted widths | Same ≥20-trial capture: log `face_width` while standing still and note natural breathing/lean spread. Set the tolerance just above the still-standing spread but below a different-distance approach. |
| `POSITION_TOL_FRACTION` | 0.05 | Per-axis bbox-centre deviation from the median of the last N counted centres, as a fraction of 640×360 (i.e. ±32 px X, ±18 px Y) | Same trials: log `face_bbox` centres while standing still and during normal sway. Tighten until sway does not arm; widen until a genuinely still subject is not rejected. |
| `TIMEOUT_MS` | 10000 | No-stop window measured from the first valid frame in an attempt | Zone/timing check: measure how long a person actually pauses at the gate. The timeout must expire before a passer-by reaches the reading position but after a real subject has had time to settle. |
| `STALE_BLOB_MS` | 2000 | Maximum age of the locally fetched frame before the analysis is skipped AND the gate is reset (count and no-stop window cleared) | Fetch-stall timing: stall `/camera/stream/frame` and confirm the page logs `[gate] reset stale_blob` and skips rather than posting a stale blob. Must be comfortably larger than normal frame cadence and smaller than a face-change interval. |

The 2000 ms stale-blob value was chosen from the normal cadence (frames fetched every
`FRAME_MS = 60`, analysed every `ANALYZE_MS = 800`); it is still provisional.

## How the gate decides

Each `/camera/analyze` response is fed to the gate as `{ analysis, now }`:

1. A response is usable only when `cached === false`, `ts` is numeric,
   `status !== "no_face"`, `is_close === true`, `face_width` is numeric, and
   `face_bbox` is a 4-number array.
2. Width and both bbox-centre axes must be within tolerance of the median of the last
   `N` **counted** values. Violating the band clears the count but keeps the timeout
   window, so a person who keeps swaying eventually times out instead of resetting
   the timer forever.
3. An unusable response (no face, not close, cached, busy, HTTP error, network error)
   is a hard reset with no grace: count cleared, timeout window cleared, reason
   console-logged (`console.log("[gate]", verdict, reason)`).
4. A locally fetched frame older than `STALE_BLOB_MS` is skipped without an analysis,
   and the gate is reset (`console.log("[gate]", "reset", "stale_blob")`): the count
   and the no-stop timeout window are cleared, so a stalled camera (including one
   whose `/camera/stream/frame` fetches are failing) can never hand one person's
   frames to the next.
5. Reaching `N` returns `ready`. The page logs `[gate] ready <count> <timestamp>`
   (verdict, count and ISO timestamp only — never names, confidence or image data),
   disarms the gate immediately, sets `isScanningRef`, and calls the existing
   `executeSecurityCheckpoint`.
6. If no stable face appears within `TIMEOUT_MS` of the first valid frame, the gate
   returns `warn_nostop`: the page shows the WARN banner with reason `"no stop"`, no
   trigger is sent and no model runs.

## Re-arm after a no-stop warning (D-J)

After `warn_nostop` the gate **latches** in the `warned` state. It does **not** re-arm
because the unstable subject is still in frame, and it does not re-arm on a cached or
busy response. Only **one fresh `status === "no_face"` response** (i.e.
`cached === false`) transitions it back to `seeking`, proving the previous subject
left. The page keeps the WARN banner until the next interaction; there is no
auto-clear timer.

## Cached-fallback note (K1)

The cached fallback still returns `_last_analysis` **before** `_with_freshness`, exactly
as before. `_with_freshness` is applied only to fresh results. This fix does not
change that flow: it only adds `cached: true` to a **copy** of the cached dict (the
shared `_last_analysis` is never mutated) and `cached: false` plus `face_bbox` to the
fresh result. The deployment gate treats anything other than `cached === false` as
invalid, so a cached analysis can never advance the counter.

## Running the tests

```bash
# pure gate logic (Node's built-in runner)
cd Frontend
node --test src/config/

# backend additive flags (fast, no model/camera/DB)
cd Backend
.venv/bin/python -m pytest tests/test_camera_cached_flag.py -q
```

The full backend suite remains the regression gate:

```bash
cd Backend
.venv/bin/python -m pytest tests/ -q
```
