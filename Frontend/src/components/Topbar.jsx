// Topbar.jsx
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
      case "/models":     return "Models";
      case "/about":      return "About";
      case "/report":     return "Report";
      default:            return "Page";
    }
  };

  const getDescription = () => {
    switch (location.pathname) {
      case "/":           return "Welcome — system is running";
      case "/dashboard":  return "Live sensor monitoring · Updated just now";
      case "/camera":     return "Live webcam feed · Detection active";
      case "/models":     return "3 models loaded and active";
      case "/about":      return "Team and technology overview";
      case "/report":     return "Reports and updates";
      default:            return "";
    }
  };

  return (
    <div
      className="flex p-4 rounded-xl justify-between items-center"
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
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg"
             style={{ background: "var(--bg-card-alt)", border: "1px solid var(--border)" }}>
          <SearchIcon size={14} style={{ color: "var(--text-muted)" }} />
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