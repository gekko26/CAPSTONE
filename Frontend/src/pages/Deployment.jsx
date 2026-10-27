import { useState, useEffect, useRef } from "react";
import { 
  ShieldCheck, ShieldAlert, ScanFace, Activity, 
  CheckCircle, XCircle, Camera, Lock, Unlock, AlertTriangle 
} from "lucide-react";

import { API_BASE as BASE } from "../api";
import { getTiming, busySeconds, FALLBACK_TIMING } from "../config/timing";
import { createStabilityGate, PROVISIONAL } from "../config/deploymentGate";
const FRAME_MS = 60;  // 15 FPS max for stream1 — max fps, camera-only
const ANALYZE_MS = 800;
const nowMs = () => Date.now();
// post-trigger wait comes from /sensor/timing (see src/config/timing.js)
let TIMING = FALLBACK_TIMING;

export default function Deployment() {
  const [frameUrl, setFrameUrl] = useState(null);
  const [camError, setCamError] = useState(false);
  const [systemState, setSystemState] = useState("IDLE"); // IDLE, SCANNING, PASSED, DENIED
  const [lastResult, setLastResult] = useState(null);
  const [logs, setLogs] = useState([]);
  const [overlayOn, setOverlayOn] = useState(false);
  const overlayRef = useRef(overlayOn);
  // ── Timing from the backend (single source) ──────────────────────
  useEffect(() => { let alive = true; getTiming().then(t => { if (alive) TIMING = t; });
    return () => { alive = false; }; }, []);

  useEffect(() => { overlayRef.current = overlayOn; }, [overlayOn]);

  const loopActiveRef = useRef(true);
  const isScanningRef = useRef(false);
  const latestBlobRef = useRef(null);
  const frameTimeoutRef = useRef(null);
  const analyzeTimeoutRef = useRef(null);
  const fetchingRef = useRef(false);
  const blobAtRef = useRef(0);
  const gateRef = useRef(null);
  if (gateRef.current == null) {
    gateRef.current = createStabilityGate();
  }

  const addLog = (msg, type) => {
    setLogs(prev => [{ time: new Date().toLocaleTimeString(), msg, type }, ...prev].slice(0, 8));
  };

  // ── Self-scheduling recursion + single-flight guard (A) — camera-only, FRAME_MS unchanged
  const fetchFrameLoop = async () => {
    if (!loopActiveRef.current) return;
    if (fetchingRef.current) { frameTimeoutRef.current = setTimeout(fetchFrameLoop, FRAME_MS); return; }
    fetchingRef.current = true;
    try {
      const overlayParam = overlayRef.current ? "1" : "0";
      const res = await fetch(`${BASE}/camera/stream/frame?overlay=${overlayParam}`, { cache: "no-store" });
      if (res.ok) {
        setCamError(false);
        const blob = await res.blob();
        latestBlobRef.current = blob;
        blobAtRef.current = nowMs();
        const url = URL.createObjectURL(blob);
        setFrameUrl(prev => { if (prev) URL.revokeObjectURL(prev); return url; });
      } else {
        setCamError(true);
      }
    } catch {
      setCamError(true);
    } finally {
      fetchingRef.current = false;
      frameTimeoutRef.current = setTimeout(fetchFrameLoop, FRAME_MS);
    }
  };

  // ── The Automated Tripwire ──────────────────────────────────
  const runAnalysisLoop = async () => {
    if (!loopActiveRef.current) return;
    
    // Only analyze if the system is ready for the next person.
    if (latestBlobRef.current && !isScanningRef.current && systemState === "IDLE") {
      if (nowMs() - blobAtRef.current > PROVISIONAL.STALE_BLOB_MS) {
        // A stalled frame must never let one person's count or no-stop window carry
        // over to the next: reset everything and log the stall. Camera fetch errors
        // leave the blob stale, so they land here too.
        gateRef.current.reset();
        console.log("[gate]", "reset", "stale_blob");
      } else {
        try {
          const fd = new FormData();
          fd.append("file", latestBlobRef.current, "frame.jpg");

          const ar = await fetch(`${BASE}/camera/analyze`, { method: "POST", body: fd });
          if (ar.ok) {
            const data = await ar.json();

            // TRIPWIRE: only when the SAME face has been close and stable for N
            // consecutive fresh analyses. Invalid/cached/busy responses reset it.
            const v = gateRef.current.feed({ analysis: data, now: nowMs() });
            if (v.reason) console.log("[gate]", v.verdict, v.reason);
            if (v.verdict === "ready") {
              console.log("[gate] ready", v.count, new Date(nowMs()).toISOString());
              isScanningRef.current = true;
              gateRef.current.disarm();
              executeSecurityCheckpoint(latestBlobRef.current, data);
            } else if (v.verdict === "warn_nostop") {
              setLastResult({ name: "Unknown", reason: "no stop", color: "var(--near)", bg: "var(--near-bg)" });
            }
          } else {
            const v = gateRef.current.feed({ analysis: null, now: nowMs() });
            if (v.reason) console.log("[gate]", v.verdict, v.reason);
          }
        } catch {
          // Suppress background ping errors, but never let the gate keep a stale count.
          const v = gateRef.current.feed({ analysis: null, now: nowMs() });
          if (v.reason) console.log("[gate]", v.verdict, v.reason);
        }
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
      // 1. Trigger ESP32 Hardware — capture reading_id for staleness-checked /predict/full
      let readingId = null;
      let busy = false;
      try {
        const tr = await fetch(`${BASE}/sensor/trigger`, { method: "POST" });
        if (tr.ok) {
          const tj = await tr.json();
          readingId = tj.reading_id || tj.readingId || null;
          // The ESP32 refuses a trigger while its relay sequence is running.
          busy = tj.busy === true || tj.triggered === false;
        }
      } catch {}
      
      // 1b. Busy is NOT a stale reading — do not fall into the recapture flow
      if (busy) {
        const waitS = busySeconds(TIMING);
        setSystemState("IDLE");
        setLastResult({ name: "Device busy", reason: `Relay sequence running — wait about ${waitS}s and try again`, color: "var(--near)", bg: "var(--near-bg)" });
        addLog(`ESP32 busy (relay sequence running) — retry in about ${waitS}s`, "warn");
        setTimeout(() => { setLastResult(null); isScanningRef.current = false; gateRef.current.reset(); }, 4000);
        return;
      }
      
      // 2. Wait for the pre-roll + sampling window + settle margin before reading
      await new Promise(res => setTimeout(res, TIMING.ready_after_ms));

      // 3. Hit the Deployment Gatekeeper! reading_id is required (FIX 2 staleness check)
      if (!readingId) {
        setSystemState("DENIED");
        setLastResult({ name: cvData.identified ? cvData.name : "Unknown", reason: "Trigger failed — no reading_id", color: "var(--over)", bg: "var(--over-bg)" });
        addLog("Trigger failed: no reading_id from /sensor/trigger — denied", "error");
        setTimeout(() => { setSystemState("IDLE"); setLastResult(null); isScanningRef.current = false; gateRef.current.reset(); }, 5000);
        return;
      }
      const fd = new FormData();
      fd.append("file", blob, "frame.jpg");
      fd.append("reading_id", String(readingId));
      
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
          reason: "WARN: Evaluation Failed — Access Withheld",
          color: "var(--over)",
          bg: "var(--over-bg)"
        });
        addLog(`Checkpoint error: ${prediction.error || `HTTP ${result.status}`} — denied by fail-closed policy`, "error");
      } else if (prediction.final_label === "RECAPTURE_NEEDED") {
        setSystemState("SCANNING");
        addLog("Recapture needed — please realign", "warn");
        setTimeout(() => {
          isScanningRef.current = false;
          gateRef.current.reset();
        }, 1500);
        return;
      } else {
        // The server owns the decision. This page no longer derives PASS/DENY from
        // impaired / eye_status / EAR / MAR / pitch — it renders decision.status.
        const dec = (prediction && prediction.decision) || null;
        const status = dec && typeof dec.status === "string" ? dec.status.toUpperCase() : null;
        const labels = dec && Array.isArray(dec.labels) ? dec.labels.filter(Boolean) : [];
        const reasonText = labels.length ? labels.join(" · ") : "No decision";

        if (status === "PASS") {
          setSystemState("PASSED");
          setLastResult({
            name: cvData.identified ? cvData.name : "Unknown",
            reason: "Clear to Enter",
            color: "var(--pass)",
            bg: "var(--pass-bg)"
          });
          addLog(`Access Granted: ${cvData.name || "Subject"}`, "success");
        } else if (status === "FAIL") {
          setSystemState("DENIED");
          setLastResult({
            name: cvData.identified ? cvData.name : "Unknown",
            reason: reasonText,
            color: "var(--over)",
            bg: "var(--over-bg)"
          });
          addLog(`Access Denied: ${cvData.name || "Subject"} (${reasonText})`, "error");
        } else if (status === "WARN") {
          setSystemState("WARNED");
          setLastResult({
            name: cvData.identified ? cvData.name : "Unknown",
            reason: reasonText,
            color: "var(--near)",
            bg: "var(--near-bg)"
          });
          addLog(`Access Warning: ${cvData.name || "Subject"} (${reasonText})`, "warn");
        } else {
          // Missing or malformed decision object: never PASS.
          setSystemState("WARNED");
          setLastResult({
            name: cvData.identified ? cvData.name : "Unknown",
            reason: "WARN: No decision",
            color: "var(--near)",
            bg: "var(--near-bg)"
          });
          addLog("No decision returned by the server — treated as a warning", "warn");
        }
      }

      // 5. Reset system for the next person after 5 seconds
      setTimeout(() => {
        setSystemState("IDLE");
        setLastResult(null);
        isScanningRef.current = false;
        gateRef.current.reset();
      }, 5000);

    } catch {
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
        gateRef.current.reset();
      }, 5000);
    }
  };

  // ── UI Helpers ──────────────────────────────────────────────
  const statusConfig = {
    IDLE:     { text: "System armed",      sub: "Waiting for a subject…", color: "var(--pass)", Icon: ShieldCheck, glow: "0 0 0 3px var(--pass-bg)" },
    SCANNING: { text: "Analyzing…",         sub: "Please stand still",     color: "var(--near)", Icon: ScanFace,   glow: "0 0 0 3px var(--near-bg)" },
    PASSED:   { text: "Access granted",     sub: "Door unlocked",          color: "var(--pass)", Icon: Unlock,     glow: "0 0 0 3px var(--pass-bg)" },
    WARNED:   { text: "Access warning",     sub: "Review required",         color: "var(--near)", Icon: AlertTriangle, glow: "0 0 0 3px var(--near-bg)" },
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
            {/* Overlay toggle */}
            <button
              onClick={() => setOverlayOn(v => !v)}
              title={overlayOn ? "HUD on — backend box (cost ~3 FPS)" : "HUD off — smooth 15 FPS"}
              className="absolute top-3 right-3 text-[10px] px-2 py-0.5 rounded-full border font-medium transition-colors"
              style={{ background: overlayOn ? "rgba(255,255,255,0.14)" : "rgba(0,0,0,0.45)", color: overlayOn ? "#fff" : "rgba(255,255,255,0.7)", borderColor: overlayOn ? "rgba(255,255,255,0.2)" : "rgba(255,255,255,0.12)" }}
            >
              {overlayOn ? "HUD ON" : "HUD OFF"}
            </button>

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