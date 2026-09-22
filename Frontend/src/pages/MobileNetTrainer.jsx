import { useState, useEffect } from "react";
import { API_BASE } from "../api";
import { Cpu, Play, RefreshCw, AlertTriangle, Check, Layers } from "lucide-react";

export default function MobileNetTrainer() {
  const [summary, setSummary] = useState(null);
  const [status, setStatus] = useState(null);
  const [training, setTraining] = useState(false);
  const [result, setResult] = useState(null);
  const [batchSize, setBatchSize] = useState(8);
  const [epochs, setEpochs] = useState(5);

  const load = async () => {
    try {
      const [s, m] = await Promise.all([
        fetch(`${API_BASE}/training/summary`).then(r=>r.json()),
        fetch(`${API_BASE}/models/status`).then(r=>r.json()),
      ]);
      setSummary(s);
      setStatus(m);
    } catch {}
  };
  useEffect(()=>{ load(); },[]);

  const train = async () => {
    setTraining(true);
    setResult(null);
    try {
      const r = await fetch(`${API_BASE}/training/train/mobilenet`, {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({batch_size: batchSize, epochs, incremental: true})
      });
      const d = await r.json();
      setResult(d);
      load();
    } catch(e){ setResult({error: String(e)}); }
    finally{ setTraining(false); }
  };

  const faces = summary?.face_images || {};
  const mobilenet = status?.models?.find(m=>m.name==="mobilenet");

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-4">
      <div className="flex items-center gap-2">
        <Layers size={18} style={{color:"var(--accent)"}}/>
        <h1 className="text-lg font-medium" style={{color:"var(--text-primary)"}}>MobileNet Trainer — hidden /mobilenet</h1>
        <span className="text-xs px-2 py-0.5 rounded-full" style={{background:"var(--bg-active)", color:"var(--text-muted)"}}>7GB incremental</span>
      </div>
      <p className="text-xs" style={{color:"var(--text-muted)"}}>Not in sidebar — URL only. <b>3 classes sober/fatigue/impaired =600</b> (fatigue = drowsy(ear)+yawning(mar) aggregated, yawning is mar measurement not class). Batch 8 for 7GB RAM, epochs 5 per hit.</p>

      <div className="grid grid-cols-3 gap-2">
        {["sober","fatigue","impaired"].map(k=>(
          <div key={k} className="rounded-lg p-3 text-center" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
            <div className="text-xs" style={{color:"var(--text-muted)"}}>{k}</div>
            <div className="text-xl font-medium" style={{color: (faces[k]||0)>=200?"var(--pass)":"var(--near)"}}>{faces[k]??0}/200</div>
          </div>
        ))}
      </div>

      <div className="rounded-xl p-4 flex flex-wrap gap-3 items-end" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
        <div>
          <div className="text-xs mb-1" style={{color:"var(--text-muted)"}}>Batch size (7GB → 8)</div>
          <select value={batchSize} onChange={e=>setBatchSize(parseInt(e.target.value))} className="rounded-lg px-3 py-1.5 text-sm border" style={{background:"var(--bg-active)", borderColor:"var(--border-subtle)"}}>
            <option value={8}>8 (safe)</option><option value={16}>16 (if 16GB)</option><option value={4}>4 (ultra safe)</option>
          </select>
        </div>
        <div>
          <div className="text-xs mb-1" style={{color:"var(--text-muted)"}}>Epochs per hit</div>
          <select value={epochs} onChange={e=>setEpochs(parseInt(e.target.value))} className="rounded-lg px-3 py-1.5 text-sm border" style={{background:"var(--bg-active)", borderColor:"var(--border-subtle)"}}>
            <option value={5}>5</option><option value={10}>10</option><option value={20}>20</option>
          </select>
        </div>
        <button onClick={train} disabled={training} className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50" style={{background:"var(--text-primary)", color:"var(--bg-card)"}}>
          {training ? <><RefreshCw size={13} className="animate-spin"/> Training...</> : <><Play size={13}/> Train MobileNet</>}
        </button>
        <button onClick={load} className="px-3 py-1.5 rounded-lg text-sm border" style={{borderColor:"var(--border-subtle)"}}><RefreshCw size={13}/></button>
        <div className="ml-auto text-xs" style={{color:"var(--text-muted)"}}>
          Status: {mobilenet?.trained ? <span style={{color:"var(--pass)"}}><Check size={11}/> Trained {mobilenet?.metrics?.accuracy ?? "—"}%</span> : "Not trained"}
        </div>
      </div>

      {result && (
        <div className="rounded-xl p-4 text-xs" style={{background: result.error?"color-mix(in srgb, var(--over) 8%, transparent)":"color-mix(in srgb, var(--pass) 8%, transparent)", border:"1px solid var(--border-subtle)"}}>
          <pre className="whitespace-pre-wrap break-words">{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}

      {mobilenet?.metrics && (
        <div className="rounded-xl p-4" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
          <div className="text-sm font-medium mb-2" style={{color:"var(--text-primary)"}}>Transparent metrics</div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>Accuracy: <b>{mobilenet.metrics.accuracy ?? "—"}%</b></div>
            <div>Val loss: <b>{mobilenet.metrics.val_loss ?? "—"}</b></div>
            <div>Epochs: <b>{mobilenet.metrics.epochs ?? "—"}</b> (best {mobilenet.metrics.best_epoch ?? "—"})</div>
            <div>Classes: <b>{(mobilenet.metrics.classes||[]).join(", ")}</b></div>
            <div>Final val: <b>{mobilenet.metrics.final_val_accuracy ?? "—"}%</b></div>
          </div>
          {mobilenet.metrics.history_val_accuracy && (
            <div className="mt-3 text-[11px] font-mono" style={{color:"var(--text-muted)"}}>History val_acc: {mobilenet.metrics.history_val_accuracy.join(" → ")} </div>
          )}
        </div>
      )}

      <div className="text-xs flex gap-2" style={{color:"var(--text-muted)"}}>
        <AlertTriangle size={12}/> Incremental: if interrupted, hit Train again to resume (resume via initial_epoch not yet wired — currently retrains).
      </div>
    </div>
  );
}
