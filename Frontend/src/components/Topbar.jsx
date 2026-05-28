// File: Frontend/src/components/Topbar.jsx
import { useLocation } from "react-router-dom";
import { SearchIcon, Sun, Moon } from "lucide-react";
import Searchbar from "./Searchbar";
import { useTheme } from "../context/THEME_CONTEXT";

function Topbar() {
  const location = useLocation();
  const { theme, setTheme } = useTheme();

  const getTitle = () => {
    switch (location.pathname) {
      case "/":           return "Home";
      case "/dashboard":  return "Dashboard";
      case "/camera":     return "Camera Live Stream";
      case "/models":     return "Model Topologies";
      case "/about":      return "About Project";
      case "/report":     return "Audit Logs & Reports";
      default:            return "Console Page";
    }
  };

  const getDescription = () => {
    switch (location.pathname) {
      case "/":           return "Welcome — core services initialized";
      case "/dashboard":  return "Live decision fusion monitoring stream";
      case "/camera":     return "Proximity gating array stream active";
      case "/models":     return "4 machine learning nodes loaded and operational";
      case "/about":      return "System overview architecture definitions";
      case "/report":     return "Compiled institutional compliance tracking logs";
      default:            return "Telemetry service active";
    }
  };

  return (
    <div
      className="flex p-4 rounded-xl justify-between items-center relative z-30"
      style={{
        background: "var(--topbar-bg)",
        border: "1px solid var(--topbar-border)",
      }}
    >
      <div className="flex flex-col gap-0.5">
        <h1 className="text-xl font-semibold" style={{ color: "var(--text-primary)" }}>
          {getTitle()}
        </h1>
        <p className="text-sm tracking-wide" style={{ color: "var(--text-muted)" }}>
          {getDescription()}
        </p>
      </div>

      <div className="flex items-center gap-6">
        {/* Container style handles inner inputs neatly */}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg relative"
             style={{ background: "var(--bg-card-alt)", border: "1px solid var(--border)", width: "260px" }}>
          <SearchIcon size={14} style={{ color: "var(--text-muted)" }} className="shrink-0" />
          <Searchbar />
        </div>

        <button
          onClick={() => setTheme(!theme)}
          className="flex items-center justify-center w-8 h-8 rounded-lg transition-all"
          style={{
            background: "var(--bg-card-alt)",
            border: "1px solid var(--border)",
            color: "var(--text-muted)",
            cursor: "pointer",
          }}
        >
          {theme ? <Moon size={15} /> : <Sun size={15} />}
        </button>
      </div>
    </div>
  );
}

export default Topbar;