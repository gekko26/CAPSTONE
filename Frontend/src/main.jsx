import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

import { BACProvider } from './context/BAC_CONTEXT.jsx'
import { ThemeProvider } from './context/THEME_CONTEXT.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    
    <ThemeProvider>
    <BACProvider>
    <App />
    </BACProvider>
    </ThemeProvider>
   
    
  </StrictMode>,
)
