// theme.js — shared theme context object + hook (component-only rule keeps provider in THEME_CONTEXT.jsx)
import { createContext, useContext } from "react";

export const ThemeContext = createContext();

export function useTheme() {
  return useContext(ThemeContext);
}
