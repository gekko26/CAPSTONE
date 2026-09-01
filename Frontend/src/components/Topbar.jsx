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
      case "/camera":     return "Camera";
      case "/deployment": return "Deployment Gate";
      case "/models":     return "Models";
      case "/about":      return "About";
      case "/report":     return "Reports";
      case "/training":   return "Training";
      default:            return "AlcoDetect";
    }
  };

  const getDescription = () => {
    switch (location.pathname) {
      case "/":           return "Overview of your alcohol detection system";
      case "/dashboard":  return "Live results from the deployment gate";
      case "/camera":     return "Live camera feed and face analysis";
      case "/deployment": return "Automatic access control checkpoint";
      case "/models":     return "Trained models and their real accuracy";
      case "/about":      return "About the project and the team";
      case "/report":     return "Scan history and monthly summaries";
      case "/training":   return "Collect data and train the models";
      default:            return "";
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