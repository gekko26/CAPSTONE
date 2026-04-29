import React, { useState, useEffect } from "react";
import MockBACChart from "../assets/graph";

const Dashboard = () => {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const interval = setInterval(() => {
      setNow(Date.now());
    }, 60000);

    return () => clearInterval(interval);
  }, []);

  const getRelativeTime = (time) => {
    const diff = now - new Date(time).getTime();

    const seconds = diff / 1000;
    const minutes = diff / 60000;
    const hours = diff / 3600000;

    if (seconds < 60) return "just now";
    if (minutes < 60) return `${Math.floor(minutes)} minutes ago`;
    return `${Math.floor(hours)} hours ago`;
  };

  return (
    <div className="flex flex-col justify-evenly">
      <MockBACChart />
    </div>
  );
};

export default Dashboard;