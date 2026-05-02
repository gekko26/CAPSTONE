import { useBAC } from "../context/BAC_CONTEXT";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from "recharts";

const LIMIT = 0.08;

const WEEKLY = [
  { day: "Mon", pass: 40, fail: 2 },
  { day: "Tue", pass: 55, fail: 3 },
  { day: "Wed", pass: 68, fail: 3 },
  { day: "Thu", pass: 46, fail: 3 },
  { day: "Fri", pass: 79, fail: 4 },
  { day: "Sat", pass: 88, fail: 7 },
  { day: "Sun", pass: 35, fail: 2 },
];

const PAST_REPORTS = [
  { id: "RPT-001", date: "May 1, 2026",  readings: 284, flagged: 9,  passRate: "96.8%" },
  { id: "RPT-002", date: "Apr 30, 2026", readings: 310, flagged: 11, passRate: "96.5%" },
  { id: "RPT-003", date: "Apr 29, 2026", readings: 258, flagged: 7,  passRate: "97.3%" },
  { id: "RPT-004", date: "Apr 28, 2026", readings: 195, flagged: 4,  passRate: "97.9%" },
  { id: "RPT-005", date: "Apr 27, 2026", readings: 220, flagged: 8,  passRate: "96.4%" },
];

function getStatus(bac) {
  if (bac >= LIMIT) return { label: "Over limit", cls: "bg-red-100 text-red-700" };
  if (bac >= 0.06)  return { label: "Near limit", cls: "bg-yellow-100 text-yellow-700" };
  return { label: "Pass", cls: "bg-green-100 text-green-700" };
}

