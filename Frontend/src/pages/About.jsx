// File: Frontend/src/pages/About.jsx
import picture from "../assets/pic.jpg";
import { Activity, Camera, Radar, Sparkles, AlertTriangle } from "lucide-react";
import { Card } from "../components/ui/primitives";

const TEAM = [
  { name: "Jayme, Nino Charles", role: "Full Stack & ML", img: picture },
  { name: "Gonzaga, Edrian P", role: "Backend & Database", img: picture },
  { name: "Cabahug, Jommel P", role: "Hardware & Firmware", img: picture },
];

const STACK = [
  { label: "Frontend", items: ["React", "Vite", "Tailwind CSS"] },
  { label: "Backend", items: ["Python · FastAPI", "OpenCV", "TensorFlow"] },
  { label: "Hardware", items: ["ESP32", "MQ-3 Sensors", "CCTV Camera"] },
  { label: "Connection", items: ["WiFi · HTTP"] },
];

const STEPS = [
  { icon: Camera, color: "var(--accent)", title: "Capture", desc: "Camera detects the subject's face" },
  { icon: Radar, color: "var(--near)", title: "Sense", desc: "ESP32 reads MQ-3 sensor output" },
  { icon: Sparkles, color: "var(--accent)", title: "Predict", desc: "ML models classify the result" },
  { icon: AlertTriangle, color: "var(--over)", title: "Decide", desc: "Pass / Near Limit / Over Limit" },
];

function About() {
  return (
    <div className="flex flex-col gap-4 h-full overflow-auto pr-1">

      {/* Hero */}
      <div
        className="rounded-xl p-8 flex items-center gap-8"
        style={{ background: "var(--bg-hero)" }}
      >
        <div
          className="w-20 h-20 rounded-xl flex items-center justify-center shrink-0"
          style={{ background: "rgba(255,255,255,0.08)" }}
        >
          <Activity size={44} strokeWidth={1.2} style={{ color: "var(--accent)" }} />
        </div>
        <div className="flex flex-col gap-2">
          <h2 className="text-3xl font-bold tracking-tight" style={{ color: "var(--text-on-dark)" }}>
            AlcoDetect System
          </h2>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            ESP32-based alcohol detection with ML-powered classification · Version 2.0
          </p>
          <div className="flex gap-2 mt-1">
            <span className="text-xs px-3 py-1 rounded-full" style={{ background: "rgba(255,255,255,0.08)", color: "var(--text-on-dark)" }}>
              Computer Engineering
            </span>
            <span className="text-xs px-3 py-1 rounded-full" style={{ background: "var(--accent)", color: "#fff" }}>
              Capstone Project
            </span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* Team */}
        <Card>
          <p className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>Development Team</p>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {TEAM.map((m) => (
              <div key={m.name} className="flex flex-col items-center text-center gap-2 p-3 rounded-lg" style={{ background: "var(--bg-card-alt)" }}>
                <img
                  src={m.img}
                  alt={m.name}
                  className="h-16 w-16 rounded-full object-cover"
                  style={{ border: "2px solid var(--border)" }}
                />
                <div>
                  <p className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>{m.name}</p>
                  <p className="text-[11px] mt-0.5" style={{ color: "var(--text-muted)" }}>{m.role}</p>
                </div>
              </div>
            ))}
          </div>
        </Card>

        {/* Tech stack */}
        <Card>
          <p className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>Tech Stack</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {STACK.map((s) => (
              <div key={s.label}>
                <p className="text-[11px] uppercase tracking-wider mb-2" style={{ color: "var(--text-muted)" }}>{s.label}</p>
                <ul className="space-y-1">
                  {s.items.map((it) => (
                    <li key={it} className="text-xs" style={{ color: "var(--text-secondary)" }}>{it}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* How it works */}
      <Card>
        <p className="text-sm font-semibold mb-4" style={{ color: "var(--text-primary)" }}>How it works</p>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-6">
          {STEPS.map((s, i) => (
            <div key={s.title} className="flex flex-col items-start gap-2">
              <div className="relative">
                <div className="h-11 w-11 rounded-full flex items-center justify-center" style={{ background: "var(--bg-card-alt)" }}>
                  <s.icon size={20} style={{ color: s.color }} />
                </div>
                <span
                  className="absolute -top-1 -left-1 h-5 w-5 rounded-full text-[10px] font-bold flex items-center justify-center"
                  style={{ background: "var(--accent)", color: "#fff" }}
                >
                  {i + 1}
                </span>
              </div>
              <p className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>{s.title}</p>
              <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>{s.desc}</p>
            </div>
          ))}
        </div>
      </Card>

    </div>
  );
}

export default About;
