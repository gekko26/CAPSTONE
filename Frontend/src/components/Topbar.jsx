import { useLocation } from "react-router-dom";


function Topbar() {
    const location = useLocation();
  
    const getTitle = () => {
      switch (location.pathname) {
        case "/":
          return "Home";
        case "/dashboard":
          return "Dashboard";
        case "/camera":
          return "Camera";
        case "/models":
          return "Models";
        case "/about":
          return "About";
        case "/report":
          return "Report";
        default:
          return "Page";
      }
    };
  
    return (
      <div className="p-4 border bg-gray-500 rounded-md text-black">
        <h1 className="text-xl font-bold">{getTitle()}</h1>
      </div>
    );
  }


  export default Topbar;