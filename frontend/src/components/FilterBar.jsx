import { SEVERITY_ORDER, formatSeverity } from '../utils/severity.js'

export default function FilterBar({ filters, filterCount, chartData, onChange, onReset }) {
  const municipalities = chartData?.districts?.map(d => d.municipality) || []
  const categories = chartData?.categories?.map(c => c.category) || []

  return (
    <div className="bg-white rounded-xl px-4 py-3 shadow-sm flex items-center gap-3 flex-wrap">
      <span className="text-sm text-gray-500">Район</span>
      <select
        value={filters.municipality || ''}
        onChange={e => onChange('municipality', e.target.value || null)}
        className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 bg-white"
      >
        <option value="">Все муниципалитеты</option>
        {municipalities.map(m => <option key={m} value={m}>{m}</option>)}
      </select>

      <span className="text-sm text-gray-500">Тяжесть</span>
      <select
        value={filters.severity || ''}
        onChange={e => onChange('severity', e.target.value || null)}
        className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 bg-white"
      >
        <option value="">Любая</option>
        {SEVERITY_ORDER.map(s => <option key={s} value={s}>{formatSeverity(s)}</option>)}
      </select>

      <span className="text-sm text-gray-500">Категория</span>
      <select
        value={filters.category || ''}
        onChange={e => onChange('category', e.target.value || null)}
        className="text-sm border border-gray-300 rounded-lg px-3 py-1.5 bg-white"
      >
        <option value="">Все категории</option>
        {categories.map(c => <option key={c} value={c}>{c}</option>)}
      </select>

      <div className="flex items-center gap-2 ml-auto">
        <span className="text-xs text-gray-400">Применено: {filterCount}</span>
        {filterCount > 0 && (
          <button onClick={onReset} className="text-xs text-[#0d7377] hover:underline">
            Сбросить
          </button>
        )}
      </div>
    </div>
  )
}
