import {RecentDetections} from "../assets/graph";

function Camera() {
  return (
    <div className="space-y-4">

      {/* Camera feed */}
      <div className="grid grid-cols-5 gap-4">

        {/* Feed — 3 cols */}
        <div className="col-span-3 bg-white border border-black/6 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 bg-[#0c1f14]">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-xs text-white/60 font-medium">Live · CAM-01</span>
            </div>
          </div>
          <div className="relative bg-[#0c1f14] aspect-video flex items-center justify-center">
            {/* Replace this div with your actual <video> element when ready */}
            <div className="flex flex-col items-center gap-3">
              <div className="w-16 h-16 rounded-full bg-white/5 border border-white/10 flex items-center justify-center">
                <svg className="w-7 h-7 text-white/25" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M15.75 10.5l4.72-4.72a.75.75 0 011.28.53v11.38a.75.75 0 01-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25h-9A2.25 2.25 0 002.25 9.75v9A2.25 2.25 0 004.5 18.75z" />
                </svg>
              </div>
              <p className="text-white/30 text-sm">Camera feed goes here</p>
            </div>
          </div>
        </div>

        {/* Detection info — 2 cols */}
        <div className="col-span-2 bg-white border border-black/6 rounded-xl p-4">
          <p className="text-xs font-medium text-gray-500 mb-3">Detection info</p>
          <div className="space-y-2">
            <div className="bg-gray-100 rounded-lg px-3 py-2.5">
              <p className="text-[10px] text-gray-500 mb-0.5">Subject ID</p>
              <p className="text-sm font-semibold text-gray-900">---</p>
              <p className="text-[10px] text-gray-400">Auto-assigned</p>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="bg-gray-100 rounded-lg px-3 py-2.5">
                <p className="text-[10px] text-gray-500 mb-0.5">BAC level</p>
                <p className="text-sm font-semibold text-gray-400">---</p>
              </div>
              <div className="bg-gray-100 rounded-lg px-3 py-2.5">
                <p className="text-[10px] text-gray-500 mb-0.5">Confidence</p>
                <p className="text-sm font-semibold text-gray-400">---</p>
                <p className="text-[10px] text-gray-400">LinearReg v2</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Reuse your existing graph component — chart + stats + recent detections all included */}
      <RecentDetections />

    </div>
  );
}

export default Camera;