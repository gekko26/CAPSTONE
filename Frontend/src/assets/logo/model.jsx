import React from "react";

export const ModelIcon = (props) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    {...props}
  >
    {/* Center node */}
    <circle cx="12" cy="12" r="3" />

    {/* Outer nodes */}
    <circle cx="4" cy="12" r="2" />
    <circle cx="20" cy="12" r="2" />
    <circle cx="12" cy="4" r="2" />
    <circle cx="12" cy="20" r="2" />

    {/* Connections */}
    <line x1="6" y1="12" x2="9" y2="12" />
    <line x1="15" y1="12" x2="18" y2="12" />
    <line x1="12" y1="6" x2="12" y2="9" />
    <line x1="12" y1="15" x2="12" y2="18" />
  </svg>
);
