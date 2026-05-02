import { useState } from "react";

const MODELS = [
  {
    id: 1,
    name: "Linear Regression v2",
    desc: "Baseline BAC prediction from MQ-3 analog sensor input. Fast inference, suitable for real-time use.",
    badge: "Active",
    badgeCls: "bg-green-100 text-green-700",
    barCls: "bg-blue-500",
    metrics: { Accuracy: 91.0, Precision: 88.0, Recall: 90.0 },
    stats: { Predictions: "1,284", "Avg latency": "0.9s", Version: "v2.1" },
  },
  {
    id: 2,
    name: "Random Forest Classifier",
    desc: "Ensemble model trained on 5,000+ labeled samples. Highest accuracy among all models.",
    badge: "Default",
    badgeCls: "bg-blue-100 text-blue-700",
    barCls: "bg-green-500",
    metrics: { Accuracy: 97.2, Precision: 96.0, Recall: 95.0 },
    stats: { Predictions: "847", "Avg latency": "0.7s", Version: "v1.4" },
  },
  {
    id: 3,
    name: "SVM (RBF kernel)",
    desc: "Support Vector Machine for binary classification — pass/fail. Best for edge cases near the 0.08 threshold.",
    badge: "Experimental",
    badgeCls: "bg-yellow-100 text-yellow-700",
    barCls: "bg-yellow-400",
    metrics: { Accuracy: 93.5, Precision: 92.0, Recall: 91.0 },
    stats: { Predictions: "312", "Avg latency": "0.8s", Version: "v0.9" },
  },
];

function ProgressBar({ label, value, barCls }) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-gray-500 w-16 flex-shrink-0">{label}</span>
      <div className="flex-1 bg-gray-200 rounded-full h-1.5 overflow-hidden">
        <div className={`h-full rounded-full ${barCls}`} style={{ width: `${value}%` }} />
      </div>
      <span className="text-xs text-gray-500 w-10 text-right">{value}%</span>
    </div>
  );
}

export default function Models() {
  const [selected, setSelected] = useState(2);
  const detail = MODELS.find((m) => m.id === selected);

  return (
    <div className="space-y-4">

      {/* Top stat row */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">Models loaded</p>
          <p className="text-2xl font-medium text-black">3</p>
          <p className="text-xs text-green-600 mt-1">All active</p>
        </div>
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">Best accuracy</p>
          <p className="text-2xl font-medium text-black">97.2%</p>
          <p className="text-xs text-gray-400 mt-1">Random Forest</p>
        </div>
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">Avg inference</p>
          <p className="text-2xl font-medium text-black">0.8s</p>
          <p className="text-xs text-gray-400 mt-1">Across all models</p>
        </div>
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">False positive</p>
          <p className="text-2xl font-medium text-black">1.4%</p>
          <p className="text-xs text-red-500 mt-1">▲ 0.1 this week</p>
        </div>
      </div>

      {/* Model list + detail panel */}
      <div className="grid grid-cols-5 gap-4">

        {/* Model cards — 3 cols */}
        <div className="col-span-3 space-y-3">
          {MODELS.map((m) => (
            <div
              key={m.id}
              onClick={() => setSelected(m.id)}
              className={`bg-white border rounded-xl p-5 cursor-pointer transition-all duration-150 ${
                selected === m.id
                  ? "border-teal-500 shadow-sm"
                  : "border-black/[0.06] hover:border-black/[0.14]"
              }`}
            >
              <div className="flex items-start justify-between mb-3">
                <div>
                  <p className="text-sm font-semibold text-gray-900">{m.name}</p>
                  <p className="text-xs text-gray-400 mt-0.5 leading-relaxed">{m.desc}</p>
                </div>
                <span className={`text-[10px] font-medium px-2.5 py-1 rounded-full ml-3 flex-shrink-0 ${m.badgeCls}`}>
                  {m.badge}
                </span>
              </div>

              <div className="space-y-2">
                {Object.entries(m.metrics).map(([k, v]) => (
                  <ProgressBar key={k} label={k} value={v} barCls={m.barCls} />
                ))}
              </div>

              <div className="flex gap-6 mt-4 pt-3 border-t border-gray-100">
                {Object.entries(m.stats).map(([k, v]) => (
                  <div key={k}>
                    <p className="text-sm font-semibold text-gray-900">{v}</p>
                    <p className="text-[11px] text-gray-400">{k}</p>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Detail panel — 2 cols */}
        <div className="col-span-2 space-y-3">

          {/* Selected model detail */}
          <div className="bg-white border border-black/[0.06] rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <p className="text-xs font-medium text-gray-500">Selected model</p>
              <span className={`text-[10px] font-medium px-2.5 py-1 rounded-full ${detail.badgeCls}`}>
                {detail.badge}
              </span>
            </div>
            <p className="text-sm font-semibold text-gray-900 mb-1">{detail.name}</p>
            <p className="text-xs text-gray-400 leading-relaxed">{detail.desc}</p>

            <div className="mt-4 space-y-2.5">
              {Object.entries(detail.metrics).map(([k, v]) => (
                <div key={k} className="flex items-center justify-between">
                  <span className="text-xs text-gray-500">{k}</span>
                  <div className="flex items-center gap-2">
                    <div className="w-24 bg-gray-200 rounded-full h-1.5 overflow-hidden">
                      <div className={`h-full rounded-full ${detail.barCls}`} style={{ width: `${v}%` }} />
                    </div>
                    <span className="text-xs font-medium text-gray-700 w-10 text-right">{v}%</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Accuracy comparison */}
          <div className="bg-white border border-black/[0.06] rounded-xl p-5">
            <p className="text-xs font-medium text-gray-500 mb-3">Accuracy comparison</p>
            <div className="space-y-2.5">
              {MODELS.map((m) => (
                <div key={m.id} className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${m.barCls}`} />
                  <span className="text-xs text-gray-500 flex-1 truncate">
                    {m.name.split(" ").slice(0, 2).join(" ")}
                  </span>
                  <div className="w-20 bg-gray-100 rounded-full h-1.5 overflow-hidden">
                    <div
                      className={`h-full rounded-full ${m.barCls} ${selected === m.id ? "opacity-100" : "opacity-40"}`}
                      style={{ width: `${m.metrics.Accuracy}%` }}
                    />
                  </div>
                  <span className={`text-xs w-10 text-right font-medium ${selected === m.id ? "text-gray-900" : "text-gray-400"}`}>
                    {m.metrics.Accuracy}%
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Set as default */}
          <div className="bg-white border border-black/[0.06] rounded-xl p-5">
            <p className="text-xs font-medium text-gray-500 mb-1">Set as default</p>
            <p className="text-xs text-gray-400 mb-3">
              The default model is used for all new detections unless overridden.
            </p>
            <button className="w-full py-2 rounded-lg bg-[#0c3a2d] text-emerald-300 text-sm font-medium hover:bg-[#0d4535] transition-colors">
              Use {detail.name.split(" ").slice(0, 2).join(" ")} as default
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}