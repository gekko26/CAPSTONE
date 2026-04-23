function Homes(){

    return (

    
        <div className=" flex flex-col  flex-1 overflow-auto  h-full gap-2">
            
            <div className="flex flex-col  border-slate-600 h-1/2 w-full rounded-2xl bg-[#0f1a2e] p-16 text-white justify-center gap-8">
               
                {/* State */}
              <div className="border rounded-md w-1/10 p-1 text-center bg-slate-400"> 
                <p>System Active</p>
              </div>

              <div>
                <h2 className="font-medium text-5xl">Alcohol Detection <br />System v2.0</h2>
              </div>

              <div>
                <p className="text-2xl tracking-tighter">Real-time BAC monitoring using ESP32 sensor fusion and
                machine learning <br />models for accurate, fast detection.</p>
              </div>

              <div className="flex gap-5">
                <p className="border w-1/6 text-center text-xl p-2 bg-blue-800">Open Camera</p>
                <p className="border w-1/6 text-center text-xl p-2 bg-gray-500">View Dashboard</p>
              </div>
                 
            </div>





            <div className=" flex gap-3 border-slate-600 h-1/3 w-full  p-3">
                
                <div className="flex flex-col text-center justify-center text-black border border-gray-400/3 bg-gray-400 rounded-2xl w-1/4 ">
                 <p className="text-xl tracking-wide">Live Camera</p>
                 <p className="tracking-tight">Start Detection</p>
                </div>

                <div className="flex flex-col text-center justify-center text-black border border-gray-400/3 bg-gray-400 rounded-2xl w-1/4 ">
                 <p className="text-xl tracking-wide">Dashboard</p>
                 <p className="tracking-tight">Live analytics</p>
                </div>
                
                <div className="flex flex-col text-center justify-center text-black border border-gray-400/3 bg-gray-400 rounded-2xl w-1/4 ">
                 <p className="text-xl tracking-wide">ML models</p>
                 <p className="tracking-tight">3 models loaded</p>
                </div>

                <div className="flex flex-col text-center justify-center text-black border border-gray-400/3 bg-gray-400 rounded-2xl  w-1/4 ">
                 <p className="text-xl tracking-wide">About us</p>
                 <p className="tracking-tight">Team & tech stack</p>
                </div>
                 
            </div>


            <div className=" flex  border-slate-600 h-1/2 w-full justify-between text-black p-3 gap-3"> 
               
                <div className="border border-gray-400/30 w-1/3 rounded-2xl bg-gray-400">
                  <div className="flex justify-between p-5 font-medium text-xl ">
                    <h3>Today's readings</h3>
                    <p className="border w-1/7 text-center rounded-4xl text-sm p-2">Live</p>
                  </div>

                  <div className="p-4 text-black">
                    <p className="text-6xl">256</p>
                    <p> ▲ 12 from yesterday</p>
                  </div>

                </div>
 
                <div className="border border-gray-400/3 w-1/3 rounded-2xl bg-gray-400">

                   <div className="flex justify-between p-6 font-medium text-xl ">
                    <h3>Pass Rate</h3>
                    <p className="border w-1/7 text-center rounded-4xl text-sm p-2">Today</p>
                   </div>

                  <div className="p-4 text-black">
                    <p className="text-6xl">96.8%</p>
                    <p>27 passed - 9 flagged</p>
                   </div>

                 </div>


                <div className="border border-gray-400/30 w-1/3 bg-gray-400 rounded-2xl">
                   
                   <div className="flex justify-between p-5 font-medium text-xl ">
                      <h3>Avg response</h3>
                      <p className="border w-1/7 text-center rounded-4xl text-sm p-2">System</p>
                    </div>

                   <div className="p-4 text-black">
                     <p className="text-6xl">1.2s</p>
                     <p> Detection Latency</p>
                   </div>
                
                </div>

            </div>
            
            
       </div>
    )
}

export default Homes;