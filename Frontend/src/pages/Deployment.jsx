import { useState, useEffect, useRef, useCallback } from "react";
import { 
  ShieldCheck, ShieldAlert, ScanFace, Activity, 
  CheckCircle, XCircle, Camera, Lock, Unlock 
} from "lucide-react";

const BASE = "http://localhost:8000";
const FRAME_MS = 200;
const ANALYZE_MS = 800;

export default function Deployment() {
  const [frameUrl, setFrameUrl] = useState(null);
  const [camError, setCamError] = useState(false);
  const [systemState, setSystemState] = useState("IDLE"); // IDLE, SCANNING, PASSED, DENIED
  const [lastResult, setLastResult] = useState(null);
  const [logs, setLogs] = useState([]);

  const loopActiveRef = useRef(true);
  const isScanningRef = useRef(false);
  const latestBlobRef = useRef(null);
  const frameTimeoutRef = useRef(null);
  const analyzeTimeoutRef = useRef(null);

  const addLog = (msg, type) => {
    setLogs(prev => [{ time: new Date().toLocaleTimeString(), msg, type }, ...prev].slice(0, 8));
  };

  // ── High-Speed Camera Feed ──────────────────────────────────
  const fetchFrameLoop = async () => {
    if (!loopActiveRef.current) return;
    try {
      const res = await fetch(`${BASE}/camera/stream/frame`, { cache: "no-store" });
      if (res.ok) {
        setCamError(false);
        const blob = await res.blob();
        latestBlobRef.current = blob;
        const url = URL.createObjectURL(blob);
        setFrameUrl(prev => { if (prev) URL.revokeObjectURL(prev); return url; });
      } else {
        setCamError(true);
      }
    } catch {
      setCamError(true);
    }
    frameTimeoutRef.current = setTimeout(fetchFrameLoop, FRAME_MS);
  };

  // ── The Automated Tripwire ──────────────────────────────────
  const runAnalysisLoop = async () => {
    if (!loopActiveRef.current) return;
    
    // Only analyze if the system is ready for the next person
    if (latestBlobRef.current && !isScanningRef.current && systemState === "IDLE") {
      try {
        const fd = new FormData();
        fd.append("file", latestBlobRef.current, "frame.jpg");

        const ar = await fetch(`${BASE}/camera/analyze`, { method: "POST", body: fd });
        if (ar.ok) {
          const data = await ar.json();
          
          // TRIPWIRE TRIGGERED! Someone stepped up to the camera
          if (data.is_close) {
            isScanningRef.current = true;
            executeSecurityCheckpoint(latestBlobRef.current, data);
          }
        }
      } catch (e) {
        // Suppress background ping errors
      }
    }
    analyzeTimeoutRef.current = setTimeout(runAnalysisLoop, ANALYZE_MS);
  };

  useEffect(() => {
    fetchFrameLoop();
    runAnalysisLoop();
    return () => {
      loopActiveRef.current = false;
      clearTimeout(frameTimeoutRef.current);
      clearTimeout(analyzeTimeoutRef.current);
    };
  }, []);

  // ── Security Checkpoint Logic ───────────────────────────────
  const executeSecurityCheckpoint = async (blob, cvData) => {
    setSystemState("SCANNING");
    addLog(`Subject detected: ${cvData.identified ? cvData.name : "Unknown"}`, "info");

    try {
      // 1. Trigger ESP32 Hardware (You can modify this to fetch live MQ3 arrays if needed)
      await fetch(`${BASE}/sensor/trigger`, { method: "POST" }).catch(() => {});
      
      // 2. Wait 3 seconds for breathalyzer payload
      await new Promise(res => setTimeout(res, 3000));

      // 3. Hit the Deployment Gatekeeper! 
      const fd = new FormData();
      fd.append("file", blob, "frame.jpg");
      
      // Note: In production, you'll pass the real MQ3 arrays from your ESP32 here
      const result = await fetch(`${BASE}/predict/full`, { 
        method: "POST", 
        body: fd 
      });
      
      const prediction = await result.json();
      
      // 4. Decision Engine
      const isImpaired = prediction.final_label === "Over Limit" || prediction.final_label === "Near Limit";
      const isDrowsy = prediction.impaired || prediction.eye_status === "Drowsy";

      if (isImpaired || isDrowsy) {
        setSystemState("DENIED");
        setLastResult({
          name: cvData.identified ? cvData.name : "Unknown",
          reason: isImpaired ? "Alcohol Detected" : "Fatigue Detected",
          color: "text-red-500",
          bg: "bg-red-500/10",
          border: "border-red-500/30"
        });
        addLog(`Access Denied: ${cvData.name || "Subject"} (${isImpaired ? "Alcohol" : "Fatigue"})`, "error");
      } else {
        setSystemState("PASSED");
        setLastResult({
          name: cvData.identified ? cvData.name : "Unknown",
          reason: "Clear to Enter",
          color: "text-emerald-400",
          bg: "bg-emerald-400/10",
          border: "border-emerald-400/30"
        });
        addLog(`Access Granted: ${cvData.name || "Subject"}`, "success");
      }

      // 5. Reset system for the next person after 5 seconds
      setTimeout(() => {
        setSystemState("IDLE");
        setLastResult(null);
        isScanningRef.current = false;
      }, 5000);

    } catch (error) {
      setSystemState("IDLE");
      isScanningRef.current = false;
      addLog("Checkpoint evaluation failed", "error");
    }
  };

  // ── UI Helpers ──────────────────────────────────────────────
  const statusConfig = {
    IDLE: { text: "SYSTEM ARMED", sub: "Waiting for subject...", color: "text-emerald-400", Icon: ShieldCheck, ring: "ring-emerald-400/20" },
    SCANNING: { text: "ANALYZING BIOMETRICS", sub: "Please stand still...", color: "text-amber-400", Icon: ScanFace, ring: "ring-amber-400/50 animate-pulse" },
    PASSED: { text: "ACCESS GRANTED", sub: "Door Unlocked", color: "text-emerald-400", Icon: Unlock, ring: "ring-emerald-400/50" },
    DENIED: { text: "ACCESS DENIED", sub: "Security Notified", color: "text-red-500", Icon: Lock, ring: "ring-red-500/50 shadow-[0_0_50px_rgba(239,68,68,0.2)]" }
  };

  const CurrentStatus = statusConfig[systemState];
  const StatusIcon = CurrentStatus.Icon;

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-slate-200 p-6 font-mono flex flex-col items-center">
      
      {/* Header */}
      <div className="w-full max-w-5xl flex justify-between items-center mb-8 pb-4 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <Activity size={24} className="text-emerald-400" />
          <h1 className="text-2xl font-bold tracking-widest text-white">ALCODETECT <span className="font-light text-slate-500">GATEWAY</span></h1>
        </div>
        <div className="flex items-center gap-2 text-xs font-bold tracking-widest px-3 py-1 rounded bg-slate-900 border border-slate-800">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
          LIVE NODE 01
        </div>
      </div>

      <div className="w-full max-w-5xl grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Left Col: Camera Feed */}
        <div className="lg:col-span-2 flex flex-col gap-4">
          <div className={`relative w-full aspect-video bg-black rounded-xl border border-slate-800 overflow-hidden ring-4 transition-all duration-500 ${CurrentStatus.ring}`}>
            
            {frameUrl && !camError ? (
              <img src={frameUrl} alt="Live Security Feed" className="w-full h-full object-cover opacity-80" />
            ) : (
              <div className="w-full h-full flex flex-col items-center justify-center text-slate-600 gap-3">
                <Camera size={40} />
                <p className="tracking-widest uppercase text-sm">Camera Feed Offline</p>
              </div>
            )}

            {/* Overlays */}
            <div className="absolute top-4 left-4 flex gap-2">
              <span className="px-2 py-1 bg-black/60 text-[10px] uppercase tracking-widest rounded text-slate-300 backdrop-blur-sm border border-white/10">
                1080P HD
              </span>
              <span className="px-2 py-1 bg-black/60 text-[10px] uppercase tracking-widest rounded text-emerald-400 backdrop-blur-sm border border-white/10">
                AI ACTIVE
              </span>
            </div>

            {/* Crosshairs when idle */}
            {systemState === "IDLE" && (
               <div className="absolute inset-0 flex items-center justify-center opacity-30 pointer-events-none">
                 <div className="w-48 h-48 border border-emerald-400/50 rounded-lg"></div>
               </div>
            )}
            
          </div>
        </div>

        {/* Right Col: Status & Logs */}
        <div className="flex flex-col gap-6">
          
          {/* Big Status Box */}
          <div className={`p-6 rounded-xl border flex flex-col items-center justify-center text-center transition-all duration-500 bg-slate-900 border-slate-800 h-48`}>
            <StatusIcon size={48} className={`mb-4 ${CurrentStatus.color}`} />
            <h2 className={`text-xl font-bold tracking-widest uppercase ${CurrentStatus.color}`}>
              {CurrentStatus.text}
            </h2>
            <p className="text-xs tracking-widest uppercase text-slate-500 mt-2">
              {CurrentStatus.sub}
            </p>
          </div>

          {/* Result Card (Pops up on Pass/Fail) */}
          {lastResult && (
            <div className={`p-4 rounded-xl border ${lastResult.bg} ${lastResult.border} animate-in fade-in slide-in-from-bottom-4`}>
              <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">Last Scan Result</div>
              <div className={`text-lg font-bold tracking-wider ${lastResult.color}`}>{lastResult.name}</div>
              <div className="text-sm font-medium mt-1 text-slate-300">{lastResult.reason}</div>
            </div>
          )}

          {/* Security Log */}
          <div className="flex-1 bg-slate-900 rounded-xl border border-slate-800 p-4 flex flex-col overflow-hidden">
            <h3 className="text-xs uppercase tracking-widest text-slate-500 mb-4 flex items-center justify-between">
              Event Log
              <ShieldCheck size={12} />
            </h3>
            <div className="flex flex-col gap-3 overflow-y-auto">
              {logs.map((log, i) => (
                <div key={i} className="flex gap-3 text-[11px]">
                  <span className="text-slate-600 shrink-0">{log.time}</span>
                  <span className={
                    log.type === "error" ? "text-red-400" :
                    log.type === "success" ? "text-emerald-400" : "text-slate-300"
                  }>
                    {log.msg}
                  </span>
                </div>
              ))}
              {logs.length === 0 && (
                <div className="text-center text-slate-700 text-xs italic mt-4">No recent activity</div>
              )}
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}