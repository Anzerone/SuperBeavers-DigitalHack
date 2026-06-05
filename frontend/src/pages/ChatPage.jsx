import { useEffect, useRef, useState } from 'react'
import { exportChatResult, sendChatMessage } from '../api.js'
import { formatSeverity } from '../utils/severity.js'

const QUICK_ACTIONS = [
  'Топ-10 районов',
  'Проблемы в Омске',
  'Критические проблемы ЖКХ',
  'Сводка по дорогам',
  'Сколько обращений по здравоохранению',
  'Главные проблемы по всем районам',
  'Проблемы по благоустройству',
  'Топ районов по транспорту',
]

const COLUMN_LABELS = {
  appeal_count: 'Обращений',
  avg_rank: 'Средний ранг',
  category: 'Категория',
  centroid_text: 'Выдержка',
  cluster_count: 'Кластеров',
  cluster_name: 'Проблема',
  municipality: 'Район',
  problem_count: 'Проблемных обращений',
  rank: 'Ранг',
  severity: 'Тяжесть',
  summary_text: 'Сводка',
  top_issues: 'Ключевые проблемы',
}

const formatColumn = (column) => COLUMN_LABELS[column] || column

const formatCell = (column, value) => {
  if (value == null) return ''
  if (column === 'severity') return formatSeverity(value)
  if (['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].includes(value)) return formatSeverity(value)
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export default function ChatPage({ runId }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const endRef = useRef()

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = async (text) => {
    if (!text.trim() || !runId || loading) return
    setMessages((prev) => [...prev, { role: 'user', text }])
    setInput('')
    setLoading(true)

    try {
      const data = await sendChatMessage(runId, text)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: data.answer,
          sql: data.sql,
          data: data.data,
          columns: data.columns,
        },
      ])
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: 'Ошибка: ' + (e.response?.data?.detail || e.message),
        },
      ])
    } finally {
      setLoading(false)
    }
  }

  if (!runId) {
    return (
      <div className="flex h-[80vh] items-center justify-center">
        <p className="text-gray-400">Сначала загрузите и обработайте файл на дашборде</p>
      </div>
    )
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-60px)] max-w-4xl flex-col p-4">
      <div className="mb-4 flex-1 space-y-4 overflow-y-auto">
        {messages.length === 0 && (
          <div className="mt-20 text-center">
            <p className="mb-6 text-gray-500">Задайте вопрос по загруженным данным</p>
            <div className="flex flex-wrap justify-center gap-2">
              {QUICK_ACTIONS.map((query) => (
                <button
                  key={query}
                  onClick={() => send(query)}
                  className="rounded-full bg-white px-4 py-2 text-sm shadow-sm transition hover:shadow-md"
                >
                  {query}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message, index) => (
          <div key={index} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[80%] rounded-xl px-4 py-3 ${
                message.role === 'user' ? 'bg-[#0d7377] text-white' : 'bg-white shadow-sm'
              }`}
            >
              <p className="whitespace-pre-wrap text-sm">{message.text}</p>

              {message.sql && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-gray-400">SQL запрос</summary>
                  <pre className="mt-1 overflow-x-auto rounded bg-gray-50 p-2 text-xs">{message.sql}</pre>
                </details>
              )}

              {message.data && message.data.length > 0 && (
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr>
                        {message.columns?.map((column) => (
                          <th key={column} className="border-b px-2 py-1 text-left font-medium text-gray-500">
                            {formatColumn(column)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {message.data.slice(0, 10).map((row, rowIndex) => (
                        <tr key={rowIndex} className="border-b border-gray-100">
                          {message.columns?.map((column) => (
                            <td key={column} className="max-w-[220px] truncate px-2 py-1">
                              {formatCell(column, row[column])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {message.data.length > 10 && (
                    <p className="mt-1 text-xs text-gray-400">Показано 10 из {message.data.length}</p>
                  )}

                  <button
                    onClick={() => exportChatResult(runId)}
                    className="mt-2 rounded border border-gray-300 px-3 py-1 text-xs hover:bg-gray-50"
                  >
                    Скачать результат XLSX
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="rounded-xl bg-white px-4 py-3 shadow-sm">
              <p className="text-sm text-gray-400">Анализирую данные...</p>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send(input)}
          placeholder="Задайте вопрос по данным..."
          className="flex-1 rounded-xl border border-gray-200 px-4 py-3 text-sm shadow-sm"
        />
        <button
          onClick={() => send(input)}
          disabled={loading || !input.trim()}
          className="rounded-xl bg-[#0d7377] px-6 py-3 text-sm font-medium text-white hover:bg-[#0a5c5f] disabled:opacity-40"
        >
          Отправить
        </button>
      </div>
    </div>
  )
}
