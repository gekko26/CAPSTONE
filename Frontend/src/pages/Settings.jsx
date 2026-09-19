// Settings.jsx — functional: persists to localStorage, applies instantly
import { useState, useEffect } from "react";
import { Sun, Moon, Monitor, Maximize2 } from "lucide-react";
import { useTheme } from "../context/THEME_CONTEXT";
import { useDisplay } from "../context/DISPLAY_CONTEXT";

const NAV = ["General", "Gates", "Sensors", "AI Model", "Alerts", "Users", "System", "Backup & Logs"];

const LS_KEYS = {
  systemName: "alcogate-system-name",
  timeZone: "alcogate-time-zone",
  dateFormat: "alcogate-date-format",
  timeFormat: "alcogate-time-format",
  notifications: "alcogate-notifications",
};

const DEFAULTS = {
  systemName: "ALCOGATE System",
  timeZone: "Asia/Manila",
  dateFormat: "MM/DD/YYYY",
  timeFormat: "12h",
  notifications: { email: true, flagged: true, system: true },
};

const TZ_OPTIONS = [
  { value: "Asia/Manila", label: "(GMT+08:00) Asia/Manila" },
  { value: "Asia/Singapore", label: "(GMT+08:00) Asia/Singapore" },
  { value: "Asia/Tokyo", label: "(GMT+09:00) Asia/Tokyo" },
  { value: "UTC", label: "(GMT+00:00) UTC" },
  { value: "America/New_York", label: "(GMT-05:00) America/New_York" },
  { value: "Europe/London", label: "(GMT+00:00) Europe/London" },
];

const DATE_OPTIONS = [
  { value: "MM/DD/YYYY", label: "MM/DD/YYYY" },
  { value: "DD/MM/YYYY", label: "DD/MM/YYYY" },
  { value: "YYYY-MM-DD", label: "YYYY-MM-DD" },
];

const TIME_OPTIONS = [
  { value: "12h", label: "12 Hour (01:00 PM)" },
  { value: "24h", label: "24 Hour (13:00)" },
];

function loadLS(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v ? JSON.parse(v) : fallback;
  } catch { return fallback; }
}
function saveLS(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
}

