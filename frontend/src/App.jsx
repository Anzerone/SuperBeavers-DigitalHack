import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, NavLink, Route, Routes } from 'react-router-dom'
import ChatPage from './pages/ChatPage.jsx'
import DashboardPage from './pages/DashboardPage.jsx'
import InfoPage from './pages/InfoPage.jsx'
import LearningPage from './pages/LearningPage.jsx'
import LoginPage from './pages/LoginPage.jsx'
import GovIcon from './components/GovIcon.jsx'
import omskOblastCoatOfArms from './assets/omsk-oblast-coat-of-arms.png'
import { getAuthToken, getCurrentUser, getLatestRun, login, logout } from './api.js'

export default function App() {
  const [authUser, setAuthUser] = useState(null)
  const [authLoading, setAuthLoading] = useState(Boolean(getAuthToken()))
  const [runId, setRunIdState] = useState(null)
  const [runInitialized, setRunInitialized] = useState(false)

  const setRunId = (nextRunId) => {
    setRunIdState(nextRunId)
  }

  useEffect(() => {
    if (!authUser || !runInitialized) return
    const storageKey = `currentRunId:${authUser.id}`
    if (runId) localStorage.setItem(storageKey, String(runId))
    else localStorage.removeItem(storageKey)
  }, [authUser, runId, runInitialized])

  useEffect(() => {
    if (!getAuthToken()) return
    getCurrentUser()
      .then(setAuthUser)
      .catch(() => {
        logout()
        setAuthUser(null)
      })
      .finally(() => setAuthLoading(false))
  }, [])

  useEffect(() => {
    if (!authUser) {
      setRunInitialized(false)
      return
    }
    let cancelled = false
    const storageKey = `currentRunId:${authUser.id}`
    const saved = localStorage.getItem(storageKey)
    if (saved) {
      setRunIdState(Number(saved))
      setRunInitialized(true)
      return
    }
    getLatestRun()
      .then(data => {
        if (!cancelled) {
          setRunIdState(data.run_id || null)
          setRunInitialized(true)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRunIdState(null)
          setRunInitialized(true)
        }
      })
    return () => {
      cancelled = true
    }
  }, [authUser])

  const handleLogin = async (username, password) => {
    const user = await login(username, password)
    setAuthUser(user)
  }

  const handleLogout = () => {
    logout()
    setAuthUser(null)
    setRunId(null)
    setRunInitialized(false)
  }

  if (authLoading) {
    return (
      <div className="min-h-screen bg-[#e8f0f2] flex items-center justify-center text-sm text-gray-500">
        Проверка доступа...
      </div>
    )
  }

  if (!authUser) {
    return <LoginPage onLogin={handleLogin} />
  }

  const isAdmin = authUser.role === 'admin'

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-[#e8f0f2]">
        <header className="flex items-center justify-between bg-[#0d7377] px-6 py-3 text-white shadow-md">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white p-1 shadow-sm">
              <img src={omskOblastCoatOfArms} alt="Герб Омской области" className="h-full w-full object-contain" />
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
            {isAdmin && (
              <NavLink
                to="/learning"
                className={({ isActive }) =>
                  `rounded-md px-4 py-1.5 text-sm font-medium transition ${
                    isActive ? 'bg-white text-[#0d7377]' : 'text-white/90 hover:bg-white/10'
                  }`
                }
              >
                Обратная связь
              </NavLink>
            )}
          </nav>

          <div className="flex items-center gap-3">
            <NavLink
              to="/help"
              title="Справка"
              aria-label="Справка"
              className={({ isActive }) =>
                `flex h-8 w-8 items-center justify-center rounded-full border text-sm font-semibold transition ${
                  isActive
                    ? 'border-white bg-white text-[#0d7377]'
                    : 'border-white/25 text-white/90 hover:bg-white/10'
                }`
              }
            >
              ?
            </NavLink>
            <span className="text-sm text-white/70">{authUser.username}</span>
            <button
              onClick={handleLogout}
              className="rounded-md border border-white/20 px-3 py-1.5 text-sm text-white/90 hover:bg-white/10"
            >
              Выйти
            </button>
          </div>
        </header>

        <Routes>
          <Route path="/" element={<DashboardPage runId={runId} setRunId={setRunId} />} />
          <Route path="/chat" element={<ChatPage runId={runId} />} />
          <Route path="/help" element={<InfoPage isAdmin={isAdmin} />} />
          <Route path="/learning" element={isAdmin ? <LearningPage runId={runId} /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
