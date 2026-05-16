import { useState, useEffect, useRef, useCallback } from "react";
import {
  FlaskConical, RefreshCw, Download, ChevronDown, ChevronUp,
  Tag, Pencil, Trash2, Check, X, Play, Camera,
  Wind, Droplets, AlertTriangle, Activity, Cpu,
} from "lucide-react";

const BASE = "http://localhost:8000";
const LABEL_NAMES = { "-1": "Pending", 0: "No alcohol", 1: "Breath alcohol", 2: "Sanitizer" };

// ── Toast ─────────────────────────────────────────────────────
function useToast() {
  const [toasts, setToasts] = useState([]);
  const add = useCallback((msg, type = "success") => {
    const id = Date.now();
    setToasts((p) => [...p, { id, msg, type }]);
    setTimeout(() => setToasts((p) => p.filter((t) => t.id !== id)), 3500);
  }, []);
  return { toasts, toast: add };
}

function Toasts({ toasts }) {
  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium border pointer-events-auto
            ${t.type === "success"
              ? "bg-(--pass)/10 text-(--pass) border-(--pass)/30"
              : "bg-(--over)/10 text-(--over) border-(--over)/30"}`}
        >
          {t.type === "success" ? <Check size={14} /> : <AlertTriangle size={14} />}
          {t.msg}
        </div>
      ))}
    </div>
  );
}

// ── Progress bar ──────────────────────────────────────────────
function ProgressBar({ value, target = 50, color = "var(--pass)" }) {
  const pct = Math.min(Math.round((value / target) * 100), 100);
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-1.5 rounded-full bg-(--bg-active) overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="text-xs text-(--text-muted) w-14 text-right tabular-nums">
        {value} / {target}
      </span>
    </div>
  );
}

// ── Label badge ───────────────────────────────────────────────
function LabelBadge({ label }) {
  const map = {
    "-1": "bg-[var(--near)]/15 text-[var(--near)] border-[var(--near)]/30",
    0:    "bg-[var(--pass)]/15 text-[var(--pass)] border-[var(--pass)]/30",
    1:    "bg-[var(--over)]/15 text-[var(--over)] border-[var(--over)]/30",
    2:    "bg-[var(--text-secondary)]/10 text-[var(--text-secondary)] border-[var(--border-subtle)]",
  };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${map[label] ?? map["-1"]}`}>
      {LABEL_NAMES[label] ?? "Unknown"}
    </span>
  );
}

// ── Modal ─────────────────────────────────────────────────────
function Modal({ open, onClose, title, icon: Icon, iconColor = "var(--text-primary)", children }) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.45)" }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className="w-full max-w-sm rounded-xl border p-5"
        style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
      >
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2 font-medium text-sm" style={{ color: "var(--text-primary)" }}>
            {Icon && <Icon size={16} style={{ color: iconColor }} />}
            {title}
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-(--bg-active) text-(--text-muted)">
            <X size={14} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ── Stat card ─────────────────────────────────────────────────
