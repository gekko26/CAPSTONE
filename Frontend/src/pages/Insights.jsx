// Insights.jsx — Terminal Depth: hardware extrusion + 3Dish (no generic dashboard)
import { useState, useEffect, useMemo, useRef } from "react";
import { API_BASE } from "../api";
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, BarChart, Bar, PieChart, Pie, Cell } from "recharts";

function useAnimatedNumber(value, ms = 600) {
  const [display, setDisplay] = useState(value);
  const raf = useRef(null);
  const prev = useRef(value);
  useEffect(() => {
    const from = prev.current;
    const to = Number(value);
    if (from === to) return;
    const start = performance.now();
    const step = (now) => {
      const p = Math.min((now - start) / ms, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      setDisplay(from + (to - from) * eased);
      if (p < 1) raf.current = requestAnimationFrame(step);
      else prev.current = to;
    };
    cancelAnimationFrame(raf.current);
    raf.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf.current);
  }, [value, ms]);
  return display;
}

function StatCard({ label, value, sub, color, pulse }) {
  const anim = useAnimatedNumber(Number(value) || 0);
  const isNum = !isNaN(Number(value));
  const shown = isNum ? (Number.isInteger(Number(value)) ? Math.round(anim).toLocaleString() : anim.toFixed(2)) : value;
  const [flash, setFlash] = useState(false);
  const prevVal = useRef(value);
  useEffect(() => {
    if (prevVal.current !== value) {
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 520);
      prevVal.current = value;
      return () => clearTimeout(t);
    }
  }, [value]);
  return (
    <div
      data-interactive
      className="rounded-[6px] px-3 py-2.5 cursor-pointer relative overflow-hidden group"
      style={{
        background: flash ? "var(--bg-active)" : "var(--bg-card)",
        border: `1px solid ${flash ? "var(--pass)" : "var(--border-subtle)"}`,
        boxShadow: flash ? "0 0 0 1px var(--pass-bg), 0 8px 20px rgba(0,0,0,0.35)" : "0 1px 0 rgba(255,255,255,0.02) inset, 0 6px 14px rgba(0,0,0,0.25)",
        transform: flash ? "translateY(-1px)" : "translateY(0)",
        transition: "all 0.35s ease",
      }}
    >
      <div className="absolute inset-x-0 top-0 h-px" style={{ background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.06), transparent)" }} />
      {pulse && <span className="absolute top-2 right-2 w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_6px_rgba(31,193,132,0.7)]" />}
      <div className="text-[8px] tracking-[0.08em] uppercase font-semibold" style={{ color: "var(--text-muted)" }}>{label}</div>
      <div className="text-[18px] font-bold font-mono mt-1 tabular-nums" style={{ color: flash ? "var(--pass)" : color, textShadow: flash ? "0 0 10px rgba(31,193,132,0.4)" : "none" }}>{shown}</div>
      <div className="text-[9px] flex items-center gap-1" style={{ color: "var(--text-muted)" }}>
        <span className={`w-1 h-1 rounded-full ${pulse ? "bg-emerald-500 animate-pulse" : "bg-transparent"}`} />
        {sub}
      </div>
    </div>
  );
}

// 3D block bar shape
function BlockBar(props) {
  const { x, y, width, height, fill } = props;
  if (height <= 0) return null;
  const depth = 4;
  const top = `M${x},${y} L${x + depth},${y - depth} L${x + width + depth},${y - depth} L${x + width},${y} Z`;
  const side = `M${x + width},${y} L${x + width + depth},${y - depth} L${x + width + depth},${y + height - depth} L${x + width},${y + height} Z`;
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={fill} rx={1.5} />
      <path d={top} fill="color-mix(in srgb, white 18%, transparent)" opacity={0.9} />
      <path d={side} fill="black" opacity={0.22} />
    </g>
  );
}

