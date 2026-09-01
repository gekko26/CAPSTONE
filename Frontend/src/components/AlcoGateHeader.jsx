// AlcoGateHeader.jsx — Terminal header: logo | nav | clock/LIVE
import { NavLink } from "react-router-dom";
import { Activity, Settings, Bell, Sun, Moon, Maximize2 } from "lucide-react";
import { useTheme } from "../context/THEME_CONTEXT";
import { useDisplay } from "../context/DISPLAY_CONTEXT";
import { useState, useEffect } from "react";

const SHOW_TRAINING = import.meta.env.VITE_SHOW_TRAINING !== "false";

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const time = now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true });
  return <span className="text-[11px] font-mono tabular-nums" style={{ color: "var(--text-secondary)" }}>{time}</span>;
}

function NavItem({ to, label, end = false }) {
  return (
    <NavLink
      to={to}
      end={end}
      className="px-2.5 py-1 text-[11px] font-medium tracking-wide uppercase rounded-[4px] transition-colors"
      style={({ isActive }) => ({
        color: isActive ? "var(--pass)" : "var(--text-muted)",
        background: isActive ? "var(--bg-active)" : "transparent",
        border: isActive ? "1px solid var(--border)" : "1px solid transparent",
      })}
    >
      {label}
    </NavLink>
  );
}

export default function AlcoGateHeader() {
  const { theme, setTheme } = useTheme();
  const { scale, cycle, SCALES } = useDisplay();

  return (
    <header
      className="h-[36px] flex items-center justify-between px-3 shrink-0"
      style={{ background: "var(--bg-card)", borderBottom: "1px solid var(--border-subtle)" }}
    >
      {/* Left: brand */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-[4px] flex items-center justify-center" style={{ background: "var(--bg-active)", border: "1px solid var(--border)" }}>
            <Activity size={13} strokeWidth={2} style={{ color: "var(--accent)" }} />
          </div>
          <div className="flex flex-col leading-none">
            <span className="text-[11px] font-bold tracking-[0.12em]" style={{ color: "var(--text-primary)" }}>ALCOGATE</span>
            <span className="text-[7px] tracking-[0.08em] uppercase" style={{ color: "var(--text-muted)" }}>AI GATE MONITOR SYSTEM</span>
          </div>
        </div>

        <div className="hidden lg:flex items-center gap-1 ml-4">
          <NavItem to="/" label="Live Flow" end />
          <NavItem to="/events" label="Events" />
          <NavItem to="/people" label="People" />
          <NavItem to="/insights" label="Insights" />
          {SHOW_TRAINING && <NavItem to="/training" label="Training" />}
        </div>
      </div>

      {/* Center: mobile nav fallback */}
      <div className="flex lg:hidden items-center gap-1">
        <NavItem to="/" label="Live" end />
        <NavItem to="/events" label="Events" />
        <NavItem to="/people" label="People" />
      </div>

      {/* Right: clock + system */}
      <div className="flex items-center gap-2.5">
        <Clock />
        <span className="flex items-center gap-1 text-[10px] font-medium">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          <span style={{ color: "var(--pass)" }}>LIVE</span>
        </span>

        <div className="w-px h-4 mx-1" style={{ background: "var(--border-subtle)" }} />

        <button
          onClick={cycle}
          className="px-1.5 h-6 rounded-[4px] flex items-center gap-1 text-[10px] font-mono font-bold border transition-colors"
          style={{ color: "var(--text-secondary)", borderColor: "var(--border-subtle)", background: "transparent" }}
          onMouseEnter={e => e.currentTarget.style.background = "var(--bg-active)"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}
          title={`Display size: ${scale.toUpperCase()} — click to cycle S/M/L`}
        >
          <Maximize2 size={10} />
          {SCALES[scale].label}
        </button>

        <button
          onClick={() => setTheme(!theme)}
          className="w-6 h-6 rounded-[4px] flex items-center justify-center transition-colors"
          style={{ color: "var(--text-muted)", border: "1px solid transparent" }}
          onMouseEnter={e => e.currentTarget.style.background = "var(--bg-active)"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}
          title="Toggle theme"
        >
          {theme ? <Moon size={11} /> : <Sun size={11} />}
        </button>

        <NavLink
          to="/settings"
          className="w-6 h-6 rounded-[4px] flex items-center justify-center"
          style={({ isActive }) => ({
            color: isActive ? "var(--pass)" : "var(--text-muted)",
            background: isActive ? "var(--bg-active)" : "transparent",
          })}
        >
          <Settings size={11} />
        </NavLink>

        <button
          className="w-6 h-6 rounded-[4px] flex items-center justify-center"
          style={{ color: "var(--text-muted)" }}
          onMouseEnter={e => e.currentTarget.style.background = "var(--bg-active)"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}
        >
          <Bell size={11} />
        </button>
      </div>
    </header>
  );
}
