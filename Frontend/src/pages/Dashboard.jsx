// File: Frontend/src/pages/Dashboard.jsx
import React, { useState, useEffect } from "react";
import MockBACChart from "../assets/graph";
import { Shield, LayoutDashboard, Terminal, RefreshCw } from "lucide-react";

const Dashboard = () => {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    // Dynamic background ticking to handle relative interval computations cleanly
    const interval = setInterval(() => {
      setNow(Date.now());
    }, 30000);

    return () => clearInterval(interval);
  }, []);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      
      {/* Dashboard Executive Header */}
      <div className="flex items-center justify-between bg-(--bg-card) border border-(--border-subtle) p-4 rounded-xl shadow-sm">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-400">
            <LayoutDashboard size={20} />
          </div>
          <div>
            <h1 className="text-lg font-bold" style={{ color: "var(--text-primary)" }}>
              AlcoDetect Access Telemetry Command Center
            </h1>
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              Multi-modal screening operations console checking biological features and spatial sensor variance
            </p>
          </div>
        </div>

        {/* Real-time operations status indicators */}
        <div className="hidden sm:flex items-center gap-4 text-xs font-mono">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-(--bg-active) border border-(--border-subtle)">
            <Terminal size={12} className="text-indigo-400" />
            <span style={{ color: "var(--text-secondary)" }}>Core Node:</span>
            <span className="text-emerald-400 font-semibold">Active</span>
          </div>
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-(--bg-active) border border-(--border-subtle)">
            <Shield size={12} className="text-amber-400" />
            <span style={{ color: "var(--text-secondary)" }}>Ensemble Mode:</span>
            <span style={{ color: "var(--text-primary)" }} className="font-semibold">Fusion</span>
          </div>
        </div>
      </div>

      {/* Primary Analytics Ingestion Panel */}
      <div className="rounded-xl border shadow-sm overflow-hidden" 
           style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}>
        <MockBACChart />
      </div>

    </div>
  );
};

export default Dashboard;