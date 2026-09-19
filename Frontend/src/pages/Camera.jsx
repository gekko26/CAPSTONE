// File: Frontend/src/pages/Camera.jsx
import { useState, useEffect, useRef, useCallback } from "react";
import { RecentDetections } from "../assets/graph";

import { API_BASE as BASE } from "../api";
const FRAME_MS   = 60;  // 15 FPS max for stream1 — max fps, camera-only
const ANALYZE_MS = 400; // decoupled analysis

// ── Status helpers ────────────────────────────────────────────
function proximityColor(proximity) {
  if (proximity === "close")  return "var(--over)";
  if (proximity === "medium") return "var(--near)";
  return "var(--text-muted)";
}

function earColor(status) {
  if (status === "impaired") return "var(--over)";
  if (status === "drowsy")   return "var(--near)";
  return "var(--pass)";
}

function StatBox({ label, value, sub, valueColor }) {
  return (
    <div
      className="rounded-lg px-3 py-2.5"
      style={{ background: "var(--bg-active)" }}
    >
      <p className="text-[10px] mb-0.5" style={{ color: "var(--text-muted)" }}>{label}</p>
      <p className="text-sm font-semibold" style={{ color: valueColor ?? "var(--text-primary)" }}>
        {value ?? "---"}
      </p>
      {sub && <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>{sub}</p>}
    </div>
  );
}

