import { BrowserRouter, Routes, Route } from "react-router-dom";
import { useState } from "react";

import Homes from "./pages/Home";
import Dashboard from "./pages/Dashboard";
import Camera from "./pages/Camera";
import Model from "./pages/Models";
import About from "./pages/About";
import Report from "./pages/Report";

import Sidebar from "./components/Sidebar";
import Topbar from "./components/Topbar";

import { ActivityIcon } from "lucide-react";

function App() {
  const [open, setOpen] = useState(false);

  return (
    <BrowserRouter>
      <div className="flex h-screen w-screen overflow-hidden">

        <div
          onClick={() => setOpen(!open)}
          className="w-1 h-50 bg-slate-800 hover:bg-slate-700 flex items-center justify-center cursor-pointer transition rounded-md"
        >
          <ActivityIcon className="w-3 h-6 text-red-500" />
        </div>

        <div
          className={`flex flex-col h-full bg-[#0f1a2e] border border-slate-700 rounded-md transition-all duration-300 overflow-hidden ${
            open ? "w-60" : "w-0"
          }`}
        >
          <div className="flex items-center justify-center gap-3 p-6">
            <ActivityIcon size={40} strokeWidth={1} color="red" />
            <div className="flex flex-col">
              <h2 className="font-bold text-white tracking-tighter text-2xl">
                AlcoDetect
              </h2>
              <span className="text-xs text-gray-300">
                Palahubog Detector
              </span>
            </div>
          </div>

          <Sidebar />
        </div>

        <div className="flex flex-col flex-1 overflow-hidden p-2 gap-2">
          <Topbar />

          <main className="flex-1 overflow-auto">
            <Routes>
              <Route path="/" element={<Homes />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/camera" element={<Camera />} />
              <Route path="/models" element={<Model />} />
              <Route path="/about" element={<About />} />
              <Route path="/report" element={<Report />} />
            </Routes>
          </main>
        </div>

      </div>
    </BrowserRouter>
  );
}

export default App;