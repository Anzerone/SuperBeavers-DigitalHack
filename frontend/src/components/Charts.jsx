import { useState, useMemo } from 'react'
import {
  Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Treemap,
} from 'recharts'
import { SEVERITY_COLORS, formatSeverity } from '../utils/severity.js'
import GovIcon from './GovIcon.jsx'

const CAT_ICONS = {
  'ЖКХ': 'home', 'Дороги': 'road', 'Дороги и транспорт': 'road',
  'Образование': 'education', 'Физическая культура и спорт': 'sport',
  'Здравоохранение': 'medical', 'Медицина': 'medical',
  'Благоустройство': 'tree', 'Общественный транспорт': 'bus',
  'Социальное обслуживание и защита': 'social', 'Военная служба': 'shield',
  'Безопасность и правопорядок': 'shield', 'ЧС и безопасность': 'shield',
  'Энергетика': 'energy', 'Экология': 'eco', 'Обращение с отходами': 'waste',
  'Связь и телевидение': 'signal', 'Строительство и архитектура': 'construction',
  'Культура': 'culture', 'Земельные отношения': 'map', 'Торговля и услуги': 'shop',
  'Трудовые отношения': 'work', 'Миграционная политика': 'passport',
  'Туризм': 'travel', 'Молодежная политика': 'youth',
  'Имущественные отношения': 'property', 'Регистрация актов гражд. состояния': 'registry',
  'Ветеринария': 'veterinary', 'Другое': 'document',
}

const CAT_PALETTE = [
  '#2B3990', '#14b8a6', '#0ea5e9', '#6366f1', '#8b5cf6',
  '#ec4899', '#f97316', '#eab308', '#84cc16', '#22c55e',
]

const SEVERITY_RANK = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
}

const TOOLTIP_LABELS = {
  count: 'Количество',
  value: 'Количество',
  size: 'Количество',
}

function tooltipLabel(p) {
  return TOOLTIP_LABELS[p.dataKey] || p.name || TOOLTIP_LABELS[p.name] || 'Количество'
}

function CustomTooltip({ active, payload, label, suffix = '' }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-white shadow-lg border border-gray-200 rounded-lg px-3 py-2 text-xs">
      {label && <p className="font-medium text-gray-800 mb-1">{label}</p>}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ background: p.color || p.fill }} />
          <span className="text-gray-600">{tooltipLabel(p)}:</span>
          <span className="font-semibold text-gray-900">{p.value?.toLocaleString('ru')}{suffix}</span>
        </div>
      ))}
    </div>
  )
}

// Информативная плитка треемапа: название + количество прямо в ячейке.
// `selected` — массив выбранных имён; невыбранные приглушаются (мультивыбор).
function TreemapCell(props) {
  const { x, y, width, height, name, size, fill, selected } = props
  const value = size ?? props.value
  if (width <= 0 || height <= 0) return null
  const showText = width > 50 && height > 24
  const sel = Array.isArray(selected) ? selected : []
  const isSelected = sel.length === 0 || sel.includes(name)
  const isActive = sel.includes(name)
  return (
    <g style={{ cursor: 'pointer' }} opacity={isSelected ? 1 : 0.28}>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        fill={fill}
        stroke={isActive ? '#1F2A6E' : '#fff'}
        strokeWidth={isActive ? 3 : 2}
      />
      {isActive && width > 18 && height > 18 && (
        <text x={x + width - 6} y={y + 14} fontSize={12} fill="#fff" fontWeight="700" textAnchor="end" style={{ pointerEvents: 'none' }}>✓</text>
      )}
      {showText && (
        <>
          <text x={x + 6} y={y + 16} fontSize={11} fill="#fff" fontWeight="600" style={{ pointerEvents: 'none' }}>
            {String(name).length > Math.floor(width / 7) ? String(name).slice(0, Math.floor(width / 7)) + '…' : name}
          </text>
          {height > 38 && (
            <text x={x + 6} y={y + 32} fontSize={12} fill="#fff" fontWeight="700" style={{ pointerEvents: 'none' }}>
              {value?.toLocaleString('ru')}
            </text>
          )}
        </>
      )}
    </g>
  )
}

