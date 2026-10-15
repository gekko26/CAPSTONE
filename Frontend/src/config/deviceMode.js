// deviceMode.js — read/change the ESP32's training <-> deployment mode.
//
// The mode decides which table a sampled MQ3 window lands in:
//   training    -> /training/collect/sensor-data -> training_data
//   deployment  -> /sensor                       -> sensor_readings
// The firmware owns that decision (and persists it in NVS); this is only a proxy, so
// the frontend never talks to the ESP32 directly.
//
// Lives outside the component file so that file exports components only.

import { useEffect, useState } from "react";
import { API_BASE } from "../api";

const POLL_MS = 10000;

let listeners = new Set();
let current = { mode: null, online: false, writable: false, sequence_state: null };
let timer = null;

async function refresh() {
  try {
    const r = await fetch(`${API_BASE}/sensor/mode`);
    const j = r.ok ? await r.json() : {};
    current = {
      mode: j.mode ?? null,
      online: !!j.online,
      writable: j.writable !== false,
      sequence_state: j.sequence_state ?? null,
      error: j.error,
    };
  } catch {
    current = { mode: null, online: false, writable: false, sequence_state: null };
  }
  listeners.forEach((fn) => fn(current));
}

function subscribe(fn) {
  listeners.add(fn);
  if (!timer) {
    refresh();
    timer = setInterval(refresh, POLL_MS);
  }
  fn(current);
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

/** Current device mode, shared across every component that asks for it. */
export function useDeviceMode() {
  const [state, setState] = useState(current);
  useEffect(() => subscribe(setState), []);
  return state;
}

/**
 * Change the device mode.
 * @returns {Promise<{ok: boolean, message: string}>}
 */
export async function setDeviceMode(mode) {
  try {
    const r = await fetch(`${API_BASE}/sensor/mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: false, message: j.detail || `HTTP ${r.status}` };
    await refresh();
    return { ok: true, message: j.message || "Mode updated" };
  } catch (e) {
    return { ok: false, message: e.message || "ESP32 unreachable" };
  }
}
