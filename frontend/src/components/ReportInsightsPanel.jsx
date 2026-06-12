import { useEffect, useMemo, useState } from 'react'
import { getReportInsights } from '../api.js'
import GovIcon from './GovIcon.jsx'

export default function ReportInsightsPanel({ runId, refreshKey, onSelectCategory, onSelectMunicipality }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    if (!runId) return
    getReportInsights(runId)
      .then(setData)
      .catch(() => setData(null))
  }, [runId, refreshKey])

  const matrix = data?.category_municipality_matrix || []
  const categories = (data?.top_categories || []).slice(0, 5).map(item => item.category)
  const municipalities = (data?.top_municipalities || []).slice(0, 6).map(item => item.municipality)
  const lookup = useMemo(() => {
    const out = new Map()
    matrix.forEach(item => out.set(`${item.municipality}|${item.category}`, item.count))
    return out
  }, [matrix])
  const maxCell = Math.max(...matrix.map(item => item.count || 0), 1)

  if (!data) return null

  return (
    <section className="rounded-xl bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-700">
            <GovIcon name="report" className="h-4 w-4 text-[#2B3990]" />
            Расширенный срез отчета
          </h3>
          <p className="text-xs text-gray-400">Где пересекаются крупнейшие категории и районы</p>
        </div>
      </div>

      <div className="grid grid-cols-[1fr_280px] gap-4">
        <div className="overflow-x-auto">
          <table className="w-full table-fixed text-xs">
            <thead>
              <tr>
                <th className="w-36 px-2 py-1 text-left font-medium text-gray-400">Район</th>
                {categories.map(category => (
                  <th key={category} className="px-2 py-1 text-left font-medium text-gray-400">
                    <button
                      onClick={() => onSelectCategory?.(category)}
                      className="max-w-full truncate hover:text-[#2B3990]"
                      title={category}
                    >
                      {category}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {municipalities.map(municipality => (
                <tr key={municipality} className="border-t border-gray-100">
                  <td className="px-2 py-1.5">
                    <button
                      onClick={() => onSelectMunicipality?.(municipality)}
                      className="max-w-full truncate font-medium text-gray-700 hover:text-[#2B3990]"
                      title={municipality}
                    >
                      {municipality}
                    </button>
                  </td>
                  {categories.map(category => {
                    const value = lookup.get(`${municipality}|${category}`) || 0
                    const opacity = value ? Math.max(0.15, value / maxCell) : 0
                    return (
                      <td key={category} className="px-2 py-1.5">
                        <div
                          className="rounded px-2 py-1 text-right font-semibold"
                          style={{
                            backgroundColor: value ? `rgba(43, 57, 144, ${opacity})` : '#f3f4f6',
                            color: opacity > 0.45 ? '#fff' : '#374151',
                          }}
                        >
                          {value || '—'}
                        </div>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Долго открытые зоны</h4>
          <div className="space-y-1.5">
            {(data.long_open || []).slice(0, 6).map(item => (
              <button
                key={item.municipality}
                onClick={() => onSelectMunicipality?.(item.municipality)}
                className="w-full rounded-lg px-2 py-1.5 text-left hover:bg-gray-50"
              >
                <div className="flex items-center justify-between gap-3 text-xs">
                  <span className="truncate font-medium text-gray-700" title={item.municipality}>{item.municipality}</span>
                  <span className="shrink-0 text-gray-500">{item.avg_age_days} дн.</span>
                </div>
                <p className="text-[11px] text-gray-400">{item.count.toLocaleString('ru')} открытых проблем</p>
              </button>
            ))}
            {(data.long_open || []).length === 0 && (
              <p className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700">Долго открытых проблем не найдено</p>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
