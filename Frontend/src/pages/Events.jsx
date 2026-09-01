// Events.jsx — ALCOGATE terminal: chronological timeline
import { useState, useEffect, useMemo } from "react";
import { API_BASE } from "../api";

function tone(label = "") {
  const l = label.toLowerCase();
  if (l.includes("flag") || l.includes("over") || l.includes("denied")) return { color: "var(--over)", bg: "var(--over-bg)", dot: "bg-red-500", gate: "LOCKED" };
  if (l.includes("analyz") || l.includes("waiting")) return { color: "var(--near)", bg: "var(--near-bg)", dot: "bg-amber-500", gate: "WAITING" };
  return { color: "var(--pass)", bg: "var(--pass-bg)", dot: "bg-emerald-500", gate: "OPENED" };
}

export default function Events() {
  const [logs, setLogs] = useState([]);
  const [subjects, setSubjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [f, setF] = useState("All Events");
  const [gate, setGate] = useState("All Gates");
  const [day, setDay] = useState("Today");
  const [page, setPage] = useState(1);
  const perPage = 8;

  useEffect(() => {
    let a = true;
    const load = async () => {
      try {
        const [r, s] = await Promise.all([
          fetch(`${API_BASE}/deployment-logs?limit=200`),
          fetch(`${API_BASE}/recognition/subjects`).catch(() => null),
        ]);
        if (r.ok && a) {
          const j = await r.json();
          setLogs(j.logs || []);
        }
        if (s && s.ok && a) {
          const sj = await s.json();
          setSubjects(sj || []);
        }
      } catch {}
      if (a) setLoading(false);
    };
    load();
    const t = setInterval(load, 6000);
    return () => { a = false; clearInterval(t); };
  }, []);

  // useEffect pagination reset on filter change
  useEffect(() => { setPage(1); }, [f, gate, day, q]);

  const mock = [
    { id: "184", date: new Date().toISOString(), prediction: "Analyzing", confidence: 0.964, bac: 0.13, name: "Unknown", gate: "WAITING" },
    { id: "183", date: new Date(Date.now() - 100000).toISOString(), prediction: "Over Limit", confidence: 0.782, bac: 0.45, name: "Mark Solis" },
    { id: "182", date: new Date(Date.now() - 200000).toISOString(), prediction: "Pass", confidence: 0.978, bac: 0.10, name: "Jane Dela Cruz" },
    { id: "181", date: new Date(Date.now() - 300000).toISOString(), prediction: "Pass", confidence: 0.971, bac: 0.10, name: "Juan Dela Cruz" },
    { id: "180", date: new Date(Date.now() - 400000).toISOString(), prediction: "Pass", confidence: 0.965, bac: 0.11, name: "Ken Alvarez" },
  ];

  const raw = logs.length ? logs : mock;

  const subjectMap = useMemo(() => {
    const m = new Map();
    for (const s of subjects) m.set(String(s.id), s.name);
    return m;
  }, [subjects]);

  const data = useMemo(() => raw.map((l) => {
    const pred = (l.prediction || l.label || "Pass").toString();
    const label = pred.toUpperCase().includes("OVER") ? "FLAGGED" : pred.toUpperCase().includes("ANALYZ") ? "ANALYZING" : pred.toUpperCase() === "PASS" ? "PASSED" : pred.toUpperCase();
    const t = tone(label);
    // person match: if log has subject_id and we know the name, show it; otherwise Unknown
    const sid = l.subject_id != null ? String(l.subject_id) : null;
    const resolved = sid && subjectMap.get(sid);
    const name = resolved || "Unknown";
    const isUnknown = !resolved;
    return {
      id: String(l.id),
      time: l.date ? new Date(l.date).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true }) : "--",
      label,
      color: t.color, bg: t.bg, dot: t.dot,
      name,
      isUnknown,
      ppm: l.bac != null ? Number(l.bac).toFixed(2) : l.mq3_avg != null ? Number(l.mq3_avg).toFixed(2) : "0.10",
      conf: l.confidence != null ? `${(Number(l.confidence) * 100).toFixed(1)}%` : "—",
      gate: l.gate || t.gate,
      gateTone: t.color,
      rawDate: l.date,
      riskGate: gate,
    };
  }), [raw, gate, subjectMap]);

  const filtered = useMemo(() => {
    let d = data;
    if (f !== "All Events") d = d.filter((x) => x.label === f.toUpperCase());
    if (gate !== "All Gates") d = d.filter(() => true); // single gate for now
    if (day === "Today") {
      const today = new Date().toDateString();
      d = d.filter((x) => !x.rawDate || new Date(x.rawDate).toDateString() === today || !logs.length); // keep mock
    }
    if (q.trim()) {
      const s = q.toLowerCase();
      d = d.filter((x) => x.id.toLowerCase().includes(s) || x.name.toLowerCase().includes(s) || x.label.toLowerCase().includes(s));
    }
    return d;
  }, [data, f, gate, day, q, logs.length]);

  const pages = Math.max(1, Math.ceil(filtered.length / perPage));
  const paged = filtered.slice((page - 1) * perPage, page * perPage);
  const totalPages = Math.min(pages, 24); // cap like reference

  return (
    <div className="p-2 max-w-[1100px] mx-auto flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h1 className="text-[11px] font-bold tracking-[0.08em]" style={{ color: "var(--text-primary)" }}>EVENTS</h1>
        <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>All gate activity in chronological order</span>
      </div>

      <div className="flex gap-1.5 items-center flex-wrap">
        <select value={f} onChange={(e) => setF(e.target.value)} className="text-[11px] px-2 py-1 rounded-[4px] border" style={{ background: "var(--bg-card)", color: "var(--text-secondary)", borderColor: "var(--border-subtle)" }}>
          <option>All Events</option>
          <option>Passed</option>
          <option>Flagged</option>
          <option>Analyzing</option>
        </select>
        <select value={gate} onChange={(e) => setGate(e.target.value)} className="text-[11px] px-2 py-1 rounded-[4px] border" style={{ background: "var(--bg-card)", color: "var(--text-secondary)", borderColor: "var(--border-subtle)" }}>
          <option>All Gates</option>
          <option>GATE 01</option>
        </select>
        <select value={day} onChange={(e) => setDay(e.target.value)} className="text-[11px] px-2 py-1 rounded-[4px] border" style={{ background: "var(--bg-card)", color: "var(--text-secondary)", borderColor: "var(--border-subtle)" }}>
          <option>Today</option>
          <option>All time</option>
        </select>
        <div className="relative">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search event, ID, name…" className="text-[11px] pl-6 pr-2 py-1 rounded-[4px] border min-w-[200px]" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }} />
          <span className="absolute left-2 top-1.5 text-[10px]" style={{ color: "var(--text-muted)" }}>⌕</span>
        </div>
        <span className="ml-auto text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>{filtered.length} events</span>
      </div>

      <div className="rounded-[6px] overflow-hidden flex flex-col" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
        {/* column headers — dense like reference */}
        <div className="hidden sm:grid grid-cols-12 gap-2 px-3 py-1.5 text-[8px] tracking-[0.08em] uppercase font-semibold" style={{ background: "var(--bg-card-alt)", color: "var(--text-muted)", borderBottom: "1px solid var(--border-subtle)" }}>
          <span className="col-span-2">Time / ID</span>
          <span className="col-span-2">Status</span>
          <span className="col-span-2">Person</span>
          <span className="col-span-2">MQ-3 Avg</span>
          <span className="col-span-2">Confidence</span>
          <span className="col-span-2">Gate</span>
        </div>

        <div className="relative min-h-[320px]">
          {/* timeline line */}
          <div className="absolute left-[18px] top-2 bottom-2 w-px hidden sm:block" style={{ background: "var(--border-subtle)" }} />

          {loading ? (
            <div className="p-4 space-y-2">
              {[...Array(6)].map((_, i) => <div key={i} className="skeleton h-11" />)}
            </div>
          ) : paged.length === 0 ? (
            <div className="p-8 text-center text-[11px]" style={{ color: "var(--text-muted)" }}>No events match filters</div>
          ) : (
            <div className="divide-y" style={{ borderColor: "var(--border-subtle)" }}>
              {paged.map((r) => (
                <div key={r.id} data-interactive className="grid grid-cols-12 gap-2 items-center px-2 sm:px-3 py-2.5 cursor-pointer" style={{ background: "transparent", borderLeft: "2px solid transparent" }}>
                  <div className="col-span-4 sm:col-span-2 flex items-center gap-2">
                    <div className="hidden sm:flex w-3.5 h-3.5 rounded-full items-center justify-center shrink-0" style={{ background: r.bg, border: `1px solid ${r.color}` }}>
                      <span className={`w-1.5 h-1.5 rounded-full ${r.dot} ${r.label === "ANALYZING" ? "animate-pulse" : ""}`} />
                    </div>
                    <div className="flex flex-col min-w-0">
                      <span className="text-[10px] font-mono leading-none" style={{ color: "var(--text-secondary)" }}>{r.time}</span>
                      <span className="text-[9px] font-mono leading-none mt-0.5" style={{ color: "var(--text-muted)" }}>ID #{r.id}</span>
                    </div>
                  </div>

                  <div className="col-span-4 sm:col-span-2">
                    <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-[3px] border inline-block tracking-wide" style={{ color: r.color, borderColor: r.color, background: r.bg }}>{r.label}</span>
                  </div>

                  <div className="col-span-4 sm:col-span-2 flex items-center gap-2 min-w-0">
                    <div className="w-6 h-6 rounded-full bg-[#080C10] shrink-0 flex items-center justify-center overflow-hidden" style={{ border: `1px solid ${r.isUnknown ? "var(--border-subtle)" : "var(--border)"}` }}>
                      <span className="text-[7px] font-bold" style={{ color: r.isUnknown ? "var(--text-muted)" : "var(--text-secondary)" }}>{r.isUnknown ? "?" : r.name.slice(0, 2).toUpperCase()}</span>
                    </div>
                    <div className="flex flex-col min-w-0">
                      <span className="text-[11px] truncate leading-none" style={{ color: r.isUnknown ? "var(--text-muted)" : "var(--text-primary)", fontStyle: r.isUnknown ? "italic" : "normal" }}>{r.name}</span>
                      <span className="text-[9px] truncate leading-none mt-0.5" style={{ color: "var(--text-muted)" }}>{r.isUnknown ? "No match in system" : r.label === "ANALYZING" ? "Analyzing…" : r.label === "FLAGGED" ? "Flagged" : "Cleared"}</span>
                    </div>
                  </div>

                  <div className="hidden sm:flex col-span-2 flex-col">
                    <span className="text-[7px] tracking-wide uppercase" style={{ color: "var(--text-muted)" }}>MQ-3 Avg</span>
                    <span className="text-[11px] font-mono font-medium" style={{ color: r.label === "FLAGGED" ? "var(--over)" : "var(--text-primary)" }}>{r.ppm} PPM</span>
                  </div>
                  <div className="hidden sm:flex col-span-2 flex-col">
                    <span className="text-[7px] tracking-wide uppercase" style={{ color: "var(--text-muted)" }}>Confidence</span>
                    <span className="text-[11px] font-mono font-medium" style={{ color: r.color }}>{r.conf}</span>
                  </div>
                  <div className="hidden sm:flex col-span-2 flex-col">
                    <span className="text-[7px] tracking-wide uppercase" style={{ color: "var(--text-muted)" }}>Gate</span>
                    <span className="text-[10px] font-bold tracking-wide" style={{ color: r.gateTone }}>{r.gate}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center justify-center gap-1 py-2 px-2 flex-wrap" style={{ borderTop: "1px solid var(--border-subtle)", background: "var(--bg-card)" }}>
          <div className="flex items-center gap-1">
            {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => i + 1).map((n) => (
              <button
                key={n}
                onClick={() => setPage(n)}
                className="w-6 h-6 rounded-[4px] text-[10px] font-medium"
                style={{ background: page === n ? "var(--pass)" : "transparent", color: page === n ? "#fff" : "var(--text-muted)", border: `1px solid ${page === n ? "var(--pass)" : "var(--border-subtle)"}` }}
              >
                {n}
              </button>
            ))}
            {pages > 5 && <span className="text-[10px] px-1" style={{ color: "var(--text-muted)" }}>… {pages}</span>}
            {pages > 5 && <button onClick={() => setPage(pages)} className="w-6 h-6 rounded-[4px] text-[10px]" style={{ color: "var(--text-muted)", border: "1px solid var(--border-subtle)" }}>{pages}</button>}
          </div>
          <button onClick={() => setPage((p) => Math.min(p + 1, pages))} disabled={page >= pages} className="ml-2 text-[10px] px-2 py-1 rounded-[4px] border disabled:opacity-40" style={{ color: "var(--text-secondary)", borderColor: "var(--border-subtle)", background: "var(--bg-card)" }}>Next ›</button>
        </div>
      </div>
    </div>
  );
}
