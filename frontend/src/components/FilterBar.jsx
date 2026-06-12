import { useEffect, useRef, useState } from 'react'
import { SEVERITY_ORDER, formatSeverity } from '../utils/severity.js'

function asArray(value) {
  if (Array.isArray(value)) return value
  return value ? [value] : []
}

// Компактный выпадающий список с чекбоксами — позволяет выбрать
// несколько значений одного фильтра одновременно.
function MultiSelect({ label, placeholder, options, selected, onChange, formatOption }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const toggle = (value) => {
    if (selected.includes(value)) onChange(selected.filter(v => v !== value))
    else onChange([...selected, value])
  }

  const summary = selected.length === 0
    ? placeholder
    : selected.length === 1
      ? (formatOption ? formatOption(selected[0]) : selected[0])
      : `Выбрано: ${selected.length}`

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={`text-sm border rounded-lg px-3 py-1.5 bg-white flex items-center gap-2 min-w-[160px] max-w-[240px] ${
          selected.length ? 'border-[#2B3990] text-[#2B3990]' : 'border-gray-300 text-gray-700'
        }`}
      >
        <span className="truncate flex-1 text-left">{summary}</span>
        <svg className="h-3.5 w-3.5 shrink-0 opacity-60" viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.06l3.71-3.83a.75.75 0 111.08 1.04l-4.25 4.39a.75.75 0 01-1.08 0L5.21 8.27a.75.75 0 01.02-1.06z" clipRule="evenodd" />
        </svg>
      </button>

      {open && (
        <div className="absolute z-30 mt-1 w-64 max-h-72 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg p-1">
          {selected.length > 0 && (
            <button
              type="button"
              onClick={() => onChange([])}
              className="w-full text-left text-xs text-gray-500 hover:text-gray-800 px-2 py-1"
            >
              Очистить «{label}»
            </button>
          )}
          {options.length === 0 && (
            <div className="px-2 py-2 text-xs text-gray-400">Нет вариантов</div>
          )}
          {options.map(opt => (
            <label
              key={opt}
              className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-gray-50 cursor-pointer text-sm"
            >
              <input
                type="checkbox"
                checked={selected.includes(opt)}
                onChange={() => toggle(opt)}
                className="accent-[#2B3990]"
              />
              <span className="truncate">{formatOption ? formatOption(opt) : opt}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

export default function FilterBar({ filters, filterCount, municipalities = [], categories = [], onChange, onReset }) {
  return (
    <div className="bg-white rounded-xl px-4 py-3 shadow-sm flex items-center gap-3 flex-wrap">
      <span className="text-sm text-gray-500">Район</span>
      <MultiSelect
        label="Район"
        placeholder="Все муниципалитеты"
        options={municipalities}
        selected={asArray(filters.municipality)}
        onChange={(values) => onChange('municipality', values)}
      />

      <span className="text-sm text-gray-500">Тяжесть</span>
      <MultiSelect
        label="Тяжесть"
        placeholder="Любая"
        options={SEVERITY_ORDER}
        selected={asArray(filters.severity)}
        onChange={(values) => onChange('severity', values)}
        formatOption={(s) => formatSeverity(s)}
      />

      <span className="text-sm text-gray-500">Категория</span>
      <MultiSelect
        label="Категория"
        placeholder="Все категории"
        options={categories}
        selected={asArray(filters.category)}
        onChange={(values) => onChange('category', values)}
      />

      <div className="flex items-center gap-2 ml-auto">
        <span className="text-xs text-gray-400">Применено: {filterCount}</span>
        {filterCount > 0 && (
          <button onClick={onReset} className="text-xs text-[#2B3990] hover:underline">
            Сбросить
          </button>
        )}
      </div>
    </div>
  )
}
