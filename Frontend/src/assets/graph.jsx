// File: Frontend/src/assets/graph.jsx
import React, { useState, useEffect } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer, BarChart, Bar, Cell
} from "recharts";
import { ShieldCheck, ShieldAlert, Eye, Cpu } from "lucide-react";
import { API_BASE } from "../api";

function getRiskStyles(label) {
  if (label === "Over Limit") return { text: "var(--over)", bg: "color-mix(in srgb, var(--over) 10%, transparent)", border: "var(--over)" };
  if (label === "Near Limit") return { text: "var(--near)", bg: "color-mix(in srgb, var(--near) 10%, transparent)", border: "var(--near)" };
  if (label === "Sanitizer" || label?.includes("Sanitizer")) return { text: "var(--text-secondary)", bg: "var(--bg-active)", border: "var(--border-subtle)" };
  return { text: "var(--pass)", bg: "color-mix(in srgb, var(--pass) 10%, transparent)", border: "var(--pass)" };
}

const CustomTelemetryDot = (props) => {
  const { cx, cy, payload } = props;
  let color = "var(--pass)";
  if (payload.label === "Over Limit") color = "var(--over)";
  if (payload.label === "Near Limit") color = "var(--near)";
  if (payload.label === "Sanitizer" || payload.label?.includes("Sanitizer")) color = "var(--text-secondary)";
  return <circle cx={cx} cy={cy} r={5} fill={color} stroke="var(--bg-card)" strokeWidth={1.5} />;
};

// Maps a real deployment_logs row into the chart entry shape
function mapLog(l) {
  const time = l.date
    ? new Date(l.date).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false })
    : "--";
  return {
    id: l.id,
    time,
    label: l.prediction || "Pass",
    confidence: l.confidence ?? 0,
    type: l.risk_level || l.model_version || "Deployment",
  };
}

