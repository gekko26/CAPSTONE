import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

import { ThemeProvider } from './context/THEME_CONTEXT.jsx'
import { DisplayProvider } from './context/DISPLAY_CONTEXT.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ThemeProvider>
      <DisplayProvider>
        <App />
      </DisplayProvider>
    </ThemeProvider>
  </StrictMode>,
)
