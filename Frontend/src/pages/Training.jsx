import { useState, useEffect, useRef, useCallback } from "react";
import {
  FlaskConical, RefreshCw, Download, ChevronDown, ChevronUp,
  Tag, Pencil, Trash2, Check, X, Play, Camera,
  Wind, Droplets, AlertTriangle, Activity, Cpu, Wifi, WifiOff, 
  Square, Eye, Hourglass, Sliders, Thermometer, Gauge
} from "lucide-react";

import { API_BASE as BASE } from "../api";
const FRAME_MS    = 200;   
const ANALYZE_MS  = 800;   

const LABEL_NAMES = { "-1": "Pending", 0: "No alcohol", 1: "Breath alcohol", 2: "Sanitizer" };
const SUB_NAMES   = { sanitizer: "Rubbing alcohol", perfume: "Perfume / cologne", drowsy: "Drowsy (Fake Sleep)", clear_air: "Clear Air (Empty)" };

// ── Toast ─────────────────────────────────────────────────────
function useToast() {
  const [toasts, setToasts] = useState([]);
  const add = useCallback((msg, type = "success") => {
    const id = Date.now();
    setToasts(p => [...p, { id, msg, type }]);
    setTimeout(() => setToasts(p => p.filter(t => t.id !== id)), 3500);
  }, []);
  return { toasts, toast: add };
}

function Toasts({ toasts }) {
  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => (
        <div key={t.id} className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium border pointer-events-auto"
          style={{
            background:  `color-mix(in srgb, ${t.type === "success" ? "var(--pass)" : "var(--over)"} 10%, transparent)`,
            color:       t.type === "success" ? "var(--pass)" : "var(--over)",
            borderColor: `color-mix(in srgb, ${t.type === "success" ? "var(--pass)" : "var(--over)"} 30%, transparent)`,
          }}>
          {t.type === "success" ? <Check size={14}/> : <AlertTriangle size={14}/>}
          {t.msg}
        </div>
      ))}
    </div>
  );
}

function ProgressBar({ value, target = 50, color = "var(--pass)" }) {
  const pct = Math.min(Math.round((value / target) * 100), 100);
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-active)" }}>
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: color }}/>
      </div>
      <span className="text-xs w-14 text-right tabular-nums" style={{ color: "var(--text-muted)" }}>
        {value} / {target}
      </span>
    </div>
  );
}

function LabelBadge({ label, subLabel }) {
  const styles = {
    "-1": { bg: "var(--near)", text: "var(--near)" },
    0:    { bg: "var(--pass)", text: "var(--pass)" },
    1:    { bg: "var(--over)", text: "var(--over)" },
    2:    { bg: "var(--text-secondary)", text: "var(--text-secondary)" },
  };
  const s = styles[label] ?? styles["-1"];
  return (
    <div className="flex items-center gap-1 flex-wrap">
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border"
        style={{
          background:  `color-mix(in srgb, ${s.bg} 12%, transparent)`,
          color:       s.text,
          borderColor: `color-mix(in srgb, ${s.bg} 30%, transparent)`,
        }}>
        {LABEL_NAMES[label] ?? "Unknown"}
      </span>
      {subLabel && (
        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-medium"
          style={{ background: "var(--bg-active)", color: "var(--text-muted)" }}>
          {SUB_NAMES[subLabel] ?? subLabel}
        </span>
      )}
    </div>
  );
}

function Modal({ open, onClose, title, icon: Icon, iconColor, children }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.5)" }}
      onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-sm rounded-xl border p-5"
        style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2 font-medium text-sm" style={{ color: "var(--text-primary)" }}>
            {Icon && <Icon size={16} style={{ color: iconColor }}/>}
            {title}
          </div>
          <button onClick={onClose} className="p-1 rounded" style={{ color: "var(--text-muted)" }}>
            <X size={14}/>
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function StatCard({ label, value, color = "var(--text-primary)" }) {
  return (
    <div className="rounded-lg p-3.5" style={{ background: "var(--bg-active)" }}>
      <div className="text-xs mb-1.5 uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>{label}</div>
      <div className="text-2xl font-medium tabular-nums" style={{ color }}>{value ?? "—"}</div>
    </div>
  );
}

function EventCard({ active, color, icon: Icon, label, hint, badge, onClick }) {
  return (
    <button onClick={onClick}
      className="flex items-start gap-3 p-3 rounded-lg border text-left transition-all w-full"
      style={{
        borderColor: active ? color : "var(--border-subtle)",
        background:  active ? `color-mix(in srgb, ${color} 8%, transparent)` : "transparent",
      }}>
      <div className="mt-0.5 p-1.5 rounded-md shrink-0"
        style={{
          background: active ? `color-mix(in srgb, ${color} 15%, transparent)` : "var(--bg-active)",
          color:      active ? color : "var(--text-muted)",
        }}>
        <Icon size={14}/>
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium" style={{ color: active ? color : "var(--text-primary)" }}>{label}</span>
          <span className="text-[11px] px-1.5 py-0.5 rounded font-medium"
            style={{ background: "var(--bg-active)", color: "var(--text-muted)" }}>{badge}</span>
        </div>
        <div className="text-xs mt-0.5 leading-relaxed" style={{ color: "var(--text-muted)" }}>{hint}</div>
      </div>
      {active && <Check size={13} style={{ color, marginTop: 3, flexShrink: 0 }}/>}
    </button>
  );
}

