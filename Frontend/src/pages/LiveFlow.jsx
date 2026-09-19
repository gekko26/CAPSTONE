// LiveFlow.jsx — ALCOGATE terminal GATE 01 — high density, real data
import { useState, useEffect, useRef, useCallback } from "react";
import { API_BASE } from "../api";

const FRAME_MS = 60;  // 15 FPS max for stream1 — max fps without affecting other functionalities
const ANALYZE_MS = 400; // decoupled analysis
const SENSOR_POLL = 1500;

function Step({ label, state }) {
  // state: done | active | idle
  const done = state === "done";
  const active = state === "active";
  return (
    <div className="flex items-center gap-1.5">
      <div
        className="w-3 h-3 rounded-full flex items-center justify-center shrink-0"
        style={{
          background: done ? "var(--pass-bg)" : active ? "var(--near-bg)" : "transparent",
          border: `1px solid ${done ? "var(--pass)" : active ? "var(--near)" : "var(--border-strong)"}`,
          color: done ? "var(--pass)" : active ? "var(--near)" : "var(--text-muted)",
        }}
      >
        {done ? <span className="text-[7px] leading-none">✓</span> : active ? <span className="w-1 h-1 rounded-full animate-pulse" style={{ background: "var(--near)" }} /> : null}
      </div>
      <span className="text-[10px] leading-none" style={{ color: done ? "var(--pass)" : active ? "var(--near)" : "var(--text-muted)" }}>{label}</span>
    </div>
  );
}

function levelLabel(v) {
  if (v == null) return "—";
  if (v >= 0.4) return "HIGH";
  if (v >= 0.2) return "MED";
  return "LOW";
}