export default function MockBACChart() {
  const [history, setHistory] = useState([]);
  const [isLive, setIsLive] = useState(true);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/deployment-logs?limit=50`);
        if (!res.ok) throw new Error(res.status);
        const data = await res.json();
        if (!active) return;
        setOffline(false);
        setHistory(
          data.logs
            .map(mapLog)
            .reverse() // chronological order for the trend line
        );
      } catch {
        if (active) setOffline(true);
      }
    };
    load();
    if (isLive) {
      const interval = setInterval(load, 10000);
      return () => { active = false; clearInterval(interval); };
    }
    return () => { active = false; };
  }, [isLive]);

  // Compute metric accumulations from real deployment logs
  const totalAttempts = history.length;
  const criticalViolations = history.filter(r => r.label === "Over Limit").length;
  const sanitizersNeutralized = history.filter(r => r.label?.includes("Sanitizer")).length;
  const avgConfidence = totalAttempts
    ? (history.reduce((acc, r) => acc + (r.confidence || 0), 0) / totalAttempts).toFixed(2)
    : "---";

  // Convert categorical variables into distribution matrices for bar rendering
  const distributionData = [
    { name: "Pass", count: history.filter(r => r.label === "Pass").length, color: "var(--pass)" },
    { name: "Near Limit", count: history.filter(r => r.label === "Near Limit").length, color: "var(--near)" },
    { name: "Over Limit", count: history.filter(r => r.label === "Over Limit").length, color: "var(--over)" },
    { name: "Sanitizer", count: history.filter(r => r.label?.includes("Sanitizer")).length, color: "var(--text-secondary)" }
  ];

  return (
    <div className="p-6 space-y-6">
      
      {/* Real telemetry cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          <p className="text-xs mb-1 font-medium" style={{ color: "var(--text-muted)" }}>Total scans</p>
          <p className="text-2xl font-bold tabular-nums" style={{ color: "var(--text-primary)" }}>{totalAttempts}</p>
        </div>
        <div className="rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          <p className="text-xs mb-1 font-medium flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
            <ShieldAlert size={12} style={{ color: "var(--over)" }}/> Denied
          </p>
          <p className="text-2xl font-bold tabular-nums" style={{ color: "var(--over)" }}>{criticalViolations}</p>
        </div>
        <div className="rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          <p className="text-xs mb-1 font-medium flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
            <Cpu size={12} style={{ color: "var(--text-muted)" }}/> Sanitizer events
          </p>
          <p className="text-2xl font-bold tabular-nums" style={{ color: "var(--text-secondary)" }}>{sanitizersNeutralized}</p>
        </div>
        <div className="rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          <p className="text-xs mb-1 font-medium flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
            <Eye size={12} style={{ color: "var(--pass)" }}/> Avg confidence
          </p>
          <p className="text-2xl font-bold tabular-nums" style={{ color: "var(--pass)" }}>{avgConfidence}</p>
        </div>
      </div>

      {/* Control Header */}
      <div className="flex items-center justify-between border-b pb-3" style={{ borderColor: "var(--border-subtle)" }}>
        <div>
          <p className="text-xs text-muted" style={{ color: "var(--text-muted)" }}>Deployment Log Stream</p>
          {offline && (
            <p className="text-xs mt-1" style={{ color: "var(--over)" }}>⚠ Backend offline — showing no data</p>
          )}
          {!offline && history.length === 0 && (
            <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>No deployment events recorded yet</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs px-2 py-1 rounded font-medium border"
                style={{ 
                  background: isLive ? "color-mix(in srgb, var(--pass) 10%, transparent)" : "var(--bg-active)",
                  color: isLive ? "var(--pass)" : "var(--text-muted)",
                  borderColor: isLive ? "color-mix(in srgb, var(--pass) 30%, transparent)" : "var(--border-subtle)"
                }}>
            {isLive ? "● Live" : "Paused"}
          </span>
          <button
            onClick={() => setIsLive(p => !p)}
            className="text-xs px-3 py-1 rounded border font-medium transition-colors"
            style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }}
            onMouseEnter={e => e.currentTarget.style.background = "var(--bg-active)"}
            onMouseLeave={e => e.currentTarget.style.background = "var(--bg-card)"}
          >
            {isLive ? "Pause" : "Resume"}
          </button>
        </div>
      </div>

      {/* Analytics Charts split view */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Main Line Graph — Model confidence tracing */}
        <div className="lg:col-span-2 rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          <p className="text-xs font-semibold uppercase tracking-wider mb-4" style={{ color: "var(--text-muted)" }}>Confidence trend</p>
          <div className="w-full h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={history} margin={{ top: 10, right: 10, left: -25, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="color-mix(in srgb, var(--border-subtle) 40%, transparent)" />
                <XAxis dataKey="time" tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickLine={false} />
                <YAxis domain={[0.5, 1.0]} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickLine={false} axisLine={false} />
                <Tooltip 
                  contentStyle={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)", borderRadius: "8px", fontSize: "12px", color: "var(--text-primary)" }}
                  itemStyle={{ color: "var(--text-secondary)" }}
                  formatter={(value, name, props) => [`${(value * 100).toFixed(1)}%`, "Confidence"]}
                  labelFormatter={(label, items) => items[0] ? `Time: ${label} [${items[0].payload.type}]` : label}
                />
                <ReferenceLine y={0.8} stroke="var(--near)" strokeDasharray="4 4" strokeWidth={1} />
                <Line type="monotone" dataKey="confidence" stroke="var(--text-primary)" strokeWidth={2.5}
                  dot={<CustomTelemetryDot />} activeDot={{ r: 6 }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Categorical Distribution Chart */}
        <div className="rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          <p className="text-xs font-semibold uppercase tracking-wider mb-4" style={{ color: "var(--text-muted)" }}>Outcomes</p>
          <div className="w-full h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={distributionData} margin={{ top: 10, right: 5, left: -30, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 2" stroke="color-mix(in srgb, var(--border-subtle) 30%, transparent)" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 10, fill: "var(--text-muted)" }} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fontSize: 10, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} />
                <Tooltip cursor={{ fill: "var(--bg-active)", opacity: 0.4 }} contentStyle={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", fontSize: "11px" }} />
                <Bar dataKey="count" radius={[4, 4, 0, 0]} maxBarSize={35} isAnimationActive={false}>
                  {distributionData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

      </div>

      {/* Real-time Incident Feed */}
      <div>
        <p className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: "var(--text-muted)" }}>Recent activity</p>
        <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
          {[...history].reverse().map((log) => {
            const style = getRiskStyles(log.label);
            return (
              <div key={log.id} className="flex items-center justify-between rounded-xl border p-3 text-sm transition-all"
                   style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)" }}>
                <div className="flex items-center gap-3">
                  <span className="font-mono text-xs" style={{ color: "var(--text-muted)" }}>#{log.id}</span>
                  <span className="font-medium" style={{ color: "var(--text-primary)" }}>{log.type}</span>
                </div>
                <div className="flex items-center gap-4">
                  <span className="text-xs font-semibold font-mono" style={{ color: "var(--text-primary)" }}>
                    Conf: {(log.confidence * 100).toFixed(0)}%
                  </span>
                  <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold border"
                        style={{ background: style.bg, color: style.text, borderColor: style.border }}>
                    {log.label.toUpperCase()}
                  </span>
                  <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>{log.time}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

    </div>
  );
}

export function RecentDetections() {
  const [logs, setLogs] = useState([]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/deployment-logs?limit=6`);
        if (!res.ok) throw new Error(res.status);
        const data = await res.json();
        if (active) setLogs(data.logs);
      } catch { /* keep last known */ }
    };
    load();
    const interval = setInterval(load, 10000);
    return () => { active = false; clearInterval(interval); };
  }, []);

  return (
    <div className="rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
      <p className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: "var(--text-muted)" }}>
        Recent Detections
      </p>
      {logs.length === 0 ? (
        <p className="text-xs text-center py-3" style={{ color: "var(--text-muted)" }}>
          No deployment events recorded yet
        </p>
      ) : (
        <div className="space-y-2 max-h-44 overflow-y-auto pr-1">
          {logs.map((l) => {
            const label = l.prediction || "Pass";
            const style = getRiskStyles(label);
            const time = l.date
              ? new Date(l.date).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false })
              : "--";
            return (
              <div key={l.id} className="flex items-center justify-between text-xs">
                <span className="font-mono" style={{ color: "var(--text-muted)" }}>{time}</span>
                <span className="font-medium" style={{ color: "var(--text-primary)" }}>
                  {l.risk_level || label}
                </span>
                <span className="inline-flex items-center px-2 py-0.5 rounded-full font-semibold border"
                      style={{ background: style.bg, color: style.text, borderColor: style.border }}>
                  {label.toUpperCase()}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}