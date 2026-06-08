import { useEffect, useState } from 'react'
import { getAlerts } from '../api.js'
import GovIcon from './GovIcon.jsx'

const TYPE_STYLE = {
  NEW: { bg: 'bg-blue-50', text: 'text-blue-700', icon: 'new', label: 'Новая проблема' },
  GROWING: { bg: 'bg-red-50', text: 'text-red-700', icon: 'trend', label: 'Резкий рост' },
}

export default function AlertsPanel({ runId, onSelect }) {
  const [data, setData] = useState(null)
  const [collapsed, setCollapsed] = useState(false)

  useEffect(() => {
    if (!runId) return
    getAlerts(runId)
      .then(setData)
      .catch(() => setData(null))
  }, [runId])

  if (!data?.alerts?.length) return null

  const newCount = data.alerts.filter(a => a.type === 'NEW').length
  const growCount = data.alerts.filter(a => a.type === 'GROWING').length

  return (
    <div className="bg-white rounded-xl shadow-sm overflow-hidden">
      <button
        onClick={() => setCollapsed(c => !c)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-50"
      >
        <div className="flex items-center gap-3">
          <GovIcon name="bell" className="h-5 w-5 text-[#0d7377]" />
          <h3 className="text-sm font-semibold text-gray-700">
            Прирост проблем
          </h3>
          <div className="flex gap-2">
            {growCount > 0 && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-50 text-red-700 text-xs font-medium">
                <GovIcon name="trend" className="h-3.5 w-3.5" /> {growCount} растущих
              </span>
            )}
            {newCount > 0 && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 text-xs font-medium">
                <GovIcon name="new" className="h-3.5 w-3.5" /> {newCount} новых
              </span>
            )}
          </div>
        </div>
        <GovIcon name={collapsed ? 'chevronDown' : 'chevronUp'} className="h-4 w-4 text-gray-400" />
      </button>

      {!collapsed && (
        <div className="border-t divide-y divide-gray-100 max-h-[280px] overflow-y-auto">
          {data.alerts.slice(0, 15).map((a, i) => {
            const style = TYPE_STYLE[a.type]
            return (
              <button
                key={i}
                onClick={() => onSelect?.(a.municipality)}
                className="w-full text-left px-4 py-2 hover:bg-gray-50 flex items-start gap-3"
              >
                <span className={`shrink-0 w-7 h-7 rounded-lg ${style.bg} flex items-center justify-center text-sm`}>
                  <GovIcon name={style.icon} className={`h-4 w-4 ${style.text}`} />
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 text-xs mb-0.5">
                    <span className={`font-medium ${style.text}`}>{style.label}</span>
                    {a.growth_x && (
                      <span className="font-semibold text-red-600">×{a.growth_x}</span>
                    )}
                    <span className="text-gray-400">·</span>
                    <span className="text-gray-500">{a.municipality}</span>
                    <span className="text-gray-300">·</span>
                    <span className="text-gray-500">{a.category}</span>
                  </div>
                  <div className="text-sm text-gray-700 truncate" title={a.name}>
                    {a.name}
                  </div>
                  <div className="text-xs text-gray-400">
                    {a.appeal_count} обращений
                    {a.previous_count > 0 && ` (было ${a.previous_count})`}
                  </div>
                </div>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
