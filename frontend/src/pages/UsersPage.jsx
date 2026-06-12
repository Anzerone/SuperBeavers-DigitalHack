import { useEffect, useState } from 'react'
import { createUser, getUsers } from '../api.js'
import GovIcon from '../components/GovIcon.jsx'

export default function UsersPage() {
  const [users, setUsers] = useState([])
  const [form, setForm] = useState({ username: '', password: '', role: 'user' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState('')

  const loadUsers = async () => {
    const data = await getUsers()
    setUsers(data)
  }

  useEffect(() => {
    loadUsers().catch(() => setUsers([]))
  }, [])

  const submit = async (event) => {
    event.preventDefault()
    setLoading(true)
    setError('')
    setCreated('')
    try {
      const user = await createUser(form)
      setCreated(`Создан аккаунт ${user.username}`)
      setForm({ username: '', password: '', role: 'user' })
      await loadUsers()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="mx-auto max-w-5xl p-4">
      <div className="mb-4 bg-white p-5 shadow-sm rounded-xl">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-gray-800">
          <GovIcon name="building" className="h-5 w-5 text-[#2B3990]" />
          Пользователи
        </h2>
        <p className="mt-1 text-sm text-gray-500">
          Администратор создает учетные записи для работы с загрузками, отчетами и обратной связью.
        </p>
      </div>

      <div className="grid grid-cols-[360px_1fr] gap-4">
        <form onSubmit={submit} className="bg-white p-5 shadow-sm rounded-xl">
          <h3 className="mb-4 text-sm font-semibold text-gray-700">Новый аккаунт</h3>
          <label className="mb-3 block">
            <span className="mb-1 block text-xs text-gray-500">Логин</span>
            <input
              value={form.username}
              onChange={e => setForm(prev => ({ ...prev, username: e.target.value }))}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/30"
              minLength={3}
              required
            />
          </label>
          <label className="mb-3 block">
            <span className="mb-1 block text-xs text-gray-500">Пароль</span>
            <input
              type="password"
              value={form.password}
              onChange={e => setForm(prev => ({ ...prev, password: e.target.value }))}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/30"
              minLength={6}
              required
            />
          </label>
          <div className="mb-4">
            <span className="mb-1 block text-xs text-gray-500">Роль</span>
            <div className="grid grid-cols-2 gap-2 rounded-lg bg-gray-100 p-1">
              {[
                ['user', 'Пользователь'],
                ['admin', 'Админ'],
              ].map(([role, label]) => (
                <button
                  key={role}
                  type="button"
                  onClick={() => setForm(prev => ({ ...prev, role }))}
                  className={`rounded-md px-3 py-2 text-sm transition ${
                    form.role === role ? 'bg-white text-[#2B3990] shadow-sm' : 'text-gray-500 hover:text-gray-700'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {error && <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
          {created && <p className="mb-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{created}</p>}

          <button
            disabled={loading}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-[#2B3990] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#1F2A6E] disabled:opacity-50"
          >
            <GovIcon name="plus" className="h-4 w-4" />
            Создать
          </button>
        </form>

        <div className="overflow-hidden bg-white shadow-sm rounded-xl">
          <div className="border-b px-5 py-3">
            <h3 className="text-sm font-semibold text-gray-700">Аккаунты</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-400">
                <tr>
                  <th className="px-4 py-2 text-left">Логин</th>
                  <th className="px-4 py-2 text-left">Роль</th>
                  <th className="px-4 py-2 text-left">Создан</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {users.map(user => (
                  <tr key={user.id} className="hover:bg-gray-50">
                    <td className="px-4 py-2 font-medium text-gray-800">{user.username}</td>
                    <td className="px-4 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                        user.role === 'admin' ? 'bg-indigo-50 text-indigo-700' : 'bg-gray-100 text-gray-600'
                      }`}>
                        {user.role}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-gray-500">
                      {user.created_at ? new Date(user.created_at).toLocaleString('ru') : '—'}
                    </td>
                  </tr>
                ))}
                {users.length === 0 && (
                  <tr>
                    <td className="px-4 py-8 text-center text-gray-400" colSpan={3}>Пользователи не найдены</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}
