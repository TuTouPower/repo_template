import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router'
import './index.css'
import App from './App.tsx'

// HashRouter：vite base='./' 且可能部署到子目录/file://，BrowserRouter 无 basename 时路由不匹配
createRoot(document.getElementById('root')!).render(
  <HashRouter>
    <App />
  </HashRouter>,
)
