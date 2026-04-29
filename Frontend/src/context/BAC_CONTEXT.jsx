//BAC_CONTEXT.jsx

import { createContext, useContext, useState } from "react";

const BACContext = createContext();

export const BACProvider = ({ children }) => {
  const [readings, setReadings] = useState([]);

  return (
    <BACContext.Provider value={{ readings, setReadings }}>
      {children}
    </BACContext.Provider>
  );
};

export const useBAC = () => {
  const context = useContext(BACContext);
  if (!context) {
    throw new Error("useBAC must be used inside BACProvider");
  }
  return context;
};