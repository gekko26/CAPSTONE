import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import Homes from "./pages/Home";
import Dashboard from "./pages/Dashboard";
import Camera from "./pages/Camera";
import Deployment from "./pages/Deployment";
import Model from "./pages/Models";
import About from "./pages/About";
import Report from "./pages/Report";
import Training from "./pages/Training";

// ALCOGATE terminal pages (new IA) — re-use existing logic initially
import LiveFlow from "./pages/LiveFlow";
import Events from "./pages/Events";
import People from "./pages/People";
import Insights from "./pages/Insights";
import Settings from "./pages/Settings";

import AlcoGateHeader from "./components/AlcoGateHeader";

const SHOW_TRAINING = import.meta.env.VITE_SHOW_TRAINING !== "false";

function App() {
  return (
    <BrowserRouter>
      <div className="flex flex-col h-screen w-screen overflow-hidden" style={{ background: "var(--bg-page)" }}>
        <AlcoGateHeader />

        <main className="flex-1 overflow-auto">
          <Routes>
            {/* ── ALCOGATE primary IA (reference image) ── */}
            <Route path="/" element={<LiveFlow />} />
            <Route path="/live-flow" element={<Navigate to="/" replace />} />
            <Route path="/events" element={<Events />} />
            <Route path="/people" element={<People />} />
            <Route path="/insights" element={<Insights />} />
            <Route path="/settings" element={<Settings />} />

            {/* ── Legacy routes — keep working (preserve functionality) ── */}
            <Route path="/home" element={<Homes />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/camera" element={<Camera />} />
            <Route path="/deployment" element={<Deployment />} />
            <Route path="/models" element={<Model />} />
            <Route path="/about" element={<About />} />
            <Route path="/report" element={<Report />} />

            {/* ── Training — preserved, hideable via env ── */}
            {SHOW_TRAINING ? (
              <Route path="/training" element={<Training />} />
            ) : (
              <Route path="/training" element={<Navigate to="/" replace />} />
            )}

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