export default function Charts({ data, filters, onChartClick, activeTab = 'clusters' }) {
  const [catView, setCatView] = useState('list') // list | treemap
  const [distView, setDistView] = useState('bar')  // bar | treemap

  const selectedOf = (key) => {
    const f = filters?.[key]
    return Array.isArray(f) ? f : (f ? [f] : [])
  }
  const hasFilter = (key, val) => selectedOf(key).includes(val)
  const filterLabel = (key) => selectedOf(key).join(', ')
  // Приглушаем невыбранные элементы, когда в этом измерении что-то выбрано —
  // так на самом графике видно мультивыбор.
  const dimOpacity = (key, val) => {
    const sel = selectedOf(key)
    return sel.length === 0 || sel.includes(val) ? 1 : 0.28
  }

  const selMunicipalities = selectedOf('municipality')
  const selCategories = selectedOf('category')
  const selSeverities = selectedOf('severity')

  const districts = (data.districts || []).slice(0, 10)
  const severities = useMemo(() => {
    const severitySource = activeTab === 'appeals' ? (data.severity_appeals || data.severity || []) : (data.severity || [])
    return [...severitySource]
      .filter(s => s?.severity)
      .sort((a, b) => (SEVERITY_RANK[a.severity] ?? 99) - (SEVERITY_RANK[b.severity] ?? 99))
  }, [activeTab, data.severity, data.severity_appeals])
  const categories = data.categories || []
  const topCategories = categories.slice(0, 6)
  const totalSev = severities.reduce((s, d) => s + d.count, 0) || 1
  const totalCat = categories.reduce((s, d) => s + d.count, 0) || 1

  const pieData = useMemo(() => severities.map(s => ({
    name: formatSeverity(s.severity, { short: true }),
    value: s.count,
    fill: SEVERITY_COLORS[s.severity] || '#9ca3af',
    severity: s.severity,
  })), [severities])
  const severityTitle = activeTab === 'appeals' ? 'Тяжесть обращений' : 'Тяжесть кластеров'
  const severityUnit = activeTab === 'appeals' ? 'обращений' : 'кластеров'

  const severeCount = severities
    .filter(s => s.severity === 'CRITICAL' || s.severity === 'HIGH')
    .reduce((sum, s) => sum + s.count, 0)

  // Центр пирога: если есть крит/высокая — показываем их суммарную долю;
  // если их нет — доминирующую severity (чтобы не было сбивающего «0%»).
  const dominant = useMemo(() => {
    if (!severities.length) return null
    const top = [...severities].sort((a, b) => b.count - a.count)[0]
    return top
  }, [severities])

  const hasSevere = severeCount > 0
  const centerPct = hasSevere
    ? Math.round((severeCount / totalSev) * 100)
    : (dominant ? Math.round((dominant.count / totalSev) * 100) : 0)
  const centerLabel = hasSevere
    ? 'крит. + высок.'
    : (dominant ? formatSeverity(dominant.severity, { short: true }).toLowerCase() : 'нет данных')

  const catTreemap = useMemo(() => topCategories.map((c, i) => ({
    name: c.category,
    size: c.count,
    fill: CAT_PALETTE[i % CAT_PALETTE.length],
  })), [topCategories])

  const distTreemap = useMemo(() => districts.map((d, i) => ({
    name: d.municipality,
    size: d.count,
    fill: CAT_PALETTE[i % CAT_PALETTE.length],
  })), [districts])

  const ViewSwitch = ({ value, onChange, options }) => (
    <div className="flex gap-0.5 bg-gray-100 rounded-md p-0.5">
      {options.map(opt => (
        <button
          key={opt.value}
          title={opt.label}
          aria-label={opt.label}
          onClick={() => onChange(opt.value)}
          className={`flex h-8 w-9 items-center justify-center rounded transition ${
            value === opt.value ? 'bg-white shadow-sm text-gray-800 font-medium' : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          <GovIcon name={opt.icon} className="h-4 w-4" />
        </button>
      ))}
    </div>
  )

  return (
    <div className="grid grid-cols-[1fr_280px_280px] gap-3">
      {/* Districts panel */}
      <div className="bg-white rounded-xl p-4 shadow-sm flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-700">Топ-10 районов</h3>
            {selMunicipalities.length > 0 && (
              <p className="text-[11px] text-gray-400">Выбрано: {selMunicipalities.length} · {filterLabel('municipality')}</p>
            )}
          </div>
          <ViewSwitch
            value={distView}
            onChange={setDistView}
            options={[
              { value: 'bar', label: 'Бар', icon: 'bar' },
              { value: 'treemap', label: 'Тримап', icon: 'grid' },
            ]}
          />
        </div>

        {distView === 'bar' ? (
          <div className="space-y-1 overflow-y-auto pr-1" style={{ maxHeight: 250 }}>
            {districts.map((d, i) => {
              const maxCount = districts[0]?.count || 1
              const pct = Math.max(2, Math.round((d.count / maxCount) * 100))
              const active = hasFilter('municipality', d.municipality)
              const dim = selMunicipalities.length > 0 && !active
              return (
                <button
                  key={i}
                  onClick={() => onChartClick('municipality', d.municipality)}
                  title={d.municipality}
                  className={`w-full flex items-center gap-2 px-1.5 py-1 rounded-lg text-left transition focus:outline-none ${
                    active ? 'bg-indigo-50 ring-1 ring-indigo-200' : 'hover:bg-gray-50'
                  } ${dim ? 'opacity-40' : ''}`}
                >
                  <span className="w-[120px] shrink-0 truncate text-xs text-gray-600">{d.municipality}</span>
                  <div className="flex-1 h-4 bg-gray-100 rounded overflow-hidden">
                    <div
                      className="h-full rounded"
                      style={{ width: `${pct}%`, background: active ? '#1F2A6E' : '#2B3990' }}
                    />
                  </div>
                  <span className="w-14 shrink-0 text-right text-xs font-medium text-gray-700">{d.count.toLocaleString('ru')}</span>
                </button>
              )
            })}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={250}>
            <Treemap
              data={distTreemap}
              dataKey="size"
              nameKey="name"
              aspectRatio={1.6}
              stroke="#fff"
              isAnimationActive={false}
              content={<TreemapCell selected={selMunicipalities} />}
              onClick={(d) => d?.name && onChartClick('municipality', d.name)}
            >
              <Tooltip content={<CustomTooltip suffix=" обращ." />} />
            </Treemap>
          </ResponsiveContainer>
        )}
      </div>

      {/* Severity donut */}
      <div className="bg-white rounded-xl p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">{severityTitle}{selSeverities.length > 0 ? ` · выбрано: ${selSeverities.length}` : ''}</h3>
        <div className="relative">
          <ResponsiveContainer width="100%" height={180}>
            <PieChart>
              <Pie
                data={pieData}
                innerRadius={50}
                outerRadius={75}
                dataKey="value"
                cursor="pointer"
                paddingAngle={2}
                onClick={(d) => onChartClick('severity', d.severity)}
              >
                {pieData.map((d, i) => (
                  <Cell
                    key={i}
                    fill={d.fill}
                    fillOpacity={dimOpacity('severity', d.severity)}
                    stroke="#fff"
                    strokeWidth={2}
                  />
                ))}
              </Pie>
              <Tooltip content={<CustomTooltip />} />
            </PieChart>
          </ResponsiveContainer>
          <div
            className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none"
            style={{ top: -10 }}
            title={`${severeCount} критических и высоких ${severityUnit}`}
          >
            <span className="text-xl font-bold leading-tight text-gray-700">{centerPct}%</span>
            <span className="text-[10px] leading-tight text-gray-400">{centerLabel}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 justify-center mt-1">
          {pieData.map((d, i) => (
            <button
              key={i}
              onClick={() => onChartClick('severity', d.severity)}
              className={`flex items-center gap-1 text-xs px-1.5 py-0.5 rounded transition ${
                hasFilter('severity', d.severity) ? 'bg-gray-100 font-medium' : 'hover:bg-gray-50'
              }`}
            >
              <span className="w-2 h-2 rounded-full" style={{ background: d.fill }} />
              {d.name}
            </button>
          ))}
        </div>
      </div>

      {/* Categories with view switch */}
      <div className="bg-white rounded-xl p-4 shadow-sm flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-700">Топ-6 категорий</h3>
            {selCategories.length > 0 && (
              <p className="text-[11px] text-gray-400">Выбрано: {selCategories.length}</p>
            )}
          </div>
          <ViewSwitch
            value={catView}
            onChange={setCatView}
            options={[
              { value: 'list', label: 'Список', icon: 'list' },
              { value: 'treemap', label: 'Тримап', icon: 'grid' },
            ]}
          />
        </div>

        {catView === 'list' && (
          <div className="space-y-2 overflow-y-auto pr-1" style={{ maxHeight: 280 }}>
            {topCategories.map((c, i) => {
              const pct = Math.round(c.count / totalCat * 100)
              const isActive = hasFilter('category', c.category)
              return (
                <button
                  key={i}
                  onClick={() => onChartClick('category', c.category)}
                  className={`w-full text-left flex items-center gap-2 p-1.5 rounded-lg transition ${
                    isActive ? 'bg-indigo-50 ring-1 ring-indigo-200' : 'hover:bg-gray-50'
                  }`}
                  title={c.category}
                >
                  <span className="w-7 h-7 bg-gray-100 rounded-lg flex items-center justify-center text-sm shrink-0">
                    <GovIcon name={CAT_ICONS[c.category] || 'document'} className="h-4 w-4 text-gray-600" />
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between text-xs mb-0.5">
                      <span className="truncate font-medium text-gray-700">{c.category}</span>
                      <span className="text-gray-500 ml-2 shrink-0">{pct}%</span>
                    </div>
                    <div className="h-1 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${pct}%`, background: CAT_PALETTE[i % CAT_PALETTE.length] }}
                      />
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        )}

        {catView === 'treemap' && (
          <ResponsiveContainer width="100%" height={260}>
            <Treemap
              data={catTreemap}
              dataKey="size"
              nameKey="name"
              aspectRatio={1.6}
              stroke="#fff"
              isAnimationActive={false}
              content={<TreemapCell selected={selCategories} />}
              onClick={(d) => d?.name && onChartClick('category', d.name)}
            >
              <Tooltip content={<CustomTooltip suffix=" обращ." />} />
            </Treemap>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}
