import {
    BarChart,Line,LineChart,
    CartesianGrid,
    XAxis,
    YAxis,
    Tooltip,
    Legend,
    Bar,
  } from "recharts";
  
 function Prac(){

    const data = [
    
            { name: "a", value: 16 },
            { name: "b", value: 12 },
            { name: "c", value: 18 },
          
      ];

    return(

<div>
<LineChart
  width={730}
  height={250}
  data={data}
  margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
>
  <CartesianGrid strokeDasharray="3 3" />
  <XAxis dataKey="name" />
  <YAxis />
  <Tooltip />
  <Legend />
  <Line type="monotone" dataKey="value" stroke="#8884d8" />
</LineChart>
  </div>

    );

}


export default Prac;

