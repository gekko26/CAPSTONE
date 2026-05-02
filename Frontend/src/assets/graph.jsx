import { useBAC } from "../context/BAC_CONTEXT";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer,
} from "recharts";

const LIMIT = 0.08;

function getStatus(bac) {
  if (bac >= LIMIT) return { label: "Over limit", cls: "bg-red-100 text-red-700" };
  if (bac >= 0.06) return { label: "Near limit", cls: "bg-yellow-100 text-yellow-700" };
  return { label: "Pass", cls: "bg-green-100 text-green-700" };
}

function getDotColor(bac) {
  if (bac >= LIMIT) return "#c0392b";
  if (bac >= 0.06) return "#f39c12";
  return "#27ae60";
}

const CustomDot = (props) => {
  const { cx, cy, payload } = props;
  return <circle cx={cx} cy={cy} r={4} fill={getDotColor(payload.bac)} stroke="none" />;
};

export default function MockBACChart() {
  const { readings, running, setRunning } = useBAC();

  const recent = [...readings].reverse().slice(0, 5);
  const total = readings.length;
  const above = readings.filter((r) => r.bac >= LIMIT).length;
  const avg = total ? (readings.reduce((s, r) => s + r.bac, 0) / total).toFixed(3) : "0.000";
  const passRate = total ? (((total - above) / total) * 100).toFixed(1) + "%" : "—";

  return (
    <div className="p-4 space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">Total readings</p>
          <p className="text-2xl text-black font-medium">{total}</p>
        </div>
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">Above limit</p>
          <p className="text-2xl font-medium text-red-600">{above}</p>
        </div>
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">Avg BAC</p>
          <p className="text-2xl text-black font-medium">{avg}</p>
        </div>
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">Pass rate</p>
          <p className="text-2xl font-medium text-green-600">{passRate}</p>
        </div>
      </div>

      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-gray-700">Live BAC readings (mock)</span>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-1 rounded ${running ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
            {running ? "● Simulating" : "◼ Paused"}
          </span>
          <button
            onClick={() => setRunning((r) => !r)}
            className="text-xs px-3 py-1 border border-gray-300 rounded hover:bg-gray-100 transition"
          >
            {running ? "Pause" : "Resume"}
          </button>
        </div>
      </div>

      <div className="w-full h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={readings} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.06)" />
            <XAxis dataKey="time" tick={{ fontSize: 10 }} interval="preserveStartEnd" tickLine={false} />
            <YAxis domain={[0, 0.16]} tickFormatter={(v) => v.toFixed(3)} tick={{ fontSize: 10 }} tickLine={false} axisLine={false} width={48} />
            <Tooltip formatter={(v) => [v.toFixed(3), "BAC"]} contentStyle={{ fontSize: 12 }} />
            <ReferenceLine y={LIMIT} stroke="#c0392b" strokeDasharray="6 4" strokeWidth={1.5}
              label={{ value: "Limit 0.08", position: "right", fontSize: 10, fill: "#c0392b" }} />
            <Line type="monotone" dataKey="bac" stroke="#2980b9" strokeWidth={2}
              dot={<CustomDot />} activeDot={{ r: 5 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div>
        <p className="text-xs font-medium text-gray-500 mb-2">Recent detections</p>
        <div className="space-y-2">
          {recent.map((r) => {
            const s = getStatus(r.bac);
            return (
              <div key={r.id} className="flex items-center justify-between text-black bg-gray-50 rounded-lg px-3 py-2 text-sm">
                <span>ID-{r.id}</span>
                <span className="font-medium">BAC: {r.bac.toFixed(3)}</span>
                <span className={`text-xs px-2 py-0.5 rounded ${s.cls}`}>{s.label}</span>
                <span className="text-gray-400 text-xs">{r.time}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export function RecentDetections() {
  const { readings } = useBAC();
  const recent = [...readings].reverse().slice(0, 4);

  return (
    <div className="space-y-2">
      {recent.length === 0 ? (
        <p className="text-xs text-gray-400 text-center py-4">No readings yet</p>
      ) : (
        recent.map((r) => {
          const s = getStatus(r.bac);
          return (
            <div key={r.id} className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2 text-sm">
              <span>ID-{r.id}</span>
              <span className="font-medium">BAC: {r.bac.toFixed(3)}</span>
              <span className={`text-xs px-2 py-0.5 rounded ${s.cls}`}>{s.label}</span>
              <span className="text-gray-400 text-xs">{r.time}</span>
            </div>
          );
        })
      )}
    </div>
  );
}