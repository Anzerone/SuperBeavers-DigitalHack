import { useEffect, useState } from 'react'
import { BarChart3 } from 'lucide-react'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import ChatPage from './pages/ChatPage.jsx'
import DashboardPage from './pages/DashboardPage.jsx'

export default function App() {
  const [runId, setRunIdState] = useState(() => {
    const saved = localStorage.getItem('currentRunId')
    return saved ? Number(saved) : null
  })

  const setRunId = (nextRunId) => {
    setRunIdState(nextRunId)
  }

  useEffect(() => {
    if (runId) localStorage.setItem('currentRunId', String(runId))
    else localStorage.removeItem('currentRunId')
  }, [runId])

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-[#e8f0f2]">
        <header className="flex items-center justify-between bg-[#0d7377] px-6 py-3 text-white shadow-md">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white/20">
              <BarChart3 className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-lg font-semibold leading-tight">Классификатор обращений</h1>
              <p className="text-xs text-white/70">Омская область</p>
            </div>
          </div>

          <nav className="flex gap-1 rounded-lg bg-white/10 p-1">
            <NavLink
              to="/"
              className={({ isActive }) =>
                `rounded-md px-4 py-1.5 text-sm font-medium transition ${
                  isActive ? 'bg-white text-[#0d7377]' : 'text-white/90 hover:bg-white/10'
                }`
              }
            >
              Дашборд
            </NavLink>
            <NavLink
              to="/chat"
              className={({ isActive }) =>
                `rounded-md px-4 py-1.5 text-sm font-medium transition ${
                  isActive ? 'bg-white text-[#0d7377]' : 'text-white/90 hover:bg-white/10'
                }`
              }
            >
              Чатбот
            </NavLink>
          </nav>

          <span className="text-sm text-white/60">SuperBeavers</span>
        </header>

        <Routes>
          <Route path="/" element={<DashboardPage runId={runId} setRunId={setRunId} />} />
          <Route path="/chat" element={<ChatPage runId={runId} />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
