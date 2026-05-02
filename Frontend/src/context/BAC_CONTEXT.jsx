// BAC_CONTEXT.jsx

import { createContext, useContext, useEffect, useRef, useState } from "react";

const BACContext = createContext();

let idCounter = 4800;

function genBAC() {
  const r = Math.random();
  if (r < 0.65) return parseFloat((Math.random() * 0.03).toFixed(3));
  if (r < 0.85) return parseFloat((0.03 + Math.random() * 0.04).toFixed(3));
  if (r < 0.95) return parseFloat((0.07 + Math.random() * 0.01).toFixed(3));
  return parseFloat((0.08 + Math.random() * 0.06).toFixed(3));
}

export const BACProvider = ({ children }) => {
  const [readings, setReadings] = useState([]);
  const [running, setRunning] = useState(true);
  const intervalRef = useRef(null);

  useEffect(() => {
    const tick = () => {
      const bac = genBAC();
      const time = new Date().toLocaleTimeString("en-US", {
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
      });
      idCounter++;
      setReadings((prev) => {
        const next = [...prev, { id: idCounter, bac, time }];
        return next.length > 200 ? next.slice(-200) : next;
      });
    };

    tick();
    if (running) {
      intervalRef.current = setInterval(tick, 2000);
    }
    return () => clearInterval(intervalRef.current);
  }, [running]);

  return (
    <BACContext.Provider value={{ readings, setReadings, running, setRunning }}>
      {children}
    </BACContext.Provider>
  );
};

export const useBAC = () => {
  const context = useContext(BACContext);
  if (!context) throw new Error("useBAC must be used inside BACProvider");
  return context;
};