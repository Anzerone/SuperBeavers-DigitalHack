import { useState, useEffect, useCallback } from 'react'
import FileUpload from '../components/FileUpload.jsx'
import ProcessingStatus from '../components/ProcessingStatus.jsx'
import MetricCards from '../components/MetricCards.jsx'
import FilterBar from '../components/FilterBar.jsx'
import Charts from '../components/Charts.jsx'
import OmskMap from '../components/OmskMap.jsx'
import Timeline from '../components/Timeline.jsx'
import ComparisonPanel from '../components/ComparisonPanel.jsx'
import FilterPresets from '../components/FilterPresets.jsx'
import DataTabs from '../components/DataTabs.jsx'
import ReportDownloadMenu from '../components/ReportDownloadMenu.jsx'
import ReportInsightsPanel from '../components/ReportInsightsPanel.jsx'
import { getStats, getChartData, exportAppeals } from '../api.js'
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

export default function DashboardPage({ runId, setRunId, processingStatus, dataVersion }) {
  const [stats, setStats] = useState(null)
  const [chartData, setChartData] = useState(null)
  const [filterOptions, setFilterOptions] = useState({ municipalities: [], categories: [] })
  const [filters, setFilters] = useState({})
  const [filterCount, setFilterCount] = useState(0)
  const [dataRefreshKey, setDataRefreshKey] = useState(0)
  // Активная вкладка таблиц («clusters» или «appeals») — поднята из DataTabs
  // чтобы donut «Тяжесть» строился по соответствующему срезу данных.
  const [activeTab, setActiveTab] = useState('clusters')

  // Полный (неотфильтрованный) список вариантов для выпадающих фильтров —
  // чтобы выбор одного значения не убирал остальные из списка.
  // Ретраи: один неудачный запрос (например, бэкенд перезапускался) не должен
  // оставлять списки пустыми до перезагрузки страницы.
  useEffect(() => {
    if (!runId) return
    let cancelled = false
    let timer = null
    const load = (attempt = 0) => {
      getChartData(runId, {})
        .then(d => {
          if (cancelled) return
          setFilterOptions({
            municipalities: (d.districts || []).map(x => x.municipality),
            categories: (d.categories || []).map(x => x.category),
          })
        })
        .catch(() => {
          if (!cancelled && attempt < 5) timer = setTimeout(() => load(attempt + 1), 3000)
        })
    }
    load()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [runId, dataVersion])

  const loadData = useCallback(async () => {
    if (!runId) return
    try {
      const chartScope = activeTab === 'appeals' ? 'appeals' : 'clusters'
      // Кросс-фильтрация: каждый график фильтруем по другим измерениям, но не
      // по своему собственному — так все его значения остаются видимыми и
      // кликабельными (можно выбрать несколько прямо на графике).
      const [s, dDist, dCat, dSev] = await Promise.all([
        getStats(runId),
        getChartData(runId, stripKey(filters, 'municipality'), chartScope),
        getChartData(runId, stripKey(filters, 'category'), chartScope),
        getChartData(runId, stripKey(filters, 'severity'), chartScope),
      ])
      setStats(s)
      setChartData({
        districts: dDist.districts || [],
        categories: dCat.categories || [],
        severity: dSev.severity || [],
        severity_appeals: dSev.severity_appeals || [],
      })
    } catch (e) {
      console.error('Failed to load data:', e)
    }
  }, [runId, filters, activeTab])

  useEffect(() => { loadData() }, [loadData])

  // Все 4 уровня тяжести = отсутствие фильтра: выбор всего набора
  // эквивалентен «без фильтра», поэтому фильтр сбрасывается.
  const SEVERITY_LEVELS = 4
  const isFullSeveritySet = (key, values) =>
    key === 'severity' && new Set(values).size >= SEVERITY_LEVELS

  // Каждый фильтр хранит массив выбранных значений (мультивыбор).
  const toggleFilter = (key, value) => {
    if (!value) return
    setFilters(prev => {
      const next = { ...prev }
      const current = Array.isArray(next[key]) ? next[key] : (next[key] ? [next[key]] : [])
      const exists = current.includes(value)
      const updated = exists ? current.filter(v => v !== value) : [...current, value]
      if (updated.length && !isFullSeveritySet(key, updated)) next[key] = updated
      else delete next[key]
      return next
    })
  }

  // Полная замена набора значений для одного фильтра (из выпадающего списка).
  const setFilterValues = (key, values) => {
    setFilters(prev => {
      const next = { ...prev }
      if (values && values.length && !isFullSeveritySet(key, values)) next[key] = values
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

  // Завершение обработки фиксирует App (dataVersion растёт) — обновляем срезы,
  // даже если завершение случилось, пока пользователь был на другой странице.
  useEffect(() => {
    if (!dataVersion) return
    loadData()
    setDataRefreshKey(key => key + 1)
  }, [dataVersion, loadData])

  return (
    <div className="flex gap-4 p-4 max-w-[1400px] mx-auto">
      {/* Left sidebar */}
      <div className="w-[280px] shrink-0 flex flex-col gap-4">
        <FileUpload runId={runId} setRunId={setRunId} />
        <ProcessingStatus runId={runId} status={processingStatus} />
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
              <ReportDownloadMenu runId={runId} />
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
                    className="flex items-center gap-1 px-2 py-0.5 bg-indigo-50 text-indigo-700 rounded-full text-xs font-medium hover:bg-indigo-100 transition"
                    title="Кликните чтобы убрать"
                  >
                    {formatFilterValue(k, val)} <GovIcon name="close" className="h-3 w-3 text-indigo-400" />
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

        {runId && <ComparisonPanel runId={runId} refreshKey={dataRefreshKey} />}

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
          <Charts data={chartData} filters={filters} onChartClick={handleChartClick} activeTab={activeTab} />
        )}

        {runId && (
          <ReportInsightsPanel
            runId={runId}
            refreshKey={dataRefreshKey}
            onSelectCategory={(category) => toggleFilter('category', category)}
            onSelectMunicipality={(municipality) => toggleFilter('municipality', municipality)}
          />
        )}

        {/* Timeline */}
        {runId && <Timeline runId={runId} filters={filters} refreshKey={dataRefreshKey} activeTab={activeTab} />}

        {/* Tables */}
        {runId && (
          <DataTabs runId={runId} filters={filters} refreshKey={dataRefreshKey} onExport={() => exportAppeals(runId, filters)} activeTab={activeTab} onTabChange={setActiveTab} />
        )}
      </div>
    </div>
  )
}
