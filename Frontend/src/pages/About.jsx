

function About(){
    return (
        <div className="flex flex-col gap-5 h-full ">

            <div className="flex  items-center flex-1 w-full bg-[#0f1a2e] rounded-2xl p-2 gap-9">
                 
                 <div className="w-1/14 border h-2/3 rounded-2xl ml-12">logo ni</div>
                 <div className=" flex flex-col h-3/4  gap-2 justify-center"> 
                     <h2  className="text-4xl tracking-tighter">AlcoDetect <span className="ml-2">System</span> </h2>
                     <p className="text-xl tracking-tight text-gray-400">ESP32-based alcohol detection with ML-powered BAC estimation · Version 2.0</p>
                     <div className="flex gap-5 text-md h-1/4 items-center">
                        <p className=" h-3/4 flex items-center p-4 rounded-4xl bg-blue-200 text-blue-800">Computer Engineering</p>
                        <p className=" h-3/4 flex items-center p-4 rounded-4xl bg-green-100 text-green-600">Capstone Project</p>
                     </div>
                     
                 </div>
 
            </div>


            <div className="flex h-[40%] w-full  gap-4">
                  
                  <div  className="flex flex-col border w-1/2 bg-white rounded-2xl text-black p-5">
                     <p className="text-xls">Development Team</p>

                     <div className="flex flex-col flex-1 p-4">
                         
                       {/* dslfkdskf */}
                        <div className= "grid grid-cols-2 grid-rows-2 text-white flex-1 border border-red-500 ">

                            <div className="bg-white flex items-center justify-center gap-3">

                            <p className=" border border-black rounded-full h-3/4  w-1/3 bg-blue-200"></p>
                            <div className="flex flex-col text-black">
                              <p>Name. G</p>
                              <p>Role</p>
                             </div>
                                 
                            </div>

                            <div className="bg-white flex items-center justify-center gap-3">

                                <p className=" border border-black rounded-full h-3/4  w-1/3 bg-blue-200"></p>
                                <div className="flex flex-col text-black">
                                <p>Name. G</p>
                                <p>Role</p>
                                </div>

                            </div>

                          <div className="bg-white flex items-center justify-center gap-3">

                                    <p className=" border border-black rounded-full h-3/4  w-1/3 bg-blue-200"></p>
                                    <div className="flex flex-col text-black">
                                    <p>Name. G</p>
                                    <p>Role</p>
                                    </div>

                          </div>

                        </div>

                     </div>
                  </div>


                  <div className="border w-1/2 bg-white rounded-2xl text-black p-5">
                  <p>Tech Stack</p>
       
                  
                  </div>


            </div>

   {/* How it works */}
            <div className="flex-1 w-full bg-white rounded-2xl text-black p-6">
               <p>How it works</p>
                
               <div className="flex p-8 items-center justify-center gap-30">

                  <div>
                     <div className="rounded-full bg-blue-300 h-15 w-15"></div>
                    <p>1. Capture</p>
                    <p>Camera Detects subjects face</p>
                  </div>

                  <div>
                    <div className="rounded-full bg-yellow-300 h-15 w-15"></div>
                    <p>2. Sense</p>
                    <p>ESP32 reads MQ-3 sensor output</p>
                  </div>

                  <div>
                   <div className="rounded-full bg-green-300 h-15 w-15"></div>
                    <p>3. Predict</p>
                    <p>ML model estimates BAC level</p>
                  </div>

                  <div>
                   <div className="rounded-full bg-red-300 h-15 w-15"></div>
                    <p>4. Alert</p>
                    <p>Pass/fail result displayed</p>
                  </div>

               </div>
            </div>


        </div>
    )
}


export default About;