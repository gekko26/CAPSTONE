// display.js — shared display context object + hook + scale table (provider in DISPLAY_CONTEXT.jsx)
import { createContext, useContext } from "react";

export const SCALES = {
  sm: { label: "S", font: "12px", cls: "scale-sm" },
  md: { label: "M", font: "13px", cls: "scale-md" },
  lg: { label: "L", font: "14px", cls: "scale-lg" },
};

export const DisplayContext = createContext();

export function useDisplay() {
  return useContext(DisplayContext);
}
