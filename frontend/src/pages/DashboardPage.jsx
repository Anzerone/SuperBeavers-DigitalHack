import { useState, useEffect, useCallback } from 'react'
import FileUpload from '../components/FileUpload.jsx'
import ProcessingStatus from '../components/ProcessingStatus.jsx'
import MetricCards from '../components/MetricCards.jsx'
import FilterBar from '../components/FilterBar.jsx'
import Charts from '../components/Charts.jsx'
import OmskMap from '../components/OmskMap.jsx'
import Timeline from '../components/Timeline.jsx'
import AlertsPanel from '../components/AlertsPanel.jsx'
import FilterPresets from '../components/FilterPresets.jsx'
import DataTabs from '../components/DataTabs.jsx'
import { getStats, getChartData, exportAppeals, downloadReport } from '../api.js'
import { formatSeverity } from '../utils/severity.js'
import GovIcon from '../components/GovIcon.jsx'

function formatFilterValue(key, value) {
  return key === 'severity' ? formatSeverity(value) : value
}

const stripKey = (obj, key) => {
  const next = { ...obj }
  delete next[key]
  return next
}

export default function DashboardPage({ runId, setRunId }) {
  const [stats, setStats] = useState(null)
  const [chartData, setChartData] = useState(null)
  const [filterOptions, setFilterOptions] = useState({ municipalities: [], categories: [] })
  const [filters, setFilters] = useState({})
  const [filterCount, setFilterCount] = useState(0)
  const [dataRefreshKey, setDataRefreshKey] = useState(0)

  // Полный (неотфильтрованный) список вариантов для выпадающих фильтров —
  // чтобы выбор одного значения не убирал остальные из списка.
  useEffect(() => {
    if (!runId) return
    let cancelled = false
    getChartData(runId, {})
      .then(d => {
        if (cancelled) return
        setFilterOptions({
          municipalities: (d.districts || []).map(x => x.municipality),
          categories: (d.categories || []).map(x => x.category),
        })
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [runId])

  const loadData = useCallback(async () => {
    if (!runId) return
    try {
      // Кросс-фильтрация: каждый график фильтруем по ДРУГИМ измерениям, но не
      // по своему собственному — так все его значения остаются видимыми и
      // кликабельными (можно выбрать несколько прямо на графике).
      const [s, dDist, dCat, dSev] = await Promise.all([
        getStats(runId),
        getChartData(runId, stripKey(filters, 'municipality')),
        getChartData(runId, stripKey(filters, 'category')),
        getChartData(runId, stripKey(filters, 'severity')),
      ])
      setStats(s)
      setChartData({
        districts: dDist.districts || [],
        categories: dCat.categories || [],
        severity: dSev.severity || [],
      })
    } catch (e) {
      console.error('Failed to load data:', e)
    }
  }, [runId, filters])

  useEffect(() => { loadData() }, [loadData])

  // Каждый фильтр хранит массив выбранных значений (мультивыбор).
  const toggleFilter = (key, value) => {
    if (!value) return
    setFilters(prev => {
      const next = { ...prev }
      const current = Array.isArray(next[key]) ? next[key] : (next[key] ? [next[key]] : [])
      const exists = current.includes(value)
      const updated = exists ? current.filter(v => v !== value) : [...current, value]
      if (updated.length) next[key] = updated
      else delete next[key]
      return next
    })
  }

  // Полная замена набора значений для одного фильтра (из выпадающего списка).
  const setFilterValues = (key, values) => {
    setFilters(prev => {
      const next = { ...prev }
      if (values && values.length) next[key] = values
      else delete next[key]
      return next
    })
  }

  useEffect(() => {
    const count = Object.values(filters).reduce(
      (sum, v) => sum + (Array.isArray(v) ? v.length : v ? 1 : 0),
      0,
    )
    setFilterCount(count)
  }, [filters])

  const resetFilters = () => setFilters({})

  const handleChartClick = (key, value) => toggleFilter(key, value)

  const handleProcessingComplete = useCallback(() => {
    loadData()
    setDataRefreshKey(key => key + 1)
  }, [loadData])

  return (
    <div className="flex gap-4 p-4 max-w-[1400px] mx-auto">
      {/* Left sidebar */}
      <div className="w-[280px] shrink-0 flex flex-col gap-4">
        <FileUpload runId={runId} setRunId={setRunId} />
        <ProcessingStatus runId={runId} onComplete={handleProcessingComplete} />
      </div>

      {/* Main area */}
      <div className="flex-1 flex flex-col gap-4 min-w-0">
        {/* Top bar: title + active filters chips + download */}
        <div className="bg-white rounded-xl p-4 shadow-sm sticky top-0 z-10">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-gray-800">Сводка по текущей загрузке</h2>
              <p className="text-xs text-gray-400">Кликните по графику чтобы отфильтровать — таблицы и экспорт обновятся автоматически</p>
            </div>
            <div className="flex gap-2 shrink-0 items-center">
              <FilterPresets filters={filters} onApply={setFilters} />
              <button
                onClick={() => runId && downloadReport(runId)}
                disabled={!runId}
                className="px-4 py-2 bg-[#0d7377] text-white rounded-lg text-sm font-medium hover:bg-[#0a5c5f] disabled:opacity-40 transition"
              >
                <span className="inline-flex items-center gap-2">
                  <GovIcon name="report" className="h-4 w-4" />
                  Отчет
                </span>
              </button>
            </div>
          </div>
          {filterCount > 0 && (
            <div className="flex items-center gap-2 mt-3 pt-3 border-t border-gray-100 flex-wrap">
              <span className="text-xs text-gray-500">Активные фильтры:</span>
              {Object.entries(filters).flatMap(([k, v]) => {
                const values = Array.isArray(v) ? v : (v ? [v] : [])
                return values.map(val => (
                  <button
                    key={`${k}:${val}`}
                    onClick={() => toggleFilter(k, val)}
                    className="flex items-center gap-1 px-2 py-0.5 bg-teal-50 text-teal-700 rounded-full text-xs font-medium hover:bg-teal-100 transition"
                    title="Кликните чтобы убрать"
                  >
                    {formatFilterValue(k, val)} <GovIcon name="close" className="h-3 w-3 text-teal-400" />
                  </button>
                ))
              })}
              <button
                onClick={resetFilters}
                className="text-xs text-gray-500 hover:text-gray-800 underline ml-1"
              >
                Сбросить все
              </button>
            </div>
          )}
        </div>

        {/* Metrics */}
        {stats && <MetricCards stats={stats} />}

        {/* Alerts (если есть предыдущий run) */}
        {runId && (
          <AlertsPanel
            runId={runId}
            onSelect={(m) => toggleFilter('municipality', m)}
          />
        )}

        {/* Filters */}
        <FilterBar
          filters={filters}
          filterCount={filterCount}
          municipalities={filterOptions.municipalities}
          categories={filterOptions.categories}
          onChange={setFilterValues}
          onReset={resetFilters}
        />

        {/* Map of Omsk Oblast */}
        {chartData?.districts?.length > 0 && (
          <OmskMap
            districts={chartData.districts}
            activeMunicipality={filters.municipality}
            onSelect={(m) => handleChartClick('municipality', m)}
          />
        )}

        {/* Charts */}
        {chartData && (
          <Charts data={chartData} filters={filters} onChartClick={handleChartClick} />
        )}

        {/* Timeline */}
        {runId && <Timeline runId={runId} filters={filters} refreshKey={dataRefreshKey} />}

        {/* Tables */}
        {runId && (
          <DataTabs runId={runId} filters={filters} refreshKey={dataRefreshKey} onExport={() => exportAppeals(runId, filters)} />
        )}
      </div>
    </div>
  )
}
