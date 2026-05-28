// File: Frontend/src/components/Searchbar.jsx
import React, { useState, useEffect, useRef } from "react";
import { useBAC } from "../context/BAC_CONTEXT";
import { ShieldAlert, ShieldCheck, Eye, Zap, CornerDownLeft } from "lucide-react";

export default function Searchbar() {
  const { readings } = useBAC(); // Extract complete shared dataset array
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const dropdownRef = useRef(null);

  // Close dropdown if developer clicks outside the lookup hit-box
  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Live filter engine parsing historical inputs matching data models
  useEffect(() => {
    if (!query.trim() || !readings) {
      setResults([]);
      return;
    }

    const cleanQuery = query.toLowerCase().replace("#", "").trim();

    const filtered = readings.filter((row) => {
      const matchId = String(row.id).includes(cleanQuery);
      const matchLabel = row.label ? row.label.toLowerCase().includes(cleanQuery) : false;
      const matchType = row.type ? row.type.toLowerCase().includes(cleanQuery) : false;
      
      return matchId || matchLabel || matchType;
    });

    setResults(filtered.slice(0, 5)); // Limit dropdown rendering container layout payload to 5 rows
  }, [query, readings]);

  const handleSelectResult = (id) => {
    setShowDropdown(false);
    setQuery("");
    
    // Smooth scrolling mechanism automatically scrolls your data tables straight to the targeted row
    const targetElement = document.getElementById(`row-${id}`);
    if (targetElement) {
      targetElement.scrollIntoView({ behavior: "smooth", block: "center" });
      targetElement.style.background = "var(--bg-active)";
      setTimeout(() => {
        targetElement.style.background = "transparent";
      }, 1500);
    }
  };

  const getLogIcon = (label) => {
    if (label?.toLowerCase().includes("over")) return <ShieldAlert size={12} className="text-(--over)" />;
    if (label?.toLowerCase().includes("filtered") || label?.toLowerCase().includes("sanitizer")) return <Zap size={12} className="text-indigo-400" />;
    return <ShieldCheck size={12} className="text-(--pass)" />;
  };

  return (
    <div className="w-full" ref={dropdownRef}>
      <input
        type="text"
        placeholder="Search logs by ID, status..."
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setShowDropdown(true);
        }}
        onFocus={() => setShowDropdown(true)}
        className="w-full bg-transparent text-xs outline-none border-none font-medium"
        style={{ color: "var(--text-primary)" }}
      />

      {/* Floating Operational Results Container */}
      {showDropdown && results.length > 0 && (
        <div 
          className="absolute left-0 right-0 mt-3 rounded-xl border p-2 shadow-xl flex flex-col gap-1 w-65"
          style={{ 
            background: "var(--bg-card)", 
            borderColor: "var(--border-subtle)",
            boxShadow: "0 10px 25px -5px rgba(0,0,0,0.3)"
          }}
        >
          <div className="text-[9px] uppercase font-bold tracking-wider px-2 py-1" style={{ color: "var(--text-muted)" }}>
            Matching Telemetry Nodes ({results.length})
          </div>
          
          {results.map((item) => (
            <button
              key={item.id}
              onClick={() => handleSelectResult(item.id)}
              className="flex items-center justify-between text-left w-full px-2 py-1.5 rounded-lg text-xs transition-colors hover:bg-(--bg-active) group"
            >
              <div className="flex items-center gap-2 min-w-0">
                {getLogIcon(item.label || "Pass")}
                <span className="font-mono text-[11px] font-bold" style={{ color: "var(--text-secondary)" }}>
                  #{item.id}
                </span>
                <span className="truncate max-w-30 font-medium" style={{ color: "var(--text-primary)" }}>
                  {item.label || "Pass"}
                </span>
              </div>
              
              <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                <span className="text-[9px] font-mono" style={{ color: "var(--text-muted)" }}>Go to</span>
                <CornerDownLeft size={10} style={{ color: "var(--text-muted)" }} />
              </div>
            </button>
          ))}
        </div>
      )}

      {/* Empty State Guard Notification */}
      {showDropdown && query.trim() && results.length === 0 && (
        <div 
          className="absolute left-0 right-0 mt-3 rounded-xl border p-3 text-center text-xs w-65"
          style={{ background: "var(--bg-card)", borderColor: "var(--border-subtle)" }}
        >
          <p style={{ color: "var(--text-muted)" }} className="italic">No deployment metrics match query.</p>
        </div>
      )}
    </div>
  );
}