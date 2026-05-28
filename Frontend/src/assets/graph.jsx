// File: Frontend/src/assets/graph.jsx
import React, { useState, useEffect } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer, BarChart, Bar, Cell
} from "recharts";
import { ShieldCheck, ShieldAlert, Eye, Cpu } from "lucide-react";

// Mock data reflecting real deployment logs from the Fusion Model
const MOCK_DEPLOYMENT_HISTORY = [
  { id: 101, time: "08:15", label: "Pass", confidence: 0.94, ear: 0.28, type: "Sober Access" },
  { id: 102, time: "09:30", label: "Pass", confidence: 0.89, ear: 0.26, type: "Sober Access" },
  { id: 103, time: "10:45", label: "Sanitizer Filtered", confidence: 0.91, ear: 0.27, type: "Sanitizer Mist" },
  { id: 104, time: "13:10", label: "Near Limit", confidence: 0.76, ear: 0.22, type: "Drowsy/Fragrance" },
  { id: 105, time: "14:25", label: "Over Limit", confidence: 0.95, ear: 0.17, type: "Alcohol Event" },
  { id: 106, time: "16:00", label: "Pass", confidence: 0.92, ear: 0.29, type: "Sober Access" },
  { id: 107, time: "17:15", label: "Over Limit", confidence: 0.88, ear: 0.19, type: "Alcohol Event" }
];

function getRiskStyles(label) {
  if (label === "Over Limit") return { text: "var(--over)", bg: "color-mix(in srgb, var(--over) 10%, transparent)", border: "var(--over)" };
  if (label === "Near Limit") return { text: "var(--near)", bg: "color-mix(in srgb, var(--near) 10%, transparent)", border: "var(--near)" };
  if (label === "Sanitizer Filtered") return { text: "var(--text-secondary)", bg: "var(--bg-active)", border: "var(--border-subtle)" };
  return { text: "var(--pass)", bg: "color-mix(in srgb, var(--pass) 10%, transparent)", border: "var(--pass)" };
}

const CustomTelemetryDot = (props) => {
  const { cx, cy, payload } = props;
  let color = "var(--pass)";
  if (payload.label === "Over Limit") color = "var(--over)";
  if (payload.label === "Near Limit") color = "var(--near)";
  if (payload.label === "Sanitizer Filtered") color = "var(--text-secondary)";
  return <circle cx={cx} cy={cy} r={5} fill={color} stroke="var(--bg-card)" strokeWidth={1.5} />;
};