function Report() {
  const { readings } = useBAC();

  // live stats from context
  const total    = readings.length;
  const above    = readings.filter((r) => r.bac >= LIMIT).length;
  const avg      = total ? (readings.reduce((s, r) => s + r.bac, 0) / total).toFixed(3) : "0.000";
  const passRate = total ? (((total - above) / total) * 100).toFixed(1) + "%" : "—";

  // BAC distribution from context
  const buckets = [
    { label: "0.00",      color: "bg-green-500",  count: readings.filter((r) => r.bac < 0.01).length },
    { label: "0.01–0.04", color: "bg-green-300",  count: readings.filter((r) => r.bac >= 0.01 && r.bac < 0.05).length },
    { label: "0.05–0.07", color: "bg-yellow-400", count: readings.filter((r) => r.bac >= 0.05 && r.bac < 0.08).length },
    { label: "0.08+",     color: "bg-red-500",    count: readings.filter((r) => r.bac >= 0.08).length },
  ];

  const recentLog = [...readings].reverse().slice(0, 8);

  return (
    <div className="p-4 space-y-4">

      {/* Live summary — same card style as dashboard */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">Total readings</p>
          <p className="text-2xl font-medium text-black">{total}</p>
          <p className="text-xs text-green-600 mt-1">Live count</p>
        </div>
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">Flagged</p>
          <p className="text-2xl font-medium text-red-600">{above}</p>
          <p className="text-xs text-gray-400 mt-1">Above 0.08</p>
        </div>
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">Avg BAC</p>
          <p className="text-2xl font-medium text-black">{avg}</p>
          <p className="text-xs text-gray-400 mt-1">Session average</p>
        </div>
        <div className="bg-gray-100 rounded-lg p-4">
          <p className="text-xs text-gray-500 mb-1">Pass rate</p>
          <p className="text-2xl font-medium text-green-600">{passRate}</p>
          <p className="text-xs text-gray-400 mt-1">This session</p>
        </div>
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-5 gap-4">

        {/* Weekly bar chart — 3 cols */}
        <div className="col-span-3 bg-white border border-black/6 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-medium text-gray-700">Weekly readings</span>
            <div className="flex items-center gap-3 text-[10px] text-gray-400">
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-sm bg-green-500 inline-block" />Pass
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-sm bg-red-400 inline-block" />Flagged
              </span>
            </div>
          </div>
          <div className="w-full h-52">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={WEEKLY} margin={{ top: 4, right: 8, left: 0, bottom: 0 }} barSize={14}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.06)" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fontSize: 10 }} tickLine={false} axisLine={false} width={28} />
                <Tooltip
                  contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e2e8f0" }}
                  formatter={(v, name) => [v, name === "pass" ? "Pass" : "Flagged"]}
                />
                <Bar dataKey="pass" stackId="a" fill="#27ae60" />
                <Bar dataKey="fail" stackId="a" fill="#e74c3c" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* BAC distribution — 2 cols */}
        <div className="col-span-2 bg-white border border-black/6 rounded-xl p-4">
          <p className="text-sm font-medium text-gray-700 mb-3">BAC distribution</p>
          <div className="space-y-3">
            {buckets.map((b) => {
              const pct = total > 0 ? (b.count / total) * 100 : 0;
              return (
                <div key={b.label} className="flex items-center gap-3">
                  <span className="text-xs text-gray-500 w-20 shrink-0">{b.label}</span>
                  <div className="flex-1 bg-gray-100 rounded-full h-2 overflow-hidden">
                    <div className={`h-full rounded-full ${b.color} transition-all duration-500`}
                      style={{ width: `${pct}%` }} />
                  </div>
                  <span className="text-xs text-gray-500 w-6 text-right">{b.count}</span>
                </div>
              );
            })}
          </div>

          <div className="mt-4 pt-4 border-t border-gray-100 grid grid-cols-2 gap-3">
            <div className="bg-green-50 rounded-lg p-3 text-center">
              <p className="text-lg font-semibold text-green-700">{total - above}</p>
              <p className="text-[10px] text-green-600">Passed</p>
            </div>
            <div className="bg-red-50 rounded-lg p-3 text-center">
              <p className="text-lg font-semibold text-red-700">{above}</p>
              <p className="text-[10px] text-red-600">Flagged</p>
            </div>
          </div>
        </div>
      </div>

      {/* Bottom row */}
      <div className="grid grid-cols-2 gap-4">

        {/* Live detection log */}
        <div className="bg-white border border-black/6 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <p className="text-sm font-medium text-gray-700">Recent detections</p>
            <span className="text-xs px-2 py-1 rounded bg-green-100 text-green-700">● Live</span>
          </div>
          <div className="space-y-2">
            {recentLog.length === 0 ? (
              <p className="text-xs text-gray-400 text-center py-8">No readings yet</p>
            ) : (
              recentLog.map((r, i) => {
                const s = getStatus(r.bac);
                return (
                  <div key={i} className="flex items-center justify-between bg-gray-50 rounded-lg px-3 py-2 text-sm">
                    <span className="text-gray-600 text-xs">{r.time}</span>
                    <span className="font-medium text-black">BAC: {r.bac.toFixed(3)}</span>
                    <span className={`text-xs px-2 py-0.5 rounded ${s.cls}`}>{s.label}</span>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Past reports */}
        <div className="bg-white border border-black/6 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <p className="text-sm font-medium text-gray-700">Past reports</p>
            <button className="text-xs text-teal-600 hover:text-teal-700 font-medium">Export CSV</button>
          </div>
          <div className="space-y-2">
            {PAST_REPORTS.map((r) => (
              <div key={r.id} className="flex items-center gap-3 bg-gray-50 rounded-lg px-3 py-2.5 text-xs">
                <span className="font-medium text-gray-700 w-16 shrink-0">{r.id}</span>
                <span className="flex-1 text-gray-500">{r.date}</span>
                <span className="text-gray-600">{r.readings} readings</span>
                <span className="text-red-500 w-14 text-center">{r.flagged} flagged</span>
                <span className="text-green-600 font-medium w-12 text-right">{r.passRate}</span>
              </div>
            ))}
          </div>
          <button className="mt-3 w-full py-2 rounded-lg border border-black/8 text-xs text-gray-500 hover:bg-gray-50 transition-colors">
            View all reports
          </button>
        </div>
      </div>
    </div>
  );
}

export default Report;