export default function Settings() {
  const { theme, setTheme } = useTheme();
  const { scale, setScale, SCALES } = useDisplay();

  const [active, setActive] = useState("General");
  const [systemName, setSystemName] = useState(() => {
    try { return JSON.parse(localStorage.getItem(LS_KEYS.systemName)) ?? DEFAULTS.systemName; } catch { return DEFAULTS.systemName; }
  });
  const [timeZone, setTimeZone] = useState(() => {
    try { return JSON.parse(localStorage.getItem(LS_KEYS.timeZone)) ?? DEFAULTS.timeZone; } catch { return DEFAULTS.timeZone; }
  });
  const [dateFormat, setDateFormat] = useState(() => {
    try { return JSON.parse(localStorage.getItem(LS_KEYS.dateFormat)) ?? DEFAULTS.dateFormat; } catch { return DEFAULTS.dateFormat; }
  });
  const [timeFormat, setTimeFormat] = useState(() => {
    try { return JSON.parse(localStorage.getItem(LS_KEYS.timeFormat)) ?? DEFAULTS.timeFormat; } catch { return DEFAULTS.timeFormat; }
  });
  const [flags, setFlags] = useState(() => loadLS(LS_KEYS.notifications, DEFAULTS.notifications));
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState(false);

  // Track dirty when any value diverges from stored LS
  useEffect(() => {
    const cur = {
      systemName: (() => { try { return JSON.parse(localStorage.getItem(LS_KEYS.systemName)) ?? DEFAULTS.systemName; } catch { return DEFAULTS.systemName; } })(),
      timeZone: (() => { try { return JSON.parse(localStorage.getItem(LS_KEYS.timeZone)) ?? DEFAULTS.timeZone; } catch { return DEFAULTS.timeZone; } })(),
      dateFormat: (() => { try { return JSON.parse(localStorage.getItem(LS_KEYS.dateFormat)) ?? DEFAULTS.dateFormat; } catch { return DEFAULTS.dateFormat; } })(),
      timeFormat: (() => { try { return JSON.parse(localStorage.getItem(LS_KEYS.timeFormat)) ?? DEFAULTS.timeFormat; } catch { return DEFAULTS.timeFormat; } })(),
    };
    const notifStored = loadLS(LS_KEYS.notifications, DEFAULTS.notifications);
    const isDirty = systemName !== cur.systemName || timeZone !== cur.timeZone || dateFormat !== cur.dateFormat || timeFormat !== cur.timeFormat
      || JSON.stringify(flags) !== JSON.stringify(notifStored);
    setDirty(isDirty);
  }, [systemName, timeZone, dateFormat, timeFormat, flags]);

  // Notifications persist instantly (toggle feels live), but also covered by Save
  useEffect(() => { saveLS(LS_KEYS.notifications, flags); }, [flags]);

  const save = () => {
    saveLS(LS_KEYS.systemName, systemName);
    saveLS(LS_KEYS.timeZone, timeZone);
    saveLS(LS_KEYS.dateFormat, dateFormat);
    saveLS(LS_KEYS.timeFormat, timeFormat);
    saveLS(LS_KEYS.notifications, flags);
    // Notify other components (header clock reads LS)
    window.dispatchEvent(new Event("alcogate-settings-changed"));
    setSaved(true);
    setDirty(false);
    setTimeout(() => setSaved(false), 1800);
  };

  const resetGeneral = () => {
    setSystemName(DEFAULTS.systemName);
    setTimeZone(DEFAULTS.timeZone);
    setDateFormat(DEFAULTS.dateFormat);
    setTimeFormat(DEFAULTS.timeFormat);
    setFlags(DEFAULTS.notifications);
  };

  return (
    <div className="p-2 flex flex-col gap-2 max-w-[1200px] mx-auto">
      <div className="flex items-center gap-2">
        <h1 className="text-[11px] font-bold tracking-[0.08em]" style={{ color: "var(--text-primary)" }}>SETTINGS</h1>
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>System configuration and preferences</span>
        <span className="ml-auto flex items-center gap-2">
          {dirty && !saved && <span className="text-[10px] font-mono" style={{ color: "var(--near)" }}>● Unsaved changes</span>}
          <span className="text-[10px] font-mono" style={{ color: saved ? "var(--pass)" : "transparent" }}>{saved ? "✓ Saved" : "·"}</span>
        </span>
      </div>

      <div className="grid grid-cols-12 gap-2">
        <div className="col-span-12 lg:col-span-2 rounded-[6px] p-1 flex flex-col gap-0.5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "var(--shadow-card)" }}>
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
          {/* APPEARANCE — theme + density */}
          <div className="rounded-[6px] p-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "var(--shadow-card)" }}>
            <div className="text-[10px] font-bold tracking-[0.08em] mb-3" style={{ color: "var(--text-primary)" }}>APPEARANCE</div>
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[11px]" style={{ color: "var(--text-primary)" }}>Theme</div>
                  <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>{theme ? "Dark — terminal" : "Light — airy"} • synced with header toggle</div>
                </div>
                <div className="flex items-center gap-1 p-1 rounded-full" style={{ background: "var(--bg-card-alt)", border: "1px solid var(--border-subtle)" }}>
                  <button onClick={() => setTheme(false)} className="px-3 py-1 rounded-full text-[11px] flex items-center gap-1.5 transition-colors" style={{ background: !theme ? "var(--bg-card)" : "transparent", color: !theme ? "var(--text-primary)" : "var(--text-muted)", boxShadow: !theme ? "0 1px 4px rgba(0,0,0,0.1)" : "none", border: !theme ? "1px solid var(--border)" : "1px solid transparent" }}>
                    <Sun size={12} /> Light
                  </button>
                  <button onClick={() => setTheme(true)} className="px-3 py-1 rounded-full text-[11px] flex items-center gap-1.5 transition-colors" style={{ background: theme ? "var(--bg-card)" : "transparent", color: theme ? "var(--text-primary)" : "var(--text-muted)", boxShadow: theme ? "0 1px 4px rgba(0,0,0,0.2)" : "none", border: theme ? "1px solid var(--border)" : "1px solid transparent" }}>
                    <Moon size={12} /> Dark
                  </button>
                </div>
              </div>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[11px]" style={{ color: "var(--text-primary)" }}>Display density</div>
                  <div className="text-[10px]" style={{ color: "var(--text-muted)" }}>Affects font size across all terminals • header also cycles S/M/L</div>
                </div>
                <div className="flex items-center gap-1 p-1 rounded-full" style={{ background: "var(--bg-card-alt)", border: "1px solid var(--border-subtle)" }}>
                  {Object.entries(SCALES).map(([k, v]) => (
                    <button key={k} onClick={() => setScale(k)} className="px-2.5 py-1 rounded-full text-[11px] font-mono font-bold min-w-[32px] transition-colors" style={{ background: scale === k ? "var(--bg-card)" : "transparent", color: scale === k ? "var(--pass)" : "var(--text-muted)", border: scale === k ? "1px solid var(--border)" : "1px solid transparent", boxShadow: scale === k ? "0 1px 4px rgba(0,0,0,0.1)" : "none" }}>
                      {v.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="rounded-[6px] p-3 relative overflow-hidden" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "var(--shadow-card)" }}>
            <div className="absolute inset-0 pointer-events-none opacity-[0.02] dark:opacity-[0.04]" style={{ background: "repeating-linear-gradient(0deg, transparent 0 2px, var(--text-primary) 2px 3px)" }} />
            <div className="flex items-center justify-between mb-3">
              <div className="text-[10px] font-bold tracking-[0.08em]" style={{ color: "var(--text-primary)" }}>GENERAL — {active.toUpperCase()}</div>
              {active === "General" && <button onClick={resetGeneral} className="text-[10px] px-2 py-1 rounded border" style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)" }}>Reset defaults</button>}
            </div>
            <div className="space-y-2.5 text-[11px] relative">
              {active === "General" ? (
                <>
                  <div className="flex items-center justify-between gap-3 group">
                    <span style={{ color: "var(--text-muted)" }}>System Name</span>
                    <input value={systemName} onChange={e => setSystemName(e.target.value)} placeholder="ALCOGATE System" className="text-[11px] px-2 py-1.5 rounded-[4px] border min-w-[190px] outline-none focus:border-[var(--accent)]" style={{ background: "var(--bg-card-alt)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }} />
                  </div>
                  <div className="flex items-center justify-between gap-3 group">
                    <span style={{ color: "var(--text-muted)" }}>Time Zone</span>
                    <select value={timeZone} onChange={e => setTimeZone(e.target.value)} className="text-[11px] px-2 py-1.5 rounded-[4px] border min-w-[190px] cursor-pointer outline-none" style={{ background: "var(--bg-card-alt)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }}>
                      {TZ_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div className="flex items-center justify-between gap-3 group">
                    <span style={{ color: "var(--text-muted)" }}>Date Format</span>
                    <select value={dateFormat} onChange={e => setDateFormat(e.target.value)} className="text-[11px] px-2 py-1.5 rounded-[4px] border min-w-[190px] cursor-pointer outline-none" style={{ background: "var(--bg-card-alt)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }}>
                      {DATE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div className="flex items-center justify-between gap-3 group">
                    <span style={{ color: "var(--text-muted)" }}>Time Format</span>
                    <select value={timeFormat} onChange={e => setTimeFormat(e.target.value)} className="text-[11px] px-2 py-1.5 rounded-[4px] border min-w-[190px] cursor-pointer outline-none" style={{ background: "var(--bg-card-alt)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }}>
                      {TIME_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                </>
              ) : (
                <div className="py-6 text-center text-[11px]" style={{ color: "var(--text-muted)" }}>
                  <Monitor size={16} className="mx-auto mb-2 opacity-40" />
                  {active} settings — no configurable fields yet. Switch to <b style={{ color: "var(--text-secondary)" }}>General</b> for live preferences.
                </div>
              )}
            </div>
          </div>

          <div className="rounded-[6px] p-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "var(--shadow-card)" }}>
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
          <div className="rounded-[6px] p-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "var(--shadow-card)" }}>
            <div className="text-[10px] font-bold tracking-[0.08em] mb-2" style={{ color: "var(--text-primary)" }}>SYSTEM INFO</div>
            <div className="space-y-1.5 text-[10px]">
              <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Version</span><span className="font-mono" style={{ color: "var(--text-primary)" }}>1.0.0</span></div>
              <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Device</span><span className="font-mono" style={{ color: "var(--text-primary)" }}>{systemName} · ALCOGATE-01</span></div>
              <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Status</span><span className="flex items-center gap-1" style={{ color: "var(--pass)" }}><span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_6px_#1FC184]" />Online</span></div>
              <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Uptime</span><span className="font-mono" style={{ color: "var(--text-primary)" }}>3d 14h 22m</span></div>
              <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Last Restart</span><span className="font-mono" style={{ color: "var(--text-primary)" }}>May 16, 06:20 AM</span></div>
            </div>
          </div>

          <div className="rounded-[6px] p-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "var(--shadow-card)" }}>
            <div className="text-[10px] font-bold tracking-[0.08em] mb-2" style={{ color: "var(--text-primary)" }}>STORAGE</div>
            <div className="space-y-3">
              <div>
                <div className="flex justify-between text-[10px]"><span style={{ color: "var(--text-muted)" }}>Database</span><span className="font-mono" style={{ color: "var(--text-secondary)" }}>3.2 GB / 50 GB</span></div>
                <div className="h-1.5 rounded-full mt-1 overflow-hidden relative" style={{ background: "var(--bg-card-alt)", boxShadow: "inset 0 1px 2px rgba(0,0,0,0.15)" }}><div className="h-full rounded-full" style={{ width: "12%", background: "linear-gradient(90deg, #0F231B, #1FC184)", boxShadow: "0 0 6px rgba(31,193,132,0.5)" }} /></div>
              </div>
              <div>
                <div className="flex justify-between text-[10px]"><span style={{ color: "var(--text-muted)" }}>Logs</span><span className="font-mono" style={{ color: "var(--text-secondary)" }}>1.1 GB / 20 GB</span></div>
                <div className="h-1.5 rounded-full mt-1 overflow-hidden relative" style={{ background: "var(--bg-card-alt)", boxShadow: "inset 0 1px 2px rgba(0,0,0,0.15)" }}><div className="h-full rounded-full" style={{ width: "8%", background: "var(--accent)" }} /></div>
              </div>
            </div>
          </div>

          <button onClick={save} disabled={!dirty && !saved} className="ml-auto text-[11px] px-4 py-1.5 rounded-[4px] font-medium transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed" style={{ background: saved ? "var(--pass)" : dirty ? "var(--accent)" : "var(--border-strong)", color: saved || dirty ? "#fff" : "var(--text-muted)", boxShadow: saved ? "0 0 12px rgba(31,193,132,0.5)" : dirty ? "0 4px 12px rgba(0,0,0,0.15)" : "none" }}>{saved ? "✓ Saved" : dirty ? "Save Changes" : "All saved"}</button>
          <div className="text-[10px] text-center" style={{ color: "var(--text-muted)" }}>Theme & density apply instantly; General fields need Save.</div>
        </div>
      </div>
    </div>
  );
}
