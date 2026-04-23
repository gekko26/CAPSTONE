import React, { useState, useEffect } from 'react';
import { generateRecentDetections } from '../assets/mockdata';
import MockBACChart from '../assets/graph';

const Dashboard = () => {
  
  const [reading, setReading] = useState(generateRecentDetections());




const [now, setNow] = useState(Date.now);


  const getRelativeTime = (time)=>{
    const diff = now - new Date(time).getTime();

    const seconds = diff/1000;
    const minute = diff/60000;
    const hour = diff/3600000;
    
    if (seconds<60 && seconds==0) return `just now `;
    if (minute<60) return `${minute} minutes ago`;
    return `${hour} hour ago`;
  }

  return(
    <div className='flex flex-col justify-evenly'>

        <div>
           <MockBACChart/>
        </div>

     
    </div>
  );
};


export default Dashboard;