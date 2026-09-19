import { useState, useEffect } from "react";
import { API_BASE } from "../api";
import { GitMerge, Play, RefreshCw, AlertTriangle, Check } from "lucide-react";

export default function FusionTrainer() {
  const [status, setStatus] = useState(null);
  const [training, setTraining] = useState(false);
  const [result, setResult] = useState(null);

  const load = async () => {
    try {
      const m = await fetch(`${API_BASE}/models/status`).then(r=>r.json());
      setStatus(m);
    } catch {}
  };
  useEffect(()=>{ load(); },[]);

  const train = async () => {
    setTraining(true);
    setResult(null);
    try {
      const r = await fetch(`${API_BASE}/training/train/fusion`, {method:"POST"});
      const d = await r.json();
      setResult(d);
      load();
    } catch(e){ setResult({error: String(e)}); }
    finally{ setTraining(false); }
  };

  const fusion = status?.models?.find(m=>m.name==="fusion");

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-4">
      <div className="flex items-center gap-2">
        <GitMerge size={18} style={{color:"var(--accent)"}}/>
        <h1 className="text-lg font-medium" style={{color:"var(--text-primary)"}}>Fusion Trainer — hidden /fusion</h1>
        <span className="text-xs px-2 py-0.5 rounded-full" style={{background:"var(--bg-active)", color:"var(--text-muted)"}}>URL only</span>
      </div>
      <p className="text-xs" style={{color:"var(--text-muted)"}}>Not in sidebar — URL only. Trains fusion (sensor + visual + EAR → pass/near/over). Uses <b>deployment_logs if available</b> (paired sensor+visual from Live Flow) — if less than 10 logs, it auto-generates a synthetic demo dataset so you can still train/validate. In production, collect via Live Flow for real paired data.</p>

      <div className="rounded-xl p-4 flex gap-3 items-center" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
        <button onClick={train} disabled={training} className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50" style={{background:"var(--text-primary)", color:"var(--bg-card)"}}>
          {training ? <><RefreshCw size={13} className="animate-spin"/> Training...</> : <><Play size={13}/> Train Fusion</>}
        </button>
        <button onClick={load} className="px-3 py-1.5 rounded-lg text-sm border" style={{borderColor:"var(--border-subtle)"}}><RefreshCw size={13}/></button>
        <div className="ml-auto text-xs" style={{color:"var(--text-muted)"}}>
          Status: {fusion?.trained ? <span style={{color:"var(--pass)"}}><Check size={11}/> Trained {fusion?.metrics?.accuracy ?? "—"}%</span> : "Not trained"}
        </div>
      </div>

      {result && (
        <div className="rounded-xl p-4 text-xs" style={{background: result.error?"color-mix(in srgb, var(--over) 8%, transparent)":"color-mix(in srgb, var(--pass) 8%, transparent)", border:"1px solid var(--border-subtle)"}}>
          <pre className="whitespace-pre-wrap break-words">{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}

      {fusion?.metrics && (
        <div className="rounded-xl p-4" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
          <div className="text-sm font-medium mb-2" style={{color:"var(--text-primary)"}}>Transparent metrics</div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>Accuracy: <b>{fusion.metrics.accuracy ?? "—"}%</b></div>
            <div>Precision: <b>{fusion.metrics.precision ?? "—"}%</b></div>
            <div>Recall: <b>{fusion.metrics.recall ?? "—"}%</b></div>
            <div>F1: <b>{fusion.metrics.f1 ?? "—"}%</b></div>
            <div>Samples: <b>{fusion.metrics.samples ?? "—"}</b></div>
            <div>Confusion: <b>{fusion.metrics.confusion_matrix ? "yes" : "—"}</b></div>
          </div>
          {fusion.metrics.confusion_matrix && (
            <pre className="text-[11px] mt-2 p-2 rounded" style={{background:"var(--bg-active)"}}>{JSON.stringify(fusion.metrics.confusion_matrix,null,2)}</pre>
          )}
          {fusion.metrics.feature_importances && (
            <div className="text-[11px] mt-2" style={{color:"var(--text-muted)"}}>Feature importances: {fusion.metrics.feature_importances.join(", ")}</div>
          )}
        </div>
      )}

      <div className="text-xs" style={{color:"var(--text-muted)"}}>No deployment_logs required — will synthesize demo data if none found. For real accuracy, collect paired Live Flow events first.</div>
    </div>
  );
}