// ── Sensor Pattern Visualizer Component ───────────────────────
function SensorPatternVisualizer({ row }) {
  if (!row || row.mq3_1_max == null) {
    return (
      <div className="text-center py-4 text-xs italic" style={{ color: "var(--near)" }}>
        ⏳ Waiting for ESP32 hardware package execution to compile metrics...
      </div>
    );
  }

  const s1 = row.mq3_1_max;
  const s2 = row.mq3_2_max;
  const s3 = row.mq3_3_max;
  const variance = row.spatial_variance ?? 0;
  const rise = row.rise_time ?? 0;
  const decay = row.decay_time ?? 0;

  const maxSensorVal = Math.max(s1, s2, s3, 1);
  const getPct = (val) => Math.min(Math.round((val / maxSensorVal) * 100), 100);

  let varianceAnalysis = "Uniform Distribution (Likely Breath Source)";
  let varianceColor = "var(--pass)";
  if (variance > 150) {
    varianceAnalysis = "Extreme Spatial Deviation (Classic Localized Sanitizer Spike)";
    varianceColor = "var(--over)";
  } else if (variance > 60) {
    varianceAnalysis = "Moderate Variance (Possible Fragrance/Perfume Mist)";
    varianceColor = "var(--near)";
  }

  return (
    <div className="space-y-4 p-4 rounded-xl border mt-2 text-left" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
      <div className="flex items-center gap-2 text-xs font-semibold tracking-wide uppercase" style={{ color: "var(--text-muted)" }}>
        <Sliders size={12} style={{ color: "var(--pass)" }}/>
        ML Feature Array Engine Diagnosis
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Topology Heatmap */}
        <div className="p-3 rounded-lg border flex flex-col justify-between" style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)" }}>
          <div>
            <span className="text-[11px] uppercase font-medium" style={{ color: "var(--text-muted)" }}>Spatial Array Topology</span>
            <p className="text-xs font-semibold mt-0.5 mb-3" style={{ color: "var(--text-primary)" }}>Chamber Intake Load</p>
          </div>
          <div className="flex justify-around items-end gap-2 h-16 px-2">
            {[
              { id: "MQ3 #1", val: s1, pct: getPct(s1) },
              { id: "MQ3 #2", val: s2, pct: getPct(s2) },
              { id: "MQ3 #3", val: s3, pct: getPct(s3) },
            ].map((sensor) => (
              <div key={sensor.id} className="flex flex-col items-center flex-1 group relative">
                <span className="text-[11px] mb-1 font-mono" style={{ color: "var(--text-secondary)" }}>{Math.round(sensor.val)}</span>
                <div className="w-full rounded-t" 
                     style={{ 
                       height: `${sensor.pct}%`, 
                       minHeight: "4px",
                       background: `linear-gradient(to top, var(--bg-active), ${sensor.pct > 70 ? "var(--over)" : "var(--pass)"})`
                     }} 
                />
                <span className="text-[11px] mt-1 text-center scale-90" style={{ color: "var(--text-muted)" }}>{sensor.id}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Deviation Meter */}
        <div className="p-3 rounded-lg border flex flex-col justify-between" style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)" }}>
          <div>
            <span className="text-[11px] uppercase font-medium" style={{ color: "var(--text-muted)" }}>Mathematical Deviation</span>
            <p className="text-xs font-semibold mt-0.5 mb-1" style={{ color: "var(--text-primary)" }}>Spatial Variance Coefficient</p>
          </div>
          <div className="my-2">
            <div className="flex justify-between items-baseline mb-1">
              <span className="text-xl font-bold tabular-nums" style={{ color: varianceColor }}>{variance.toFixed(2)}</span>
              <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>σ index</span>
            </div>
            <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg-card)" }}>
              <div className="h-full rounded-full transition-all duration-500" 
                   style={{ width: `${Math.min((variance / 220) * 100, 100)}%`, background: varianceColor }}/>
            </div>
          </div>
          <p className="text-[11px] font-medium leading-normal" style={{ color: "var(--text-muted)" }}>
            Verdict: <span style={{ color: varianceColor }}>{varianceAnalysis}</span>
          </p>
        </div>

        {/* Waveform Kinetics */}
        <div className="p-3 rounded-lg border flex flex-col justify-between" style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)" }}>
          <div>
            <span className="text-[11px] uppercase font-medium" style={{ color: "var(--text-muted)" }}>Waveform Kinetics</span>
            <p className="text-xs font-semibold mt-0.5 mb-2" style={{ color: "var(--text-primary)" }}>Intake Time Coefficients</p>
          </div>
          <div className="space-y-2.5">
            <div>
              <div className="flex justify-between text-[11px] mb-0.5" style={{ color: "var(--text-secondary)" }}>
                <span>Rise Profile (Slope Velocity)</span>
                <span className="font-mono">{rise.toFixed(1)} samples</span>
              </div>
              <div className="w-full h-1 rounded-full overflow-hidden" style={{ background: "var(--bg-card)" }}>
                <div className="h-full bg-amber-400" style={{ width: `${Math.min((rise / 30) * 100, 100)}%` }} />
              </div>
            </div>
            <div>
              <div className="flex justify-between text-[11px] mb-0.5" style={{ color: "var(--text-secondary)" }}>
                <span>Decay Profile (Desorption Rate)</span>
                <span className="font-mono">{decay.toFixed(1)} samples</span>
              </div>
              <div className="w-full h-1 rounded-full overflow-hidden" style={{ background: "var(--bg-card)" }}>
                <div className="h-full bg-indigo-400" style={{ width: `${Math.min((decay / 30) * 100, 100)}%` }} />
              </div>
            </div>
          </div>
          <div className="text-[11px] italic mt-1" style={{ color: "var(--text-muted)" }}>
            *Sanitizers show steep rise / slow decay configurations.
          </div>
        </div>
      </div>
      {/* Atmospheric Compensation Status Footer */}
      <div className="flex items-center gap-4 pt-2 border-t text-[11px]" style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>
        <div className="flex items-center gap-1">
          <Thermometer size={12} className="text-orange-400"/>
          Ambient Temp: <span className="font-semibold text-mono" style={{ color: "var(--text-primary)" }}>{row.temperature ? `${row.temperature.toFixed(1)}°C` : "—"}</span>
        </div>
        <div className="flex items-center gap-1">
          <Droplets size={12} className="text-blue-400"/>
          Intake Humidity: <span className="font-semibold text-mono" style={{ color: "var(--text-primary)" }}>{row.humidity ? `${row.humidity.toFixed(1)}%` : "—"}</span>
        </div>
        <div className="flex items-center gap-1 ml-auto">
          <Gauge size={12} className="text-emerald-400 animate-pulse"/>
          <span className="font-medium font-mono" style={{ color: "var(--pass)" }}>Dynamic Baseline Compensation Calibrated</span>
        </div>
      </div>
    </div>
  );
}

