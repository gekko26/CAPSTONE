import picture from "../assets/pic.jpg"
import { Radar, Sparkles, AlertTriangle, Camera, ActivityIcon} from "lucide-react";
import { useTheme } from "../context/THEME_CONTEXT";


function About(){
  const {theme, setTheme} = useTheme()
  const cardBg = theme?"text-white":"bg-white";


    return (
        <div className="flex flex-col gap-5 h-full ">

            <div className="flex  items-center flex-1 w-full bg-[#0f1a2e] rounded-2xl p-2 gap-9">
                 
                 <div className="w-1/14 border h-2/3 rounded-2xl ml-12 flex items-center justify-center border-red-300"><ActivityIcon color="red" size={100}/></div>
                 <div className=" flex flex-col h-3/4  gap-2 justify-center"> 
                     <h2  className="text-4xl tracking-tighter text-green-500">AlcoDetect <span className="ml-2">System</span> </h2>
                     <p className="text-xl tracking-tight text-gray-400">ESP32-based alcohol detection with ML-powered BAC estimation · Version 2.0</p>
                     <div className="flex gap-5 text-md h-1/4 items-center">
                        <p className=" h-3/4 flex items-center p-4 rounded-4xl bg-blue-200 text-blue-800">Computer Engineering</p>
                        <p className=" h-3/4 flex items-center p-4 rounded-4xl bg-green-100 text-green-600">Capstone Project</p>
                     </div>
                     
                 </div>
 
            </div>


            <div className="flex h-[40%] w-full  gap-4">
                  
                  <div  className={`flex flex-col border w-1/2 rounded-2xl text-black p-5 bg-white`}>
                     <p className="text-xls">Development Team</p>

                     <div className="flex flex-col flex-1 p-4">
                         
                       {/* dslfkdskf */}
                        <div className= {`grid grid-cols-2 grid-rows-2 text-white flex-1 border `}>

                            <div className="bg-white flex items-center justify-center gap-3">

                              <div className=" border  rounded-full h-25  w-25 bg-blue-200 overflow-clip "> <img src={picture} alt="" /> </div> 
                            <div className="flex flex-col text-black">
                            <p className="font-bold text-xl tracking-tighter">Jayme, Nino Charles</p>
                              <p>Paperrrrrrrrrrrrrrs</p>
                             </div>
                                 
                            </div>

                            <div className="bg-white flex items-center justify-center gap-3">

                                <div className=" border rounded-full h-25  w-25 bg-blue-200 overflow-clip "> <img src={picture} alt="" /> </div> 
                                <div className="flex flex-col text-black">
                                <p className="font-bold text-xl tracking-tighter">Gonzaga, Edrian P</p>
                                <p>Sicret..</p>
                                </div>

                            </div>

                          <div className="bg-white flex items-center justify-center gap-3">

                                  <div className=" border rounded-full h-25  w-25 bg-blue-200 overflow-clip "> <img src={picture} alt="" /> </div> 
                                    <div className="flex flex-col text-black">
                                    <p className="font-bold text-xl tracking-tighter">Cabahug, Jommel P</p>
                                    <p>Hardware Ultra spesyal</p>
                                    </div>

                          </div>

                        </div>

                     </div>
                  </div>


                  <div className="border w-1/2 bg-white rounded-2xl text-black p-5">
                  <p className="mb-4">Tech Stack</p>
                   <div className="p-6 flex  justify-evenly items-center h-60">
                       
                     <div className="flex flex-col h-full gap-3  ">
                       <p className="text-xl font-bold tracking-wider">Front End</p>
                       <div className="flex flex-col gap-3">
                       <p>⚛️ React</p>
                       <p>⚡ Vite</p>
                       <p>🎨 Tailwind CSS</p>
                       </div>
                       
                     </div>

                     <div className="flex flex-col h-full gap-3">
                       <p className="text-xl font-bold tracking-wider">Back End</p>
                       <div className="flex flex-col gap-3">
                       <p>🐍 Python</p>
                       <p>👁️ OpenCV</p>
                       <p>🧠 Machine Learning</p>
                       </div>
                       
                     </div>

                     <div className="flex flex-col h-full gap-3">
                       <p className="text-xl font-bold tracking-wider">Hardware</p>
                       <div className="flex flex-col gap-3">
                       <p>ESP32</p>
                       <p>MQ-3 Sensors</p>
                       <p>CCTV Camera</p>
                       </div>
                     </div>

                     <div className="flex flex-col h-full gap-3">
                      <p className="text-xl font-bold tracking-wider">Communication</p>
                      <p>WIFI</p>
                     </div>

                   </div>
       
                  
                  </div>


            </div>

   {/* How it works */}
            <div className=   {`"flex-1 w-full rounded-2xl text-black p-6" ${cardBg}`}>
               <p className="m-4">How it works</p>
                
               <div className="flex p-8 items-center justify-center gap-30">

                  <div>
                     <div className="rounded-full bg-blue-300 h-15 w-15 flex items-center justify-center mb-2"><Camera color="blue"/></div>
                    <p>1. Capture</p>
                    <p>Camera Detects subjects face</p>
                  </div>

                  <div>
                    <div className="rounded-full bg-yellow-200 h-15 w-15 flex items-center justify-center mb-2"><Radar color="" strokeWidth={2} className="text-yellow-700 stroke-current"/></div>
                    <p>2. Sense</p>
                    <p>ESP32 reads MQ-3 sensor output</p>
                  </div>

                  <div>
                   <div className="rounded-full bg-green-300 h-15 w-15 flex items-center justify-center mb-2"><Sparkles color="green"/></div>
                    <p>3. Predict</p>
                    <p>ML model estimates BAC level</p>
                  </div>

                  <div >
                   <div className="rounded-full bg-red-300 h-15 w-15 flex items-center justify-center mb-2"><AlertTriangle color="red"/></div>
                    <p>4. Alert</p>
                    <p>Pass/fail result displayed</p>
                  </div>

               </div>
            </div>


        </div>
    )
}


export default About;