// timing.js — single source of the ESP32 sampling timeline for the web UI.
//
// Must stay identical to Backend/config/sensor_timing.py (and to the constants in
// alcodetect_esp32). GET /sensor/timing reports whether the firmware agrees, so drift
// between these three copies is detectable rather than silent.
//
//   PREROLL  0 .. 3000 ms   relays ON, no sampling (airflow settles first)
//   SAMPLING 3000 .. 8000   relays ON, the 5 s MQ3 window
//   POSTROLL after window   relays ON for 2000 ms
//   IDLE                   relays OFF
//
// Identical in training and deployment mode — airflow changes what the MQ3 sensors
// read, so the fan sequence must not vary by mode.

import { API_BASE } from "../api";

// Fallbacks are used only until (or instead of) a successful fetch.
export const FALLBACK_TIMING = {
  preroll_ms: 3000,
  buffer_ms: 5000,
  postroll_ms: 2000,
  settle_margin_ms: 1000,
  ready_after_ms: 9000,   // preroll + window + settle margin
  busy_until_ms: 10000,   // preroll + window + postroll
};

let cached = null;

/**
 * Fetch the backend's timing once and cache it. Falls back to the defaults on any
 * failure, and never throws.
 * @param {number} timeoutMs short so a missing ESP32 cannot block the UI
 * @returns {Promise<typeof FALLBACK_TIMING>}
 */
export async function getTiming(timeoutMs = 1500) {
  if (cached) return cached;
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    const res = await fetch(`${API_BASE}/sensor/timing`, { signal: ctrl.signal });
    clearTimeout(t);
    if (!res.ok) throw new Error(`status ${res.status}`);
    const j = await res.json();
    cached = {
      preroll_ms: j.preroll_ms ?? FALLBACK_TIMING.preroll_ms,
      buffer_ms: j.buffer_ms ?? FALLBACK_TIMING.buffer_ms,
      postroll_ms: j.postroll_ms ?? FALLBACK_TIMING.postroll_ms,
      settle_margin_ms: j.settle_margin_ms ?? FALLBACK_TIMING.settle_margin_ms,
      ready_after_ms: j.ready_after_ms ?? FALLBACK_TIMING.ready_after_ms,
      busy_until_ms: j.busy_until_ms ?? FALLBACK_TIMING.busy_until_ms,
    };
    return cached;
  } catch {
    return FALLBACK_TIMING;
  }
}

/** Seconds to show in "device busy — wait Ns" messages (rounded up). */
export function busySeconds(timing) {
  const ms = timing?.busy_until_ms ?? FALLBACK_TIMING.busy_until_ms;
  return Math.ceil(ms / 1000);
}

/** Human label for the phase the ESP32 is in at `elapsedMs` after a trigger. */
export function phaseAt(elapsedMs, timing = FALLBACK_TIMING) {
  if (elapsedMs < timing.preroll_ms) return "preroll";
  if (elapsedMs < timing.preroll_ms + timing.buffer_ms) return "sampling";
  return "postroll";
}

/** Remaining ms in the current phase (never negative). */
export function phaseRemaining(elapsedMs, timing = FALLBACK_TIMING) {
  const p = timing.preroll_ms;
  if (elapsedMs < p) return p - elapsedMs;
  if (elapsedMs < p + timing.buffer_ms) return p + timing.buffer_ms - elapsedMs;
  return Math.max(0, p + timing.buffer_ms + timing.postroll_ms - elapsedMs);
}
