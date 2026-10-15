// DeviceMode.jsx — UI for the ESP32's training <-> deployment mode.
//
//   training    -> /training/collect/sensor-data -> training_data
//   deployment  -> /sensor                       -> sensor_readings
//
// The firmware owns the decision; these components only display it and ask for a
// change. Data plumbing lives in ../config/deviceMode.js.

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowRightLeft, Check } from "lucide-react";
import { useDeviceMode, setDeviceMode } from "../config/deviceMode";

/** Small read-only badge: shows the current mode and whether the device is online. */
export function DeviceModeBadge() {
  const { mode, online } = useDeviceMode();
  const label = mode ? mode.toUpperCase() : "UNKNOWN";
  const training = mode === "training";
  const color = !online ? "var(--text-muted)" : training ? "var(--over)" : "var(--pass)";
  const bg = !online ? "transparent"
    : training ? "color-mix(in srgb, var(--over) 10%, transparent)"
    : "color-mix(in srgb, var(--pass) 10%, transparent)";
  return (
    <span
      className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border"
      style={{ color, background: bg, borderColor: `color-mix(in srgb, ${color} 30%, transparent)` }}
      title={online
        ? `ESP32 is in ${label} mode — sampled windows go to ${training ? "training_data" : "sensor_readings"}`
        : "ESP32 offline — mode unknown"}
    >
      <ArrowRightLeft size={10} /> {online ? label : "MODE ?"}
    </span>
  );
}

/**
 * The control, with an explicit confirmation before entering TRAINING mode,
 * because a wrong-mode window would write gate readings into the training table.
 */
export function DeviceModeSelector({ compact = false }) {
  const { mode, online, writable, sequence_state } = useDeviceMode();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [confirming, setConfirming] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => () => clearTimeout(timerRef.current), []);

  const flash = (m) => {
    setMsg(m);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setMsg(""), 4000);
  };

  const apply = async (target) => {
    setBusy(true);
    setConfirming(false);
    const r = await setDeviceMode(target);
    setBusy(false);
    flash(r.ok ? `Device is now in ${target} mode` : r.message);
  };

  if (!online) {
    return (
      <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>
        ESP32 offline — mode cannot be read or changed.
      </div>
    );
  }

  const training = mode === "training";
  const blocked = !writable && sequence_state && sequence_state !== "idle";

  return (
    <div className={compact ? "" : "space-y-2"}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>Device mode</span>
        <div className="inline-flex rounded-[4px] overflow-hidden border" style={{ borderColor: "var(--border-subtle)" }}>
          {["deployment", "training"].map((m) => {
            const active = mode === m;
            const isTraining = m === "training";
            return (
              <button
                key={m}
                disabled={busy || (blocked && !active)}
                onClick={() => (isTraining && !active ? setConfirming(true) : apply(m))}
                className="px-2.5 py-1 text-[10px] font-medium disabled:opacity-50"
                style={{
                  background: active
                    ? (isTraining ? "var(--over)" : "var(--pass)")
                    : "transparent",
                  color: active ? "#fff" : "var(--text-secondary)",
                }}
              >
                {active && <Check size={9} className="inline mr-1" />}
                {m}
              </button>
            );
          })}
        </div>
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
          writes to <span className="font-mono">{training ? "training_data" : "sensor_readings"}</span>
        </span>
      </div>

      {blocked && (
        <p className="text-[10px]" style={{ color: "var(--near)" }}>
          Relay sequence is <span className="font-mono">{sequence_state}</span> — wait until idle to change mode.
        </p>
      )}

      {confirming && (
        <div className="flex flex-col gap-1.5 p-2 rounded-[4px]"
          style={{ background: "color-mix(in srgb, var(--over) 10%, transparent)", border: "1px solid color-mix(in srgb, var(--over) 30%, transparent)" }}>
          <span className="text-[10px] flex items-center gap-1" style={{ color: "var(--over)" }}>
            <AlertTriangle size={11} /> Switch the device to <b>TRAINING</b> mode?
          </span>
          <span className="text-[10px]" style={{ color: "var(--text-secondary)" }}>
            Every sampled window will be written to the <b>training</b> table. Keep it in deployment mode
            for real gate use, otherwise live readings enter the training dataset.
          </span>
          <div className="flex gap-1.5">
            <button onClick={() => apply("training")} disabled={busy}
              className="text-[10px] px-2 py-0.5 rounded-[3px] font-medium disabled:opacity-50"
              style={{ background: "var(--over)", color: "#fff" }}>Yes, enter training mode</button>
            <button onClick={() => setConfirming(false)}
              className="text-[10px] px-2 py-0.5 rounded-[3px] border"
              style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>Cancel</button>
          </div>
        </div>
      )}

      {msg && <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>{msg}</p>}
    </div>
  );
}
