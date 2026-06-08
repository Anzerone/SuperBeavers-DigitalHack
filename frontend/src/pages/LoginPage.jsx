import { useState } from 'react'
import GovIcon from '../components/GovIcon.jsx'

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('admin123')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (event) => {
    event.preventDefault()
    setError('')
    setLoading(true)
    try {
      await onLogin(username.trim(), password)
    } catch (e) {
      setError(e.response?.data?.detail || 'Не удалось войти')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#e8f0f2] flex items-center justify-center p-6">
      <form onSubmit={submit} className="w-full max-w-[420px] bg-white rounded-xl shadow-sm p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-[#0d7377]/10 text-[#0d7377]">
            <GovIcon name="app" className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-gray-800">Классификатор обращений</h1>
            <p className="text-sm text-gray-500">Вход в систему</p>
          </div>
        </div>

        <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="username">
          Логин
        </label>
        <input
          id="username"
          value={username}
          onChange={e => setUsername(e.target.value)}
          className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#0d7377]"
          autoComplete="username"
        />

        <label className="block text-sm font-medium text-gray-700 mt-4 mb-1" htmlFor="password">
          Пароль
        </label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={e => setPassword(e.target.value)}
          className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#0d7377]"
          autoComplete="current-password"
        />

        {error && (
          <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="mt-5 w-full rounded-lg bg-[#0d7377] px-4 py-2.5 text-sm font-medium text-white hover:bg-[#0a5c5f] disabled:opacity-50"
        >
          {loading ? 'Вход...' : 'Войти'}
        </button>

        <div className="mt-5 rounded-lg bg-gray-50 px-3 py-3 text-xs text-gray-500">
          <p className="font-medium text-gray-700 mb-1">Учётные записи</p>
          <p>Администратор: admin / admin123</p>
          <p>Пользователь: user / user123</p>
        </div>
      </form>
    </div>
  )
}
