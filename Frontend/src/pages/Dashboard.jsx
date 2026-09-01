// File: Frontend/src/pages/Dashboard.jsx
import MockBACChart from "../assets/graph";

const Dashboard = () => {
  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div>
        <h1 className="text-lg font-bold" style={{ color: "var(--text-primary)" }}>
          Dashboard
        </h1>
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Live results from the deployment gate
        </p>
      </div>

      <div
        className="rounded-xl overflow-hidden"
        style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}
      >
        <MockBACChart />
      </div>
    </div>
  );
};

export default Dashboard;