export default function Insights() {
  const [stats, setStats] = useState({ total: 0, passed: 0, flagged: 0, avg: 0 });
  const [weekly, setWeekly] = useState([]);
  const [logs, setLogs] = useState([]);
  const [range, setRange] = useState("Today");
  const [gate, setGate] = useState("All Gates");
  const [livePulse, setLivePulse] = useState(false);

  useEffect(() => {
    let a = true;
    const load = async () => {
      try {
        const r = await fetch(`${API_BASE}/deployment-logs?limit=200`);
        if (r.ok && a) {
          const j = await r.json();
          const total = j.stats?.total ?? j.logs?.length ?? 0;
          const over = j.stats?.over_limit ?? 0;
          const near = j.stats?.near_limit ?? 0;
          const logsArr = j.logs || [];
          // avg estimated BAC over breath events only
          const estVals = logsArr.map(l=> l.estimated_bac).filter(v=> v!=null);
          const avgEst = estVals.length ? estVals.reduce((a,b)=>a+b,0)/estVals.length : 0;
          setStats({ total, passed: total - over - near, flagged: over + near, avg: avgEst });
          setWeekly(j.weekly || []);
          setLogs(logsArr);
          setLivePulse(true);
          setTimeout(() => setLivePulse(false), 600);
        }
      } catch {}
    };
    load();
    const t = setInterval(load, 5000);
    return () => { a = false; clearInterval(t); };
  }, []);

  const trend = useMemo(() => {
    if (weekly.length) return weekly.map((w) => ({ t: w.day, v: w.clear + w.alert + w.breach + w.intercepted }));
    return [];
  }, [weekly]);

  const passPct = stats.total ? ((stats.passed / stats.total) * 100).toFixed(1) : "—";
  const distribution = [
    { name: "Passed", value: Number(passPct) },
    { name: "Flagged", value: Number((100 - Number(passPct)).toFixed(1)) },
  ];

  const alcoholDist = useMemo(() => {
    const empty = [
      { label: "Trace 0.01-0.02", v: 0, pct: 0, color: "var(--near)" },
      { label: "Light 0.02-0.05", v: 0, pct: 0, color: "var(--near)" },
      { label: "Over PH ≥0.05", v: 0, pct: 0, color: "var(--over)" },
    ];
    if (!logs.length) return empty;
    const buckets = { trace: 0, light: 0, over: 0, sober: 0 };
    for (const l of logs) {
      const b = l.estimated_bac ?? l.bac ?? 0;
      const tier = l.bac_tier || (b >= 0.05 ? "over" : b >= 0.02 ? "light" : b > 0.001 ? "trace" : "sober");
      if (tier === "trace") buckets.trace++;
      else if (tier === "light") buckets.light++;
      else if (tier === "over") buckets.over++;
      else buckets.sober++;
    }
    const denom = buckets.trace + buckets.light + buckets.over || 1;
    return [
      { label: "Trace 0.01-0.02", v: buckets.trace, pct: ((buckets.trace / denom) * 100).toFixed(1), color: "var(--near)" },
      { label: "Light 0.02-0.05", v: buckets.light, pct: ((buckets.light / denom) * 100).toFixed(1), color: "var(--near)" },
      { label: "Over PH ≥0.05", v: buckets.over, pct: ((buckets.over / denom) * 100).toFixed(1), color: "var(--over)" },
    ];
  }, [logs]);

  const peak = useMemo(() => {
    if (logs.length) {
      const byHour = Array(24).fill(0);
      for (const l of logs) if (l.date) byHour[new Date(l.date).getHours()]++;
      return byHour.map((v, h) => ({ h, v }));
    }
    return Array.from({ length: 24 }, (_, i) => ({ h: i, v: 0 }));
  }, [logs]);

  const animPct = useAnimatedNumber(Number(passPct));

  return (
    <div className="p-2 flex flex-col gap-2 max-w-[1200px] mx-auto">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <h1 className="text-[11px] font-bold tracking-[0.08em]" style={{ color: "var(--text-primary)" }}>INSIGHTS</h1>
          <span className="flex items-center gap-1 text-[10px] font-medium">
            <span className={`w-1.5 h-1.5 rounded-full bg-emerald-500 ${livePulse ? "animate-ping" : "animate-pulse"} shadow-[0_0_8px_rgba(31,193,132,0.8)]`} />
            <span style={{ color: livePulse ? "var(--pass)" : "var(--text-muted)" }}>{livePulse ? "UPDATING…" : "LIVE"}</span>
          </span>
        </div>
        <div className="flex gap-1.5">
          <select value={range} onChange={(e) => setRange(e.target.value)} className="text-[11px] px-2 py-1 rounded-[4px] border cursor-pointer" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>
            <option>Today</option>
            <option>7 days</option>
            <option>All time</option>
          </select>
          <select value={gate} onChange={(e) => setGate(e.target.value)} className="text-[11px] px-2 py-1 rounded-[4px] border cursor-pointer" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>
            <option>All Gates</option>
            <option>GATE 01</option>
          </select>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <StatCard label="TOTAL PASSAGES" value={stats.total} sub={range === "Today" ? "today • live" : "+ 12% vs yesterday"} color="var(--text-primary)" pulse />
        <StatCard label="PASSED" value={stats.passed} sub={`${passPct}% • cleared`} color="var(--pass)" pulse />
        <StatCard label="FLAGGED / DENIED" value={stats.flagged} sub={`${(100 - Number(passPct)).toFixed(1)}% • blocked`} color="var(--over)" pulse={stats.flagged > 0} />
        <StatCard label="AVG EST. BAC" value={stats.avg != null ? Number(stats.avg).toFixed(3) : "—"} sub={`${stats.avg != null && stats.avg >=0.05 ? "PH FAIL • avg ≥0.05" : "LOW • calibrated (PH 0.05)"}`} color={stats.avg != null && stats.avg >=0.05 ? "var(--over)" : "var(--text-primary)"} />
      </div>

      <div className="grid grid-cols-12 gap-2">
        {/* Passages — depth area with neon glow */}
        <div className="col-span-12 lg:col-span-8 rounded-[6px] p-3 relative overflow-hidden" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "0 10px 28px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.03)" }}>
          <div className="absolute inset-0 opacity-[0.03]" style={{ background: "repeating-linear-gradient(0deg, transparent 0 2px, rgba(255,255,255,0.4) 2px 3px)" }} />
          <div className="text-[9px] tracking-[0.08em] uppercase font-semibold mb-2 flex items-center gap-1.5 relative" style={{ color: "var(--text-muted)" }}>
            Passages Over Time
            <span className="w-1 h-1 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_6px_#1FC184]" />
          </div>
          <div className="h-[150px] relative">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={trend} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="passFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#1FC184" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#1FC184" stopOpacity={0} />
                  </linearGradient>
                  <filter id="glow">
                    <feGaussianBlur stdDeviation="3" result="coloredBlur" />
                    <feMerge>
                      <feMergeNode in="coloredBlur" />
                      <feMergeNode in="SourceGraphic" />
                    </feMerge>
                  </filter>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" vertical={false} />
                <XAxis dataKey="t" tick={{ fontSize: 9, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 9, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)", fontSize: 10, borderRadius: 4 }} cursor={{ stroke: "var(--border)", strokeDasharray: "3 3" }} />
                <Area type="monotone" dataKey="v" stroke="#1FC184" strokeWidth={2} fill="url(#passFill)" dot={false} activeDot={{ r: 3.5, fill: "#1FC184", stroke: "var(--bg-card)", strokeWidth: 1.5 }} filter="url(#glow)" isAnimationActive animationDuration={900} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Outcome — extruded donut depth */}
        <div className="col-span-12 lg:col-span-4 rounded-[6px] p-3 flex flex-col items-center relative overflow-hidden" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "0 10px 28px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.03)" }}>
          <div className="text-[9px] tracking-[0.08em] uppercase font-semibold self-start" style={{ color: "var(--text-muted)" }}>Outcome Distribution</div>
          <div className="h-[150px] w-full relative" style={{ filter: "drop-shadow(0 8px 14px rgba(0,0,0,0.45))" }}>
            {/* depth ring under */}
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none" style={{ transform: "translateY(4px)", opacity: 0.9 }}>
              <div className="w-[124px] h-[124px] rounded-full" style={{ background: "radial-gradient(circle, #0A0E13 58%, #1A212C 60%)", border: "1px solid var(--border-subtle)" }} />
            </div>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={distribution} dataKey="value" cx="50%" cy="50%" innerRadius={48} outerRadius={64} strokeWidth={0} paddingAngle={2} isAnimationActive animationDuration={800}>
                  <Cell fill="#1FC184" />
                  <Cell fill="#E85454" />
                </Pie>
                <Tooltip contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)", fontSize: 10, borderRadius: 4 }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="text-[13px] font-bold font-mono -mt-7 tabular-nums relative" style={{ color: "var(--pass)", textShadow: "0 0 12px rgba(31,193,132,0.5)" }}>{animPct.toFixed(1)}%</div>
          <div className="text-[9px] mt-0.5" style={{ color: "var(--text-muted)" }}>Passed</div>
          <div className="flex gap-3 mt-2 text-[9px] flex-wrap justify-center">
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full" style={{ background: "var(--pass-bg)", color: "var(--pass)", boxShadow: "0 1px 6px rgba(31,193,132,0.25)" }}><span className="w-2 h-2 rounded-full" style={{ background: "var(--pass)" }} /> Passed {stats.passed} ({passPct}%)</span>
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full" style={{ background: "var(--over-bg)", color: "var(--over)" }}><span className="w-2 h-2 rounded-full" style={{ background: "var(--over)" }} /> Flagged {stats.flagged} ({(100 - Number(passPct)).toFixed(1)}%)</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-2">
        <div className="col-span-12 lg:col-span-6 rounded-[6px] p-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "0 8px 20px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.03)" }}>
          <div className="text-[9px] tracking-[0.08em] uppercase font-semibold mb-3" style={{ color: "var(--text-muted)" }}>Est. BAC Distribution — PH Tiers (Inside Breath Alcohol)</div>
          <div className="space-y-2.5">
            {alcoholDist.map((d) => (
              <div key={d.label} className="flex items-center gap-2 group cursor-pointer" data-interactive>
                <span className="text-[10px] w-16 shrink-0" style={{ color: "var(--text-secondary)" }}>{d.label}</span>
                <div className="flex-1 h-2 rounded-full overflow-hidden relative" style={{ background: "var(--bg-card-alt)", boxShadow: "inset 0 1px 2px rgba(0,0,0,0.4)" }}>
                  <div className="h-full rounded-full transition-all duration-700 ease-out relative" style={{ width: `${Math.min(Number(d.pct), 100)}%`, background: d.color === "var(--over)" ? "linear-gradient(90deg, #2a1414, #E85454)" : "linear-gradient(90deg, #0F231B, #1FC184)", boxShadow: d.color === "var(--over)" ? "0 0 8px rgba(232,84,84,0.45)" : "0 0 8px rgba(31,193,132,0.45)" }} />
                </div>
                <span className="text-[10px] font-mono w-20 text-right shrink-0" style={{ color: "var(--text-secondary)" }}>{d.v} ({d.pct}%)</span>
              </div>
            ))}
          </div>
        </div>
        <div className="col-span-12 lg:col-span-6 rounded-[6px] p-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", boxShadow: "0 8px 20px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.03)" }}>
          <div className="text-[9px] tracking-[0.08em] uppercase font-semibold mb-2" style={{ color: "var(--text-muted)" }}>Peak Hours — 3D Blocks</div>
          <div className="h-[100px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={peak} margin={{ top: 4, right: 0, left: -20, bottom: 0 }} barCategoryGap={3}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" vertical={false} />
                <XAxis dataKey="h" tick={{ fontSize: 7, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} interval={3} tickFormatter={(v) => `${v}:00`} />
                <YAxis tick={{ fontSize: 8, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} width={24} />
                <Tooltip contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)", fontSize: 10, borderRadius: 4 }} cursor={{ fill: "var(--bg-card-alt)" }} />
                <Bar dataKey="v" fill="#1FC184" radius={[2, 2, 0, 0]} shape={<BlockBar />} isAnimationActive animationDuration={800} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
}
