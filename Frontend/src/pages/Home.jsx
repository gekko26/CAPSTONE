// Home.jsx
import { useBAC } from "../context/BAC_CONTEXT";
import { CameraIcon, BarChart3, Cpu, InfoIcon } from "lucide-react";
import { Link } from "react-router-dom";

function Homes() {
  const LIMIT = 0.08;
  const { readings } = useBAC();
  const total = readings.length;
  const above = readings.filter((r) => r.bac >= LIMIT).length;
  const passRate = total ? (((total - above) / total) * 100).toFixed(1) + "%" : "-";

  const navCard = (to, Icon, label, sub) => (
    <Link
      to={to}
      className="flex justify-center items-center gap-5 rounded-2xl w-1/4 transition-all duration-150"
      style={{
        background: "var(--bg-card)",
        border: "1px solid var(--border)",
        color: "var(--text-primary)",
        padding: "1.25rem",
      }}
      onMouseEnter={e => e.currentTarget.style.borderColor = "var(--accent)"}
      onMouseLeave={e => e.currentTarget.style.borderColor = "var(--border)"}
    >
      <Icon size={40} strokeWidth={1.5} style={{ color: "var(--accent)" }} />
      <div className="flex flex-col">
        <p className="text-lg font-medium tracking-wide" style={{ color: "var(--text-primary)" }}>{label}</p>
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>{sub}</p>
      </div>
    </Link>
  );

  const statCard = (title, badge, value, valueColor, sub) => (
    <div
      className="rounded-2xl w-1/3"
      style={{ background: "var(--bg-card)", border: "1px solid var(--border)", padding: "1.25rem 1.5rem" }}
    >
      <div className="flex justify-between items-center mb-4">
        <h3 className="font-medium text-base" style={{ color: "var(--text-primary)" }}>{title}</h3>
        <span
          className="text-xs px-3 py-1 rounded-full font-medium"
          style={{ background: "var(--bg-active)", color: "var(--pass)", border: "1px solid var(--border)" }}
        >
          {badge}
        </span>
      </div>
      <p className="text-6xl font-semibold mb-1" style={{ color: valueColor || "var(--text-primary)" }}>
        {value}
      </p>
      <p className="text-sm" style={{ color: "var(--text-muted)" }}>{sub}</p>
    </div>
  );

  return (
    <div className="flex flex-col flex-1 overflow-auto h-full gap-3 p-1">

      {/* Hero */}
      <div
        className="flex flex-col rounded-2xl p-16 justify-center gap-7"
        style={{ background: "var(--bg-hero)", minHeight: "50%" }}
      >
        <div>
          <span
            className="text-xs font-medium px-3 py-1.5 rounded-md tracking-widest uppercase"
            style={{ background: "var(--pass-bg)", color: "var(--pass)", border: "1px solid var(--accent)" }}
          >
            ● System Active
          </span>
        </div>

        <h2 className="font-semibold text-5xl leading-tight" style={{ color: "var(--text-on-dark)" }}>
          Alcohol Detection<br />System v2.0
        </h2>

        <p className="text-lg" style={{ color: "var(--text-muted)", maxWidth: "560px" }}>
          Real-time BAC monitoring using ESP32 sensor fusion and machine learning models for accurate, fast detection.
        </p>

        <div className="flex gap-4">
          <Link to="/camera">
            <span
              className="inline-block text-center text-base px-6 py-2.5 rounded-lg font-medium transition-all"
              style={{ background: "var(--accent)", color: "#fff", cursor: "pointer" }}
            >
              Open Camera
            </span>
          </Link>
          <Link to="/dashboard">
            <span
              className="inline-block text-center text-base px-6 py-2.5 rounded-lg font-medium transition-all"
              style={{
                background: "transparent",
                color: "var(--text-secondary)",
                border: "1px solid var(--border)",
                cursor: "pointer",
              }}
            >
              View Dashboard
            </span>
          </Link>
        </div>
      </div>

      {/* Nav cards */}
      <div className="flex gap-3 w-full" style={{ minHeight: "120px" }}>
        {navCard("/camera",    CameraIcon, "Live Camera",  "Start Detection")}
        {navCard("/dashboard", BarChart3,  "Dashboard",    "Live analytics")}
        {navCard("/models",    Cpu,        "ML models",    "3 models loaded")}
        {navCard("/about",     InfoIcon,   "About us",     "Team & tech stack")}
      </div>

      {/* Stat cards */}
      <div className="flex gap-3 w-full" style={{ minHeight: "140px" }}>
        {statCard("Today's readings", "Live", total, "var(--text-primary)", `▲ ${total} from yesterday`)}
        {statCard("Pass Rate", "Today", passRate, "var(--pass)", `${total - above} passed · ${above} flagged`)}
        {statCard("Avg response", "System", "1.2s", "var(--text-primary)", "Detection Latency")}
      </div>

    </div>
  );
}

export default Homes;