// File: Frontend/src/pages/Models.jsx
import { useState } from "react";
import { Cpu, CheckCircle, ShieldAlert, Zap, Layers } from "lucide-react";

const MODELS = [
  {
    id: 1,
    name: "Random Forest Classifier",
    desc: "Primary sensor engine. Evaluates 15 extracted window features (max, avg, std, rise/decay times, spatial variance) from the triple MQ3 matrix. Serves as the ensemble tiebreaker.",
    badge: "Ensemble Core",
    badgeCls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-400",
    barCls: "bg-emerald-500",
    metrics: { Accuracy: 97.4, Precision: 96.8, Recall: 96.2 },
    stats: { Evaluations: "2,148", "Avg latency": "8ms", Version: "v1.4" },
  },
  {
    id: 2,
    name: "XGBoost Classifier",
    desc: "Ensemble co-evaluator running concurrently on the live MQ3 sensor windows. Boosts spatial variance recognition accuracy to separate sanitizer spray anomalies from uniform breath curves.",
    badge: "Ensemble Partner",
    badgeCls: "bg-blue-100 text-blue-700 dark:bg-blue-950/40 dark:text-blue-400",
    barCls: "bg-blue-500",
    metrics: { Accuracy: 96.8, Precision: 95.9, Recall: 96.0 },
    stats: { Evaluations: "2,148", "Avg latency": "11ms", Version: "v1.2" },
  },
  {
    id: 3,
    name: "MobileNetV2 (Transfer Learning)",
    desc: "Computer Vision classifier handling facial framing data. Classifies subjects into three visual target indices: Sober, Drowsy, or Impaired based on structural feature tracking.",
    badge: "Vision Core",
    badgeCls: "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-400",
    barCls: "bg-amber-500",
    metrics: { Accuracy: 92.1, Precision: 90.5, Recall: 91.0 },
    stats: { Frames: "15.4k", "Avg latency": "24ms", Version: "v2.0-tflite" },
  },
  {
    id: 4,
    name: "Neural Decision Fusion Engine",
    desc: "The top-level operational model. Ingests downstream sensor classes/confidence levels, visual metrics, and raw EAR coefficients to determine ultimate gate access: Pass, Near Limit, or Over Limit.",
    badge: "Active Decision Engine",
    badgeCls: "bg-purple-100 text-purple-700 dark:bg-purple-950/40 dark:text-purple-400",
    barCls: "bg-purple-500",
    metrics: { Accuracy: 98.2, Precision: 98.0, Recall: 97.9 },
    stats: { AccessDecisions: "1,204", "Avg latency": "4ms", Version: "v3.1-final" },
  },
];

function ProgressBar({ label, value, barCls }) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-gray-500 w-20 shrink-0 font-medium">{label}</span>
      <div className="flex-1 bg-gray-200 dark:bg-zinc-800 rounded-full h-1.5 overflow-hidden">
        <div className={`h-full rounded-full ${barCls}`} style={{ width: `${value}%` }} />
      </div>
      <span className="text-xs text-gray-500 w-10 text-right font-mono">{value}%</span>
    </div>
  );
}

