// THEME_CONTEXT.jsx — provider only; context object + hook live in ./theme
import { useState, useEffect } from "react";
import { ThemeContext } from "./theme";

export function ThemeProvider({ children }) {
  // true = dark, false = light
  const [theme, setTheme] = useState(() => {
    const stored = localStorage.getItem("alcodetect-theme");
    if (stored) return stored === "dark";
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme) {
      root.classList.add("dark");
      localStorage.setItem("alcodetect-theme", "dark");
    } else {
      root.classList.remove("dark");
      localStorage.setItem("alcodetect-theme", "light");
    }
  }, [theme]);

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
