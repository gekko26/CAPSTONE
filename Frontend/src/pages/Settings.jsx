// Settings.jsx — terminal depth: extruded panels, hardware feel
import { useState } from "react";

const NAV = ["General", "Gates", "Sensors", "AI Model", "Alerts", "Users", "System", "Backup & Logs"];

export default function Settings() {
  const [active, setActive] = useState("General");
  const [flags, setFlags] = useState({ email: true, flagged: true, system: true });
  const [saved, setSaved] = useState(false);

  const save = () => { setSaved(true); setTimeout(() => setSaved(false), 1800); };

  return (
    <div className="p-2 flex flex-col gap-2 max-w-[1200px] mx-auto">
      <div className="flex items-center gap-2">
        <h1 className="text-[11px] font-bold tracking-[0.08em]" style={{ color: "var(--text-primary)" }}>SETTINGS</h1>
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>System configuration and preferences</span>
        <span className="ml-auto text-[10px] font-mono" style={{ color: saved ? "var(--pass)" : "transparent" }}>{saved ? "✓ Saved" : ""}</span>
      </div>

      <div className="grid grid-cols-12 gap-2">
        <div className="col-span-12 lg:col-span-2 rounded-[6px] p-1 flex flex-col gap-0.5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "0 8px 20px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.03)" }}>
          {NAV.map((n) => (
            <button
              key={n}
              onClick={() => setActive(n)}
              data-interactive
              className="text-left text-[11px] px-2.5 py-1.5 rounded-[4px] flex items-center gap-2 cursor-pointer"
              style={{ background: active === n ? "var(--bg-active)" : "transparent", color: active === n ? "var(--pass)" : "var(--text-muted)", borderLeft: active === n ? "2px solid var(--pass)" : "2px solid transparent", boxShadow: active === n ? "0 0 8px rgba(31,193,132,0.15)" : "none" }}
            >
              <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: active === n ? "var(--pass)" : "var(--border-strong)", boxShadow: active === n ? "0 0 6px var(--pass)" : "none" }} />
              {n}
            </button>
          ))}
        </div>

        <div className="col-span-12 lg:col-span-7 flex flex-col gap-2">
          <div className="rounded-[6px] p-3 relative overflow-hidden" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "0 10px 28px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.03)" }}>
            <div className="absolute inset-0 pointer-events-none opacity-[0.02]" style={{ background: "repeating-linear-gradient(0deg, transparent 0 2px, white 2px 3px)" }} />
            <div className="text-[10px] font-bold tracking-[0.08em] mb-3" style={{ color: "var(--text-primary)" }}>GENERAL SETTINGS — {active.toUpperCase()}</div>
            <div className="space-y-2.5 text-[11px] relative">
              {[
                { label: "System Name", value: "ALCOGATE System" },
                { label: "Time Zone", value: "(GMT+08:00) Asia/Manila" },
                { label: "Date Format", value: "MM/DD/YYYY" },
                { label: "Time Format", value: "12 Hour (01:00 PM)" },
              ].map((f) => (
                <div key={f.label} className="flex items-center justify-between gap-3 group">
                  <span style={{ color: "var(--text-muted)" }}>{f.label}</span>
                  <select className="text-[11px] px-2 py-1.5 rounded-[4px] border min-w-[190px] cursor-pointer group-hover:border-[var(--border)]" style={{ background: "var(--bg-card-alt)", borderColor: "var(--border-subtle)", color: "var(--text-primary)", boxShadow: "inset 0 1px 2px rgba(0,0,0,0.25)" }}>
                    <option>{f.value}</option>
                  </select>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-[6px] p-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "0 8px 20px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.03)" }}>
            <div className="text-[10px] font-bold tracking-[0.08em] mb-3" style={{ color: "var(--text-primary)" }}>NOTIFICATIONS</div>
            {[
              { k: "email", label: "Email Alerts", sub: "Enable email notifications" },
              { k: "flagged", label: "Flagged / Denied Alerts", sub: "Notify on flagged or denied passages" },
              { k: "system", label: "System Alerts", sub: "Notify on system issues" },
            ].map((r) => (
              <div key={r.k} className="flex items-center justify-between py-2.5 group cursor-pointer" style={{ borderBottom: r.k !== "system" ? "1px solid var(--border-subtle)" : "none" }} onClick={() => setFlags((p) => ({ ...p, [r.k]: !p[r.k] }))}>
                <div>
                  <div className="text-[11px]" style={{ color: "var(--text-primary)" }}>{r.label}</div>
                  <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>{r.sub}</div>
                </div>
                <div className="w-9 h-5 rounded-full relative transition-all duration-200" style={{ background: flags[r.k] ? "var(--pass)" : "var(--border-strong)", boxShadow: flags[r.k] ? "0 0 8px rgba(31,193,132,0.4), inset 0 1px 2px rgba(0,0,0,0.25)" : "inset 0 1px 2px rgba(0,0,0,0.3)" }}>
                  <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all duration-200" style={{ left: flags[r.k] ? "18px" : "2px", boxShadow: "0 1px 4px rgba(0,0,0,0.4)" }} />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="col-span-12 lg:col-span-3 flex flex-col gap-2">
          <div className="rounded-[6px] p-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "0 8px 20px rgba(0,0,0,0.3)" }}>
            <div className="text-[10px] font-bold tracking-[0.08em] mb-2" style={{ color: "var(--text-primary)" }}>SYSTEM INFO</div>
            <div className="space-y-1.5 text-[10px]">
              <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Version</span><span className="font-mono" style={{ color: "var(--text-primary)" }}>1.0.0</span></div>
              <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Device</span><span className="font-mono" style={{ color: "var(--text-primary)" }}>ALCOGATE-01</span></div>
              <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Status</span><span className="flex items-center gap-1" style={{ color: "var(--pass)" }}><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_6px_#1FC184]" />Online</span></div>
              <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Uptime</span><span className="font-mono" style={{ color: "var(--text-primary)" }}>3d 14h 22m</span></div>
              <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Last Restart</span><span className="font-mono" style={{ color: "var(--text-primary)" }}>May 16, 06:20 AM</span></div>
            </div>
          </div>

          <div className="rounded-[6px] p-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "0 8px 20px rgba(0,0,0,0.3)" }}>
            <div className="text-[10px] font-bold tracking-[0.08em] mb-2" style={{ color: "var(--text-primary)" }}>STORAGE</div>
            <div className="space-y-3">
              <div>
                <div className="flex justify-between text-[10px]"><span style={{ color: "var(--text-muted)" }}>Database</span><span className="font-mono" style={{ color: "var(--text-secondary)" }}>3.2 GB / 50 GB</span></div>
                <div className="h-1.5 rounded-full mt-1 overflow-hidden relative" style={{ background: "var(--bg-card-alt)", boxShadow: "inset 0 1px 2px rgba(0,0,0,0.4)" }}><div className="h-full rounded-full" style={{ width: "12%", background: "linear-gradient(90deg, #0F231B, #1FC184)", boxShadow: "0 0 6px rgba(31,193,132,0.5)" }} /></div>
              </div>
              <div>
                <div className="flex justify-between text-[10px]"><span style={{ color: "var(--text-muted)" }}>Logs</span><span className="font-mono" style={{ color: "var(--text-secondary)" }}>1.1 GB / 20 GB</span></div>
                <div className="h-1.5 rounded-full mt-1 overflow-hidden relative" style={{ background: "var(--bg-card-alt)", boxShadow: "inset 0 1px 2px rgba(0,0,0,0.4)" }}><div className="h-full rounded-full" style={{ width: "8%", background: "var(--accent)" }} /></div>
              </div>
            </div>
          </div>

          <button onClick={save} className="ml-auto text-[11px] px-4 py-1.5 rounded-[4px] font-medium transition-all active:scale-95" style={{ background: saved ? "var(--pass)" : "var(--pass)", color: "#fff", boxShadow: saved ? "0 0 12px rgba(31,193,132,0.5)" : "0 4px 12px rgba(0,0,0,0.25)" }}>{saved ? "✓ Saved" : "Save Changes"}</button>
        </div>
      </div>
    </div>
  );
}