// ═════════════════════════════════════════════════════════════
export default function Camera() {
  const [frameUrl, setFrameUrl]       = useState(null);
  const [analysis, setAnalysis]       = useState(null);
  const [camError, setCamError]       = useState(false);
  const [streaming, setStreaming]     = useState(true);
  const [overlayOn, setOverlayOn]     = useState(false);
  const overlayRef                    = useRef(false);
  useEffect(() => { overlayRef.current = overlayOn; }, [overlayOn]);

  const frameTimeoutRef               = useRef(null);
  const analyzeTimeoutRef             = useRef(null);
  const latestBlobRef                 = useRef(null);
  const errorCountRef                 = useRef(0);
  const loopActiveRef                 = useRef(false);
  const fetchingRef                   = useRef(false);

  // MJPEG URL — single persistent connection, no per-frame fetch/GC (B)
  // Camera uses MJPEG for smooth live view; Training/Deployment keep fetch loop (see note below)
  const mjpegUrl = `${BASE}/camera/stream/mjpeg?overlay=${overlayOn ? "1" : "0"}&fps=15`;

  // FIX: Tracks hardware trigger lockouts to guarantee deployment calls fire exactly ONCE per approach
  const hasTriggeredSensorRef         = useRef(false);

  // ── Self-scheduling recursion + single-flight guard (A) — kept for Training/Deployment
  // Camera no longer uses fetchFrameLoop for display (uses MJPEG), but kept for backward compat
  const fetchFrameLoop = async () => {
    if (!loopActiveRef.current) return;
    if (fetchingRef.current) { frameTimeoutRef.current = setTimeout(fetchFrameLoop, FRAME_MS); return; }
    fetchingRef.current = true;
    try {
      const overlayParam = overlayRef.current ? "1" : "0";
      const frameRes = await fetch(`${BASE}/camera/stream/frame?overlay=${overlayParam}`, { cache: "no-store" });
      if (!frameRes.ok) {
        errorCountRef.current += 1;
        if (errorCountRef.current >= 5) setCamError(true);
      } else {
        errorCountRef.current = 0;
        setCamError(false);
        const blob = await frameRes.blob();
        latestBlobRef.current = blob;
        const url = URL.createObjectURL(blob);
        setFrameUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
      }
    } catch {
      errorCountRef.current += 1;
      if (errorCountRef.current >= 5) setCamError(true);
    } finally {
      fetchingRef.current = false;
      frameTimeoutRef.current = setTimeout(fetchFrameLoop, FRAME_MS);
    }
  };

  const runAnalyzeLoop = async () => {
    if (!loopActiveRef.current) return;
    try {
      const frameRes = await fetch(`${BASE}/camera/stream/frame?overlay=0`, { cache: "no-store" });
      if (!frameRes.ok) throw new Error("frame");
      const blob = await frameRes.blob();
      latestBlobRef.current = blob;
      const fd = new FormData();
      fd.append("file", blob, "frame.jpg");
      const analyzeRes = await fetch(`${BASE}/camera/analyze`, { method: "POST", body: fd });
      if (analyzeRes.ok) {
        const data = await analyzeRes.json();
        setAnalysis(data);
        // FIX: Triggers deployment mode sensor logging via Option B middleman when proximity shifts
        if (data.is_close) {
          if (!hasTriggeredSensorRef.current) {
            hasTriggeredSensorRef.current = true;
            fetch(`${BASE}/sensor/trigger`, { method: "POST" }).catch(() => {});
          }
        } else {
          // Unlocks trigger barrier only when subject moves clear of target zone
          hasTriggeredSensorRef.current = false;
        }
      }
    } catch {}
    analyzeTimeoutRef.current = setTimeout(runAnalyzeLoop, ANALYZE_MS);
  };

  // ── Start / stop stream ────────────────────────────────────
  // Camera uses MJPEG (B) — no fetchFrameLoop polling; analyze runs independently
  const startStream = useCallback(() => {
    if (loopActiveRef.current) return;
    loopActiveRef.current = true;
    setStreaming(true);
    runAnalyzeLoop();
  }, []);

  const stopStream = useCallback(() => {
    loopActiveRef.current = false;
    clearTimeout(analyzeTimeoutRef.current);
    analyzeTimeoutRef.current = null;
    setStreaming(false);
  }, []);

  useEffect(() => {
    startStream();
    return () => {
      stopStream();
    };
  }, [startStream, stopStream]);

  // ── Derived display values ─────────────────────────────────
  const proximity  = analysis?.proximity  ?? "---";
  const isClose    = analysis?.is_close   ?? false;
  const earStatus  = analysis?.status     ?? "---";
  const ear        = analysis?.ear        != null ? analysis.ear.toFixed(3) : "---";
  const identified = analysis?.identified ?? false;
  const name       = analysis?.name       ?? "---";
  const confidence = analysis?.confidence != null
    ? `${(analysis.confidence * 100).toFixed(1)}%`
    : "---";

  return (
    <div className="space-y-4">

      {/* ── Camera feed + detection info ── */}
      <div className="grid grid-cols-5 gap-4">

        {/* Feed — 3 cols */}
        <div
          className="col-span-3 rounded-xl overflow-hidden border"
          style={{ borderColor: "var(--border-subtle)", background: "var(--bg-card)" }}
        >
          {/* Top bar */}
          <div className="flex items-center justify-between px-4 py-2.5" style={{ background: "var(--bg-card)", borderBottom: "1px solid var(--border-subtle)" }}>
            <div className="flex items-center gap-2">
              <span
                className={`w-2 h-2 rounded-full ${streaming && !camError ? "bg-emerald-400 animate-pulse" : "bg-red-500"}`}
              />
              <span className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
                {camError ? "Camera unavailable" : "Live · CAM-01"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {/* Overlay toggle */}
              <button
                onClick={() => setOverlayOn(v => !v)}
                title={overlayOn ? "HUD on — backend box (cost ~3 FPS)" : "HUD off — smooth (15 FPS)"}
                className="text-[10px] px-2 py-0.5 rounded-full border transition-colors"
                style={{
                  color: overlayOn ? "var(--text-primary)" : "var(--text-muted)",
                  background: overlayOn ? "var(--bg-active)" : "transparent",
                  borderColor: overlayOn ? "var(--border-strong)" : "var(--border-subtle)",
                }}
              >
                {overlayOn ? "HUD ON" : "HUD OFF"}
              </button>
              {/* Proximity badge */}
              {analysis && (
                <span
                  className="text-[10px] font-medium px-2 py-0.5 rounded-full border"
                  style={{
                    color:       proximityColor(proximity),
                    borderColor: proximityColor(proximity),
                    background:  `color-mix(in srgb, ${proximityColor(proximity)} 10%, transparent)`,
                  }}
                >
                  {proximity.toUpperCase()}
                </span>
              )}
              {/* Stream toggle */}
              <button
                onClick={streaming ? stopStream : startStream}
                className="text-[10px] px-2 py-0.5 rounded-full border text-white/50 border-white/20 hover:border-white/40 transition-colors"
              >
                {streaming ? "Pause" : "Resume"}
              </button>
            </div>
          </div>

          {/* Viewfinder — MJPEG single connection (B) */}
          {/* Media surface stays dark in both themes for video contrast */}
            <div className="relative aspect-video flex items-center justify-center overflow-hidden" style={{ background: "#101418" }}>
            {streaming && !camError ? (
              <>
                <img
                  src={mjpegUrl}
                  alt="C200C live feed"
                  className="w-full h-full object-cover"
                  onLoad={() => { errorCountRef.current = 0; setCamError(false); }}
                  onError={() => { errorCountRef.current += 1; if (errorCountRef.current >= 5) setCamError(true); }}
                />
                {/* EAR overlay — top left */}
                {analysis && (
                  <div
                    className="absolute top-2 left-2 flex flex-col gap-1"
                  >
                    <div
                      className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                      style={{
                        background: `color-mix(in srgb, ${earColor(earStatus)} 15%, rgba(0,0,0,0.6))`,
                        color: earColor(earStatus),
                      }}
                    >
                      EAR {ear} · {earStatus.toUpperCase()}
                    </div>
                    {analysis?.mar != null && (
                      <div
                        className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                        style={{
                          background: "rgba(0,0,0,0.6)",
                          color: analysis.yawning ? "#f87171" : "rgba(255,255,255,0.85)",
                        }}
                      >
                        MAR {analysis.mar.toFixed(2)}{analysis.yawning ? " · YAWNING" : ""}
                      </div>
                    )}
                    {identified && (
                      <div
                        className="text-[10px] font-medium px-2 py-0.5 rounded-full"
                        style={{ background: "rgba(0,0,0,0.6)", color: "var(--pass)" }}
                      >
                        ✓ {name}
                      </div>
                    )}
                  </div>
                )}
                {/* Proximity indicator — bottom right */}
                {isClose && (
                  <div
                    className="absolute bottom-2 right-2 text-[10px] font-medium px-2 py-0.5 rounded-full animate-pulse"
                    style={{ background: "rgba(220,38,38,0.8)", color: "#fff" }}
                  >
                    ⚠ CLOSE — triggering sensor
                  </div>
                )}
              </>
            ) : (
              /* Placeholder when camera unavailable */
              <div className="flex flex-col items-center gap-3">
                <div className="w-16 h-16 rounded-full bg-white/5 border border-white/10 flex items-center justify-center">
                  <svg className="w-7 h-7 text-white/25" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round"
                      d="M15.75 10.5l4.72-4.72a.75.75 0 011.28.53v11.38a.75.75 0 01-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25h-9A2.25 2.25 0 002.25 9.75v9A2.25 2.25 0 004.5 18.75z" />
                  </svg>
                </div>
                <p className="text-white/30 text-sm">
                  {camError ? "Cannot reach camera — check RTSP_URL" : "Connecting..."}
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Detection info — 2 cols */}
        <div
          className="col-span-2 rounded-xl border p-4"
          style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
        >
          <p className="text-xs font-medium mb-3" style={{ color: "var(--text-muted)" }}>
            Detection info
          </p>

          <div className="space-y-2">
            {/* Subject ID */}
            <StatBox
              label="Subject"
              value={identified ? name : "Unidentified"}
              sub={identified ? `${confidence} match` : "No face matched"}
              valueColor={identified ? "var(--pass)" : "var(--text-secondary)"}
            />

            {/* EAR + Proximity */}
            <div className="grid grid-cols-2 gap-2">
              <StatBox
                label="EAR"
                value={ear}
                sub={earStatus}
                valueColor={earColor(earStatus)}
              />
              <StatBox
                label="Proximity"
                value={proximity}
                sub={isClose ? "Sensor active" : "Waiting"}
                valueColor={proximityColor(proximity)}
              />
            </div>

            {/* Left / Right EAR */}
            <div className="grid grid-cols-2 gap-2">
              <StatBox
                label="Left EAR"
                value={analysis?.left_ear != null ? analysis.left_ear.toFixed(3) : "---"}
                valueColor="var(--text-secondary)"
              />
              <StatBox
                label="Right EAR"
                value={analysis?.right_ear != null ? analysis.right_ear.toFixed(3) : "---"}
                valueColor="var(--text-secondary)"
              />
            </div>

            {/* MAR + yawn */}
            <div className="grid grid-cols-2 gap-2">
              <StatBox
                label="MAR"
                value={analysis?.mar != null ? analysis.mar.toFixed(3) : "---"}
                sub={analysis?.yawning ? "Yawning" : analysis ? "Normal" : undefined}
                valueColor={
                  !analysis || analysis.mar == null
                    ? "var(--text-primary)"
                    : analysis.yawning
                    ? "var(--over)"
                    : analysis.mar > 0.4
                    ? "var(--near)"
                    : "var(--pass)"
                }
              />
              <StatBox
                label="Head pose"
                value={
                  analysis?.pitch != null
                    ? `P ${Math.round(analysis.pitch)}°`
                    : "---"
                }
                sub={
                  analysis?.head_down
                    ? "Head down"
                    : analysis?.yaw != null
                    ? `Y ${Math.round(analysis.yaw)}° · R ${Math.round(analysis.roll ?? 0)}°`
                    : undefined
                }
                valueColor={
                  analysis?.head_down ? "var(--over)" : "var(--text-secondary)"
                }
              />
            </div>

            {/* Impairment status */}
            <div
              className="rounded-lg px-3 py-2.5 flex items-center gap-2"
              style={{ background: "var(--bg-active)" }}
            >
              <div
                className="w-2 h-2 rounded-full shrink-0"
                style={{
                  background: analysis?.impaired
                    ? "var(--over)"
                    : analysis
                    ? "var(--pass)"
                    : "var(--text-muted)",
                }}
              />
              <div>
                <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>Visual status</p>
                <p className="text-sm font-semibold" style={{
                  color: analysis?.impaired ? "var(--over)" : "var(--text-primary)"
                }}>
                  {analysis
                    ? analysis.impaired ? "Impaired" : "Normal"
                    : "---"}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Recent detections — reuses existing component */}
      <RecentDetections />
    </div>
  );
}