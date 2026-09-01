import { useState, useEffect, useRef } from "react";
import { 
  ShieldCheck, ShieldAlert, ScanFace, Activity, 
  CheckCircle, XCircle, Camera, Lock, Unlock 
} from "lucide-react";

import { API_BASE as BASE } from "../api";
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
      const res = await fetch(`${BASE}/camera/stream/frame?overlay=0`, { cache: "no-store" });
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

      // 4. Decision Engine — FAIL-CLOSED: any missing/error payload denies access
      if (!result.ok || prediction.error || !prediction.final_label) {
        setSystemState("DENIED");
        setLastResult({
          name: cvData.identified ? cvData.name : "Unknown",
          reason: "Evaluation Failed — Access Withheld",
          color: "var(--over)",
          bg: "var(--over-bg)"
        });
        addLog(`Checkpoint error: ${prediction.error || `HTTP ${result.status}`} — denied by fail-closed policy`, "error");
      } else {
        const isImpaired = prediction.final_label === "Over Limit" || prediction.final_label === "Near Limit";
        const isDrowsy = prediction.impaired || prediction.eye_status === "drowsy";

        if (isImpaired || isDrowsy) {
          setSystemState("DENIED");
          setLastResult({
            name: cvData.identified ? cvData.name : "Unknown",
            reason: isImpaired ? "Alcohol Detected" : "Fatigue Detected",
            color: "var(--over)",
            bg: "var(--over-bg)"
          });
          addLog(`Access Denied: ${cvData.name || "Subject"} (${isImpaired ? "Alcohol" : "Fatigue"})`, "error");
        } else {
          setSystemState("PASSED");
          setLastResult({
            name: cvData.identified ? cvData.name : "Unknown",
            reason: "Clear to Enter",
            color: "var(--pass)",
            bg: "var(--pass-bg)"
          });
          addLog(`Access Granted: ${cvData.name || "Subject"}`, "success");
        }
      }

      // 5. Reset system for the next person after 5 seconds
      setTimeout(() => {
        setSystemState("IDLE");
        setLastResult(null);
        isScanningRef.current = false;
      }, 5000);

    } catch (error) {
      setSystemState("DENIED");
      setLastResult({
        name: "Unknown",
        reason: "System Fault — Access Withheld",
        color: "var(--over)",
        bg: "var(--over-bg)"
      });
      addLog("Checkpoint evaluation failed — denied by fail-closed policy", "error");

      setTimeout(() => {
        setSystemState("IDLE");
        setLastResult(null);
        isScanningRef.current = false;
      }, 5000);
    }
  };

  // ── UI Helpers ──────────────────────────────────────────────
  const statusConfig = {
    IDLE:     { text: "System armed",      sub: "Waiting for a subject…", color: "var(--pass)", Icon: ShieldCheck, glow: "0 0 0 3px var(--pass-bg)" },
    SCANNING: { text: "Analyzing…",         sub: "Please stand still",     color: "var(--near)", Icon: ScanFace,   glow: "0 0 0 3px var(--near-bg)" },
    PASSED:   { text: "Access granted",     sub: "Door unlocked",          color: "var(--pass)", Icon: Unlock,     glow: "0 0 0 3px var(--pass-bg)" },
    DENIED:   { text: "Access denied",      sub: "Security notified",      color: "var(--over)", Icon: Lock,       glow: "0 0 0 3px var(--over-bg)" },
  };

  const CurrentStatus = statusConfig[systemState];
  const StatusIcon = CurrentStatus.Icon;

  return (
    <div className="flex flex-col items-center p-4 gap-6">

      {/* Header */}
      <div className="w-full max-w-5xl flex justify-between items-center">
        <div className="flex items-center gap-3">
          <Activity size={22} style={{ color: "var(--accent)" }} />
          <h1 className="text-xl font-bold tracking-tight" style={{ color: "var(--text-primary)" }}>
            Deployment Gate
          </h1>
        </div>
        <div
          className="flex items-center gap-2 text-xs font-medium px-3 py-1.5 rounded-lg"
          style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" }}
        >
          <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: "var(--pass)" }}></span>
          Live · Node 01
        </div>
      </div>

      <div className="w-full max-w-5xl grid grid-cols-1 lg:grid-cols-3 gap-4">

        {/* Left: camera feed */}
        <div className="lg:col-span-2">
          <div
            className="relative w-full aspect-video rounded-xl overflow-hidden transition-all duration-500"
            style={{ background: "#101418", boxShadow: CurrentStatus.glow }}
          >
            {frameUrl && !camError ? (
              <img src={frameUrl} alt="Live feed" className="w-full h-full object-cover" />
            ) : (
              <div className="w-full h-full flex flex-col items-center justify-center gap-3" style={{ color: "var(--text-muted)" }}>
                <Camera size={36} strokeWidth={1.5} />
                <p className="text-sm">Camera feed offline</p>
              </div>
            )}

            {/* Status chip */}
            <div className="absolute top-3 left-3 flex items-center gap-2 px-2.5 py-1 rounded-lg backdrop-blur-sm"
                 style={{ background: "rgba(0,0,0,0.55)" }}>
              <StatusIcon size={13} style={{ color: CurrentStatus.color }} />
              <span className="text-xs font-medium" style={{ color: "#fff" }}>{CurrentStatus.text}</span>
            </div>

            {/* Crosshair when idle */}
            {systemState === "IDLE" && (
               <div className="absolute inset-0 flex items-center justify-center opacity-30 pointer-events-none">
                 <div className="w-48 h-48 rounded-lg" style={{ border: `1px solid ${CurrentStatus.color}` }}></div>
               </div>
            )}
          </div>
        </div>

        {/* Right: status + logs */}
        <div className="flex flex-col gap-4">

          {/* Big status card */}
          <div
            className="rounded-xl p-5 flex flex-col items-center justify-center text-center transition-all duration-500 min-h-[11rem]"
            style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}
          >
            <StatusIcon size={40} strokeWidth={1.5} style={{ color: CurrentStatus.color }} className="mb-3" />
            <h2 className="text-lg font-bold tracking-tight capitalize" style={{ color: CurrentStatus.color }}>
              {CurrentStatus.text}
            </h2>
            <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
              {CurrentStatus.sub}
            </p>
          </div>

          {/* Result card */}
          {lastResult && (
            <div className="rounded-xl p-4 animate-in fade-in slide-in-from-bottom-2"
                 style={{ background: lastResult.bg, border: `1px solid ${lastResult.color}33` }}>
              <div className="text-[11px] uppercase tracking-wide mb-1" style={{ color: "var(--text-muted)" }}>Last scan</div>
              <div className="text-base font-bold" style={{ color: lastResult.color }}>{lastResult.name}</div>
              <div className="text-xs font-medium mt-0.5" style={{ color: "var(--text-secondary)" }}>{lastResult.reason}</div>
            </div>
          )}

          {/* Event log */}
          <div
            className="flex-1 rounded-xl p-4 flex flex-col overflow-hidden"
            style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}
          >
            <h3 className="text-xs uppercase tracking-wider mb-3 flex items-center justify-between" style={{ color: "var(--text-muted)" }}>
              Event log
              <ShieldCheck size={12} />
            </h3>
            <div className="flex flex-col gap-2.5 overflow-y-auto max-h-64">
              {logs.map((log, i) => (
                <div key={i} className="flex gap-3 text-xs">
                  <span className="shrink-0 font-mono" style={{ color: "var(--text-muted)" }}>{log.time}</span>
                  <span style={{
                    color:
                      log.type === "error" ? "var(--over)" :
                      log.type === "success" ? "var(--pass)" : "var(--text-secondary)",
                  }}>
                    {log.msg}
                  </span>
                </div>
              ))}
              {logs.length === 0 && (
                <div className="text-center text-xs py-4" style={{ color: "var(--text-muted)" }}>No recent activity</div>
              )}
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}