export default function LiveFlow() {
  const [frameUrl, setFrameUrl] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [camError, setCamError] = useState(false);
  const [streaming, setStreaming] = useState(true);
  const [overlayOn, setOverlayOn] = useState(false);
  const overlayRef = useRef(false);
  useEffect(() => { overlayRef.current = overlayOn; }, [overlayOn]);
  const [gateEnabled, setGateEnabled] = useState(false); // default OFF — protects training_data from LiveFlow triggers
  const gateEnabledRef = useRef(false);
  useEffect(() => { gateEnabledRef.current = gateEnabled; }, [gateEnabled]);
  // MJPEG URL — single persistent connection, no per-frame fetch/GC (B)
  const mjpegUrl = `${API_BASE}/camera/stream/mjpeg?overlay=${overlayOn ? "1" : "0"}&fps=15`;
  const [mq3, setMq3] = useState([null, null, null]);
  const [dht, setDht] = useState({ t: null, h: null });
  const [recent, setRecent] = useState([]);
  const [currentId, setCurrentId] = useState(null);
  const [subjects, setSubjects] = useState([]);

  const frameTimeoutRef = useRef(null);
  const analyzeTimeoutRef = useRef(null);
  const latestBlobRef = useRef(null);
  const errRef = useRef(0);
  const activeRef = useRef(false);
  const triggeredRef = useRef(false);
  const fetchingRef = useRef(false);
  const frameUrlRef = useRef(null);
  frameUrlRef.current = frameUrl;

  // ── Self-scheduling recursion + single-flight guard (A) — camera-only, FRAME_MS unchanged
  const fetchFrameLoop = async () => {
    if (!activeRef.current) return;
    if (fetchingRef.current) { frameTimeoutRef.current = setTimeout(fetchFrameLoop, FRAME_MS); return; }
    fetchingRef.current = true;
    try {
      const overlayParam = overlayRef.current ? "1" : "0";
      const fr = await fetch(`${API_BASE}/camera/stream/frame?overlay=${overlayParam}`, { cache: "no-store" });
      if (!fr.ok) {
        errRef.current += 1;
        if (errRef.current >= 5) setCamError(true);
      } else {
        errRef.current = 0;
        setCamError(false);
        const blob = await fr.blob();
        latestBlobRef.current = blob;
        const url = URL.createObjectURL(blob);
        setFrameUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return url; });
      }
    } catch {
      errRef.current += 1;
      if (errRef.current >= 5) setCamError(true);
    } finally {
      fetchingRef.current = false;
      frameTimeoutRef.current = setTimeout(fetchFrameLoop, FRAME_MS);
    }
  };

  const runAnalyzeLoop = async () => {
    if (!activeRef.current) return;
    try {
      const fr = await fetch(`${API_BASE}/camera/stream/frame?overlay=0`, { cache: "no-store" });
      if (!fr.ok) throw new Error("frame");
      const blob = await fr.blob();
      latestBlobRef.current = blob;
      const fd = new FormData();
      fd.append("file", blob, "frame.jpg");
      const ar = await fetch(`${API_BASE}/camera/analyze`, { method: "POST", body: fd });
      if (ar.ok) {
        const d = await ar.json();
        setAnalysis(d);
        // gate OFF default — preview stays smooth but sensor trigger blocked to protect training_data
        if (d.is_close && gateEnabledRef.current) {
          if (!triggeredRef.current) { triggeredRef.current = true; fetch(`${API_BASE}/sensor/trigger`, { method: "POST" }).catch(() => {}); }
        } else if (!d.is_close) {
          triggeredRef.current = false;
        } else if (!gateEnabledRef.current) {
          // keep analysis live but never fire sensor when gate OFF
          if (triggeredRef.current) triggeredRef.current = false;
        }
      }
    } catch {}
    analyzeTimeoutRef.current = setTimeout(runAnalyzeLoop, ANALYZE_MS);
  };

  // LiveFlow uses MJPEG (B) — no fetchFrameLoop polling; analyze runs independently
  const start = useCallback(() => { if (activeRef.current) return; activeRef.current = true; setStreaming(true); runAnalyzeLoop(); }, []);
  const stop = useCallback(() => { activeRef.current = false; clearTimeout(analyzeTimeoutRef.current); setStreaming(false); }, []);

  // recent passages — real deployment_logs
  const loadRecent = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/deployment-logs?limit=5`);
      if (r.ok) {
        const j = await r.json();
        const logs = j.logs || [];
        setRecent(logs.slice(0, 5).reverse()); // oldest first for strip
        if (logs.length) setCurrentId(Math.max(...logs.map((l) => l.id)) + 1);
        else setCurrentId(184);
      }
    } catch {}
  }, []);

  const loadSensors = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/sensor/status`);
      if (r.ok) {
        const d = await r.json();
        // real sensor values only — no mock fallback
        if (Array.isArray(d.mq3) && d.mq3.some((v) => v != null)) setMq3(d.mq3.map((v) => (v != null ? Number(v) : null)));
        else if (d.mq3_1 != null) setMq3([d.mq3_1, d.mq3_2, d.mq3_3].map((v) => (v != null ? Number(v) : null)));
        else if (d.body && Array.isArray(d.body.mq3)) setMq3(d.body.mq3.map((v) => (v != null ? Number(v) : null)));
        else if (d.t != null || d.h != null) {
          // ignore mock DHT fallbacks
        } else {
          // keep previous or null — do not inject mock
        }
        const t = d.temperature ?? d.body?.temperature ?? d.t ?? d.body?.t ?? null;
        const h = d.humidity ?? d.body?.humidity ?? d.h ?? d.body?.h ?? null;
        if (t != null || h != null) setDht({ t: t != null ? Number(t) : null, h: h != null ? Number(h) : null });
      }
    } catch {}
  }, []);

  const loadSubjects = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/recognition/subjects`);
      if (r.ok) setSubjects(await r.json());
    } catch {}
  }, []);

  useEffect(() => {
    start();
    loadRecent();
    loadSensors();
    loadSubjects();
    const t1 = setInterval(loadRecent, 4000);
    const t2 = setInterval(loadSensors, SENSOR_POLL);
    const t3 = setInterval(loadSubjects, 10000);
    return () => {
      stop();
      clearInterval(t1); clearInterval(t2); clearInterval(t3);
    };
  }, [start, stop, loadRecent, loadSensors, loadSubjects]);

  // derived
  const hasFace = !!analysis && analysis.proximity !== "far" && analysis.proximity !== "---";
  const inZone = !!analysis?.is_close;
  const analyzing = inZone && !!analysis;
  const confidence = analysis?.confidence != null ? (analysis.confidence * 100).toFixed(1) : recent[recent.length - 1]?.confidence != null ? (recent[recent.length - 1].confidence * 100).toFixed(1) : "—";
  const displayId = currentId ?? 184;
  const gateStatus = analyzing ? "ANALYZING" : "READY";

  // stepper states
  const steps = [
    { label: "Detected", state: hasFace || inZone || analyzing ? "done" : "idle" },
    { label: "Tracking", state: hasFace || inZone ? "done" : "idle" },
    { label: "In Sensor Zone", state: inZone ? "done" : hasFace ? "active" : "idle" },
    { label: "Analyzing", state: analyzing ? "active" : "idle" },
    { label: "Decision", state: "idle" },
  ];

  // subject lookup for logs
  const subjectMap = subjects.reduce((m, s) => { m[String(s.id)] = s.name; return m; }, {});

  // recent strip — real logs only, no mock fallback — includes est BAC tier
  const strip = (() => {
    if (!recent.length) return [{ id: displayId, time: "—", est: "—", label: "No history yet", color: "var(--text-muted)", active: true, pulse: false, name: "—", isUnknown: true }];
    const mapped = recent.slice(-4).map((l) => {
      const lab = (l.prediction || "Pass").toUpperCase();
      const isOver = lab.includes("OVER") || lab.includes("FLAGGED") || l.bac_tier === "over";
      const name = l.subject_id != null && subjectMap[String(l.subject_id)] ? subjectMap[String(l.subject_id)] : "Unknown";
      const est = l.estimated_bac != null ? `${Number(l.estimated_bac).toFixed(2)}% ${l.bac_tier === "over" ? "PH FAIL" : l.bac_tier === "light" ? "Light" : l.bac_tier === "trace" ? "Trace" : ""}`.trim() : (l.bac != null ? Number(l.bac).toFixed(2) : "—");
      return {
        id: l.id,
        time: l.date ? new Date(l.date).toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }).replace(/^24:/, "00:") : "--:--:--",
        est,
        bac_tier: l.bac_tier,
        label: isOver ? "PH FAIL" : lab.includes("PASS") ? "PASSED" : lab,
        color: isOver ? "var(--over)" : "var(--pass)",
        name,
        isUnknown: name === "Unknown",
      };
    });
    return [
      ...mapped,
      { id: displayId, time: "—", est: analyzing ? "Est…" : "—", label: analyzing ? "Analyzing..." : "Waiting…", color: "var(--near)", active: true, pulse: analyzing, name: analysis?.identified && analysis.name ? analysis.name : "Unknown", isUnknown: !analysis?.identified },
    ];
  })();

  return (
    <div className="p-2 flex flex-col gap-2 max-w-[1400px] mx-auto">
      {/* GATE header */}
      <div className="flex items-center gap-2 px-1">
        <span className="text-[11px] font-bold tracking-[0.08em]" style={{ color: "var(--text-primary)" }}>GATE 01</span>
        <span className="w-1 h-1 rounded-full bg-emerald-500" />
        <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>Main Entrance</span>
        <button
          onClick={() => setGateEnabled(v => !v)}
          title={gateEnabled ? "Gate ON — is_close will POST /sensor/trigger → deployment_logs" : "Gate OFF — preview only, training_data safe"}
          className="ml-auto text-[10px] px-2 py-0.5 rounded-[4px] border font-medium transition-colors"
          style={{ background: gateEnabled ? "var(--over-bg)" : "var(--bg-card)", borderColor: gateEnabled ? "var(--over)" : "var(--border)", color: gateEnabled ? "var(--over)" : "var(--text-muted)" }}
        >
          {gateEnabled ? "Gate ON" : "Gate OFF"}
        </button>
        <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>{camError ? "OFFLINE" : "LIVE"} · 15 FPS</span>
      </div>

      <div className="grid grid-cols-12 gap-2">
        {/* LEFT: camera */}
        <div className="col-span-12 lg:col-span-8 rounded-[6px] overflow-hidden flex flex-col" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
          {/* camera top bar */}
          <div className="flex items-center justify-between px-2.5 py-1.5 shrink-0" style={{ borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-card)" }}>
            <span className="flex items-center gap-1.5 text-[10px] font-medium">
              <span className={`w-1.5 h-1.5 rounded-full ${streaming && !camError ? "bg-emerald-500 animate-pulse" : "bg-red-500"}`} />
              <span style={{ color: streaming && !camError ? "var(--pass)" : "var(--over)" }}>{camError ? "Camera unavailable" : "LIVE"}</span>
              <span style={{ color: "var(--text-muted)" }}>· Camera 01</span>
            </span>
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => setOverlayOn(v => !v)}
                title={overlayOn ? "HUD on — backend box (cost ~3 FPS)" : "HUD off — smooth 15 FPS"}
                className="text-[9px] px-1.5 py-0.5 rounded-[4px] border transition-colors"
                style={{ background: overlayOn ? "var(--bg-active)" : "transparent", borderColor: overlayOn ? "var(--border-strong)" : "var(--border)", color: overlayOn ? "var(--text-primary)" : "var(--text-muted)" }}
              >
                {overlayOn ? "HUD ON" : "HUD OFF"}
              </button>
              {analysis && (
                <span className="text-[9px] px-1.5 py-0.5 rounded-full border font-medium uppercase" style={{ color: inZone ? "var(--over)" : "var(--text-muted)", borderColor: inZone ? "var(--over)" : "var(--border)", background: inZone ? "var(--over-bg)" : "transparent" }}>
                  {analysis.proximity}
                </span>
              )}
              <button onClick={streaming ? stop : start} className="text-[10px] px-2 py-0.5 rounded-[4px] border" style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>
                {streaming ? "Pause" : "Resume"}
              </button>
            </div>
          </div>

          {/* viewfinder — MJPEG single connection (B) */}
          <div className="relative aspect-[16/7.6] bg-[#080C10] flex items-center justify-center overflow-hidden shrink-0">
            {streaming && !camError ? (
              <>
                <img src={mjpegUrl} alt="gate feed" className="w-full h-full object-cover" onLoad={() => { errRef.current = 0; setCamError(false); }} onError={() => { errRef.current += 1; if (errRef.current >= 5) setCamError(true); }} />
                {/* corner brackets — reference style */}
                <div className="absolute top-3 left-3 w-4 h-4 border-l-2 border-t-2 rounded-tl-[2px]" style={{ borderColor: "var(--pass)" }} />
                <div className="absolute top-3 right-3 w-4 h-4 border-r-2 border-t-2 rounded-tr-[2px]" style={{ borderColor: "var(--pass)" }} />
                <div className="absolute bottom-3 left-3 w-4 h-4 border-l-2 border-b-2 rounded-bl-[2px]" style={{ borderColor: "var(--pass)" }} />
                <div className="absolute bottom-3 right-3 w-4 h-4 border-r-2 border-b-2 rounded-br-[2px]" style={{ borderColor: "var(--pass)" }} />
                {/* bottom meta */}
                <div className="absolute bottom-2 left-2 flex items-center gap-1.5">
                  <span className="text-[9px] px-1.5 py-0.5 rounded-[3px] font-mono" style={{ background: "rgba(0,0,0,0.65)", color: "var(--text-secondary)", border: "1px solid rgba(255,255,255,0.08)" }}>
                    EAR {analysis?.ear != null ? analysis.ear.toFixed(3) : "---"} · {analysis?.status ?? "---"}
                  </span>
                  <span className="text-[9px] px-1.5 py-0.5 rounded-[3px]" style={{ background: "rgba(0,0,0,0.65)", color: analysis?.identified ? "var(--pass)" : "var(--text-muted)", border: "1px solid rgba(255,255,255,0.08)", fontStyle: analysis?.identified ? "normal" : "italic" }}>
                    {analysis?.identified ? `✓ ${analysis.name}` : "Unknown"}
                  </span>
                </div>
                {inZone && <span className="absolute bottom-2 right-2 text-[9px] px-2 py-0.5 rounded-full font-bold animate-pulse" style={{ background: "var(--over)", color: "#fff" }}>● IN ZONE — sampling</span>}
              </>
            ) : (
              <div className="flex flex-col items-center gap-2">
                <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" }}>
                  <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>◯</span>
                </div>
                <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>{camError ? "Cannot reach camera — check RTSP_URL" : "Connecting…"}</span>
              </div>
            )}
            {/* dashed center guide like reference */}
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none opacity-20">
              <div className="w-[38%] h-[72%] rounded-[4px]" style={{ border: "1px dashed var(--text-muted)" }} />
            </div>
          </div>

          {/* bottom strips — 4 panels, hairline dividers — no mock, shows — when null */}
          <div className="grid grid-cols-4 gap-px shrink-0" style={{ background: "var(--border-subtle)", borderTop: "1px solid var(--border-subtle)" }}>
            <div className="px-2.5 py-2" style={{ background: "var(--bg-card)" }}>
              <div className="text-[8px] tracking-[0.1em] uppercase font-semibold" style={{ color: "var(--text-muted)" }}>MQ-3 Sensors (raw ADC) · Est. BAC (PH 0.05)</div>
              <div className="flex gap-3 mt-1.5">
                {[
                  { k: "SENSOR 1", v: mq3[0] },
                  { k: "SENSOR 2", v: mq3[1] },
                  { k: "SENSOR 3", v: mq3[2] },
                ].map((s) => (
                  <div key={s.k} className="flex flex-col">
                    <span className="text-[7px] tracking-wide" style={{ color: "var(--text-muted)" }}>{s.k}</span>
                    <span className="text-[14px] font-bold font-mono leading-none mt-0.5" style={{ color: s.v != null ? "var(--pass)" : "var(--text-muted)" }}>{s.v != null ? s.v.toFixed(2) : "—"}</span>
                    <span className="text-[7px] font-medium" style={{ color: s.v != null && s.v >= 0.4 ? "var(--over)" : "var(--text-muted)" }}>{levelLabel(s.v)}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="px-2.5 py-2 flex flex-col items-center justify-center" style={{ background: "var(--bg-card)" }}>
              <div className="text-[8px] tracking-[0.1em] uppercase font-semibold" style={{ color: "var(--text-muted)" }}>AI Confidence</div>
              <div className="text-[18px] font-bold font-mono leading-none mt-1.5" style={{ color: "var(--pass)" }}>{confidence}%</div>
              <div className="w-full h-1 rounded-full mt-1.5 overflow-hidden" style={{ background: "var(--bg-card-alt)" }}>
                <div className="h-full rounded-full transition-all duration-500" style={{ width: `${Math.min(parseFloat(confidence) || 0, 100)}%`, background: parseFloat(confidence) > 80 ? "var(--pass)" : "var(--near)" }} />
              </div>
              <div className="text-[7px] mt-1" style={{ color: "var(--text-muted)" }}>Model: RF + XGBoost</div>
            </div>

            <div className="px-2.5 py-2" style={{ background: "var(--bg-card)" }}>
              <div className="text-[8px] tracking-[0.1em] uppercase font-semibold" style={{ color: "var(--text-muted)" }}>Environment (DHT11)</div>
              <div className="flex gap-5 mt-1.5">
                <div>
                  <div className="text-[11px] font-mono font-semibold" style={{ color: dht.t != null ? "var(--text-primary)" : "var(--text-muted)" }}>{dht.t != null ? `${dht.t.toFixed(1)}°C` : "—"}</div>
                  <div className="text-[7px]" style={{ color: "var(--text-muted)" }}>Temperature</div>
                </div>
                <div>
                  <div className="text-[11px] font-mono font-semibold" style={{ color: dht.h != null ? "var(--text-primary)" : "var(--text-muted)" }}>{dht.h != null ? `${dht.h}%` : "—"}</div>
                  <div className="text-[7px]" style={{ color: "var(--text-muted)" }}>Humidity</div>
                </div>
              </div>
            </div>

            <div className="px-2.5 py-2 flex flex-col items-center justify-center" style={{ background: "var(--bg-card)" }}>
              <div className="text-[8px] tracking-[0.1em] uppercase font-semibold" style={{ color: "var(--text-muted)" }}>Gate Status</div>
              <div className="flex items-center gap-2 mt-1.5">
                <div className="w-7 h-7 rounded-full flex items-center justify-center" style={{ background: gateStatus === "READY" ? "var(--pass-bg)" : "var(--near-bg)", border: `1px solid ${gateStatus === "READY" ? "var(--pass)" : "var(--near)"}` }}>
                  <span className="text-[10px]">{gateStatus === "READY" ? "✓" : "◷"}</span>
                </div>
                <div>
                  <div className="text-[11px] font-bold tracking-wide leading-none" style={{ color: gateStatus === "READY" ? "var(--pass)" : "var(--near)" }}>{gateStatus}</div>
                  <div className="text-[7px]" style={{ color: "var(--text-muted)" }}>Auto Mode</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* RIGHT: current passage */}
        <div className="col-span-12 lg:col-span-4 rounded-[6px] p-3 flex flex-col gap-3" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
          <div className="text-[9px] tracking-[0.12em] uppercase font-bold" style={{ color: "var(--pass)" }}>Current Passage</div>
          <div className="flex gap-3">
            <div className="flex-1 min-w-0">
              <div className="text-[8px] tracking-wide uppercase" style={{ color: "var(--text-muted)" }}>ID</div>
              <div className="text-[20px] font-bold font-mono leading-none" style={{ color: "var(--pass)" }}>#{displayId}</div>
              <div className="text-[8px] tracking-wide uppercase mt-2" style={{ color: "var(--text-muted)" }}>Status</div>
              <div className="text-[11px] font-bold flex items-center gap-1.5 mt-0.5" style={{ color: analyzing ? "var(--near)" : "var(--pass)" }}>
                {analyzing ? "ANALYZING" : "IDLE"}
                {analyzing && <span className="w-3 h-3 rounded-full border-[1.5px] border-current border-t-transparent animate-spin" />}
              </div>
              <div className="text-[10px] font-mono mt-1 flex flex-col" style={{ color: "var(--text-secondary)" }}>
                <span>{mq3[0] != null ? `${mq3[0].toFixed(2)} raw` : "— raw"} · {confidence}%</span>
                {recent.length && recent[recent.length - 1]?.estimated_bac != null && (
                  <span className="text-[9px] font-bold" style={{ color: recent[recent.length - 1]?.bac_tier === "over" ? "var(--over)" : recent[recent.length - 1]?.bac_tier === "light" ? "var(--near)" : "var(--text-muted)" }}>
                    Est. BAC {Number(recent[recent.length - 1].estimated_bac).toFixed(2)}% {recent[recent.length - 1].bac_tier === "over" ? "— PH FAIL" : recent[recent.length - 1].bac_tier === "light" ? "— Light" : recent[recent.length - 1].bac_tier === "trace" ? "— Trace" : ""} {recent[recent.length - 1].bac_tier === "over" ? "⛔" : ""}
                  </span>
                )}
              </div>
            </div>
            <div className="flex-1 flex flex-col gap-1.5 py-0.5">
              {steps.map((s) => <Step key={s.label} label={s.label} state={s.state} />)}
            </div>
          </div>

          {/* divider + mini detection log */}
          <div className="pt-2 mt-1" style={{ borderTop: "1px solid var(--border-subtle)" }}>
            <div className="text-[9px] tracking-[0.08em] uppercase font-semibold mb-1.5" style={{ color: "var(--text-muted)" }}>Detection Log</div>
            <div className="space-y-1">
              {[
                { k: "Face", v: analysis?.identified ? `✓ ${analysis.name}` : hasFace ? "Detected (Unknown)" : "No face", c: analysis?.identified ? "var(--pass)" : hasFace ? "var(--text-muted)" : "var(--text-muted)", italic: !analysis?.identified && hasFace },
                { k: "EAR", v: analysis?.ear != null ? `${analysis.ear.toFixed(3)} · ${analysis.status ?? "—"}` : "—", c: "var(--text-secondary)" },
                { k: "Proximity", v: analysis?.proximity ?? "far", c: inZone ? "var(--near)" : "var(--text-muted)" },
              ].map((r) => (
                <div key={r.k} className="flex justify-between text-[10px] px-2 py-1 rounded-[4px]" style={{ background: "var(--bg-card-alt)" }}>
                  <span style={{ color: "var(--text-muted)" }}>{r.k}</span>
                  <span className="font-mono font-medium" style={{ color: r.c, fontStyle: r.italic ? "italic" : "normal" }}>{r.v}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* RECENT PASSAGES strip — reference bottom */}
      <div className="rounded-[6px] p-2.5" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
        <div className="flex items-center justify-between">
          <span className="text-[9px] tracking-[0.12em] uppercase font-bold" style={{ color: "var(--text-muted)" }}>LIVE FLOW — RECENT PASSAGES</span>
          <span className="text-[9px] font-mono" style={{ color: "var(--text-muted)" }}>{recent.length ? `${recent.length} events` : "no history"}</span>
        </div>

        <div className="flex items-center gap-0 mt-2 overflow-x-auto pb-1">
          {strip.map((r, idx) => (
            <div key={`${r.id}-${idx}`} className="flex items-center shrink-0">
              <div
                data-interactive
                className="rounded-[5px] px-2.5 py-2 flex items-center gap-2 min-w-[124px] cursor-pointer"
                style={{
                  background: r.active ? "var(--near-bg)" : "var(--bg-card-alt)",
                  border: `1px solid ${r.active ? "var(--near)" : "var(--border-subtle)"}`,
                  boxShadow: r.active ? "0 0 0 1px var(--near-bg)" : "none",
                }}
              >
                <div className="w-7 h-7 rounded-full bg-[#080C10] shrink-0 overflow-hidden flex items-center justify-center" style={{ border: `1px solid ${r.isUnknown ? "var(--border-subtle)" : "var(--border)"}` }}>
                  <span className="text-[7px] font-bold" style={{ color: r.isUnknown ? "var(--text-muted)" : "var(--text-secondary)" }}>{r.isUnknown ? "?" : String(r.name).slice(0, 2).toUpperCase()}</span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[11px] font-mono font-bold leading-none" style={{ color: r.color }}>#{r.id}</div>
                  <div className="text-[7px] font-mono leading-none mt-0.5 truncate" style={{ color: r.isUnknown ? "var(--text-muted)" : r.bac_tier === "over" ? "var(--over)" : "var(--text-secondary)", fontStyle: r.isUnknown ? "italic" : "normal" }}>{r.isUnknown ? "Unknown" : r.name} · {r.est}</div>
                  <div className={`text-[8px] font-bold tracking-wide mt-0.5 ${r.pulse ? "animate-pulse" : ""}`} style={{ color: r.color }}>{r.label}</div>
                </div>
                {r.pulse && <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: "var(--near)" }} />}
              </div>

              {idx < strip.length - 1 && (
                <div className="mx-1 flex items-center" style={{ color: "var(--border-strong)" }}>
                  <span className="text-[10px] tracking-[0.2em]">→</span>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
