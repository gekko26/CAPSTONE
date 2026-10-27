// DISPLAY_CONTEXT.jsx — provider only; context object + hook live in ./display
import { useState, useEffect } from "react";
import { SCALES, DisplayContext } from "./display";

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