export default function Models() {
  const [selected, setSelected] = useState(4); // Default to Fusion Engine
  const detail = MODELS.find((m) => m.id === selected);

  return (
    <div className="space-y-6 max-w-6xl mx-auto p-4">

      {/* Top operational metrics row */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="bg-gray-100 dark:bg-zinc-900 border dark:border-zinc-800 rounded-xl p-4">
          <p className="text-xs text-gray-500 font-medium mb-1">Architecture Node Pool</p>
          <p className="text-2xl font-bold text-gray-900 dark:text-gray-100">4</p>
          <p className="text-xs text-emerald-500 font-medium mt-1 flex items-center gap-1">
            <CheckCircle size={11}/> Core Systems Synchronized
          </p>
        </div>
        <div className="bg-gray-100 dark:bg-zinc-900 border dark:border-zinc-800 rounded-xl p-4">
          <p className="text-xs text-gray-500 font-medium mb-1">Peak Fusion Accuracy</p>
          <p className="text-2xl font-bold text-gray-900 dark:text-gray-100">98.2%</p>
          <p className="text-xs text-gray-400 mt-1">Ensemble + Computer Vision</p>
        </div>
        <div className="bg-gray-100 dark:bg-zinc-900 border dark:border-zinc-800 rounded-xl p-4">
          <p className="text-xs text-gray-500 font-medium mb-1">Total Pipeline Latency</p>
          <p className="text-2xl font-bold text-gray-900 dark:text-gray-100">43ms</p>
          <p className="text-xs text-gray-400 mt-1">MediaPipe + DeepFace + Fusion</p>
        </div>
        <div className="bg-gray-100 dark:bg-zinc-900 border dark:border-zinc-800 rounded-xl p-4">
          <p className="text-xs text-gray-500 font-medium mb-1">Sanitizer False Alarm Rate</p>
          <p className="text-2xl font-bold text-emerald-500">0.4%</p>
          <p className="text-xs text-gray-400 mt-1">Filtered by Spatial Variance</p>
        </div>
      </div>

      {/* Model list + detail panel layout splits */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">

        {/* Model Listing Pipeline Column */}
        <div className="lg:col-span-3 space-y-3">
          {MODELS.map((m) => (
            <div
              key={m.id}
              onClick={() => setSelected(m.id)}
              className={`bg-white dark:bg-zinc-900 border rounded-xl p-5 cursor-pointer transition-all ${
                selected === m.id
                  ? "border-purple-500 shadow-sm ring-1 ring-purple-500/20"
                  : "border-gray-200 dark:border-zinc-800 hover:border-gray-300 dark:hover:border-zinc-700"
              }`}
            >
              <div className="flex items-start justify-between mb-3">
                <div className="space-y-1">
                  <p className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
                    {m.id === 4 ? <Layers size={14} className="text-purple-400"/> : <Cpu size={14} className="text-gray-400"/>}
                    {m.name}
                  </p>
                  <p className="text-xs text-gray-500 leading-relaxed max-w-xl">{m.desc}</p>
                </div>
                <span className={`text-[10px] font-semibold px-2.5 py-1 rounded-full ml-3 shrink-0 ${m.badgeCls}`}>
                  {m.badge}
                </span>
              </div>

              <div className="space-y-2 mt-4">
                {Object.entries(m.metrics).map(([k, v]) => (
                  <ProgressBar key={k} label={k} value={v} barCls={m.barCls} />
                ))}
              </div>

              <div className="flex gap-6 mt-4 pt-3 border-t border-gray-100 dark:border-zinc-800">
                {Object.entries(m.stats).map(([k, v]) => (
                  <div key={k}>
                    <p className="text-xs font-bold text-gray-900 dark:text-gray-100 font-mono">{v}</p>
                    <p className="text-[10px] text-gray-400 uppercase tracking-wide mt-0.5">{k}</p>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Selected Model Focus Panel Column */}
        <div className="lg:col-span-2 space-y-4">

          {/* Core Configuration Metrics */}
          <div className="bg-white dark:bg-zinc-900 border border-gray-200 dark:border-zinc-800 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <p className="text-xs font-bold uppercase tracking-wider text-gray-400">Inspecting Classifier Node</p>
              <span className={`text-[10px] font-semibold px-2.5 py-1 rounded-full ${detail.badgeCls}`}>
                {detail.badge}
              </span>
            </div>
            <p className="text-base font-bold text-gray-900 dark:text-gray-100 mb-1.5">{detail.name}</p>
            <p className="text-xs text-gray-500 leading-relaxed border-b dark:border-zinc-800 pb-3">{detail.desc}</p>

            <div className="mt-4 space-y-3">
              {Object.entries(detail.metrics).map(([k, v]) => (
                <div key={k} className="flex items-center justify-between">
                  <span className="text-xs text-gray-500 font-medium">{k}</span>
                  <div className="flex items-center gap-3">
                    <div className="w-28 bg-gray-200 dark:bg-zinc-800 rounded-full h-1.5 overflow-hidden">
                      <div className={`h-full rounded-full ${detail.barCls}`} style={{ width: `${v}%` }} />
                    </div>
                    <span className="text-xs font-bold text-gray-700 dark:text-gray-300 w-10 text-right font-mono">{v}%</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Model Structural Performance Hierarchy */}
          <div className="bg-white dark:bg-zinc-900 border border-gray-200 dark:border-zinc-800 rounded-xl p-5">
            <p className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-3">System Accuracy Calibration Map</p>
            <div className="space-y-3">
              {MODELS.map((m) => (
                <div key={m.id} className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full shrink-0 ${m.barCls}`} />
                  <span className="text-xs text-gray-600 dark:text-gray-400 flex-1 truncate font-medium">
                    {m.name.split(" ").slice(0, 2).join(" ")}
                  </span>
                  <div className="w-24 bg-gray-100 dark:bg-zinc-800 rounded-full h-1.5 overflow-hidden">
                    <div
                      className={`h-full rounded-full ${m.barCls} ${selected === m.id ? "opacity-100" : "opacity-35"}`}
                      style={{ width: `${m.metrics.Accuracy}%` }}
                    />
                  </div>
                  <span className={`text-xs w-12 text-right font-bold font-mono ${selected === m.id ? "text-purple-400 font-extrabold" : "text-gray-400"}`}>
                    {m.metrics.Accuracy}%
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Deployment Default Locking Controller */}
          <div className="bg-white dark:bg-zinc-900 border border-gray-200 dark:border-zinc-800 rounded-xl p-5">
            <p className="text-xs font-bold uppercase tracking-wider text-gray-400 mb-1">Set Gate Routing Default</p>
            <p className="text-xs text-gray-500 mb-3 leading-relaxed">
              This system profile governs edge decision weights at the physical barrier unless specialized training parameters override the runtime configurations.
            </p>
            <button className="w-full py-2.5 rounded-lg bg-zinc-900 dark:bg-purple-900 text-white dark:text-purple-100 text-xs font-semibold hover:opacity-90 transition-opacity flex items-center justify-center gap-1.5">
              <Zap size={13}/> Establish {detail.name.split(" ").slice(0, 2).join(" ")} as Primary Router
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}