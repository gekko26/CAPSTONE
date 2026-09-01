// DISPLAY_CONTEXT.jsx — interactive display sizing (S/M/L) for all terminals
import { createContext, useContext, useState, useEffect } from "react";

const DisplayContext = createContext();

const SCALES = {
  sm: { label: "S", font: "12px", cls: "scale-sm" },
  md: { label: "M", font: "13px", cls: "scale-md" },
  lg: { label: "L", font: "14px", cls: "scale-lg" },
};

export function DisplayProvider({ children }) {
  const [scale, setScale] = useState(() => localStorage.getItem("alcogate-scale") || "md");

  useEffect(() => {
    localStorage.setItem("alcogate-scale", scale);
    const root = document.documentElement;
    root.classList.remove("scale-sm", "scale-md", "scale-lg");
    root.classList.add(SCALES[scale].cls);
    root.style.fontSize = SCALES[scale].font;
  }, [scale]);

  const cycle = () => setScale((s) => (s === "sm" ? "md" : s === "md" ? "lg" : "sm"));

  return (
    <DisplayContext.Provider value={{ scale, setScale, cycle, SCALES }}>
      {children}
    </DisplayContext.Provider>
  );
}

export function useDisplay() {
  return useContext(DisplayContext);
}