// ═════════════════════════════════════════════════════════════
export default function Training() {
  const { toasts, toast } = useToast();

  const [summary, setSummary]             = useState(null);
  const [rows, setRows]                   = useState([]);
  const [filter, setFilter]               = useState("all");
  const [expandedId, setExpandedId]       = useState(null);
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [loadingData, setLoadingData]     = useState(false);
  const [training, setTraining]           = useState(false);
  
  // ── FIX: STATE TUNNEL ──
  const [activeEvent, setActiveEvent] = useState("sober");
  const activeEventRef = useRef(activeEvent);
  
  useEffect(() => {
    activeEventRef.current = activeEvent;
  }, [activeEvent]);

  const [esp32Online, setEsp32Online]     = useState(null);

  // ── Camera state ──────────────────────────────────────────
  const [frameUrl, setFrameUrl]   = useState(null);
  const [analysis, setAnalysis]   = useState(null);
  const [camError, setCamError]   = useState(false);
  const [streaming, setStreaming] = useState(false);

  const frameTimeoutRef     = useRef(null);
  const analyzeTimeoutRef   = useRef(null);
  const frameErrCountRef    = useRef(0);
  const latestBlobRef       = useRef(null);
  const capturedRef         = useRef(false);
  const loopActiveRef       = useRef(false);

  const collectingRef = useRef(false);
  const [collecting, setCollecting] = useState(false);
  const setCollectingSync = (val) => {
    collectingRef.current = val;
    setCollecting(val);
  };

  const [countdown, setCountdown]       = useState(0);
  const [hardwareState, setHardwareState] = useState("idle");

  const [bacModal, setBacModal]           = useState(null);
  const [bacValue, setBacValue]           = useState("");
  const [labelModal, setLabelModal]       = useState(null);
  const [labelBac, setLabelBac]           = useState("");
  const [labelSanitizer, setLabelSanitizer] = useState(false);
  const [relabelModal, setRelabelModal]   = useState(null);
  const [relabelValue, setRelabelValue]   = useState("0");
  const [relabelSubLabel, setRelabelSubLabel] = useState("");
  const [relabelReason, setRelabelReason] = useState("");

  const isCameraEvent = ["alcohol", "perfume", "sober", "drowsy", "yawning"].includes(activeEvent);

  // ── Data ──────────────────────────────────────────────────
  const loadSummary = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/training/summary`);
      if (r.ok) setSummary(await r.json());
    } catch { toast("Could not reach backend", "error"); }
  }, [toast]);

  const loadData = useCallback(async () => {
    setLoadingData(true);
    try {
      const r = await fetch(`${BASE}/training/data`);
      if (r.ok) setRows(await r.json());
    } catch { toast("Failed to load data", "error"); }
    finally { setLoadingData(false); }
  }, [toast]);

  const loadAll = useCallback(() => { loadSummary(); loadData(); }, [loadSummary, loadData]);
  useEffect(() => { loadAll(); }, [loadAll]);

  // ── ESP32 ─────────────────────────────────────────────────
  const checkEsp32 = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/sensor/status`);
      if (r.ok) { const d = await r.json(); setEsp32Online(d.online); }
    } catch { setEsp32Online(false); }
  }, []);

  useEffect(() => {
    checkEsp32();
    const t = setInterval(checkEsp32, 10000);
    return () => clearInterval(t);
  }, [checkEsp32]);

  // ── Countdown Timer Ticker ────────────────────────────────
  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setTimeout(() => setCountdown(c => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown]);

  // ── Safe Non-Overlapping Loops ────────────────────────────
  const fetchFrameLoop = async () => {
    if (!loopActiveRef.current) return;
    try {
      const res = await fetch(`${BASE}/camera/stream/frame?overlay=0`, { cache: "no-store" });
      if (!res.ok) {
        frameErrCountRef.current++;
        if (frameErrCountRef.current >= 5) setCamError(true);
      } else {
        frameErrCountRef.current = 0;
        setCamError(false);
        const blob = await res.blob();
        latestBlobRef.current = blob;
        const url = URL.createObjectURL(blob);
        setFrameUrl(prev => { if (prev) URL.revokeObjectURL(prev); return url; });
      }
    } catch {
      frameErrCountRef.current++;
      if (frameErrCountRef.current >= 5) setCamError(true);
    }
    frameTimeoutRef.current = setTimeout(fetchFrameLoop, FRAME_MS);
  };

  const runAnalysisLoop = async () => {
    if (!loopActiveRef.current) return;
    if (latestBlobRef.current) {
      try {
        const fd = new FormData();
        fd.append("file", latestBlobRef.current, "frame.jpg");

        const ar = await fetch(`${BASE}/camera/analyze`, { method: "POST", body: fd });
        if (ar.ok) {
          const data = await ar.json();
          setAnalysis(data);

          if (data.is_close && !capturedRef.current && collectingRef.current) {
            capturedRef.current = true;
            toast("Subject detected! Capturing in 2 seconds...", "success");
            
            setTimeout(async () => {
              await handleCameraCapture(latestBlobRef.current);
            }, 2000);
          }
          if (!data.is_close) capturedRef.current = false;
        }
      } catch (e) {
        // Suppress analysis network failures gracefully
      }
    }
    analyzeTimeoutRef.current = setTimeout(runAnalysisLoop, ANALYZE_MS);
  };

  const startStream = useCallback(() => {
    if (loopActiveRef.current) return;
    loopActiveRef.current = true;
    setStreaming(true);
    fetchFrameLoop();
    runAnalysisLoop();
  }, []);

  const stopStream = useCallback(() => {
    loopActiveRef.current = false;
    clearTimeout(frameTimeoutRef.current);
    clearTimeout(analyzeTimeoutRef.current);
    frameTimeoutRef.current   = null;
    analyzeTimeoutRef.current = null;
    latestBlobRef.current      = null;
    capturedRef.current        = false;
    setStreaming(false);
    setAnalysis(null);
    setFrameUrl(null);
  }, []);

  useEffect(() => {
    if (isCameraEvent) startStream();
    else stopStream();
    return () => stopStream();
  }, [activeEvent, isCameraEvent, startStream, stopStream]);

  // ── FIX: CRASH-PROOF DATA PIPELINE ────────────────────────────
  const handleCameraCapture = async (blob) => {
    setHardwareState("buffering");
    setCountdown(5);

    try {
      const fd = new FormData();
      fd.append("file", blob, "frame.jpg");
      
      const liveEvent = activeEventRef.current;

      if (liveEvent === "alcohol") {
        fd.append("bac", "0");
        const r = await fetch(`${BASE}/training/collect/alcohol`, { method: "POST", body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail);
        setBacModal({ id: d.id });
        setBacValue("");
        toast(`Face layout captured! Row #${d.id} waiting for gas matrix.`);
      } else if (liveEvent === "perfume") {
        const r = await fetch(`${BASE}/training/collect/perfume`, { method: "POST", body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail);
        toast(`Perfume exposure logged — Row #${d.id}. Stream active.`);
      } else if (liveEvent === "sober") {
        const r = await fetch(`${BASE}/training/collect/sober`, { method: "POST", body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail);
        toast(`Sober baseline logged — Row #${d.id}. Stream active.`);
      } else if (liveEvent === "drowsy") {
        const r = await fetch(`${BASE}/training/collect/drowsy`, { method: "POST", body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail);
        toast(`Drowsy baseline logged — Row #${d.id}. Stream active.`);
      } else if (liveEvent === "yawning") {
        const r = await fetch(`${BASE}/training/collect/yawning`, { method: "POST", body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail);
        toast(`Yawning baseline logged — Row #${d.id}. Stream active.`);
      }
      
      setTimeout(() => {
        setHardwareState("completed");
        loadAll();
        setTimeout(() => setHardwareState("idle"), 2000);
      }, 5000);

    } catch (e) {
      toast(e.message || "Capture verification error", "error");
      setHardwareState("idle");
    } finally {
      setCollectingSync(false);
    }
  };

  const handleManualCollect = async () => {
    setCollectingSync(true);
    setHardwareState("buffering");
    setCountdown(5);

    try {
      const liveEvent = activeEventRef.current;
      
      if (liveEvent === "clear_air") {
        const r = await fetch(`${BASE}/training/collect/clear_air`, { method: "POST" });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail);
        toast(`Clear air baseline logged — Row #${d.id}.`);
      } else if (liveEvent === "sanitizer") {
        const r = await fetch(`${BASE}/training/collect/sanitizer`, { method: "POST" });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail);
        toast(`Vapor reference logged — Row #${d.id}. Keep compound active.`);
      }

      setTimeout(() => {
        setHardwareState("completed");
        loadAll();
        setCollectingSync(false);
        setTimeout(() => setHardwareState("idle"), 2000);
      }, 5000);
    } catch (e) {
      toast("Collection execution drop", "error");
      setCollectingSync(false);
      setHardwareState("idle");
    }
  };

  const handleStartCameraCollect = () => {
    capturedRef.current = false;
    setCollectingSync(true);
    setHardwareState("idle");
    
    const msgs = {
      alcohol: "Proximity pipeline armed — stand in front of lens",
      perfume: "Chemical evaluation armed — execute walkthrough",
      sober: "Baseline armed — Keep eyes open",
      drowsy: "Baseline armed — Fake sleep (close eyes, EAR)",
      yawning: "Baseline armed — Mouth wide open (MAR yawning)",
    };
    
    toast(msgs[activeEventRef.current] || "Pipeline armed");
  };

  const handleStopCameraCollect = () => {
    setCollectingSync(false);
    capturedRef.current = false;
    setHardwareState("idle");
    setCountdown(0);
  };

  // ── BAC modal ─────────────────────────────────────────────
  const submitBac = async () => {
    const bac = parseFloat(bacValue);
    if (isNaN(bac) || bac < 0) { toast("Enter a valid BAC value", "error"); return; }
    try {
      const r = await fetch(`${BASE}/training/label/${bacModal.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bac, is_sanitizer: false }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      toast(`Row #${bacModal.id} → BAC ${bac} saved`);
      setBacModal(null);
      setTimeout(loadAll, 1000);
    } catch (e) { toast(e.message || "Failed", "error"); }
  };

  // ── Label modal ───────────────────────────────────────────
  const submitLabel = async () => {
    const bac = parseFloat(labelBac);
    if (isNaN(bac) || bac < 0) { toast("Enter a valid BAC value", "error"); return; }
    try {
      const r = await fetch(`${BASE}/training/label/${labelModal.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bac, is_sanitizer: labelSanitizer }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      toast(`Row #${labelModal.id} → "${d.label_name}"`);
      setLabelModal(null);
      loadAll();
    } catch (e) { toast(e.message || "Label failed", "error"); }
  };

  // ── Relabel modal ─────────────────────────────────────────
  const openRelabelModal = (id, currentLabel, currentSubLabel) => {
    setRelabelModal({ id, currentLabel });
    setRelabelValue(String(currentLabel >= 0 ? currentLabel : 0));
    setRelabelSubLabel(currentSubLabel ?? "");
    setRelabelReason("");
  };

  const submitRelabel = async () => {
    try {
      const body = { label: parseInt(relabelValue), reason: relabelReason || undefined };
      if (relabelValue === "2" && relabelSubLabel) body.sub_label = relabelSubLabel;
      const r = await fetch(`${BASE}/training/relabel/${relabelModal.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      toast(`Row #${relabelModal.id} → "${d.new_label}"`);
      setRelabelModal(null);
      loadAll();
    } catch (e) { toast(e.message || "Relabel failed", "error"); }
  };

  // ── Delete ────────────────────────────────────────────────
  const confirmDelete = async (id) => {
    try {
      const r = await fetch(`${BASE}/training/delete/${id}`, { method: "DELETE" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      toast(`Row #${id} deleted`);
      setDeleteConfirm(null);
      loadAll();
    } catch (e) { toast(e.message || "Delete failed", "error"); }
  };

  // ── Export ────────────────────────────────────────────────
  const exportCSV = async () => {
    try {
      const r = await fetch(`${BASE}/training/export`);
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail); }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "training_data.csv"; a.click();
      URL.revokeObjectURL(url);
      toast("CSV exported");
    } catch (e) { toast(e.message || "Export failed", "error"); }
  };

  // ── Train ─────────────────────────────────────────────────
  const triggerTraining = async () => {
    setTraining(true);
    try {
      const r = await fetch(`${BASE}/training/train`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      toast(`Training complete — ${d.samples} samples`);
      loadSummary();
    } catch (e) { toast(e.message || "Training failed", "error"); }
    finally { setTraining(false); }
  };

  // ── Derived Mappings ──────────────────────────────────────
  const filtered = filter === "all" ? rows : rows.filter(r => String(r.label) === filter);
  const fmt = (v, d = 0) => v != null ? (d === 0 ? Math.round(v) : Number(v).toFixed(d)) : "—";
  const isClose = analysis?.is_close ?? false;
  const proximityStr = analysis?.proximity ?? "far";

  const proximityColorMap = {
    close: "var(--over)",
    medium: "var(--near)",
    far: "var(--text-muted)"
  };

  const hardwareStateConfig = {
    idle: { label: "System Ready", color: "var(--text-muted)", icon: Cpu },
    triggering: { label: "Triggering ESP32...", color: "var(--near)", icon: RefreshCw },
    buffering: { label: `ESP32 Sampling Pipeline Active (${countdown}s)`, color: "var(--over)", icon: Hourglass },
    completed: { label: "Matrix Synchronized Successfully! ✓", color: "var(--pass)", icon: Check }
  };

  const currentHw = hardwareStateConfig[hardwareState] || hardwareStateConfig.idle;
  const HwIcon = currentHw.icon;

  const EVENT_CONFIG = {
    clear_air: { label: "Clear Air (Empty)", hint: "Manual trigger. Records clean room environment.", color: "#3b82f6", icon: Wind, badge: "Manual", camera: false },
    sober: { label: "Sober Face", hint: "Camera auto-captures. Open eyes, mouth closed.", color: "var(--pass)", icon: Camera, badge: "Camera", camera: true },
    drowsy: { label: "Drowsy (EAR)", hint: "Camera auto-captures. Eyes closed (EAR).", color: "var(--near)", icon: Eye, badge: "Camera", camera: true },
    yawning: { label: "Yawning (MAR)", hint: "Camera auto-captures. Mouth open wide (MAR).", color: "#F59E0B", icon: Activity, badge: "Camera", camera: true },
    alcohol: { label: "Breath alcohol", hint: "Camera auto-captures. BAC required.", color: "var(--over)", icon: Wind, badge: "Camera + BAC", camera: true },
    sanitizer: { label: "Rubbing alcohol", hint: "Manual trigger. Spray near sensors.", color: "var(--text-secondary)", icon: Droplets, badge: "Manual", camera: false },
    perfume: { label: "Perfume / cologne", hint: "Camera auto-captures. Image discarded.", color: "var(--text-secondary)", icon: Camera, badge: "Camera", camera: true },
  };

  const cfg = EVENT_CONFIG[activeEvent];

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <Toasts toasts={toasts}/>

      {/* Header */}
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div className="flex items-center gap-2.5">
          <FlaskConical size={20} style={{ color: "var(--over)" }}/>
          <h1 className="text-lg font-medium" style={{ color: "var(--text-primary)" }}>Training pipeline</h1>
          <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs border ml-1"
            style={{
              borderColor: esp32Online ? "color-mix(in srgb, var(--pass) 30%, transparent)" : "var(--border-subtle)",
              color:       esp32Online ? "var(--pass)" : "var(--text-muted)",
              background:  esp32Online ? "color-mix(in srgb, var(--pass) 8%, transparent)" : "transparent",
            }}>
            {esp32Online ? <><Wifi size={10}/> ESP32 online</> : <><WifiOff size={10}/> ESP32 offline</>}
          </div>
        </div>
        <div className="flex gap-2">
          <button onClick={loadAll} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border"
            style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>
            <RefreshCw size={13}/> Refresh
          </button>
          <button onClick={exportCSV} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border"
            style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>
            <Download size={13}/> Export CSV
          </button>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2.5 mb-5">
        <StatCard label="Total"      value={summary?.total}           color="var(--text-primary)"/>
        <StatCard label="Pending"    value={summary?.pending}         color="var(--near)"/>
        <StatCard label="No alcohol" value={summary?.no_alcohol}      color="var(--pass)"/>
        <StatCard label="Breath"     value={summary?.breath_alcohol}  color="var(--over)"/>
        <StatCard label="Sanitizer"  value={summary?.sanitizer}       color="var(--text-secondary)"/>
        <StatCard label="Ready"      value={summary?.ready_to_train}  color="var(--text-primary)"/>
      </div>

      {/* Progress + Collection */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-5">

        {/* Progress Panel */}
        <div className="rounded-xl border p-4 flex flex-col gap-4"
          style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          <div>
            <div className="text-xs font-medium uppercase tracking-wide mb-3" style={{ color: "var(--text-muted)" }}>
              Sensor class balance
            </div>
            <div className="space-y-3">
              {[
                { label: "No alcohol",     val: summary?.no_alcohol,    color: "var(--pass)" },
                { label: "Breath alcohol", val: summary?.breath_alcohol, color: "var(--over)" },
                { label: "Sanitizer",      val: summary?.sanitizer,     color: "var(--text-secondary)" },
              ].map(({ label, val, color }) => (
                <div key={label}>
                  <div className="text-xs mb-1.5" style={{ color: "var(--text-secondary)" }}>{label}</div>
                  <ProgressBar value={val ?? 0} color={color}/>
                </div>
              ))}
            </div>
            {summary?.sanitizer_breakdown && (
              <div className="mt-3 pt-3 border-t flex gap-3" style={{ borderColor: "var(--border-subtle)" }}>
                <div className="flex-1 text-center rounded-lg p-2" style={{ background: "var(--bg-active)" }}>
                  <div className="text-[11px] mb-0.5" style={{ color: "var(--text-muted)" }}>Rubbing alcohol</div>
                  <div className="text-sm font-medium tabular-nums" style={{ color: "var(--text-primary)" }}>
                    {summary.sanitizer_breakdown.rubbing_alcohol}
                  </div>
                </div>
                <div className="flex-1 text-center rounded-lg p-2" style={{ background: "var(--bg-active)" }}>
                  <div className="text-[11px] mb-0.5" style={{ color: "var(--text-muted)" }}>Perfume</div>
                  <div className="text-sm font-medium tabular-nums" style={{ color: "var(--text-primary)" }}>
                    {summary.sanitizer_breakdown.perfume}
                  </div>
                </div>
              </div>
            )}
          </div>
          <div className="border-t pt-4" style={{ borderColor: "var(--border-subtle)" }}>
            <div className="text-xs font-medium uppercase tracking-wide mb-3" style={{ color: "var(--text-muted)" }}>
              Face images — MobileNet
            </div>
            <div className="space-y-3">
              {[
                { label: "Sober",    val: summary?.face_images?.sober,    color: "var(--pass)" },
                { label: "Drowsy",   val: summary?.face_images?.drowsy,   color: "var(--near)" },
                { label: "Impaired", val: summary?.face_images?.impaired, color: "var(--over)" },
              ].map(({ label, val, color }) => (
                <div key={label}>
                  <div className="text-xs mb-1.5" style={{ color: "var(--text-secondary)" }}>{label}</div>
                  <ProgressBar value={val ?? 0} color={color}/>
                </div>
              ))}
            </div>
          </div>
          {summary && (
            <div className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border self-start"
              style={{
                background:  summary.balanced ? "color-mix(in srgb, var(--pass) 10%, transparent)" : "color-mix(in srgb, var(--near) 10%, transparent)",
                color:       summary.balanced ? "var(--pass)" : "var(--near)",
                borderColor: summary.balanced ? "color-mix(in srgb, var(--pass) 30%, transparent)" : "color-mix(in srgb, var(--near) 30%, transparent)",
              }}>
              {summary.balanced
                ? <><Check size={11}/> Balanced — ready to train</>
                : <><AlertTriangle size={11}/> Unbalanced — collect more data</>}
            </div>
          )}
        </div>

        {/* Collection & Visualization Control Panel */}
        <div className="rounded-xl border p-4 flex flex-col gap-3"
          style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
          
          <div className="text-xs font-medium uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
            Collect event
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-2">
            {Object.entries(EVENT_CONFIG).map(([key, c]) => (
              <EventCard key={key} active={activeEvent === key}
                color={c.color} icon={c.icon} label={c.label} hint={c.hint} badge={c.badge}
                onClick={() => {
                  setActiveEvent(key);
                  setCollectingSync(false);
                  capturedRef.current = false;
                  setHardwareState("idle");
                  setCountdown(0);
                }}
              />
            ))}
          </div>

          {/* ── REAL-TIME TRANSACTION METRIC PIPELINE VISUALIZER ── */}
          <div className="grid grid-cols-2 gap-2 p-2 rounded-lg border text-xs" 
               style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)" }}>
            
            <div className="flex flex-col gap-1">
              <span style={{ color: "var(--text-muted)" }} className="text-[11px] uppercase font-medium">Tracking Proximity</span>
              <div className="flex items-center gap-1.5 font-semibold text-sm capitalize"
                   style={{ color: isCameraEvent ? proximityColorMap[proximityStr] : "var(--text-muted)" }}>
                <Eye size={14}/>
                {isCameraEvent ? proximityStr : "Not Active"}
              </div>
            </div>

            <div className="flex flex-col gap-1 border-l pl-3.5" style={{ borderColor: "var(--border-subtle)" }}>
              <span style={{ color: "var(--text-muted)" }} className="text-[11px] uppercase font-medium">Hardware Bus State</span>
              <div className="flex items-center gap-1.5 font-semibold text-xs"
                   style={{ color: currentHw.color }}>
                <HwIcon size={14} className={hardwareState === "triggering" || hardwareState === "buffering" ? "animate-spin" : ""}/>
                <span className="truncate">{currentHw.label}</span>
              </div>
            </div>

          </div>          
          
          {/* Camera feed */}
          {isCameraEvent && (
            <div className="relative rounded-lg overflow-hidden border" style={{ borderColor: "var(--border-subtle)", background: "#0c1f14", aspectRatio: "16/9" }}>
              {frameUrl && !camError ? (
                <>
                  <img src={frameUrl} alt="C200C live" className="w-full h-full object-cover"/>
                  {analysis && (
                    <div className="absolute top-2 left-2 flex flex-col gap-1">
                      <div className="text-[11px] font-medium px-2 py-0.5 rounded-full"
                        style={{ background: "rgba(0,0,0,0.6)", color: "#34d399" }}>
                        EAR {analysis.ear?.toFixed(3)} · {analysis.status?.toUpperCase()}
                      </div>
                      {analysis.identified && (
                        <div className="text-[11px] font-medium px-2 py-0.5 rounded-full"
                          style={{ background: "rgba(0,0,0,0.6)", color: "var(--pass)" }}>
                          ✓ {analysis.name}
                        </div>
                      )}
                    </div>
                  )}
                  {isClose && collecting && (
                    <div className="absolute bottom-2 right-2 text-[11px] font-medium px-2 py-0.5 rounded-full animate-pulse"
                      style={{ background: "rgba(220,38,38,0.85)", color: "#fff" }}>
                      ⚠ CLOSE — capturing in 2s
                    </div>
                  )}
                  {collecting && !isClose && (
                    <div className="absolute bottom-2 left-2 text-[11px] font-medium px-2 py-0.5 rounded-full"
                      style={{ background: "rgba(0,0,0,0.6)", color: "#fbbf24" }}>
                      Waiting for subject...
                    </div>
                  )}
                </>
              ) : (
                <div className="w-full h-full flex items-center justify-center">
                  <p className="text-white/30 text-xs">
                    {camError ? "Camera unavailable — check RTSP_URL" : "Connecting to C200C..."}
                  </p>
                </div>
              )}
              <div className="absolute top-2 right-2 flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium"
                style={{ background: "rgba(0,0,0,0.6)", color: streaming && !camError ? "#34d399" : "#ef4444" }}>
                <span className={`w-1.5 h-1.5 rounded-full ${streaming && !camError ? "bg-emerald-400 animate-pulse" : "bg-red-500"}`}/>
                {streaming && !camError ? "Live · CAM-01" : "Offline"}
              </div>
            </div>
          )}

          <div className="mt-auto">
            {isCameraEvent ? (
              <button
                onClick={collecting ? handleStopCameraCollect : handleStartCameraCollect}
                className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium animate-none"
                style={{
                  background: collecting ? "color-mix(in srgb, var(--over) 12%, transparent)" : cfg.color,
                  color:  collecting ? "var(--over)" : "#fff",
                  border: collecting ? "1px solid color-mix(in srgb, var(--over) 30%, transparent)" : "none",
                }}>
                {collecting ? <><Square size={13}/> Stop monitoring</> : <><Play size={13}/> Arm Pipeline — {cfg.label}</>}
              </button>
            ) : (
              <button onClick={handleManualCollect} disabled={collecting || hardwareState !== "idle"}
                className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium disabled:opacity-50"
                style={{ background: cfg.color, color: "#fff" }}>
                {collecting
                  ? <><RefreshCw size={13} className="animate-spin"/> Triggering sensor bus...</>
                  : <><Play size={13}/> Manual Trigger — {cfg.label}</>}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Train panel */}
      <div className="flex items-center justify-between rounded-xl border p-4 mb-5 flex-wrap gap-3"
        style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
        <div>
          <div className="flex items-center gap-2 text-sm font-medium mb-1" style={{ color: "var(--text-primary)" }}>
            <Cpu size={15}/> Train sensor models
          </div>
          <div className="text-xs" style={{ color: "var(--text-muted)" }}>
            Requires 20+ samples per class with complete sensor features (60 total minimum). Trains RF + XGBoost.
          </div>
        </div>
        <button onClick={triggerTraining} disabled={training}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
          style={{ background: "var(--text-primary)", color: "var(--bg-card)" }}>
          {training ? <><RefreshCw size={13} className="animate-spin"/> Training...</> : <><Play size={13}/> Run training</>}
        </button>
      </div>

      {/* ── RESTORED CLEAN LOGS TABLE ── */}
      <div className="rounded-xl border overflow-hidden" style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
        <div className="flex items-center justify-between px-4 py-3 border-b flex-wrap gap-2" style={{ borderColor: "var(--border-subtle)" }}>
          <div className="flex items-center gap-2 text-sm font-medium" style={{ color: "var(--text-primary)" }}>
            Data table
            <span className="text-xs font-normal" style={{ color: "var(--text-muted)" }}>({filtered.length})</span>
          </div>
          <div className="flex gap-1.5 flex-wrap">
            {[
              { f: "all", label: "All" }, { f: "-1", label: "Pending" },
              { f: "0", label: "Sober" }, { f: "1", label: "Alcohol" }, { f: "2", label: "Sanitizer" },
            ].map(({ f, label }) => (
              <button key={f} onClick={() => setFilter(f)}
                className="px-2.5 py-1 rounded-md text-xs border"
                style={{
                  borderColor: filter === f ? "var(--text-secondary)" : "var(--border-subtle)",
                  color:       filter === f ? "var(--text-primary)"   : "var(--text-muted)",
                  background:  filter === f ? "var(--bg-active)"      : "transparent",
                  fontWeight:  filter === f ? 500 : 400,
                }}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm" style={{ tableLayout: "fixed", minWidth: 780 }}>
            <thead>
              <tr style={{ borderBottom: "0.5px solid var(--border-subtle)" }}>
                {[
                  { h: "ID", w: 52 }, { h: "Date", w: 120 }, { h: "Label", w: 160 },
                  { h: "BAC", w: 55 }, { h: "MQ3-1↑", w: 62 }, { h: "MQ3-2↑", w: 62 },
                  { h: "MQ3-3↑", w: 62 }, { h: "SpVar", w: 60 }, { h: "°C", w: 50 },
                  { h: "%RH", w: 50 }, { h: "Actions", w: 100 },
                ].map(({ h, w }) => (
                  <th key={h} className="px-3 py-2.5 text-left text-xs font-medium uppercase tracking-wide"
                    style={{ color: "var(--text-muted)", width: w }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loadingData ? (
                <tr><td colSpan={11} className="text-center py-10 text-xs" style={{ color: "var(--text-muted)" }}>Loading...</td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={11} className="text-center py-10 text-xs" style={{ color: "var(--text-muted)" }}>No rows</td></tr>
              ) : (
                filtered.flatMap(row => {
                  const isExpanded  = expandedId === row.id;
                  const isPending   = row.label === -1;
                  const isDel       = deleteConfirm === row.id;
                  const hasFeatures = row.mq3_1_max != null;
                  const date = row.date
                    ? new Date(row.date).toLocaleDateString("en-PH", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
                    : "—";
                  return [
                    <tr key={row.id} id={`row-${row.id}`} onClick={() => setExpandedId(isExpanded ? null : row.id)}
                      className="cursor-pointer transition-colors"
                      style={{ borderBottom: "0.5px solid var(--border-subtle)" }}
                      onMouseEnter={e => e.currentTarget.style.background = "var(--bg-active)"}
                      onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                      <td className="px-3 py-2.5 text-xs font-medium" style={{ color: "var(--text-primary)" }}>
                        #{row.id}
                        {row.label >= 0 && !hasFeatures && (
                          <span className="ml-1 w-1.5 h-1.5 rounded-full bg-yellow-400 inline-block animate-pulse" title="Waiting for ESP32"/>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-xs" style={{ color: "var(--text-secondary)" }}>{date}</td>
                      <td className="px-3 py-2.5"><LabelBadge label={row.label} subLabel={row.sub_label}/></td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-primary)" }}>
                        {row.bac != null ? Number(row.bac).toFixed(2) : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.mq3_1_max)}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.mq3_2_max)}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.mq3_3_max)}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.spatial_variance, 1)}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.temperature, 1)}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.humidity, 1)}</td>
                      <td className="px-3 py-2.5" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center gap-1">
                          {isPending ? (
                            <button title="Attach label"
                              onClick={() => { setLabelModal({ id: row.id }); setLabelBac(""); setLabelSanitizer(false); }}
                              className="p-1.5 rounded border" style={{ borderColor: "var(--near)", color: "var(--near)" }}>
                              <Tag size={11}/>
                            </button>
                          ) : (
                            <button title="Relabel" onClick={() => openRelabelModal(row.id, row.label, row.sub_label)}
                              className="p-1.5 rounded border" style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>
                              <Pencil size={11}/>
                            </button>
                          )}
                          {isDel ? (
                            <>
                              <button onClick={() => confirmDelete(row.id)} className="p-1.5 rounded border"
                                style={{ borderColor: "var(--pass)", color: "var(--pass)" }}><Check size={11}/></button>
                              <button onClick={() => setDeleteConfirm(null)} className="p-1.5 rounded border"
                                style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)" }}><X size={11}/></button>
                            </>
                          ) : (
                            <button onClick={() => setDeleteConfirm(row.id)} className="p-1.5 rounded border"
                              style={{ borderColor: "var(--border-subtle)", color: "var(--over)" }}><Trash2 size={11}/></button>
                          )}
                          <button className="p-1.5 rounded border" style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)" }}>
                            {isExpanded ? <ChevronUp size={11}/> : <ChevronDown size={11}/>}
                          </button>
                        </div>
                      </td>
                    </tr>,
                    isExpanded && (
                      <tr key={`exp-${row.id}`} style={{ borderBottom: "0.5px solid var(--border-subtle)" }}>
                        <td colSpan={11} className="px-4 py-3" style={{ background: "var(--bg-active)" }}>
                          <SensorPatternVisualizer row={row} />
                        </td>
                      </tr>
                    ),
                  ];
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* BAC modal */}
      <Modal open={!!bacModal} onClose={() => setBacModal(null)}
        title={`Enter BAC — Row #${bacModal?.id}`} icon={Tag} iconColor="var(--over)">
        <div className="space-y-3">
          <div className="text-xs px-3 py-2 rounded-lg"
            style={{ background: "color-mix(in srgb, var(--over) 8%, transparent)", color: "var(--over)" }}>
            Person detected. Read the breathalyzer now and enter the BAC.
          </div>
          <div>
            <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>BAC reading</label>
            <input type="number" step="0.01" min="0" placeholder="e.g. 0.05"
              value={bacValue} onChange={e => setBacValue(e.target.value)} autoFocus
              className="w-full rounded-lg px-3 py-2 text-sm border"
              style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)", color: "var(--text-primary)", outline: "none" }}/>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button onClick={() => setBacModal(null)} className="px-3 py-1.5 rounded-lg text-sm border"
              style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>Cancel</button>
            <button onClick={submitBac} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"
              style={{ background: "var(--text-primary)", color: "var(--bg-card)" }}>
              <Check size={13}/> Save BAC
            </button>
          </div>
        </div>
      </Modal>

      {/* Label modal */}
      <Modal open={!!labelModal} onClose={() => setLabelModal(null)}
        title={`Attach label — Row #${labelModal?.id}`} icon={Tag} iconColor="var(--near)">
        <div className="space-y-3">
          <div>
            <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>BAC reading (0.00 for sober)</label>
            <input type="number" step="0.01" min="0" placeholder="e.g. 0.00"
              value={labelBac} onChange={e => setLabelBac(e.target.value)} autoFocus
              className="w-full rounded-lg px-3 py-2 text-sm border"
              style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)", color: "var(--text-primary)", outline: "none" }}/>
          </div>
          <div className="flex items-center gap-2.5 rounded-lg px-3 py-2.5" style={{ background: "var(--bg-active)" }}>
            <input type="checkbox" id="lm-san" checked={labelSanitizer}
              onChange={e => setLabelSanitizer(e.target.checked)} className="w-4 h-4 cursor-pointer shrink-0"/>
            <label htmlFor="lm-san" className="text-sm cursor-pointer" style={{ color: "var(--text-secondary)" }}>
              Mark as sanitizer / perfume event
            </label>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button onClick={() => setLabelModal(null)} className="px-3 py-1.5 rounded-lg text-sm border"
              style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>Cancel</button>
            <button onClick={submitLabel} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"
              style={{ background: "var(--text-primary)", color: "var(--bg-card)" }}>
              <Check size={13}/> Apply label
            </button>
          </div>
        </div>
      </Modal>

      {/* Relabel modal */}
      <Modal open={!!relabelModal} onClose={() => setRelabelModal(null)}
        title={`Relabel — Row #${relabelModal?.id}`} icon={Pencil} iconColor="var(--text-secondary)">
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-xs px-3 py-2 rounded-lg" style={{ background: "var(--bg-active)" }}>
            Current: <LabelBadge label={relabelModal?.currentLabel}/>
          </div>
          <div>
            <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>New label</label>
            <select value={relabelValue} onChange={e => setRelabelValue(e.target.value)}
              className="w-full rounded-lg px-3 py-2 text-sm border"
              style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }}>
              <option value="0">0 — No alcohol</option>
              <option value="1">1 — Breath alcohol</option>
              <option value="2">2 — Sanitizer</option>
            </select>
          </div>
          {relabelValue === "2" && (
            <div>
              <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Sub-label (optional)</label>
              <select value={relabelSubLabel} onChange={e => setRelabelSubLabel(e.target.value)}
                className="w-full rounded-lg px-3 py-2 text-sm border"
                style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }}>
                <option value="">— not specified —</option>
                <option value="sanitizer">Rubbing alcohol / hand sanitizer</option>
                <option value="perfume">Perfume / cologne</option>
              </select>
            </div>
          )}
          <div>
            <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Reason (optional)</label>
            <textarea rows={2} value={relabelReason} onChange={e => setRelabelReason(e.target.value)}
              placeholder="e.g. misread breathalyzer..."
              className="w-full rounded-lg px-3 py-2 text-sm border resize-none"
              style={{ background: "var(--bg-active)", borderColor: "var(--border-subtle)", color: "var(--text-primary)" }}/>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button onClick={() => setRelabelModal(null)} className="px-3 py-1.5 rounded-lg text-sm border"
              style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}>Cancel</button>
            <button onClick={submitRelabel} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"
              style={{ background: "var(--text-primary)", color: "var(--bg-card)" }}>
              <Check size={13}/> Relabel
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}