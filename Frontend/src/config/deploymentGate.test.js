import test from "node:test";
import assert from "node:assert/strict";

import { PROVISIONAL, createStabilityGate } from "./deploymentGate.js";

// A nominal fresh frame: centred at (300, 200) with face_width 100.
// Bands with defaults: width 80..120, cx 268..332, cy 182..218.
function frame(overrides = {}) {
  return {
    cached: false,
    ts: 1000,
    status: "normal",
    is_close: true,
    face_width: 100,
    face_bbox: [200, 100, 400, 300],
    ...overrides,
  };
}

function feed(gate, now, overrides) {
  return gate.feed({ analysis: frame(overrides), now });
}

test("provisional constants match the approved values", () => {
  assert.equal(PROVISIONAL.STABLE_FRAMES_N, 3);
  assert.equal(PROVISIONAL.WIDTH_TOL_FRACTION, 0.2);
  assert.equal(PROVISIONAL.POSITION_TOL_FRACTION, 0.05);
  assert.equal(PROVISIONAL.TIMEOUT_MS, 10000);
  assert.equal(PROVISIONAL.STALE_BLOB_MS, 2000);
});

test("N consecutive fresh stable frames arm the gate, then it holds", () => {
  const gate = createStabilityGate();

  assert.deepEqual(feed(gate, 0), { verdict: "wait", reason: null, state: "seeking", count: 1 });
  assert.deepEqual(feed(gate, 800), { verdict: "wait", reason: null, state: "seeking", count: 2 });
  const armed = feed(gate, 1600);
  assert.deepEqual(armed, { verdict: "ready", reason: null, state: "seeking", count: 3 });
  assert.equal(armed.count, PROVISIONAL.STABLE_FRAMES_N);

  // Ready holds until the page disarms it.
  assert.equal(feed(gate, 2400).verdict, "ready");

  gate.disarm();
  assert.equal(feed(gate, 3200).verdict, "wait");
  assert.equal(feed(gate, 4000).verdict, "wait");
  assert.equal(feed(gate, 4800).verdict, "ready");
});

test("a single close frame never arms the gate (old single-frame bug)", () => {
  const gate = createStabilityGate();
  const verdict = feed(gate, 0).verdict;
  assert.notEqual(verdict, "ready");
});

test("no_face resets the count", () => {
  const gate = createStabilityGate();
  feed(gate, 0);
  feed(gate, 800);
  assert.equal(feed(gate, 1600).verdict, "ready");

  const gate2 = createStabilityGate();
  feed(gate2, 0);
  feed(gate2, 800);
  const reset = feed(gate2, 1600, { status: "no_face" });
  assert.equal(reset.verdict, "reset");
  assert.equal(reset.reason, "no_face");

  assert.equal(feed(gate2, 2400).verdict, "wait");
  assert.equal(feed(gate2, 3200).verdict, "wait");
  assert.equal(feed(gate2, 4000).verdict, "ready");
});

test("a cached analysis resets and never counts", () => {
  const gate = createStabilityGate();
  feed(gate, 0);
  feed(gate, 800);

  const reset = feed(gate, 1600, { cached: true });
  assert.equal(reset.verdict, "reset");
  assert.equal(reset.reason, "not_fresh");

  assert.equal(feed(gate, 2400).verdict, "wait");
  assert.equal(feed(gate, 3200).verdict, "wait");
  assert.equal(feed(gate, 4000).verdict, "ready");
});

test("a missing cached flag never counts (fail-safe)", () => {
  const gate = createStabilityGate();
  const bare = frame();
  delete bare.cached;
  const reset = gate.feed({ analysis: bare, now: 0 });
  assert.equal(reset.verdict, "reset");
  assert.equal(reset.reason, "not_fresh");
});

test("busy and error responses reset the gate", () => {
  const busy = createStabilityGate();
  feed(busy, 0);
  feed(busy, 800);
  const busyReset = busy.feed({
    analysis: { error: "Analysis busy — try again", busy: true },
    now: 1600,
  });
  assert.equal(busyReset.verdict, "reset");
  assert.equal(busyReset.reason, "not_fresh");

  const error = createStabilityGate();
  feed(error, 0);
  const errReset = error.feed({
    analysis: { error: "Internal processing crash: boom" },
    now: 800,
  });
  assert.equal(errReset.verdict, "reset");
  assert.equal(errReset.reason, "not_fresh");

  const empty = createStabilityGate();
  const nullReset = empty.feed({ analysis: null, now: 0 });
  assert.equal(nullReset.verdict, "reset");
  assert.equal(nullReset.reason, "invalid_analysis");
});

test("malformed fields reset the gate", () => {
  for (const overrides of [
    { ts: "1000" },
    { face_width: Number.NaN },
    { face_bbox: [1, 2, 3] },
    { face_bbox: [1, 2, 3, Number.NaN] },
    { is_close: false },
  ]) {
    const gate = createStabilityGate();
    const reset = feed(gate, 0, overrides);
    assert.equal(reset.verdict, "reset", JSON.stringify(overrides));
    assert.equal(reset.state, "seeking");
  }
});

