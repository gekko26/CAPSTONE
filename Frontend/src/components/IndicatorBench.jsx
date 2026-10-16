// IndicatorBench — Settings -> Sensors -> Indicators.
//
// Bench controls for the deployment indicators (2x RGB LED + DFPlayer voice).
// DISPLAY ONLY: nothing here can open a lock or influence a decision.
//
// SECURITY: this component never sees, stores, sends or asks for the shared token.
// The backend holds it in ESP32_DEVICE_TOKEN and adds the header itself, so a token
// can never leak into the browser, localStorage or a network log. If the backend has
// no token the endpoint answers 503 and this panel says so plainly.
import { useState } from "react";
import { API_BASE } from "../api";

const STATES = [
  { key: "person_detected", label: "Person detected", hint: "blue, slow pulse + clip 001" },
  { key: "sampling",        label: "Sampling",        hint: "blue, fast pulse + clip 003" },
  { key: "analyzing",       label: "Analyzing",       hint: "blue steady + clip 004" },
  { key: "warn",            label: "Warn",            hint: "YELLOW + clip 006" },
  { key: "fail",            label: "Fail",            hint: "RED + clip 007" },
  { key: "off",             label: "Off",             hint: "LEDs off, no sound" },
];

export function IndicatorBench() {
  const [busy, setBusy] = useState(null);
  const [msg, setMsg] = useState(null);
  const [tone, setTone] = useState("muted");

  async function drive(state) {
    setBusy(state);
    setMsg(null);
    try {
      const r = await fetch(`${API_BASE}/sensor/indicator/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state }),
      });
      const j = await r.json().catch(() => ({}));
      if (r.ok) {
        setTone("ok");
        setMsg(`Device accepted "${state}".`);
      } else if (r.status === 503) {
        setTone("bad");
        setMsg(
          "Bench disabled — the backend has no ESP32_DEVICE_TOKEN set, so it refuses " +
          "to drive the LEDs. Export the variable and restart the backend " +
          "(docs/INDICATORS_WIRING.md)."
        );
      } else if (r.status === 401) {
        setTone("bad");
        setMsg(
          "The device rejected the token. The value in the ESP32's secrets.h and the " +
          "backend's ESP32_DEVICE_TOKEN must match exactly — then reflash."
        );
      } else if (r.status === 409) {
        setTone("warn");
        setMsg(j.detail || "A relay sequence is running — the bench is refused until it is idle.");
      } else {
        setTone("bad");
        setMsg(j.detail || `Device refused the command (HTTP ${r.status}).`);
      }
    } catch (e) {
      setTone("bad");
      setMsg(`Could not reach the backend: ${e.message}`);
    } finally {
      setBusy(null);
    }
  }

  const color =
    tone === "ok" ? "var(--text-primary)"
      : tone === "bad" ? "#e5484d"
        : tone === "warn" ? "var(--warn, #f5a524)" : "var(--text-muted)";

  return (
    <div>
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
          Indicators
        </span>
        <span className="text-[10px] px-1.5 py-0.5 rounded" style={{
          background: "var(--bg-active)", color: "var(--text-muted)",
        }}>
          display only
        </span>
      </div>

      <div className="text-[10px] mb-3" style={{ color: "var(--text-muted)" }}>
        Drive the two RGB LEDs and the voice cues on the bench, with the mains OFF.
        Green (PASS) is deliberately NOT offered here — the firmware only ever shows it
        for an authenticated verdict whose run id matches the current run.
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
        {STATES.map((s) => (
          <button
            key={s.key}
            onClick={() => drive(s.key)}
            disabled={busy !== null}
            className="text-left px-2 py-1.5 rounded-[4px] border transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            style={{
              background: "var(--bg-card)",
              borderColor: "var(--border-subtle)",
            }}
          >
            <div className="text-[11px] font-medium" style={{ color: "var(--text-secondary)" }}>
              {busy === s.key ? "sending…" : s.label}
            </div>
            <div className="text-[9px]" style={{ color: "var(--text-muted)" }}>{s.hint}</div>
          </button>
        ))}
      </div>

      {msg && (
        <div className="mt-2 text-[10px] leading-relaxed" style={{ color }}>
          {msg}
        </div>
      )}
    </div>
  );
}