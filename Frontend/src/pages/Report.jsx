// File: Frontend/src/pages/Report.jsx
import React, { useState, useEffect } from "react";
import { API_BASE } from "../api";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend
} from "recharts";
import { Clipboard, FileText } from "lucide-react";
import { Card, StatTile, StatusBadge } from "../components/ui/primitives";

function Report() {
  const [logsData, setLogsData] = useState({ logs: [], stats: null, weekly: [], monthly: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/deployment-logs?limit=200`);
        if (res.ok && active) setLogsData(await res.json());
      } catch { /* backend offline — keep last known data */ }
      if (active) setLoading(false);
    };
    load();
    const interval = setInterval(load, 10000);
    return () => { active = false; clearInterval(interval); };
  }, []);

  const { logs, stats, weekly, monthly } = logsData;

  // Live metrics from the real deployment_logs table
  const totalInterrogations = stats?.total ?? 0;
  const aboveLimitBreaches  = stats?.over_limit ?? 0;
  const traceAlerts         = stats?.near_limit ?? 0;
  const sanitizersBlocked   = stats?.sanitizer ?? 0;

  const passRatePct = totalInterrogations
    ? (((totalInterrogations - aboveLimitBreaches - traceAlerts) / totalInterrogations) * 100).toFixed(1) + "%"
    : "—";

  // Categorical classification buckets mapping back to the Fusion Model endpoints
  const decisionMatrixBuckets = [
    { label: "Pass", color: "var(--pass)", count: Math.max(totalInterrogations - aboveLimitBreaches - traceAlerts - sanitizersBlocked, 0) },
    { label: "Near Limit", color: "var(--near)", count: traceAlerts },
    { label: "Over Limit", color: "var(--over)", count: aboveLimitBreaches },
    { label: "Sanitizer", color: "var(--text-muted)", count: sanitizersBlocked },
  ];

  // Real verification event feed from deployment_logs
  const activeLogFeed = logs.slice(0, 6).map((l) => ({
    time: l.date ? new Date(l.date).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }) : "--",
    id: l.id,
    label: l.prediction || "Pass",
  }));

  return (
    <div className="p-1 space-y-4 max-w-6xl mx-auto">

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Total scans" value={loading ? null : totalInterrogations} sub="All deployment events" />
        <StatTile label="Denied" value={loading ? null : aboveLimitBreaches} valueColor="var(--over)" />
        <StatTile label="Near limit" value={loading ? null : traceAlerts} valueColor="var(--near)" />
        <StatTile label="Pass rate" value={loading ? null : passRatePct} valueColor="var(--pass)" />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">

        {/* Weekly bar chart */}
        <Card className="lg:col-span-3">
          <p className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>Last 7 days</p>
          <div className="w-full h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={weekly.length ? weekly : [{ day: "—", clear: 0, alert: 0, breach: 0, intercepted: 0 }]} margin={{ top: 10, right: 5, left: -25, bottom: 0 }} barSize={12}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 11, fill: "var(--text-muted)" }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickLine={false} axisLine={false} />
                <Tooltip
                  cursor={{ fill: "var(--bg-card-alt)" }}
                  contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 10, fontSize: 12, color: "var(--text-primary)" }}
                />
                <Legend iconSize={8} wrapperStyle={{ fontSize: 11 }} />
                <Bar name="Clear pass" dataKey="clear" stackId="a" fill="#27ae60" radius={[0, 0, 0, 0]} />
                <Bar name="Near limit" dataKey="alert" stackId="a" fill="#f1c40f" />
                <Bar name="Over limit" dataKey="breach" stackId="a" fill="#e74c3c" />
                <Bar name="Sanitizer" dataKey="intercepted" stackId="a" fill="#95a5a6" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        {/* Decision breakdown */}
        <Card className="lg:col-span-2 flex flex-col justify-between">
          <p className="text-sm font-semibold mb-4" style={{ color: "var(--text-primary)" }}>Decision breakdown</p>
          <div className="space-y-3.5">
            {decisionMatrixBuckets.map((b) => {
              const percentage = totalInterrogations > 0 ? (b.count / totalInterrogations) * 100 : 0;
              return (
                <div key={b.label} className="flex items-center gap-3">
                  <span className="text-xs w-24 shrink-0 truncate" style={{ color: "var(--text-secondary)" }}>{b.label}</span>
                  <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-card-alt)" }}>
                    <div className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${Math.max(percentage, 2)}%`, background: b.color }} />
                  </div>
                  <span className="text-xs font-bold font-mono w-8 text-right" style={{ color: "var(--text-secondary)" }}>{b.count}</span>
                </div>
              );
            })}
          </div>

          <div className="mt-4 pt-3 border-t grid grid-cols-2 gap-3" style={{ borderColor: "var(--border-subtle)" }}>
            <div className="rounded-lg p-2.5 text-center" style={{ background: "var(--pass-bg)" }}>
              <p className="text-lg font-bold font-mono" style={{ color: "var(--pass-text)" }}>{totalInterrogations - aboveLimitBreaches - traceAlerts}</p>
              <p className="text-[11px] uppercase font-semibold tracking-wide" style={{ color: "var(--pass-text)", opacity: 0.8 }}>Cleared</p>
            </div>
            <div className="rounded-lg p-2.5 text-center" style={{ background: "var(--over-bg)" }}>
              <p className="text-lg font-bold font-mono" style={{ color: "var(--over-text)" }}>{aboveLimitBreaches + traceAlerts}</p>
              <p className="text-[11px] uppercase font-semibold tracking-wide" style={{ color: "var(--over-text)", opacity: 0.8 }}>Flagged</p>
            </div>
          </div>
        </Card>
      </div>

      {/* Events + monthly history */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

        {/* Recent events */}
        <Card>
          <div className="flex items-center gap-1.5 mb-3 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            <Clipboard size={15} style={{ color: "var(--accent)" }}/>
            Recent events
          </div>
          <div className="space-y-2">
            {loading ? (
              <div className="space-y-2">{[...Array(4)].map((_, i) => <div key={i} className="skeleton h-9 rounded-xl" />)}</div>
            ) : activeLogFeed.length === 0 ? (
              <p className="text-xs text-center py-4" style={{ color: "var(--text-muted)" }}>No deployment events recorded yet</p>
            ) : (
              activeLogFeed.map((r, i) => (
                <div key={i} className="flex items-center justify-between rounded-xl px-3 py-2 text-xs"
                     style={{ background: "var(--bg-card-alt)" }}>
                  <span className="font-mono" style={{ color: "var(--text-muted)" }}>{r.time}</span>
                  <span style={{ color: "var(--text-secondary)" }}>#{r.id}</span>
                  <StatusBadge label={r.label} />
                </div>
              ))
            )}
          </div>
        </Card>

        {/* Monthly history — real data from deployment_logs */}
        <Card>
          <div className="flex items-center gap-1.5 mb-3 pb-2.5 border-b text-sm font-semibold" style={{ borderColor: "var(--border-subtle)", color: "var(--text-primary)" }}>
            <FileText size={15} style={{ color: "var(--text-muted)" }}/>
            Monthly history
          </div>
          <div className="space-y-2">
            {(monthly || []).length === 0 ? (
              <p className="text-xs text-center py-4" style={{ color: "var(--text-muted)" }}>No monthly data yet</p>
            ) : (
              monthly.map((r) => (
                <div key={r.id} className="flex items-center gap-3 rounded-xl px-3 py-2 text-xs" style={{ background: "var(--bg-card-alt)" }}>
                  <span className="flex-1 font-medium" style={{ color: "var(--text-primary)" }}>{r.label}</span>
                  <span className="font-mono" style={{ color: "var(--text-muted)" }}>{r.total} scans</span>
                  <span className="font-bold font-mono w-20 text-right" style={{ color: r.flagged > 0 ? "var(--over)" : "var(--text-muted)" }}>{r.flagged} over limit</span>
                </div>
              ))
            )}
          </div>
        </Card>

      </div>
    </div>
  );
}

export default Report;
