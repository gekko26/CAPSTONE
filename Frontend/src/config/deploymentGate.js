// Deployment trigger stability gate.
//
// The Deployment page used to start the ESP32 sequence on a single is_close frame,
// so a passer-by could trigger it and leave. This module requires the SAME face to
// be present, close, and stable in position and size for N consecutive FRESH
// analyses. Fresh means the backend explicitly flagged the analysis as not cached.
//
// Pure and deterministic: no React, no DOM, no I/O, no Date.now(). The caller
// supplies `now` on every feed, which keeps the timeout testable with a fake clock.
//
// PROVISIONAL constants below are calibrated by experiment, not guessed:
// see docs/DEPLOYMENT_TRIGGER_STABILITY.md for the measuring procedure.

export const PROVISIONAL = {
  // Consecutive fresh, in-band analyses required before the gate arms.
  STABLE_FRAMES_N: 3, // PROVISIONAL
  // Allowed per-frame face_width deviation from the median of the last N counted
  // widths, as a fraction of that median.
  WIDTH_TOL_FRACTION: 0.20, // PROVISIONAL
  // Allowed per-axis bbox-centre deviation from the median of the last N counted
  // centres, as a fraction of the 640x360 frame the server normalises to.
  POSITION_TOL_FRACTION: 0.05, // PROVISIONAL
  // How long a face may be present without becoming stable before warning.
  TIMEOUT_MS: 10000, // PROVISIONAL
  // The page refuses to analyse a frame older than this (fetch stall safety).
  STALE_BLOB_MS: 2000, // PROVISIONAL
};

const FRAME_W = 640;
const FRAME_H = 360;

const STATES = Object.freeze({ SEEKING: "seeking", WARNED: "warned" });

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1
    ? sorted[mid]
    : (sorted[mid - 1] + sorted[mid]) / 2;
}

function pushCapped(list, value, cap) {
  const next = [...list, value];
  return next.length > cap ? next.slice(next.length - cap) : next;
}

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

// Why an analysis cannot count, or null when it is a usable fresh frame.
function invalidReason(analysis) {
  if (!analysis || typeof analysis !== "object" || Array.isArray(analysis)) {
    return "invalid_analysis";
  }
  // Fail-safe: anything that is not an explicit fresh analysis (cached fallback,
  // busy/error body, missing flag) never counts.
  if (analysis.cached !== false) return "not_fresh";
  if (!isFiniteNumber(analysis.ts)) return "bad_ts";
  if (analysis.status === "no_face") return "no_face";
  if (analysis.is_close !== true) return "not_close";
  if (!isFiniteNumber(analysis.face_width)) return "bad_width";
  if (!Array.isArray(analysis.face_bbox) || analysis.face_bbox.length !== 4) {
    return "bad_bbox";
  }
  if (!analysis.face_bbox.every(isFiniteNumber)) return "bad_bbox";
  return null;
}

function isFreshNoFace(analysis) {
  return Boolean(
    analysis &&
    typeof analysis === "object" &&
    analysis.cached === false &&
    analysis.status === "no_face",
  );
}

export function createStabilityGate(options = {}) {
  const cfg = {
    STABLE_FRAMES_N:
      options.STABLE_FRAMES_N ?? PROVISIONAL.STABLE_FRAMES_N,
    WIDTH_TOL_FRACTION:
      options.WIDTH_TOL_FRACTION ?? PROVISIONAL.WIDTH_TOL_FRACTION,
    POSITION_TOL_FRACTION:
      options.POSITION_TOL_FRACTION ?? PROVISIONAL.POSITION_TOL_FRACTION,
    TIMEOUT_MS: options.TIMEOUT_MS ?? PROVISIONAL.TIMEOUT_MS,
  };

  if (!Number.isInteger(cfg.STABLE_FRAMES_N) || cfg.STABLE_FRAMES_N < 1) {
    throw new RangeError("STABLE_FRAMES_N must be a positive integer");
  }

  let phase = STATES.SEEKING;
  let ready = false;
  let count = 0;
  let firstValidAt = null;
  let widths = [];
  let centresX = [];
  let centresY = [];

  // `count` is additive: it lets the page log how many consecutive frames armed
  // the gate without exposing any identity or biometric data.
  const result = (verdict, reason) => ({ verdict, reason, state: phase, count });

  function clearCounter() {
    count = 0;
    widths = [];
    centresX = [];
    centresY = [];
  }

  function reset() {
    phase = STATES.SEEKING;
    ready = false;
    firstValidAt = null;
    clearCounter();
  }

  // Called by the page immediately after a "ready" verdict, so a held ready state
  // cannot fire the sequence twice.
  function disarm() {
    reset();
  }

  function feed(input) {
    const { analysis = null, now } = input || {};

    // D-J: after a no-stop warning the gate stays latched until a FRESH no_face
    // analysis proves the previous subject left.
    if (phase === STATES.WARNED) {
      if (isFreshNoFace(analysis)) {
        reset();
        return result("reset", "no_face_rearm");
      }
      return result("wait", null);
    }

    if (ready) return result("ready", null);

    if (!isFiniteNumber(now)) {
      reset();
      return result("reset", "invalid_now");
    }

    const reason = invalidReason(analysis);
    if (reason) {
      // Presence is no longer trustworthy: the whole attempt restarts, including
      // the no-stop timeout window.
      reset();
      return result("reset", reason);
    }

    if (firstValidAt !== null && now - firstValidAt > cfg.TIMEOUT_MS) {
      phase = STATES.WARNED;
      firstValidAt = null;
      clearCounter();
      return result("warn_nostop", "timeout");
    }

    if (firstValidAt === null) firstValidAt = now;

    const [x1, y1, x2, y2] = analysis.face_bbox;
    const cx = (x1 + x2) / 2;
    const cy = (y1 + y2) / 2;
    const width = analysis.face_width;

    if (widths.length > 0) {
      const medianWidth = median(widths);
      if (Math.abs(width - medianWidth) > cfg.WIDTH_TOL_FRACTION * medianWidth) {
        // Face is present but unstable. Keep firstValidAt so a subject who keeps
        // wobbling still hits the no-stop timeout instead of resetting it forever.
        clearCounter();
        return result("reset", "width_band");
      }
      const medianX = median(centresX);
      if (Math.abs(cx - medianX) > cfg.POSITION_TOL_FRACTION * FRAME_W) {
        clearCounter();
        return result("reset", "position_x_band");
      }
      const medianY = median(centresY);
      if (Math.abs(cy - medianY) > cfg.POSITION_TOL_FRACTION * FRAME_H) {
        clearCounter();
        return result("reset", "position_y_band");
      }
    }

    widths = pushCapped(widths, width, cfg.STABLE_FRAMES_N);
    centresX = pushCapped(centresX, cx, cfg.STABLE_FRAMES_N);
    centresY = pushCapped(centresY, cy, cfg.STABLE_FRAMES_N);
    count += 1;

    if (count >= cfg.STABLE_FRAMES_N) {
      ready = true;
      return result("ready", null);
    }
    return result("wait", null);
  }

  return { feed, reset, disarm };
}