export default function MockBACChart() {
  const [history, setHistory] = useState(MOCK_DEPLOYMENT_HISTORY);
  const [isLive, setIsLive] = useState(true);

  useEffect(() => {
    if (!isLive) return;
    const interval = setInterval(() => {
      // Simulate real-time gate transaction updates pushing into history array
      setHistory(prev => {
        const nextId = prev[prev.length - 1].id + 1;
        const now = new Date();
        const timestamp = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
        const labels = ["Pass", "Pass", "Sanitizer Filtered", "Near Limit", "Over Limit"];
        const chosenLabel = labels[Math.floor(Math.random() * labels.length)];
        
        let confidence = roundTo(0.75 + Math.random() * 0.22, 2);
        let ear = roundTo(0.24 + Math.random() * 0.06, 2);
        let type = "Sober Access";

        if (chosenLabel === "Over Limit") {
          ear = roundTo(0.15 + Math.random() * 0.05, 2);
          type = "Alcohol Incident";
        } else if (chosenLabel === "Near Limit") {
          ear = roundTo(0.20 + Math.random() * 0.03, 2);
          type = "Drowsy / Trace Gas";
        } else if (chosenLabel === "Sanitizer Filtered") {
          type = "Chemical Vapor Intercept";
        }

        const newEntry = { id: nextId, time: timestamp, label: chosenLabel, confidence, ear, type };
        return [...prev.slice(1), newEntry];
      });
    }, 8000);

    return () => clearInterval(interval);
  }, [isLive]);

  const roundTo = (num, decimals) => Number(Math.round(num + "e" + decimals) + "e-" + decimals);

  // Compute metric accumulations based on actual model outcomes
  const totalAttempts = history.length;
  const criticalViolations = history.filter(r => r.label === "Over Limit").length;
  const sanitizersNeutralized = history.filter(r => r.label === "Sanitizer Filtered").length;
  const avgAttentiveness = (history.reduce((acc, r) => acc + r.ear, 0) / totalAttempts).toFixed(3);

  // Convert categorical variables into distribution matrices for bar rendering
  const distributionData = [
    { name: "Pass", count: history.filter(r => r.label === "Pass").length, color: "var(--pass)" },
    { name: "Near Limit", count: history.filter(r => r.label === "Near Limit").length, color: "var(--near)" },
    { name: "Over Limit", count: history.filter(r => r.label === "Over Limit").length, color: "var(--over)" },
    { name: "Sanitizer", count: history.filter(r => r.label === "Sanitizer Filtered").length, color: "var(--text-secondary)" }
  ];

  return (
    <div className="p-6 space-y-6">
      
      {/* Real telemetry cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          <p className="text-xs mb-1 font-medium" style={{ color: "var(--text-muted)" }}>Gate Interrogations</p>
          <p className="text-2xl font-bold tabular-nums" style={{ color: "var(--text-primary)" }}>{totalAttempts}</p>
        </div>
        <div className="rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          <p className="text-xs mb-1 font-medium flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
            <ShieldAlert size={12} className="text-(--over)"/> Target Denials
          </p>
          <p className="text-2xl font-bold tabular-nums" style={{ color: "var(--over)" }}>{criticalViolations}</p>
        </div>
        <div className="rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          <p className="text-xs mb-1 font-medium flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
            <Cpu size={12} className="text-indigo-400"/> Sanitizers Neutralized
          </p>
          <p className="text-2xl font-bold tabular-nums" style={{ color: "var(--text-secondary)" }}>{sanitizersNeutralized}</p>
        </div>
        <div className="rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          <p className="text-xs mb-1 font-medium flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
            <Eye size={12} className="text-emerald-400"/> Attentiveness index (EAR)
          </p>
          <p className="text-2xl font-bold tabular-nums text-emerald-400">{avgAttentiveness}</p>
        </div>
      </div>

      {/* Control Header */}
      <div className="flex items-center justify-between border-b pb-3" style={{ borderColor: "var(--border-subtle)" }}>
        <div>
          <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Gate Decision Fusion Analysis Stream</h2>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>Real-time verification timeline plotting neural model confidence scores</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs px-2 py-1 rounded font-medium border"
                style={{ 
                  background: isLive ? "color-mix(in srgb, var(--pass) 10%, transparent)" : "var(--bg-active)",
                  color: isLive ? "var(--pass)" : "var(--text-muted)",
                  borderColor: isLive ? "color-mix(in srgb, var(--pass) 30%, transparent)" : "var(--border-subtle)"
                }}>
            {isLive ? "● Ingestion Stream Active" : "◼ Diagnostics Suspended"}
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
          <p className="text-xs font-semibold uppercase tracking-wider mb-4" style={{ color: "var(--text-muted)" }}>Fusion Class Confidence Trend</p>
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
          <p className="text-xs font-semibold uppercase tracking-wider mb-4" style={{ color: "var(--text-muted)" }}>Threat Categorization Profile</p>
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
        <p className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: "var(--text-muted)" }}>Live Telemetry Log Stream</p>
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
                  <span className="text-xs font-mono" style={{ color: "var(--text-secondary)" }}>EAR: {log.ear.toFixed(3)}</span>
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
  // Static wrapper block preserved cleanly for layout registration consistency
  return (
    <div className="rounded-xl border p-4" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
      <p className="text-xs font-semibold text-center uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
        Telemetry Feed Active and Standardized
      </p>
    </div>
  );
}