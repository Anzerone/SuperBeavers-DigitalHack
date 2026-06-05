import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, Legend } from 'recharts'
import { SEVERITY_COLORS, formatSeverity } from '../utils/severity.js'

const CAT_ICONS = {
  'ЖКХ': '🏠', 'Дороги': '🚗', 'Дороги и транспорт': '🚗',
  'Образование': '🎓', 'Физическая культура и спорт': '⚽',
  'Здравоохранение': '🏥', 'Медицина': '🏥',
  'Благоустройство': '🌳', 'Общественный транспорт': '🚌',
  'Социальное обслуживание и защита': '🤝', 'Военная служба': '🪖',
  'Безопасность и правопорядок': '🚨', 'ЧС и безопасность': '🚨',
  'Энергетика': '⚡', 'Экология': '🍃', 'Обращение с отходами': '🗑️',
  'Связь и телевидение': '📡', 'Строительство и архитектура': '🏗️',
  'Культура': '🎭', 'Земельные отношения': '🗺️', 'Торговля и услуги': '🛒',
  'Трудовые отношения': '💼', 'Миграционная политика': '🛂',
  'Туризм': '🧳', 'Молодежная политика': '🎒',
  'Имущественные отношения': '🏘️', 'Регистрация актов гражд. состояния': '💍',
  'Ветеринария': '🐾', 'Другое': '📋',
}

export default function Charts({ data, filters, onChartClick }) {
  const districts = (data.districts || []).slice(0, 10)
  const severities = data.severity || []
  const categories = data.categories || []
  const topCategories = categories.slice(0, 6)
  const totalSev = severities.reduce((s, d) => s + d.count, 0) || 1
  const totalCat = categories.reduce((s, d) => s + d.count, 0) || 1

  // Donut data with labels
  const pieData = severities.map(s => ({
    name: formatSeverity(s.severity, { short: true }),
    value: s.count,
    fill: SEVERITY_COLORS[s.severity] || '#9ca3af',
    severity: s.severity,
  }))

  const severeCount = severities
    .filter(s => s.severity === 'CRITICAL' || s.severity === 'HIGH')
    .reduce((sum, s) => sum + s.count, 0)
  const centerPct = severities.length > 0
    ? Math.round(severeCount / totalSev * 100)
    : 0

  return (
    <div className="grid grid-cols-[1fr_250px_250px] gap-3">
      {/* Top-10 districts bar chart */}
      <div className="bg-white rounded-xl p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">Топ-10 районов</h3>
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={districts} layout="vertical" margin={{ left: 120, right: 30 }}>
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="municipality"
              width={110}
              tick={{ fontSize: 11 }}
            />
            <Tooltip formatter={(v) => [v, 'Обращений']} />
            <Bar
              dataKey="count"
              fill="#0d7377"
              radius={[0, 4, 4, 0]}
              cursor="pointer"
              onClick={(d) => onChartClick('municipality', d.municipality)}
              label={{ position: 'right', fontSize: 11 }}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Severity donut */}
      <div className="bg-white rounded-xl p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">Тяжесть</h3>
        <div className="relative">
          <ResponsiveContainer width="100%" height={180}>
            <PieChart>
              <Pie
                data={pieData}
                innerRadius={50}
                outerRadius={75}
                dataKey="value"
                cursor="pointer"
                onClick={(d) => onChartClick('severity', d.severity)}
              >
                {pieData.map((d, i) => <Cell key={i} fill={d.fill} />)}
              </Pie>
              <Tooltip formatter={(v, name) => [v, name]} />
            </PieChart>
          </ResponsiveContainer>
          <div
            className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none"
            style={{ top: -10 }}
            title={`${severeCount} критических и высоких обращений`}
          >
            <span className="text-xl font-bold leading-tight text-gray-700">{centerPct}%</span>
            <span className="text-[10px] leading-tight text-gray-400">крит. + высок.</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 justify-center mt-1">
          {pieData.map((d, i) => (
            <div key={i} className="flex items-center gap-1 text-xs">
              <span className="w-2 h-2 rounded-full" style={{ background: d.fill }} />
              {d.name}
            </div>
          ))}
        </div>
      </div>

      {/* Categories list (scrollable) */}
      <div className="bg-white rounded-xl p-4 shadow-sm flex flex-col">
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-700">Топ-6 категорий</h3>
          <span className="text-xs text-gray-400">{topCategories.length}</span>
        </div>
        <div className="space-y-2 overflow-y-auto pr-1" style={{ maxHeight: 280 }}>
          {topCategories.map((c, i) => (
            <div
              key={i}
              className="flex items-center gap-2 p-1.5 rounded-lg hover:bg-gray-50 cursor-pointer"
              onClick={() => onChartClick('category', c.category)}
              title={c.category}
            >
              <span className="w-7 h-7 bg-gray-100 rounded-lg flex items-center justify-center text-sm shrink-0">
                {CAT_ICONS[c.category] || '📋'}
              </span>
              <span className="text-sm flex-1 truncate">{c.category}</span>
              <span className="text-sm font-medium text-gray-500 shrink-0">
                {Math.round(c.count / totalCat * 100)}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
