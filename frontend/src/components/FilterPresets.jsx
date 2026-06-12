import { useEffect, useMemo, useState } from 'react'
import { deleteFilterPreset, getFilterPresets, saveFilterPreset } from '../api.js'
import GovIcon from './GovIcon.jsx'

const FILTER_LABELS = {
  municipality: 'район',
  severity: 'тяжесть',
  category: 'категория',
}

function activeFilterCount(filters = {}) {
  return Object.values(filters).reduce((sum, value) => {
    if (Array.isArray(value)) return sum + value.length
    return sum + (value ? 1 : 0)
  }, 0)
}

function formatFilters(filters = {}) {
  const parts = Object.entries(filters)
    .filter(([, value]) => Array.isArray(value) ? value.length > 0 : Boolean(value))
    .map(([key, value]) => {
      const label = FILTER_LABELS[key] || key
      const display = Array.isArray(value) ? value.join(', ') : value
      return `${label}: ${display}`
    })
  return parts.join(' · ') || 'пусто'
}

export default function FilterPresets({ filters, onApply }) {
  const [presets, setPresets] = useState([])
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const activeCount = activeFilterCount(filters)
  const sortedPresets = useMemo(
    () => [...presets].sort((a, b) => (b.ts || 0) - (a.ts || 0)),
    [presets]
  )

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getFilterPresets()
      .then(items => {
        if (!cancelled) setPresets(items)
      })
      .catch(() => {
        if (!cancelled) setError('Не удалось загрузить пресеты')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleSave = async () => {
    const presetName = name.trim()
    if (!presetName || activeCount === 0 || saving) return
    setSaving(true)
    setError('')
    try {
      const saved = await saveFilterPreset(presetName, { ...filters })
      setPresets(current => [
        saved,
        ...current.filter(p => p.id !== saved.id && p.name !== saved.name),
      ])
      setName('')
      setOpen(false)
    } catch {
      setError('Не удалось сохранить пресет')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (presetId) => {
    if (!presetId) return
    setError('')
    try {
      await deleteFilterPreset(presetId)
      setPresets(current => current.filter(p => p.id !== presetId))
    } catch {
      setError('Не удалось удалить пресет')
    }
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="px-3 py-1.5 bg-white border border-gray-200 rounded-lg text-xs font-medium hover:bg-gray-50 flex items-center gap-1"
      >
        <GovIcon name="star" className="h-3.5 w-3.5 text-[#2B3990]" />
        Пресеты {presets.length > 0 && <span className="text-gray-400">({presets.length})</span>}
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 w-72 bg-white rounded-xl shadow-lg border border-gray-100 z-30"
          onMouseLeave={() => setOpen(false)}
        >
          <div className="p-3 border-b">
            <p className="text-xs text-gray-500 mb-2">Сохранить текущие фильтры</p>
            <div className="flex gap-1">
              <input
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="Например: «Мои районы»"
                className="flex-1 text-xs border border-gray-200 rounded px-2 py-1"
                onKeyDown={e => e.key === 'Enter' && handleSave()}
              />
              <button
                onClick={handleSave}
                disabled={!name.trim() || activeCount === 0 || saving}
                className="flex h-7 w-7 items-center justify-center bg-indigo-600 text-white rounded text-xs disabled:opacity-40"
                title={activeCount === 0 ? 'Сначала задайте фильтры' : 'Сохранить'}
              >
                <GovIcon name="plus" className="h-3.5 w-3.5" />
              </button>
            </div>
            {activeCount === 0 && (
              <p className="text-[10px] text-gray-400 mt-1">Сначала выберите фильтры на дашборде</p>
            )}
            {error && <p className="text-[10px] text-red-500 mt-1">{error}</p>}
          </div>

          <div className="max-h-60 overflow-y-auto">
            {loading && (
              <p className="px-3 py-4 text-xs text-gray-400 text-center">Загрузка...</p>
            )}
            {!loading && sortedPresets.length === 0 && (
              <p className="px-3 py-4 text-xs text-gray-400 text-center">Пока нет сохранённых пресетов</p>
            )}
            {!loading && sortedPresets.map(preset => (
              <div key={preset.id} className="px-3 py-2 hover:bg-gray-50 flex items-center justify-between gap-2">
                <button
                  onClick={() => { onApply(preset.filters || {}); setOpen(false) }}
                  className="flex-1 min-w-0 text-left"
                >
                  <div className="text-sm font-medium text-gray-700 truncate">{preset.name}</div>
                  <div className="text-[10px] text-gray-500 truncate">
                    {formatFilters(preset.filters)}
                  </div>
                </button>
                <button
                  onClick={() => handleDelete(preset.id)}
                  className="text-gray-300 hover:text-red-500 text-xs"
                  title="Удалить"
                  aria-label="Удалить"
                >
                  <GovIcon name="trash" className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
