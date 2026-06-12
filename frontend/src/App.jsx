import { useEffect, useRef, useState } from 'react'
import { BrowserRouter, Navigate, NavLink, Route, Routes } from 'react-router-dom'
import ChatPage from './pages/ChatPage.jsx'
import DashboardPage from './pages/DashboardPage.jsx'
import InfoPage from './pages/InfoPage.jsx'
import LearningPage from './pages/LearningPage.jsx'
import LoginPage from './pages/LoginPage.jsx'
import ResolvedPage from './pages/ResolvedPage.jsx'
import UsersPage from './pages/UsersPage.jsx'
import GovIcon from './components/GovIcon.jsx'
import omskOblastCoatOfArms from './assets/omsk-oblast-coat-of-arms.png'
import { getAuthToken, getCurrentUser, getLatestRun, getStatus, login, logout } from './api.js'

export default function App() {
  const [authUser, setAuthUser] = useState(null)
  const [authLoading, setAuthLoading] = useState(Boolean(getAuthToken()))
  const [runId, setRunIdState] = useState(null)
  const [runInitialized, setRunInitialized] = useState(false)
  // Глобальный статус обработки: поллинг живёт на уровне приложения, поэтому
  // прогресс не теряется и данные обновляются, даже если уйти с дашборда.
  const [processingStatus, setProcessingStatus] = useState(null)
  const [dataVersion, setDataVersion] = useState(0)
  const prevProcessingRef = useRef(null)
  const [theme, setTheme] = useState(() => localStorage.getItem('uiTheme') || 'light')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('uiTheme', theme)
  }, [theme])

  const setRunId = (nextRunId) => {
    setRunIdState(nextRunId)
  }

  useEffect(() => {
    if (!authUser || !runId) {
      setProcessingStatus(null)
      prevProcessingRef.current = null
      return
    }
    let cancelled = false
    let timer = null

    const poll = async () => {
      try {
        const status = await getStatus(runId)
        if (cancelled) return
        if (prevProcessingRef.current === 'running' && status.status === 'completed') {
          setDataVersion(version => version + 1)
        }
        prevProcessingRef.current = status.status
        setProcessingStatus(status)
        if (status.status === 'running' || status.status === 'pending') {
          timer = setTimeout(poll, 1500)
        }
      } catch {
        if (!cancelled) timer = setTimeout(poll, 4000)
      }
    }

    poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [authUser, runId])

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

  // Токен истёк посреди работы (любой запрос вернул 401) — показываем вход.
  useEffect(() => {
    const onExpired = () => {
      setAuthUser(null)
      setAuthLoading(false)
    }
    window.addEventListener('auth-expired', onExpired)
    return () => window.removeEventListener('auth-expired', onExpired)
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
      <div className="min-h-screen bg-[#eef1f7] flex items-center justify-center text-sm text-gray-500">
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
      <div className="min-h-screen bg-[#eef1f7]">
        <header className="flex items-center justify-between bg-[#2B3990] px-6 py-3 text-white shadow-md">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white p-1 shadow-sm">
              <img src={omskOblastCoatOfArms} alt="Герб Омской области" className="h-full w-full object-contain" />
            </div>
            <div>
              <h1 className="text-lg font-semibold leading-tight">Голос Омска</h1>
              <p className="text-xs text-white/70">Омская область</p>
            </div>
          </div>

          <nav className="flex gap-1 rounded-lg bg-white/10 p-1">
            <NavLink
              to="/"
              className={({ isActive }) =>
                `rounded-md px-4 py-1.5 text-sm font-medium transition ${
                  isActive ? 'bg-white text-[#2B3990]' : 'text-white/90 hover:bg-white/10'
                }`
              }
            >
              Дашборд
            </NavLink>
            <NavLink
              to="/chat"
              className={({ isActive }) =>
                `rounded-md px-4 py-1.5 text-sm font-medium transition ${
                  isActive ? 'bg-white text-[#2B3990]' : 'text-white/90 hover:bg-white/10'
                }`
              }
            >
              Чатбот
            </NavLink>
                        <NavLink
              to="/resolved"
              className={({ isActive }) =>
                `rounded-md px-4 py-1.5 text-sm font-medium transition ${
                  isActive ? 'bg-white text-[#2B3990]' : 'text-white/90 hover:bg-white/10'
                }`
              }
            >
              Решенные
            </NavLink>{isAdmin && (
              <NavLink
                to="/learning"
                className={({ isActive }) =>
                  `rounded-md px-4 py-1.5 text-sm font-medium transition ${
                    isActive ? 'bg-white text-[#2B3990]' : 'text-white/90 hover:bg-white/10'
                  }`
                }
              >
                Обратная связь
              </NavLink>
            )}
                      {isAdmin && (
              <NavLink
                to="/users"
                className={({ isActive }) =>
                  `rounded-md px-4 py-1.5 text-sm font-medium transition ${
                    isActive ? 'bg-white text-[#2B3990]' : 'text-white/90 hover:bg-white/10'
                  }`
                }
              >
                Пользователи
              </NavLink>
            )}
          </nav>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setTheme(current => (current === 'dark' ? 'light' : 'dark'))}
              title={theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
              aria-label="Переключить тему"
              className="flex h-8 w-8 items-center justify-center rounded-full border border-white/25 text-white/90 transition hover:bg-white/10"
            >
              <GovIcon name={theme === 'dark' ? 'sun' : 'moon'} className="h-4 w-4" />
            </button>
            {processingStatus?.status === 'running' && (
              <span
                className="flex items-center gap-2 rounded-full bg-white/15 px-3 py-1 text-xs font-medium text-white"
                title={processingStatus.current_step || 'Обработка файла'}
              >
                <GovIcon name="spinner" className="h-3.5 w-3.5 animate-spin" />
                Обработка <ProcessingPill status={processingStatus} />
              </span>
            )}
            {processingStatus?.status === 'pending' && (
              <span
                className="flex items-center gap-2 rounded-full bg-white/15 px-3 py-1 text-xs font-medium text-white"
                title={processingStatus.active_run?.filename
                  ? `Сейчас обрабатывается: ${processingStatus.active_run.filename}`
                  : 'Файл в очереди на обработку'}
              >
                <GovIcon name="clock" className="h-3.5 w-3.5" />
                В очереди{processingStatus.queue_position ? ` · ${processingStatus.queue_position}-й` : ''}
              </span>
            )}
            <NavLink
              to="/help"
              title="Справка"
              aria-label="Справка"
              className={({ isActive }) =>
                `flex h-8 w-8 items-center justify-center rounded-full border text-sm font-semibold transition ${
                  isActive
                    ? 'border-white bg-white text-[#2B3990]'
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
          <Route path="/" element={<DashboardPage runId={runId} setRunId={setRunId} processingStatus={processingStatus} dataVersion={dataVersion} />} />
          <Route path="/chat" element={<ChatPage runId={runId} processingStatus={processingStatus} />} />
          <Route path="/resolved" element={<ResolvedPage runId={runId} processingStatus={processingStatus} dataVersion={dataVersion} />} />
          <Route path="/help" element={<InfoPage isAdmin={isAdmin} />} />
          <Route path="/learning" element={isAdmin ? <LearningPage runId={runId} /> : <Navigate to="/" replace />} />
          <Route path="/users" element={isAdmin ? <UsersPage /> : <Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

// Этапные «вехи» прогресса бэкенда. Между обновлениями статуса число плавно
// ползёт к следующей вехе, чтобы процент всегда был динамичным, а не замирал.
const PROGRESS_MILESTONES = [0.1, 0.4, 0.55, 0.62, 0.7, 0.86, 0.9, 0.96, 1]

function ProcessingPill({ status }) {
  const [shown, setShown] = useState(0)
  const targetRef = useRef(0)

  useEffect(() => {
    targetRef.current = Math.max(0, Math.min(status?.progress || 0, 1))
  }, [status])

  useEffect(() => {
    const id = setInterval(() => {
      setShown((prev) => {
        const target = targetRef.current
        // Новый запуск или сброс — резко возвращаемся к фактическому значению.
        if (target < prev - 0.02) return target
        // Бэкенд сообщил больше — быстро подтягиваемся.
        if (target > prev) return Math.min(target, prev + Math.max((target - prev) * 0.2, 0.003))
        // Иначе медленно ползём к следующей вехе (но не достигаем её до бэкенда).
        const cap = (PROGRESS_MILESTONES.find((m) => m > target + 0.0001) ?? 1) - 0.005
        if (prev < cap) return Math.min(cap, prev + 0.0009)
        return prev
      })
    }, 120)
    return () => clearInterval(id)
  }, [])

  const pct = Math.round(Math.max(0, Math.min(shown * 100, 100)))
  return <>{pct}%</>
}
