import { useEffect, useRef, useState } from 'react'
import { exportChatResult, sendChatMessage } from '../api.js'
import GovIcon from '../components/GovIcon.jsx'

function renderWithCitations(text, citations) {
  if (!text) return text
  const parts = []
  let lastIdx = 0
  const re = /\[(\d+)\]/g
  let m
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIdx) parts.push(text.slice(lastIdx, m.index))
    const n = parseInt(m[1], 10)
    const citation = citations[n - 1]
    parts.push(
      <span
        key={`${m.index}`}
        title={citation ? `${citation.municipality} · ${citation.category}: ${citation.text.slice(0, 200)}` : ''}
        className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 mx-0.5 text-[10px] font-semibold bg-teal-100 text-teal-700 rounded cursor-help align-middle"
      >
        {n}
      </span>
    )
    lastIdx = m.index + m[0].length
  }
  if (lastIdx < text.length) parts.push(text.slice(lastIdx))
  return parts
}

const QUICK_ACTIONS = [
  'Топ-10 районов',
  'Проблемы в Омске',
  'Критические проблемы ЖКХ',
  'Сравни Омск и Калачинский',
  'Сравни ЖКХ и Дороги',
  'Сколько обращений по здравоохранению',
  'Сводка по дорогам',
  'Где больше всего критических?',
]

export default function ChatPage({ runId }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [ctxInfo, setCtxInfo] = useState(null)
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
      if (data.context) setCtxInfo(data.context)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: data.answer,
          fallback: data.fallback,
          narration: data.narration,
          citations: data.citations || [],
          sql: data.sql,
          data: data.data,
          columns: data.columns,
          suggestions: data.suggestions || [],
          fast: data.fast,
          kind: data.kind,
        },
      ])
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', text: 'Ошибка: ' + (e.response?.data?.detail || e.message), suggestions: [] },
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
      {/* Context bar */}
      {ctxInfo && (ctxInfo.last_municipality || ctxInfo.last_category) && (
        <div className="mb-3 flex items-center gap-2 text-xs text-gray-500">
          <span>Контекст:</span>
          {ctxInfo.last_municipality && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-50 text-indigo-700 rounded-full">
              <GovIcon name="location" className="h-3.5 w-3.5" />
              {ctxInfo.last_municipality}
            </span>
          )}
          {ctxInfo.last_category && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-teal-50 text-teal-700 rounded-full">
              <GovIcon name="folder" className="h-3.5 w-3.5" />
              {ctxInfo.last_category}
            </span>
          )}
          <button onClick={() => { setMessages([]); setCtxInfo(null) }} className="ml-auto text-gray-400 hover:text-gray-700 underline">
            Очистить
          </button>
        </div>
      )}

      <div className="mb-4 flex-1 space-y-4 overflow-y-auto">
        {messages.length === 0 && (
          <div className="mt-12 text-center">
            <p className="mb-2 text-lg text-gray-700 font-medium">Чем помочь?</p>
            <p className="mb-6 text-sm text-gray-400">Задайте вопрос или выберите быстрое действие</p>
            <div className="flex flex-wrap justify-center gap-2">
              {QUICK_ACTIONS.map((query) => (
                <button
                  key={query}
                  onClick={() => send(query)}
                  className="rounded-full bg-white px-4 py-2 text-sm shadow-sm transition hover:shadow-md hover:bg-teal-50 hover:text-teal-700"
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
              className={`max-w-[85%] rounded-xl px-4 py-3 ${
                message.role === 'user' ? 'bg-[#0d7377] text-white' : 'bg-white shadow-sm'
              }`}
            >
              {/* Narration (LLM) or fallback text with citation links */}
              <p className="whitespace-pre-wrap text-sm leading-relaxed">
                {message.citations?.length ? renderWithCitations(message.text, message.citations) : message.text}
              </p>

              {/* Citations list */}
              {message.citations?.length > 0 && (
                <div className="mt-3 pt-2 border-t border-gray-100 space-y-1">
                  <p className="text-[11px] text-gray-400">Источники:</p>
                  {message.citations.map((c, i) => (
                    <details key={i} className="text-xs">
                      <summary className="cursor-pointer text-teal-700 hover:text-teal-900">
                        [{i + 1}] {c.municipality} · {c.category}
                      </summary>
                      <p className="mt-1 pl-3 text-gray-600 italic border-l-2 border-teal-100">
                        {c.text}
                      </p>
                    </details>
                  ))}
                </div>
              )}

              {/* Show full fallback as collapsible details when LLM narration is shorter */}
              {message.narration && message.fallback && message.narration !== message.fallback && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-gray-400 hover:text-gray-600">Подробная сводка</summary>
                  <pre className="mt-1 whitespace-pre-wrap text-xs text-gray-600 bg-gray-50 p-2 rounded">{message.fallback}</pre>
                </details>
              )}

              {/* SQL */}
              {message.sql && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-gray-400 hover:text-gray-600">
                    <span className="inline-flex items-center gap-1">
                      SQL запрос {message.fast && <GovIcon name="energy" className="h-3.5 w-3.5 text-amber-500" />}
                    </span>
                  </summary>
                  <pre className="mt-1 overflow-x-auto rounded bg-gray-50 p-2 text-xs">{message.sql}</pre>
                </details>
              )}

              {/* Data table */}
              {message.data && message.data.length > 0 && (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full text-xs border border-gray-100 rounded">
                    <thead className="bg-gray-50">
                      <tr>
                        {message.columns?.map((column) => (
                          <th key={column} className="border-b px-2 py-1 text-left font-medium text-gray-500">
                            {column}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {message.data.slice(0, 10).map((row, rowIndex) => (
                        <tr key={rowIndex} className="border-b border-gray-100 hover:bg-gray-50">
                          {message.columns?.map((column) => (
                            <td key={column} className="max-w-[220px] truncate px-2 py-1">
                              {String(row[column] ?? '')}
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
                    className="mt-2 inline-flex items-center gap-1.5 rounded border border-gray-300 px-3 py-1 text-xs hover:bg-gray-50"
                  >
                    <GovIcon name="download" className="h-3.5 w-3.5" />
                    Скачать XLSX
                  </button>
                </div>
              )}

              {/* Suggestions */}
              {message.role === 'assistant' && message.suggestions?.length > 0 && (
                <div className="mt-3 pt-3 border-t border-gray-100 flex flex-wrap gap-1.5">
                  <span className="text-[11px] text-gray-400 self-center mr-1">Спросить ещё:</span>
                  {message.suggestions.map((s, i) => (
                    <button
                      key={i}
                      onClick={() => send(s)}
                      className="text-xs px-2.5 py-1 bg-gray-50 hover:bg-teal-50 hover:text-teal-700 rounded-full transition"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="rounded-xl bg-white px-4 py-3 shadow-sm flex items-center gap-2">
              <span className="inline-flex gap-1">
                <span className="w-1.5 h-1.5 bg-teal-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-1.5 h-1.5 bg-teal-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-1.5 h-1.5 bg-teal-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </span>
              <p className="text-sm text-gray-500">Анализирую…</p>
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
          placeholder="Задайте вопрос: район, категория, сравнение, топ…"
          className="flex-1 rounded-xl border border-gray-200 px-4 py-3 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-teal-500/30"
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
