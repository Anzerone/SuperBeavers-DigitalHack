import { useEffect, useState } from 'react'
import { getSimilar } from '../api.js'
import { SEVERITY_STYLES, formatSeverity } from '../utils/severity.js'
import GovIcon from './GovIcon.jsx'

function displaySeverity(item) {
  return item?.severity || 'MEDIUM'
}

export default function SimilarModal({ appeal, onClose }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)

  useEffect(() => {
    setLoading(true)
    getSimilar(appeal.id, 15)
      .then(setData)
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false))
  }, [appeal.id])

  return (
    <div
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-xl max-w-3xl w-full max-h-[85vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-5 py-3 border-b flex items-center justify-between">
          <div>
            <h3 className="font-semibold text-gray-800">Похожие обращения</h3>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700" aria-label="Закрыть">
            <GovIcon name="close" className="h-5 w-5" />
          </button>
        </div>

        <div className="px-5 py-3 bg-gray-50 border-b">
          <p className="text-xs text-gray-500 mb-1">Исходное обращение #{appeal.id}</p>
          <p className="text-sm text-gray-700 whitespace-pre-wrap break-words">{appeal.full_text || appeal.incident_text}</p>
        </div>

        <div className="overflow-y-auto flex-1">
          {loading && <div className="px-5 py-8 text-center text-gray-400 text-sm">Поиск похожих…</div>}
          {err && <div className="px-5 py-8 text-center text-red-500 text-sm">Ошибка: {err}</div>}
          {data?.items?.length === 0 && !loading && (
            <div className="px-5 py-8 text-center text-gray-400 text-sm">Похожие не найдены</div>
          )}
          {data?.items?.map((item, i) => {
            const severity = displaySeverity(item)
            return (
              <div key={item.id} className="px-5 py-3 border-b last:border-b-0 hover:bg-gray-50">
                <div className="flex items-baseline justify-between mb-1 gap-2">
                  <div className="text-xs text-gray-500 flex items-center gap-2 flex-wrap">
                  <span className="font-mono">#{item.id}</span>
                  <span className="px-1.5 py-0.5 bg-teal-50 text-teal-700 rounded font-medium">
                    {(item.similarity * 100).toFixed(1)}% совп.
                  </span>
                  <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${SEVERITY_STYLES[severity] || ''}`}>
                    {formatSeverity(severity)}
                  </span>
                  <span>{item.municipality}</span>
                  <span className="text-gray-400">· {item.category || item.group_name || 'Другое'}</span>
                </div>
                <span className="text-xs text-gray-400 shrink-0">{i + 1}</span>
              </div>
              <p className="text-sm text-gray-700 whitespace-pre-wrap break-words">{item.incident_text}</p>
              {item.outcome && (
                <p className="text-xs text-gray-400 mt-1">Итог: {item.outcome}</p>
              )}
            </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