test("width-band violation hard-resets the count", () => {
  const gate = createStabilityGate();
  feed(gate, 0, { face_width: 100 });
  feed(gate, 800, { face_width: 100 });

  const reset = feed(gate, 1600, { face_width: 121 });
  assert.equal(reset.verdict, "reset");
  assert.equal(reset.reason, "width_band");

  assert.equal(feed(gate, 2400, { face_width: 100 }).verdict, "wait");
  assert.equal(feed(gate, 3200, { face_width: 100 }).verdict, "wait");
  assert.equal(feed(gate, 4000, { face_width: 100 }).verdict, "ready");
});

test("position-band violation hard-resets the count", () => {
  const xGate = createStabilityGate();
  feed(xGate, 0);
  const xReset = feed(xGate, 800, { face_bbox: [210, 100, 456, 300] });
  assert.equal(xReset.verdict, "reset");
  assert.equal(xReset.reason, "position_x_band");

  const yGate = createStabilityGate();
  feed(yGate, 0);
  const yReset = feed(yGate, 800, { face_bbox: [200, 110, 400, 328] });
  assert.equal(yReset.verdict, "reset");
  assert.equal(yReset.reason, "position_y_band");
});

test("timeout after the first valid frame warns and never triggers", () => {
  const gate = createStabilityGate();
  assert.equal(feed(gate, 0).verdict, "wait");

  const warned = feed(gate, PROVISIONAL.TIMEOUT_MS + 1);
  assert.deepEqual(warned, {
    verdict: "warn_nostop",
    reason: "timeout",
    state: "warned",
    count: 0,
  });
});

test("timeout does not start before the first valid frame", () => {
  const gate = createStabilityGate();
  const reset = gate.feed({
    analysis: frame({ status: "no_face" }),
    now: 0,
  });
  assert.equal(reset.verdict, "reset");
  assert.equal(feed(gate, 60000).verdict, "wait");
});

test("band resets keep the timeout window ticking (continuous wobble warns)", () => {
  const gate = createStabilityGate();
  feed(gate, 0, { face_width: 100 });
  const reset = feed(gate, 3000, { face_width: 200 });
  assert.equal(reset.reason, "width_band");

  const warned = feed(gate, PROVISIONAL.TIMEOUT_MS + 1, { face_width: 100 });
  assert.equal(warned.verdict, "warn_nostop");
  assert.equal(warned.state, "warned");
});

test("after a warning only a fresh no_face re-arms the gate (D-J)", () => {
  const gate = createStabilityGate();
  feed(gate, 0);
  assert.equal(feed(gate, 10001).verdict, "warn_nostop");

  // Stays latched while the subject is still there or the response is cached.
  assert.deepEqual(feed(gate, 10800), { verdict: "wait", reason: null, state: "warned", count: 0 });
  assert.deepEqual(feed(gate, 11600, { status: "no_face", cached: true }), {
    verdict: "wait",
    reason: null,
    state: "warned",
    count: 0,
  });

  const rearm = feed(gate, 12400, { status: "no_face" });
  assert.equal(rearm.verdict, "reset");
  assert.equal(rearm.reason, "no_face_rearm");
  assert.equal(rearm.state, "seeking");

  assert.equal(feed(gate, 13200).verdict, "wait");
  assert.equal(feed(gate, 14000).verdict, "wait");
  assert.equal(feed(gate, 14800).verdict, "ready");
});

test("median uses the mean of the middle pair for an even count", () => {
  const gate = createStabilityGate();
  // Counted widths become [95, 105] -> median 100. 118 deviates by 18 (<=20),
  // but would deviate by 23 from 95, so acceptance proves the even median.
  assert.equal(feed(gate, 0, { face_width: 95 }).verdict, "wait");
  assert.equal(feed(gate, 800, { face_width: 105 }).verdict, "wait");
  assert.equal(feed(gate, 1600, { face_width: 118 }).verdict, "ready");
});

test("median handles an odd count with a custom N", () => {
  const gate = createStabilityGate({ STABLE_FRAMES_N: 4 });
  // Counted widths become [100, 110, 90] -> median 100.
  assert.equal(feed(gate, 0, { face_width: 100 }).verdict, "wait");
  assert.equal(feed(gate, 800, { face_width: 110 }).verdict, "wait");
  assert.equal(feed(gate, 1600, { face_width: 90 }).verdict, "wait");
  assert.equal(feed(gate, 2400, { face_width: 115 }).verdict, "ready");
});

test("constant overrides are honoured", () => {
  const gate = createStabilityGate({
    STABLE_FRAMES_N: 2,
    TIMEOUT_MS: 100,
  });
  assert.equal(feed(gate, 0).verdict, "wait");
  assert.equal(feed(gate, 50).verdict, "ready");

  const quick = createStabilityGate({ TIMEOUT_MS: 100 });
  feed(quick, 0);
  assert.equal(feed(quick, 101).verdict, "warn_nostop");
});

test("reset() clears a held ready state and re-arms", () => {
  const gate = createStabilityGate();
  feed(gate, 0);
  feed(gate, 800);
  assert.equal(feed(gate, 1600).verdict, "ready");

  gate.reset();
  assert.equal(feed(gate, 2400).verdict, "wait");
});
