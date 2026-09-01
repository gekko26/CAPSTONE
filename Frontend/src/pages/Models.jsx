// File: Frontend/src/pages/Models.jsx
import { useState, useEffect } from "react";
import { API_BASE } from "../api";
import { CheckCircle, CircleDashed } from "lucide-react";

// Maps backend artifact names (models/saved/*) to display entries
const MODELS = [
  {
    key: "random_forest",
    name: "Random Forest",
    desc: "Sensor model — classifies MQ3 window features into No alcohol / Breath alcohol / Sanitizer.",
  },
  {
    key: "xgboost",
    name: "XGBoost",
    desc: "Second sensor model — runs alongside Random Forest and the ensemble requires both to agree.",
  },
  {
    key: "mobilenet",
    name: "MobileNetV2",
    desc: "Vision model — classifies a face image as sober or impaired using transfer learning.",
  },
  {
    key: "fusion",
    name: "Fusion Model",
    desc: "Final decision model — combines sensor results, vision result and eye metrics into Pass / Near Limit / Over Limit.",
  },
];

function MetricRow({ label, value }) {
  return (
    <div className="flex items-center justify-between py-2 border-b last:border-0 ">
      <span className="text-xs" style={{ color: "var(--text-muted)" }}>{label}</span>
      <span className="text-sm font-semibold font-mono" style={{ color: "var(--text-primary)" }}>{value}</span>
    </div>
  );
}

export default function Models() {
  const [selectedKey, setSelectedKey] = useState("fusion");
  const [statusMap, setStatusMap] = useState({});
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/models/status`);
        if (!res.ok) throw new Error(res.status);
        const data = await res.json();
        if (active) {
          const map = {};
          for (const m of data.models) map[m.name] = m;
          setStatusMap(map);
          setOffline(false);
        }
      } catch {
        if (active) setOffline(true);
      }
    };
    load();
    return () => { active = false; };
  }, []);

  const entry   = statusMap[selectedKey];
  const detail  = MODELS.find((m) => m.key === selectedKey);
  const trainedCount = Object.values(statusMap).filter((s) => s.trained).length;
  // Real peak accuracy across all trained models that reported metrics
  const accuracies = Object.values(statusMap)
    .filter((s) => s.trained && s.metrics?.accuracy != null)
    .map((s) => s.metrics.accuracy);
  const bestAccuracy = accuracies.length ? Math.max(...accuracies) : null;

  return (
    <div className="space-y-6 max-w-6xl mx-auto p-1">

      {/* Summary row */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-xl p-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
          <p className="text-xs mb-1" style={{ color: "var(--text-muted)" }}>Models trained</p>
          <p className="text-2xl font-bold" style={{ color: offline ? "var(--text-muted)" : "var(--text-primary)" }}>
            {offline ? "—" : `${trainedCount}/${MODELS.length}`}
          </p>
        </div>
        <div className="rounded-xl p-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
          <p className="text-xs mb-1" style={{ color: "var(--text-muted)" }}>Best test accuracy</p>
          <p className="text-2xl font-bold" style={{ color: "var(--text-primary)" }}>
            {bestAccuracy != null ? `${bestAccuracy}%` : "—"}
          </p>
        </div>
        <div className="rounded-xl p-4" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
          <p className="text-xs mb-1" style={{ color: "var(--text-muted)" }}>Last trained</p>
          <p className="text-2xl font-bold" style={{ color: "var(--text-primary)" }}>
            {entry?.modified ? entry.modified.split(" ")[0] : "—"}
          </p>
        </div>
      </div>

      {/* Model list + detail */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">

        {/* List */}
        <div className="lg:col-span-3 space-y-2.5">
          {MODELS.map((m) => {
            const s = statusMap[m.key];
            const active = selectedKey === m.key;
            return (
              <button
                key={m.key}
                onClick={() => setSelectedKey(m.key)}
                className="w-full text-left rounded-xl p-4 transition-all"
                style={{
                  background: active ? "var(--bg-active)" : "var(--bg-card)",
                  border: `1px solid ${active ? "var(--accent)" : "var(--border-subtle)"}`,
                  cursor: "pointer",
                }}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{m.name}</p>
                    <p className="text-xs mt-1 leading-relaxed max-w-xl" style={{ color: "var(--text-muted)" }}>{m.desc}</p>
                  </div>
                  {s?.trained ? (
                    <span
                      className="text-[11px] font-semibold px-2 py-0.5 rounded-full shrink-0 flex items-center gap-1"
                      style={{ background: "var(--pass-bg)", color: "var(--pass-text)" }}
                    >
                      <CheckCircle size={10} /> Trained
                    </span>
                  ) : (
                    <span
                      className="text-[11px] font-medium px-2 py-0.5 rounded-full shrink-0 flex items-center gap-1"
                      style={{ background: "var(--bg-page)", color: "var(--text-muted)" }}
                    >
                      <CircleDashed size={10} /> Not trained
                    </span>
                  )}
                </div>

                {/* Real accuracy once trained */}
                {s?.trained && s.metrics?.accuracy != null && (
                  <div className="flex items-center gap-3 mt-3">
                    <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--border-subtle)" }}>
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${Math.min(s.metrics.accuracy, 100)}%`, background: "var(--accent)" }}
                      />
                    </div>
                    <span className="text-xs font-semibold font-mono w-14 text-right" style={{ color: "var(--text-primary)" }}>
                      {s.metrics.accuracy}%
                    </span>
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {/* Detail */}
        <div className="lg:col-span-2">
          <div className="rounded-xl p-5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
            <p className="text-xs uppercase tracking-wider mb-1" style={{ color: "var(--text-muted)" }}>Details</p>
            <p className="text-base font-bold mb-2" style={{ color: "var(--text-primary)" }}>{detail.name}</p>
            <p className="text-xs leading-relaxed pb-3 border-b" style={{ color: "var(--text-secondary)", borderColor: "var(--border-subtle)" }}>
              {detail.desc}
            </p>

            {offline ? (
              <p className="text-xs mt-4" style={{ color: "var(--danger)" }}>Backend offline — cannot read model files.</p>
            ) : !entry?.trained ? (
              <p className="text-xs mt-4" style={{ color: "var(--text-muted)" }}>
                This model hasn't been trained yet. Collect data in Training and run training to see real results here.
              </p>
            ) : (
              <div className="mt-3">
                {entry.metrics ? (
                  <>
                    <MetricRow label="Test accuracy"  value={entry.metrics.accuracy != null ? `${entry.metrics.accuracy}%` : "—"} />
                    <MetricRow label="Precision"      value={entry.metrics.precision != null ? `${entry.metrics.precision}%` : undefined} />
                    <MetricRow label="Recall"         value={entry.metrics.recall != null ? `${entry.metrics.recall}%` : undefined} />
                    <MetricRow label="Training samples" value={entry.metrics.samples ?? entry.metrics.epochs} />
                    <MetricRow label="Trained on"     value={entry.modified?.split(" ")[0] ?? "—"} />
                  </>
                ) : (
                  <p className="text-xs mt-4" style={{ color: "var(--text-muted)" }}>
                    Model file exists but no recorded evaluation metrics were found.
                  </p>
                )}
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
