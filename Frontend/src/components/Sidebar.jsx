import { Link } from "react-router-dom";
import { HomeIcon, LayoutDashboard, CameraIcon, Cpu,InfoIcon, Activity} from "lucide-react";

function Sidebar(){
   

let props = "flex text-xl pl-5 tracking-wider gap-2 hover:bg-slate-700 group  border-blue-200 p-2 hover:border-r-2 ";
let iconProps = "text-gray-400 group-hover:text-gray-200";

    return (
        <div className="flex flex-col gap-3 w-60 shrink-0 flex-1  rounded-md bg-[#0f1a2e] ">
             
             <div className=" h-1/8 flex flex-col  ">
                <p className="pl-5 tracking-widest text-gray-500 ">Main</p>
              <Link to={"/"} className={props}><HomeIcon className={iconProps}/> <p className="text-gray-400 group-hover:text-gray-200">Home</p></Link>
              <Link to={"/dashboard"} className={props}> <LayoutDashboard className={iconProps}/> <p className="text-gray-400 group-hover:text-gray-200">Dashboard</p></Link>
             </div>
           
             <div className="  h-1/8 flex flex-col  ">
             <p className="pl-5 tracking-widest text-gray-500">Analysis</p>
              <Link to={"/camera"} className={props}> <CameraIcon className={iconProps}/> <p className="text-gray-400 group-hover:text-gray-200">Camera</p></Link>
              <Link to={"/models"}className={props}><Cpu className={iconProps}/> <p className="text-gray-400 group-hover:text-gray-200">Models</p></Link>
             </div>
           
             <div className="  h-1/8 flex flex-col ">
              <p className="pl-5 tracking-widest text-gray-500 ">System</p>
              <Link to={"/report"}className={props}><Activity className={iconProps}/><p className="text-gray-400 group-hover:text-gray-200">Report</p></Link>
              <Link to={"/about"}className={props}><InfoIcon className={iconProps}/><p className="text-gray-400 group-hover:text-gray-200">About Us</p></Link>
             </div>
   
        </div>
    );
}

export default Sidebar;


