import { useEffect, useMemo, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from 'recharts'
import { getTimeline } from '../api.js'

const TOTAL_COLOR = '#64748b'
const CAT_COLORS = ['#16a34a', '#f97316', '#0ea5e9', '#6366f1', '#ec4899', '#a855f7']

function formatPeriods(count) {
  const mod10 = count % 10
  const mod100 = count % 100
  if (mod10 === 1 && mod100 !== 11) return `${count} период`
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} периода`
  return `${count} периодов`
}

function TimelineTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null

  const total = payload[0]?.payload?.count
  const rows = [...payload].sort((a, b) => (b.value ?? 0) - (a.value ?? 0))

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-lg px-3 py-2 text-xs" style={{ background: '#fff' }}>
      <p className="font-semibold text-gray-800 mb-1">{label}</p>
      {typeof total === 'number' && !rows.some(p => p.name === 'Всего') && (
        <div className="flex items-center justify-between gap-3 pb-1 mb-1 border-b border-gray-100">
          <span className="flex items-center gap-1.5 font-semibold text-gray-800">
            <span className="w-2 h-2 rounded-full" style={{ background: TOTAL_COLOR }} />
            Всего
          </span>
          <span className="font-bold text-gray-900">{total.toLocaleString('ru')}</span>
        </div>
      )}
      {rows.map((p, i) => (
        <div key={i} className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full" style={{ background: p.color }} />
            <span className={p.name === 'Всего' ? 'font-semibold text-gray-800' : 'text-gray-600'}>{p.name}</span>
          </span>
          <span className={p.name === 'Всего' ? 'font-bold text-gray-900' : 'font-medium text-gray-700'}>
            {p.value?.toLocaleString('ru')}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function Timeline({ runId, filters, refreshKey = 0 }) {
  const [granularity, setGranularity] = useState('week')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!runId) return
    let cancelled = false
    setLoading(true)
    getTimeline(runId, filters, granularity)
      .then(next => {
        if (!cancelled) setData(next)
      })
      .catch(() => {
        if (!cancelled) setData(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [runId, filters, granularity, refreshKey])

  const categories = data?.top_categories || []
  const categoryCount = categories.length
  const shouldShowChart = categoryCount > 3
  const showCategoryLines = categoryCount > 0

  const chartData = useMemo(() => {
    if (!data?.timeline?.length) return []
    return data.timeline.map(d => {
      const point = {
        date: d.date.slice(0, 10),
        count: d.count,
      }
      const catSlice = data.by_category?.[d.date] || {}
      for (const cat of categories) {
        point[cat] = catSlice[cat] || 0
      }
      return point
    })
  }, [data, categories])

  if (!loading && data && !shouldShowChart) return null

  return (
    <div className="bg-white rounded-xl p-4 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-700">Динамика обращений во времени</h3>
          <p className="text-[11px] text-gray-400">{formatPeriods(chartData.length)} · топ категорий</p>
        </div>
        <div className="flex gap-0.5 bg-gray-100 rounded-md p-0.5">
          {[
            { v: 'day', l: 'По дням' },
            { v: 'week', l: 'По неделям' },
            { v: 'month', l: 'По месяцам' },
          ].map(opt => (
            <button
              key={opt.v}
              onClick={() => setGranularity(opt.v)}
              className={`px-2 py-0.5 text-[10px] uppercase tracking-wide rounded transition ${
                granularity === opt.v ? 'bg-white shadow-sm text-gray-800 font-medium' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {opt.l}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="h-[220px] flex items-center justify-center text-gray-400 text-sm">Загрузка...</div>
      ) : !chartData.length ? (
        <div className="h-[220px] flex items-center justify-center text-gray-400 text-sm">
          Нет данных по датам для выбранных фильтров
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={chartData} margin={{ left: 0, right: 10, top: 10, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={20} />
            <YAxis tick={{ fontSize: 10 }} />
            <Tooltip content={<TimelineTooltip />} />
            <Legend wrapperStyle={{ fontSize: 11 }} iconSize={8} />
            {!showCategoryLines && (
              <Line
                type="monotone"
                dataKey="count"
                stroke={TOTAL_COLOR}
                strokeWidth={2.5}
                dot={false}
                name="Всего"
              />
            )}
            {categories.map((cat, i) => (
              <Line
                key={cat}
                type="monotone"
                dataKey={cat}
                stroke={CAT_COLORS[i % CAT_COLORS.length]}
                strokeWidth={2}
                dot={false}
                opacity={0.9}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
