// mockData.js

export const generateRecentDetections = (count = 4) => {
    return Array.from({ length: count }).map((_, i) => {
      const bac = parseFloat((Math.random() * 0.12).toFixed(3));
      let status = "Pass";
      if (bac >= 0.08) status = "Over limit";
      else if (bac >= 0.05) status = "Near limit";
  
      return {
        id: `ID-${4821 - Math.floor(Math.random() * 100)}`,
        bac: bac.toFixed(3),
        status,
        // Instead of a string, we store a timestamp from the past
        // This mocks a detection that happened (i * 5) minutes ago
        createdAt: new Date(Date.now() - i * 5 * 60000) 
      };
    });
  };


  