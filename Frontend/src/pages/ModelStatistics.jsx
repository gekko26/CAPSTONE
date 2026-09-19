import { useState, useEffect } from "react";
import { API_BASE } from "../api";
import { BarChart3, AlertTriangle, Check, Layers, Cpu, GitMerge } from "lucide-react";

function MetricRow({k,v}){ return <div className="flex justify-between py-1.5 border-b last:border-0 text-xs"><span style={{color:"var(--text-muted)"}}>{k}</span><span className="font-mono font-medium" style={{color:"var(--text-primary)"}}>{v}</span></div>; }

export default function ModelStatistics(){
  const [data,setData]=useState(null);
  const [status,setStatus]=useState(null);
  const [tab,setTab]=useState("accuracy");
  const [loading,setLoading]=useState(true);
  useEffect(()=>{
    let a=true;
    const load=async()=>{
      try{
        const [s, m]=await Promise.all([
          fetch(`${API_BASE}/training/statistics`).then(r=>r.json()),
          fetch(`${API_BASE}/models/status`).then(r=>r.json()),
        ]);
        if(a){ setData(s); setStatus(m); }
      } catch{}
      if(a) setLoading(false);
    };
    load();
    const t=setInterval(load,10000);
    return ()=>{a=false; clearInterval(t);};
  },[]);
  if(loading) return <div className="p-6 text-xs" style={{color:"var(--text-muted)"}}>Loading transparent statistics…</div>;
  const metrics=data?.metrics || {};
  const sensor=metrics.sensor || {};
  const mobilenet=metrics.mobilenet || null;
  const fusion=metrics.fusion || null;
  const reliability=data?.reliability || {};
  const failures=data?.failures || [];

  const rf=sensor.random_forest, xgb=sensor.xgboost, bac=sensor.bac_regressor;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-4">
      <div className="flex items-center gap-2">
        <BarChart3 size={18} style={{color:"var(--accent)"}}/>
        <h1 className="text-lg font-medium" style={{color:"var(--text-primary)"}}>Models Statistics — Transparent</h1>
        <span className="text-xs px-2 py-0.5 rounded-full" style={{background:"color-mix(in srgb, var(--pass) 10%, transparent)", color:"var(--pass)"}}>Accuracy + Reliability + Failures</span>
      </div>
      <p className="text-xs" style={{color:"var(--text-muted)"}}>Shows good and bad metrics — low scores in red, failures listed, no hiding. Data from metrics.json + DB.</p>

      <div className="flex gap-1.5 flex-wrap">
        {["accuracy","reliability","failures","raw"].map(t=>(
          <button key={t} onClick={()=>setTab(t)} className="px-3 py-1 rounded-full text-xs border" style={{background: tab===t?"var(--bg-active)":"transparent", borderColor: tab===t?"var(--text-secondary)":"var(--border-subtle)", color: tab===t?"var(--text-primary)":"var(--text-muted)", fontWeight: tab===t?600:400}}>{t.toUpperCase()}</button>
        ))}
        <span className="ml-auto text-xs" style={{color:"var(--text-muted)"}}>Models trained {status?.trained_count ?? 0}/{status?.total ?? 0}</span>
      </div>

      {tab==="accuracy" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="rounded-xl p-4" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
            <div className="flex items-center gap-1.5 text-sm font-medium mb-2" style={{color:"var(--text-primary)"}}><Cpu size={14}/> Sensor RF (18D Plan A)</div>
            {rf ? <>
              <MetricRow k="Accuracy" v={`${rf.accuracy}%`} />
              <MetricRow k="Precision / Recall / F1" v={`${rf.precision}% / ${rf.recall}% / ${rf.f1}%`} />
              <MetricRow k="Samples / Trials" v={`${rf.samples} / ${rf.trials ?? "—"}`} />
              {rf.per_class && Object.entries(rf.per_class).map(([cls,vals])=>(
                <div key={cls} className="text-xs mt-1" style={{color: vals["f1-score"]<70?"var(--over)":"var(--text-secondary)"}}>{cls}: F1 {vals["f1-score"]}% Prec {vals.precision}% Rec {vals.recall}%</div>
              ))}
            </> : <div className="text-xs" style={{color:"var(--over)"}}>Not trained</div>}
          </div>
          <div className="rounded-xl p-4" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
            <div className="flex items-center gap-1.5 text-sm font-medium mb-2" style={{color:"var(--text-primary)"}}><Cpu size={14}/> Sensor XGB</div>
            {xgb ? <>
              <MetricRow k="Accuracy" v={`${xgb.accuracy}%`} />
              <MetricRow k="Precision / Recall / F1" v={`${xgb.precision}% / ${xgb.recall}% / ${xgb.f1}%`} />
              <MetricRow k="Samples / Trials" v={`${xgb.samples} / ${xgb.trials ?? "—"}`} />
            </> : <div className="text-xs" style={{color:"var(--over)"}}>Not trained</div>}
          </div>
          <div className="rounded-xl p-4" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
            <div className="flex items-center gap-1.5 text-sm font-medium mb-2" style={{color:"var(--text-primary)"}}><Layers size={14}/> MobileNet 4-class</div>
            {mobilenet ? <>
              <MetricRow k="Accuracy (val)" v={`${mobilenet.accuracy}%`} />
              <MetricRow k="Val loss" v={`${mobilenet.val_loss ?? "—"}`} />
              <MetricRow k="Epochs / Best" v={`${mobilenet.epochs ?? "—"} / ${mobilenet.best_epoch ?? "—"}`} />
              <MetricRow k="Classes" v={`${(mobilenet.classes||[]).join(", ")}`} />
              <MetricRow k="Final val" v={`${mobilenet.final_val_accuracy ?? "—"}%`} />
            </> : <div className="text-xs" style={{color:"var(--over)"}}>Not trained — hit /mobilenet</div>}
          </div>
          <div className="rounded-xl p-4" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
            <div className="flex items-center gap-1.5 text-sm font-medium mb-2" style={{color:"var(--text-primary)"}}><GitMerge size={14}/> Fusion + BAC</div>
            {fusion ? <><MetricRow k="Fusion acc" v={`${fusion.accuracy}%`} /><MetricRow k="Prec/Rec/F1" v={`${fusion.precision}%/${fusion.recall}%/${fusion.f1}%`} /></> : <div className="text-xs" style={{color:"var(--over)"}}>Fusion not trained — /fusion</div>}
            {bac ? <><MetricRow k="BAC MAE/RMSE/R²" v={`${bac.mae}/${bac.rmse}/${bac.r2}`} /><MetricRow k="BAC tier acc" v={`${bac.tier_accuracy}%`} /></> : <div className="text-xs mt-2" style={{color:"var(--text-muted)"}}>BAC: {bac?.error || "not trained / needs 100+ Breath rows"}</div>}
          </div>
        </div>
      )}

      {tab==="reliability" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="rounded-xl p-4" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
            <div className="text-sm font-medium mb-2" style={{color:"var(--text-primary)"}}>Reliability</div>
            <MetricRow k="Ready / Total" v={`${reliability.ready_ratio ?? 0}% (${data?.summary?.ready_to_train ?? 0}/${data?.summary?.total ?? 0})`} />
            <MetricRow k="Pending ratio" v={`${reliability.pending_ratio ?? 0}%`} />
            <MetricRow k="Balanced" v={reliability.balanced ? "YES" : "NO — need 1000/event +200/face"} />
            <MetricRow k="Trials No/Breath/Others" v={`${reliability.trial_coverage?.["0"] ?? 0}/${reliability.trial_coverage?.["1"] ?? 0}/${reliability.trial_coverage?.["2"] ?? 0}`} />
            <MetricRow k="Faces sober/drowsy/yawning/impaired" v={`${reliability.face_coverage?.sober ?? 0}/${reliability.face_coverage?.drowsy ?? 0}/${reliability.face_coverage?.yawning ?? 0}/${reliability.face_coverage?.impaired ?? 0}`} />
            {rf?.confusion_matrix && <div className="mt-3"><div className="text-xs font-medium" style={{color:"var(--text-muted)"}}>RF confusion (rows true 0/1/2):</div><pre className="text-xs p-2 rounded mt-1" style={{background:"var(--bg-active)"}}>{JSON.stringify(rf.confusion_matrix,null,1)}</pre></div>}
            {rf?.feature_importances && <div className="mt-3"><div className="text-xs font-medium" style={{color:"var(--text-muted)"}}>RF feature importances ({rf.feature_names?.join(", ")}):</div><pre className="text-[11px] p-2 rounded mt-1" style={{background:"var(--bg-active)"}}>{rf.feature_importances.join(", ")}</pre></div>}
          </div>
          <div className="rounded-xl p-4" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
            <div className="text-sm font-medium mb-2" style={{color:"var(--text-primary)"}}>Artifacts</div>
            {(status?.models||[]).map(m=>(
              <div key={m.name} className="flex justify-between text-xs py-1.5 border-b last:border-0">
                <span style={{color:"var(--text-secondary)"}}>{m.name} ({m.file})</span>
                <span style={{color: m.trained?"var(--pass)":"var(--over)"}}>{m.trained ? `${m.size_kb}KB ${m.modified?.split(" ")[0] ?? ""}` : "missing"}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab==="failures" && (
        <div className="rounded-xl p-4" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
          <div className="text-sm font-medium mb-2 flex items-center gap-1.5" style={{color: failures.length?"var(--over)":"var(--pass)"}}>{failures.length?<AlertTriangle size={14}/> : <Check size={14}/>} {failures.length ? `${failures.length} failure(s) / warning(s)` : "No failures — all transparent"}</div>
          {failures.length? failures.map((f,i)=><div key={i} className="text-xs py-1.5 border-b last:border-0" style={{color:"var(--over)"}}>• {f}</div>) : <div className="text-xs" style={{color:"var(--text-muted)"}}>Pending 0, sensor trained, mobilenet/fusion may still need hidden trainers.</div>}
        </div>
      )}

      {tab==="raw" && (
        <div className="rounded-xl p-4" style={{background:"var(--bg-card)", border:"1px solid var(--border-subtle)"}}>
          <pre className="text-[11px] whitespace-pre-wrap break-words" style={{color:"var(--text-secondary)"}}>{JSON.stringify({metrics, reliability, failures}, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
