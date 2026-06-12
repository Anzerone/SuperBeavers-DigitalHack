import { useEffect, useState } from 'react'
import { getComparison } from '../api.js'
import GovIcon from './GovIcon.jsx'

function signed(value) {
  if (value == null) return '—'
  const abs = Math.abs(value).toLocaleString('ru')
  if (value > 0) return `+${abs}`
  if (value < 0) return `-${abs}`
  return '0'
}

function ChangeList({ title, items, nameKey = 'name' }) {
  if (!items?.length) return null
  return (
    <div>
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">{title}</h4>
      <div className="space-y-1">
        {items.slice(0, 5).map((item, index) => (
          <div key={`${item[nameKey]}:${index}`} className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 hover:bg-gray-50">
            <span className="truncate text-xs text-gray-700" title={item[nameKey] || item.name}>{item[nameKey] || item.name}</span>
            <span className={`shrink-0 text-xs font-semibold ${item.delta > 0 ? 'text-red-600' : item.delta < 0 ? 'text-emerald-600' : 'text-gray-500'}`}>
              {signed(item.delta)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function ComparisonPanel({ runId, refreshKey }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    if (!runId) return
    getComparison(runId)
      .then(setData)
      .catch(() => setData(null))
  }, [runId, refreshKey])

  if (!data?.has_previous) return null

  const change = data.cluster_change || {}

  return (
    <section className="rounded-xl bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-700">
            <GovIcon name="trend" className="h-4 w-4 text-[#2B3990]" />
            Сравнение с прошлой загрузкой
          </h3>
        </div>
        <div className={`text-right ${data.delta > 0 ? 'text-red-600' : data.delta < 0 ? 'text-emerald-600' : 'text-gray-700'}`}>
          <p className="text-2xl font-bold">{signed(data.delta)}</p>
          <p className="text-xs">обращений</p>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-4 gap-2">
        {[
          ['Новые', change.new_clusters, change.new_appeals, 'bg-blue-50 text-blue-700', 'Проблемы, которых не было в прошлой загрузке'],
          ['Закрыты', change.resolved_clusters, change.resolved_appeals, 'bg-emerald-50 text-emerald-700', 'Проблемы из прошлой загрузки, которые больше не встречаются'],
          ['Продолжаются', change.continuing_clusters, change.continuing_appeals, 'bg-gray-50 text-gray-700', 'Похожие проблемы есть в обеих загрузках'],
          ['Растут', change.growing_clusters, null, 'bg-red-50 text-red-700', 'Продолжающиеся проблемы, по которым обращений стало больше'],
        ].map(([label, count, appeals, cls, hint]) => (
          <div key={label} className={`rounded-lg px-3 py-2 ${cls}`} title={hint}>
            <p className="text-xs">{label}</p>
            <p className="text-lg font-bold">{(count || 0).toLocaleString('ru')}</p>
            {appeals != null && <p className="text-[11px] opacity-75">{appeals.toLocaleString('ru')} обращ.</p>}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <ChangeList title="Категории с главным изменением" items={data.categories || []} />
        <ChangeList title="Районы с главным изменением" items={data.municipalities || []} />
      </div>
    </section>
  )
}
