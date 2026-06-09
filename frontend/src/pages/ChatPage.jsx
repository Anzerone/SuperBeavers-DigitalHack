import { useEffect, useRef, useState } from 'react'
import { exportChatResult, getChatHistory, resetChat, sendChatMessage } from '../api.js'
import GovIcon from '../components/GovIcon.jsx'

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

const TABLE_ROW_LIMIT = 50

const COLUMN_LABELS = {
  municipality: 'Район',
  problem_count: 'Проблемных обращений',
  appeal_count: 'Обращений',
  rank: 'Ранг',
  avg_rank: 'Средний ранг',
  top_issues: 'Ключевые проблемы',
  summary_text: 'Сводка',
  cluster_name: 'Проблема',
  description: 'Описание',
  explanation: 'Пояснение',
  example: 'Пример обращения',
  category: 'Категория',
  severity: 'Тяжесть',
  centroid_text: 'Выдержка',
  cluster_count: 'Кластеров',
  count: 'Количество',
  severe_count: 'Критичных и высоких',
  muni_count: 'Муниципалитетов',
  municipality_count: 'Муниципалитетов',
  category_count: 'Категорий',
  share_percent: 'Доля, %',
  severe_share_percent: 'Доля тяжелых, %',
  group_name: 'Группа тем',
  incident_type: 'Тип инцидента',
  outcome: 'Итог',
  incident_text: 'Текст обращения',
  confidence: 'Уверенность',
}

function labelColumn(column) {
  return COLUMN_LABELS[column] || column
}

function formatCellValue(value) {
  if (value == null) return ''
  if (Array.isArray(value)) {
    return value.map(formatCellValue).filter(Boolean).join(', ')
  }
  if (typeof value === 'object') {
    if ('name' in value && 'count' in value) return `${value.name} (${value.count})`
    if ('category' in value && 'count' in value) return `${value.category} (${value.count})`
    const pairs = Object.entries(value)
      .map(([key, item]) => `${labelColumn(key)}: ${formatCellValue(item)}`)
      .filter(Boolean)
    return pairs.join(', ')
  }
  return String(value)
}

export default function ChatPage({ runId }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [ctxInfo, setCtxInfo] = useState(null)
  const endRef = useRef()
  const contextCategories = ctxInfo?.last_categories?.length
    ? ctxInfo.last_categories
    : ctxInfo?.last_category
      ? [ctxInfo.last_category]
      : []

  useEffect(() => {
    if (!runId) {
      setMessages([])
      setCtxInfo(null)
      return
    }
    let cancelled = false
    getChatHistory(runId)
      .then((data) => {
        if (cancelled) return
        setCtxInfo(data.context || null)
        const restored = (data.history || []).flatMap((item) => [
          { role: 'user', text: item.question },
          {
            role: 'assistant',
            text: item.answer,
            fallback: item.fallback,
            narration: item.narration,
            sql: item.sql,
            data: item.data,
            columns: item.columns,
            suggestions: item.suggestions || [],
            fast: item.fast,
            kind: item.kind,
          },
        ])
        setMessages(restored)
      })
      .catch(() => {
        if (!cancelled) {
          setMessages([])
          setCtxInfo(null)
        }
      })
    return () => {
      cancelled = true
    }
  }, [runId])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const clearChat = async () => {
    setMessages([])
    setCtxInfo(null)
    if (!runId) return
    try {
      await resetChat(runId)
    } catch {
      // Local cleanup is still useful if the server reset fails.
    }
  }

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
      {messages.length > 0 && (
        <div className="mb-3 flex justify-end">
          <button
            onClick={clearChat}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs text-gray-500 shadow-sm transition hover:border-gray-300 hover:text-gray-800"
          >
            <GovIcon name="trash" className="h-3.5 w-3.5" />
            Очистить
          </button>
        </div>
      )}

      {/* Context bar */}
      {ctxInfo && (ctxInfo.last_municipality || contextCategories.length > 0) && (
        <div className="mb-3 flex items-center gap-2 text-xs text-gray-500">
          <span>Контекст:</span>
          {ctxInfo.last_municipality && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-50 text-indigo-700 rounded-full">
              <GovIcon name="location" className="h-3.5 w-3.5" />
              {ctxInfo.last_municipality}
            </span>
          )}
          {contextCategories.map((category) => (
            <span key={category} className="inline-flex items-center gap-1 px-2 py-0.5 bg-teal-50 text-teal-700 rounded-full">
              <GovIcon name="folder" className="h-3.5 w-3.5" />
              {category}
            </span>
          ))}
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
              {/* Assistant answer */}
              <p className="whitespace-pre-wrap text-sm leading-relaxed">
                {message.text}
              </p>

              {/* Data table */}
              {message.data && message.data.length > 0 && (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full text-xs border border-gray-100 rounded">
                    <thead className="bg-gray-50">
                      <tr>
                        {message.columns?.map((column) => (
                          <th key={column} className="border-b px-2 py-1 text-left font-medium text-gray-500">
                            {labelColumn(column)}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {message.data.slice(0, TABLE_ROW_LIMIT).map((row, rowIndex) => (
                        <tr key={rowIndex} className="border-b border-gray-100 hover:bg-gray-50">
                          {message.columns?.map((column) => {
                            const cellValue = formatCellValue(row[column])
                            return (
                              <td key={column} title={cellValue} className="max-w-[220px] truncate px-2 py-1">
                                {cellValue}
                              </td>
                            )
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {message.data.length > TABLE_ROW_LIMIT && (
                    <p className="mt-1 text-xs text-gray-400">Показано {TABLE_ROW_LIMIT} из {message.data.length}</p>
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