function StatCard({ label, value, color = "var(--text-primary)" }) {
  return (
    <div className="rounded-lg p-3.5" style={{ background: "var(--bg-active)" }}>
      <div className="text-xs mb-1.5 uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>{label}</div>
      <div className="text-2xl font-medium tabular-nums" style={{ color }}>{value ?? "—"}</div>
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

  // Collection
  const [eventType, setEventType]   = useState("sober");
  const [collecting, setCollecting] = useState(false);

  // Camera
  const videoRef = useRef(null);
  const [camActive, setCamActive] = useState(false);

  // Label modal
  const [labelModal, setLabelModal]       = useState(null);
  const [labelBac, setLabelBac]           = useState("");
  const [labelSanitizer, setLabelSanitizer] = useState(false);

  // Relabel modal
  const [relabelModal, setRelabelModal]   = useState(null);
  const [relabelValue, setRelabelValue]   = useState("0");
  const [relabelReason, setRelabelReason] = useState("");

  // ── Data fetching ─────────────────────────────────────────
  const loadSummary = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/training/summary`);
      setSummary(await r.json());
    } catch {
      toast("Could not reach backend", "error");
    }
  }, [toast]);

  const loadData = useCallback(async () => {
    setLoadingData(true);
    try {
      const r = await fetch(`${BASE}/training/data`);
      setRows(await r.json());
    } catch {
      toast("Failed to load training data", "error");
    } finally {
      setLoadingData(false);
    }
  }, [toast]);

  const loadAll = useCallback(() => {
    loadSummary();
    loadData();
  }, [loadSummary, loadData]);

  useEffect(() => { loadAll(); }, [loadAll]);

  // ── Camera ────────────────────────────────────────────────
  const startCamera = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true });
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        videoRef.current.play();
      }
      setCamActive(true);
    } catch {
      toast("Camera access denied", "error");
    }
  };

  const stopCamera = useCallback(() => {
    if (videoRef.current?.srcObject) {
      videoRef.current.srcObject.getTracks().forEach((t) => t.stop());
      videoRef.current.srcObject = null;
    }
    setCamActive(false);
  }, []);

  useEffect(() => () => stopCamera(), [stopCamera]);

  // ── Collect event ─────────────────────────────────────────
  //
  // Flow:
  //   sober / alcohol  → ESP32 auto-posts /training/collect when proximity fires.
  //                       The operator's job is to pick the event type BEFORE the
  //                       person walks through, then tag the pending row afterward.
  //                       If camera is active we also capture a frame via /session.
  //
  //   sanitizer        → operator sprays near sensors; ESP32 posts /collect.
  //                       The pending row is then auto-labeled (is_sanitizer: true).
  //
  // The "Trigger" button here is a readiness signal + optional camera capture.
  // The ESP32 independently posts the actual MQ3 data.
  const handleCollect = async () => {
    setCollecting(true);
    try {
      if (eventType === "sanitizer") {
        // Sanitizer: just remind the operator; ESP32 data comes in automatically.
        // The label modal won't be needed — they'll use the Tag button on the pending row
        // and check the sanitizer checkbox.
        toast("Spray near sensors now. Tag the new pending row as sanitizer when it appears.", "success");
        setTimeout(loadAll, 3000); // re-fetch in 3s to catch the ESP32 row
        return;
      }

      // Sober / alcohol with camera → /session
      if (camActive && videoRef.current && eventType !== "sanitizer") {
        const canvas = document.createElement("canvas");
        canvas.width  = videoRef.current.videoWidth  || 640;
        canvas.height = videoRef.current.videoHeight || 480;
        canvas.getContext("2d").drawImage(videoRef.current, 0, 0);

        const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.85));
        const fd   = new FormData();
        // Real MQ3 values come from ESP32 via /collect, not here.
        // /session expects them, so we send a flat baseline (will be overwritten
        // if the ESP32 row arrives separately — for now camera frame is the goal).
        fd.append("file",       blob, "frame.jpg");
        fd.append("temperature","0");
        fd.append("humidity",   "0");
        fd.append("mq3_1",      "110,110,110,110,110");
        fd.append("mq3_2",      "110,110,110,110,110");
        fd.append("mq3_3",      "110,110,110,110,110");
        fd.append("event_type", eventType);

        const r = await fetch(`${BASE}/training/session`, { method: "POST", body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail);

        // Open label modal immediately so operator can enter BAC right away
        openLabelModal(d.id, false);
        toast(`Frame captured — Row #${d.id}. Enter BAC reading now.`, "success");
        loadAll();
        return;
      }

      // No camera → just remind operator; ESP32 will post the row
      toast(
        eventType === "sober"
          ? "Walk through sober now. Tag the pending row with BAC 0.00 when it appears."
          : "Walk through after drinking now. Tag the pending row with the BACtrack reading.",
        "success"
      );
      setTimeout(loadAll, 3000);
    } catch (e) {
      toast(e.message || "Collect failed", "error");
    } finally {
      setCollecting(false);
    }
  };

  // ── Label ─────────────────────────────────────────────────
  const openLabelModal = (id, isSanitizer = false) => {
    setLabelModal({ id });
    setLabelBac(isSanitizer ? "0.00" : "");
    setLabelSanitizer(isSanitizer);
  };

  const submitLabel = async () => {
    const bac = parseFloat(labelBac);
    if (isNaN(bac) || bac < 0) {
      toast("Enter a valid BAC value — use 0.00 for sober / sanitizer events", "error");
      return;
    }
    try {
      const r = await fetch(`${BASE}/training/label/${labelModal.id}`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ bac, is_sanitizer: labelSanitizer }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      toast(`Row #${labelModal.id} labeled as "${d.label_name}"`);
      setLabelModal(null);
      loadAll();
    } catch (e) {
      toast(e.message || "Label failed", "error");
    }
  };

  // ── Relabel ───────────────────────────────────────────────
  const openRelabelModal = (id, currentLabel) => {
    setRelabelModal({ id, currentLabel });
    setRelabelValue(String(currentLabel >= 0 ? currentLabel : 0));
    setRelabelReason("");
  };

  const submitRelabel = async () => {
    try {
      const r = await fetch(`${BASE}/training/relabel/${relabelModal.id}`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ label: parseInt(relabelValue), reason: relabelReason || undefined }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      toast(`Row #${relabelModal.id} → "${d.new_label}"`);
      setRelabelModal(null);
      loadAll();
    } catch (e) {
      toast(e.message || "Relabel failed", "error");
    }
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
    } catch (e) {
      toast(e.message || "Delete failed", "error");
    }
  };

  // ── Export CSV ────────────────────────────────────────────
  const exportCSV = async () => {
    try {
      const r = await fetch(`${BASE}/training/export`);
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail); }
      const blob = await r.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href = url; a.download = "training_data.csv"; a.click();
      URL.revokeObjectURL(url);
      toast("CSV exported");
    } catch (e) {
      toast(e.message || "Export failed", "error");
    }
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
    } catch (e) {
      toast(e.message || "Training failed", "error");
    } finally {
      setTraining(false);
    }
  };

  // ── Filtered rows ─────────────────────────────────────────
  const filtered = filter === "all"
    ? rows
    : rows.filter((r) => String(r.label) === filter);

  const fmt = (v, d = 0) =>
    v != null ? (d === 0 ? Math.round(v) : Number(v).toFixed(d)) : "—";

  // ── Event configs ─────────────────────────────────────────
  const eventConfig = {
    sober: {
      label:       "Sober",
      hint:        "Walk through gate sober. Enter BAC (0.00) on the pending row after.",
      color:       "var(--pass)",
      icon:        Activity,
      autoLabel:   false,
    },
    alcohol: {
      label:       "Breath alcohol",
      hint:        "Walk through after drinking. Enter BACtrack reading on the pending row after.",
      color:       "var(--over)",
      icon:        Wind,
      autoLabel:   false,
    },
    sanitizer: {
      label:       "Sanitizer / perfume",
      hint:        "Spray near sensors. Tag the pending row and check the sanitizer box.",
      color:       "var(--text-secondary)",
      icon:        Droplets,
      autoLabel:   true,
    },
  };

  const cfg = eventConfig[eventType];
  const CfgIcon = cfg.icon;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <Toasts toasts={toasts} />

      {/* ── Header ── */}
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div className="flex items-center gap-2.5">
          <FlaskConical size={20} style={{ color: "var(--over)" }} />
          <h1 className="text-lg font-medium" style={{ color: "var(--text-primary)" }}>
            Training pipeline
          </h1>
        </div>
        <div className="flex gap-2">
          <button
            onClick={loadAll}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border transition-colors hover:bg-(--bg-active)"
            style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)"}} >
            <RefreshCw size={13} /> Refresh
          </button>
          <button
            onClick={exportCSV}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border transition-colors hover:bg-(--bg-active)"
            style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}
          >
            <Download size={13} /> Export CSV
          </button>
        </div>
      </div>

      {/* ── Stat cards ── */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2.5 mb-5">
        <StatCard label="Total"      value={summary?.total}           color="var(--text-primary)" />
        <StatCard label="Pending"    value={summary?.pending}         color="var(--near)" />
        <StatCard label="No alcohol" value={summary?.no_alcohol}      color="var(--pass)" />
        <StatCard label="Breath"     value={summary?.breath_alcohol}  color="var(--over)" />
        <StatCard label="Sanitizer"  value={summary?.sanitizer}       color="var(--text-secondary)" />
        <StatCard label="Ready"      value={summary?.ready_to_train}  color="var(--text-primary)" />
      </div>

      {/* ── Progress + Collection ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-5">

        {/* Progress */}
        <div
          className="rounded-xl border p-4 flex flex-col gap-4"
          style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
        >
          <div>
            <div className="text-xs font-medium uppercase tracking-wide mb-3" style={{ color: "var(--text-muted)" }}>
              Sensor class balance
            </div>
            <div className="space-y-3">
              {[
                { label: "No alcohol",     val: summary?.no_alcohol,    color: "var(--pass)" },
                { label: "Breath alcohol", val: summary?.breath_alcohol,color: "var(--over)" },
                { label: "Sanitizer",      val: summary?.sanitizer,     color: "var(--text-secondary)" },
              ].map(({ label, val, color }) => (
                <div key={label}>
                  <div className="text-xs mb-1.5" style={{ color: "var(--text-secondary)" }}>{label}</div>
                  <ProgressBar value={val ?? 0} color={color} />
                </div>
              ))}
            </div>
          </div>

          <div className="border-t pt-4" style={{ borderColor: "var(--border-subtle)" }}>
            <div className="text-xs font-medium uppercase tracking-wide mb-3" style={{ color: "var(--text-muted)" }}>
              Face images — MobileNet
            </div>
            <div className="space-y-3">
              {[
                { label: "Sober",    val: summary?.face_images?.sober,   color: "var(--pass)" },
                { label: "Drowsy",   val: summary?.face_images?.drowsy,  color: "var(--near)" },
                { label: "Impaired", val: summary?.face_images?.impaired,color: "var(--over)" },
              ].map(({ label, val, color }) => (
                <div key={label}>
                  <div className="text-xs mb-1.5" style={{ color: "var(--text-secondary)" }}>{label}</div>
                  <ProgressBar value={val ?? 0} color={color} />
                </div>
              ))}
            </div>
          </div>

          {summary && (
            <div
              className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border self-start
                ${summary.balanced
                  ? "bg-(--pass)/10 text-(--pass) border-(--pass)/30"
                  : "bg-(--near)/10 text-(--near) border-(--near)/30"}`}
            >
              {summary.balanced
                ? <><Check size={11} /> Balanced — ready to train</>
                : <><AlertTriangle size={11} /> Unbalanced — need more data</>}
            </div>
          )}
        </div>

        {/* Collection panel */}
        <div
          className="rounded-xl border p-4 flex flex-col gap-3"
          style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
        >
          <div className="text-xs font-medium uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
            Collect event
          </div>

          {/* Event type picker */}
          <div className="flex flex-col gap-2">
            {Object.entries(eventConfig).map(([key, c]) => {
              const Icon = c.icon;
              const active = eventType === key;
              return (
                <button
                  key={key}
                  onClick={() => setEventType(key)}
                  className="flex items-start gap-3 p-3 rounded-lg border text-left transition-all"
                  style={{
                    borderColor: active ? c.color : "var(--border-subtle)",
                    background: active
                      ? `color-mix(in srgb, ${c.color} 8%, transparent)`
                      : "transparent",
                  }}
                >
                  <div
                    className="mt-0.5 p-1.5 rounded-md shrink-0"
                    style={{
                      background: active
                        ? `color-mix(in srgb, ${c.color} 15%, transparent)`
                        : "var(--bg-active)",
                      color: active ? c.color : "var(--text-muted)",
                    }}
                  >
                    <Icon size={14} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium" style={{ color: active ? c.color : "var(--text-primary)" }}>
                      {c.label}
                    </div>
                    <div className="text-xs mt-0.5 leading-relaxed" style={{ color: "var(--text-muted)" }}>
                      {c.hint}
                    </div>
                  </div>
                  {active && <Check size={13} style={{ color: c.color, marginTop: 3, flexShrink: 0 }} />}
                </button>
              );
            })}
          </div>

          {/* Camera toggle — only for sober / alcohol */}
          {eventType !== "sanitizer" && (
            <div
              className="flex items-center justify-between rounded-lg p-3"
              style={{ background: "var(--bg-active)" }}
            >
              <div className="flex items-center gap-2 text-sm" style={{ color: "var(--text-secondary)" }}>
                <Camera size={14} />
                Camera (optional — saves face image)
              </div>
              <button
                onClick={camActive ? stopCamera : startCamera}
                className="text-xs px-2.5 py-1 rounded-md border transition-colors"
                style={{
                  borderColor: camActive ? "var(--pass)" : "var(--border-subtle)",
                  color:       camActive ? "var(--pass)" : "var(--text-secondary)",
                  background:  camActive
                    ? "color-mix(in srgb, var(--pass) 8%, transparent)"
                    : "transparent",
                }}
              >
                {camActive ? "Stop" : "Start"}
              </button>
            </div>
          )}

          {/* Viewfinder */}
          {camActive && eventType !== "sanitizer" && (
            <div
              className="relative rounded-lg overflow-hidden"
              style={{ background: "#0c1f14", aspectRatio: "16/9" }}
            >
              <video ref={videoRef} className="w-full h-full object-cover" muted playsInline />
              <div
                className="absolute top-2 left-2 flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium"
                style={{ background: "rgba(0,0,0,0.55)", color: "#34d399" }}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                Live · CAM-01
              </div>
            </div>
          )}

          {/* Trigger button */}
          <div className="flex flex-col gap-2 mt-auto">
            {cfg.autoLabel && (
              <div
                className="flex items-center gap-2 text-xs px-3 py-2 rounded-lg"
                style={{ background: "var(--bg-active)", color: "var(--text-muted)" }}
              >
                <Check size={12} style={{ color: "var(--pass)" }} />
                Auto-labeled as sanitizer — tag the row and check sanitizer box
              </div>
            )}
            <button
              onClick={handleCollect}
              disabled={collecting}
              className="flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all disabled:opacity-50"
              style={{ background: cfg.color, color: "#fff" }}
            >
              {collecting
                ? <><RefreshCw size={14} className="animate-spin" /> Collecting...</>
                : <><Play size={14} /> Trigger — {cfg.label}</>}
            </button>
          </div>
        </div>
      </div>

      {/* ── Train panel ── */}
      <div
        className="flex items-center justify-between rounded-xl border p-4 mb-5 flex-wrap gap-3"
        style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
      >
        <div>
          <div className="flex items-center gap-2 text-sm font-medium mb-1" style={{ color: "var(--text-primary)" }}>
            <Cpu size={15} /> Train sensor models
          </div>
          <div className="text-xs" style={{ color: "var(--text-muted)" }}>
            Requires 20+ samples per class (60 total minimum). Trains RF + XGBoost.
          </div>
        </div>
        <button
          onClick={triggerTraining}
          disabled={training}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all disabled:opacity-50"
          style={{ background: "var(--text-primary)", color: "var(--bg-card)" }}
        >
          {training
            ? <><RefreshCw size={13} className="animate-spin" /> Training...</>
            : <><Play size={13} /> Run training</>}
        </button>
      </div>

      {/* ── Data table ── */}
      <div
        className="rounded-xl border overflow-hidden"
        style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
      >
        {/* Toolbar */}
        <div
          className="flex items-center justify-between px-4 py-3 border-b flex-wrap gap-2"
          style={{ borderColor: "var(--border-subtle)" }}
        >
          <div className="flex items-center gap-2 text-sm font-medium" style={{ color: "var(--text-primary)" }}>
            Data table
            <span className="text-xs font-normal" style={{ color: "var(--text-muted)" }}>
              ({filtered.length})
            </span>
          </div>
          <div className="flex gap-1.5 flex-wrap">
            {[
              { f: "all", label: "All" },
              { f: "-1",  label: "Pending" },
              { f: "0",   label: "No alcohol" },
              { f: "1",   label: "Breath" },
              { f: "2",   label: "Sanitizer" },
            ].map(({ f, label }) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className="px-2.5 py-1 rounded-md text-xs border transition-all"
                style={{
                  borderColor: filter === f ? "var(--text-secondary)" : "var(--border-subtle)",
                  color:       filter === f ? "var(--text-primary)"   : "var(--text-muted)",
                  background:  filter === f ? "var(--bg-active)"      : "transparent",
                  fontWeight:  filter === f ? 500 : 400,
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm" style={{ tableLayout: "fixed", minWidth: 700 }}>
            <thead>
              <tr style={{ borderBottom: "0.5px solid var(--border-subtle)" }}>
                {[
                  { h: "ID",      w: 52  },
                  { h: "Date",    w: 120 },
                  { h: "Label",   w: 115 },
                  { h: "BAC",     w: 58  },
                  { h: "MQ3-1↑", w: 65  },
                  { h: "MQ3-2↑", w: 65  },
                  { h: "MQ3-3↑", w: 65  },
                  { h: "SpVar",   w: 62  },
                  { h: "°C",      w: 52  },
                  { h: "%RH",     w: 52  },
                  { h: "Actions", w: 96  },
                ].map(({ h, w }) => (
                  <th
                    key={h}
                    className="px-3 py-2.5 text-left text-xs font-medium uppercase tracking-wide"
                    style={{ color: "var(--text-muted)", width: w }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loadingData ? (
                <tr>
                  <td colSpan={11} className="text-center py-10 text-xs" style={{ color: "var(--text-muted)" }}>
                    Loading...
                  </td>
                </tr>
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={11} className="text-center py-10 text-xs" style={{ color: "var(--text-muted)" }}>
                    No rows for this filter
                  </td>
                </tr>
              ) : (
                filtered.flatMap((row) => {
                  const isExpanded = expandedId === row.id;
                  const isPending  = row.label === -1;
                  const isLabeled  = row.label >= 0;
                  const isDel      = deleteConfirm === row.id;

                  const date = row.date
                    ? new Date(row.date).toLocaleDateString("en-PH", {
                        month:  "short",
                        day:    "numeric",
                        hour:   "2-digit",
                        minute: "2-digit",
                      })
                    : "—";

                  return [
                    <tr
                      key={row.id}
                      onClick={() => setExpandedId(isExpanded ? null : row.id)}
                      className="cursor-pointer transition-colors"
                      style={{ borderBottom: "0.5px solid var(--border-subtle)" }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-active)")}
                      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                    >
                      <td className="px-3 py-2.5 text-xs font-medium" style={{ color: "var(--text-primary)" }}>
                        #{row.id}
                      </td>
                      <td className="px-3 py-2.5 text-xs" style={{ color: "var(--text-secondary)" }}>{date}</td>
                      <td className="px-3 py-2.5"><LabelBadge label={row.label} /></td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-primary)" }}>
                        {row.bac != null ? Number(row.bac).toFixed(2) : "—"}
                      </td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.mq3_1_max)}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.mq3_2_max)}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.mq3_3_max)}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.spatial_variance_max, 1)}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.temperature, 1)}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmt(row.humidity, 1)}</td>
                      <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center gap-1">
                          {/* Tag (pending only) or Relabel (labeled only) */}
                          {isPending ? (
                            <button
                              title="Attach label"
                              onClick={() => openLabelModal(row.id, false)}
                              className="p-1.5 rounded border transition-colors"
                              style={{ borderColor: "var(--near)", color: "var(--near)" }}
                            >
                              <Tag size={11} />
                            </button>
                          ) : (
                            <button
                              title="Relabel"
                              onClick={() => openRelabelModal(row.id, row.label)}
                              className="p-1.5 rounded border transition-colors hover:bg-(--bg-active)"
                              style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}
                            >
                              <Pencil size={11} />
                            </button>
                          )}

                          {/* Delete — two-click */}
                          {isDel ? (
                            <>
                              <button
                                title="Confirm delete"
                                onClick={() => confirmDelete(row.id)}
                                className="p-1.5 rounded border"
                                style={{ borderColor: "var(--pass)", color: "var(--pass)" }}
                              >
                                <Check size={11} />
                              </button>
                              <button
                                title="Cancel"
                                onClick={() => setDeleteConfirm(null)}
                                className="p-1.5 rounded border"
                                style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)" }}
                              >
                                <X size={11} />
                              </button>
                            </>
                          ) : (
                            <button
                              title="Delete row"
                              onClick={() => setDeleteConfirm(row.id)}
                              className="p-1.5 rounded border transition-colors"
                              style={{ borderColor: "var(--border-subtle)", color: "var(--over)" }}
                            >
                              <Trash2 size={11} />
                            </button>
                          )}

                          {/* Expand toggle */}
                          <button
                            title={isExpanded ? "Collapse" : "Expand"}
                            className="p-1.5 rounded border"
                            style={{ borderColor: "var(--border-subtle)", color: "var(--text-muted)" }}
                          >
                            {isExpanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                          </button>
                        </div>
                      </td>
                    </tr>,

                    // Expanded detail row
                    isExpanded && (
                      <tr key={`exp-${row.id}`} style={{ borderBottom: "0.5px solid var(--border-subtle)" }}>
                        <td colSpan={11} className="px-4 py-3" style={{ background: "var(--bg-active)" }}>
                          <div className="text-xs font-medium mb-2.5" style={{ color: "var(--text-muted)" }}>
                            Sensor breakdown — Row #{row.id}
                          </div>
                          <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
                            {[
                              ["MQ3-1 max",   fmt(row.mq3_1_max)],
                              ["MQ3-1 avg",   fmt(row.mq3_1_avg, 1)],
                              ["MQ3-1 std",   fmt(row.mq3_1_std, 2)],
                              ["MQ3-2 max",   fmt(row.mq3_2_max)],
                              ["MQ3-2 avg",   fmt(row.mq3_2_avg, 1)],
                              ["MQ3-2 std",   fmt(row.mq3_2_std, 2)],
                              ["MQ3-3 max",   fmt(row.mq3_3_max)],
                              ["MQ3-3 avg",   fmt(row.mq3_3_avg, 1)],
                              ["MQ3-3 std",   fmt(row.mq3_3_std, 2)],
                              ["Rise time",   fmt(row.rise_time,   2)],
                              ["Decay time",  fmt(row.decay_time,  2)],
                              ["SpVar max",   fmt(row.spatial_variance_max, 2)],
                              ["SpVar avg",   fmt(row.spatial_variance_avg, 2)],
                              ["Temp (°C)",   fmt(row.temperature, 1)],
                              ["Humidity (%)",fmt(row.humidity,    1)],
                            ].map(([label, val]) => (
                              <div
                                key={label}
                                className="rounded-lg p-2.5"
                                style={{ background: "var(--bg-card)", border: "0.5px solid var(--border-subtle)" }}
                              >
                                <div className="text-[10px] uppercase tracking-wide mb-1" style={{ color: "var(--text-muted)" }}>
                                  {label}
                                </div>
                                <div className="text-sm font-medium tabular-nums" style={{ color: "var(--text-primary)" }}>
                                  {val}
                                </div>
                              </div>
                            ))}
                          </div>
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

      {/* ── Label modal ── */}
      <Modal
        open={!!labelModal}
        onClose={() => setLabelModal(null)}
        title={`Attach label — Row #${labelModal?.id}`}
        icon={Tag}
        iconColor="var(--near)"
      >
        <div className="space-y-3">
          <div>
            <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>
              BAC reading (from BACtrack S80)
            </label>
            <input
              type="number"
              step="0.01"
              min="0"
              placeholder="e.g. 0.00 for sober, 0.08 for over limit"
              value={labelBac}
              onChange={(e) => setLabelBac(e.target.value)}
              className="w-full rounded-lg px-3 py-2 text-sm border"
              style={{
                background:  "var(--bg-active)",
                borderColor: "var(--border-subtle)",
                color:       "var(--text-primary)",
                outline:     "none",
              }}
            />
            <p className="text-xs mt-1.5" style={{ color: "var(--text-muted)" }}>
              Enter <strong>0.00</strong> for sober events. The system assigns the label based on BAC + sanitizer flag + sensor pattern.
            </p>
          </div>

          <div
            className="flex items-center gap-2.5 rounded-lg px-3 py-2.5"
            style={{ background: "var(--bg-active)" }}
          >
            <input
              type="checkbox"
              id="lm-san"
              checked={labelSanitizer}
              onChange={(e) => setLabelSanitizer(e.target.checked)}
              className="w-4 h-4 cursor-pointer shrink-0"
            />
            <label htmlFor="lm-san" className="text-sm cursor-pointer" style={{ color: "var(--text-secondary)" }}>
              Mark as sanitizer / perfume event
            </label>
          </div>

          <div className="flex justify-end gap-2 pt-1">
            <button
              onClick={() => setLabelModal(null)}
              className="px-3 py-1.5 rounded-lg text-sm border"
              style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}
            >
              Cancel
            </button>
            <button
              onClick={submitLabel}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"
              style={{ background: "var(--text-primary)", color: "var(--bg-card)" }}
            >
              <Check size={13} /> Apply label
            </button>
          </div>
        </div>
      </Modal>

      {/* ── Relabel modal ── */}
      <Modal
        open={!!relabelModal}
        onClose={() => setRelabelModal(null)}
        title={`Relabel — Row #${relabelModal?.id}`}
        icon={Pencil}
        iconColor="var(--text-secondary)"
      >
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-xs px-3 py-2 rounded-lg" style={{ background: "var(--bg-active)" }}>
            Current: <LabelBadge label={relabelModal?.currentLabel} />
          </div>
          <div>
            <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>New label</label>
            <select
              value={relabelValue}
              onChange={(e) => setRelabelValue(e.target.value)}
              className="w-full rounded-lg px-3 py-2 text-sm border"
              style={{
                background:  "var(--bg-active)",
                borderColor: "var(--border-subtle)",
                color:       "var(--text-primary)",
              }}
            >
              <option value="0">0 — No alcohol</option>
              <option value="1">1 — Breath alcohol</option>
              <option value="2">2 — Sanitizer</option>
            </select>
          </div>
          <div>
            <label className="block text-xs mb-1.5" style={{ color: "var(--text-muted)" }}>Reason (optional)</label>
            <textarea
              rows={2}
              placeholder="e.g. misread breathalyzer, forgot to flag sanitizer..."
              value={relabelReason}
              onChange={(e) => setRelabelReason(e.target.value)}
              className="w-full rounded-lg px-3 py-2 text-sm border resize-none"
              style={{
                background:  "var(--bg-active)",
                borderColor: "var(--border-subtle)",
                color:       "var(--text-primary)",
              }}
            />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button
              onClick={() => setRelabelModal(null)}
              className="px-3 py-1.5 rounded-lg text-sm border"
              style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}
            >
              Cancel
            </button>
            <button
              onClick={submitRelabel}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"
              style={{ background: "var(--text-primary)", color: "var(--bg-card)" }}
            >
              <Check size={13} /> Relabel
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}