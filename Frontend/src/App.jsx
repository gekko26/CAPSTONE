import { BrowserRouter , Routes, Route } from "react-router-dom";
import { useState } from "react";

import Homes from "./pages/Home";
import Dashboard from "./pages/Dashboard";
import Camera from "./pages/Camera";
import Model from "./pages/Models";
import About from "./pages/About";
import Report from "./pages/Report";


import Sidebar from "./components/Sidebar";
import Topbar from "./components/Topbar";

import { ActivityIcon } from "lucide-react";



function App() {
 

  return (
   
   <BrowserRouter>
     <div className=" flex h-screen w-screen overflow-hidden gap-2 " >
       
           {/*SideBar*/}
           <div className="w-60 flex flex-col h-full bg-[#0f1a2e] border border-slate-700 rounded-md">

              <div className="flex items-center  justify-center p-6">
                 <div> <ActivityIcon size={50} strokeWidth={0.5} color="red"/> </div>
                 <div className="flex flex-col items-center">  
                  <h2 className="font-bold text-white tracking-tighter text-4xl ">AlcoDetect</h2>
                  <span className="text-xs font-light tracking-normal ">Palahubog Detector</span>
                 </div>
                 
              </div>

             <Sidebar />
           </div>
           

       <div className="flex flex-col flex-1   overflow-hidden p-2 gap-2" >  

          {/*Topbar*/}
         <Topbar />
         
         <main className="flex-1 ">
          
          <Routes>
           
           <Route path="/" element= {<Homes />} />
           <Route path="/dashboard" element={<Dashboard />} />
           <Route path="/camera" element={<Camera />} />
           <Route path="/models" element={<Model />} />
           <Route path="/about" element={<About />} />
           <Route path="/report" element={<Report />} />



          </Routes>

         </main>

     

       </div>




     </div>
     

   
   </BrowserRouter>
         
    
  );
}

export default App;
