// Sidebar.jsx
import { Link, useLocation } from "react-router-dom";
import {
  HomeIcon, LayoutDashboard, CameraIcon,
  Cpu, InfoIcon, Activity, BarChart3
} from "lucide-react";

function Sidebar() {
  const location = useLocation();

  const navItem = (to, Icon, label) => {
    const active = location.pathname === to;
    return (
      <Link
        to={to}
        className="flex items-center gap-3 pl-5 pr-3 py-2 text-sm tracking-wide rounded-lg mx-2 transition-all duration-150 group"
        style={{
          background: active ? "var(--bg-active)" : "transparent",
          color: active ? "var(--pass)" : "var(--text-muted)",
          borderRight: active ? "2px solid var(--pass)" : "2px solid transparent",
        }}
        onMouseEnter={e => {
          if (!active) e.currentTarget.style.background = "var(--border-subtle)";
        }}
        onMouseLeave={e => {
          if (!active) e.currentTarget.style.background = "transparent";
        }}
      >
        <Icon size={16} strokeWidth={1.8} />
        <span style={{ color: active ? "var(--pass)" : "var(--text-secondary)" }}>
          {label}
        </span>
      </Link>
    );
  };

  const sectionLabel = (label) => (
    <p className="pl-5 pt-2 pb-1 text-xs tracking-widest uppercase"
       style={{ color: "var(--text-muted)", opacity: 0.7 }}>
      {label}
    </p>
  );

  return (
    <div className="flex flex-col gap-1 w-60 shrink-0 flex-1 rounded-md pb-4  "
         style={{ background: "var(--bg-sidebar)"}}>

      {sectionLabel("Main")}
      {navItem("/", HomeIcon, "Home")}
      {navItem("/dashboard", BarChart3, "Dashboard")}

      {sectionLabel("Analysis")}
      {navItem("/camera", CameraIcon, "Camera")}
      {navItem("/models", Cpu, "Models")}

      {sectionLabel("System")}
      {navItem("/report", Activity, "Report")}
      {navItem("/about", InfoIcon, "About Us")}
    </div>
  );
}

export default Sidebar;