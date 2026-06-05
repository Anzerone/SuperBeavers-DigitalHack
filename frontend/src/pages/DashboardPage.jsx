import { useState, useEffect, useCallback } from 'react'
import FileUpload from '../components/FileUpload.jsx'
import ProcessingStatus from '../components/ProcessingStatus.jsx'
import MetricCards from '../components/MetricCards.jsx'
import FilterBar from '../components/FilterBar.jsx'
import Charts from '../components/Charts.jsx'
import DataTabs from '../components/DataTabs.jsx'
import { getStats, getChartData, exportAppeals, downloadReport } from '../api.js'

export default function DashboardPage({ runId, setRunId }) {
  const [stats, setStats] = useState(null)
  const [chartData, setChartData] = useState(null)
  const [filters, setFilters] = useState({})
  const [filterCount, setFilterCount] = useState(0)

  const loadData = useCallback(async () => {
    if (!runId) return
    try {
      const [s, c] = await Promise.all([
        getStats(runId),
        getChartData(runId, filters),
      ])
      setStats(s)
      setChartData(c)
    } catch (e) {
      console.error('Failed to load data:', e)
    }
  }, [runId, filters])

  useEffect(() => { loadData() }, [loadData])

  const handleFilterChange = (key, value) => {
    setFilters(prev => {
      const next = { ...prev }
      if (value) next[key] = value
      else delete next[key]
      return next
    })
  }

  useEffect(() => {
    setFilterCount(Object.values(filters).filter(Boolean).length)
  }, [filters])

  const resetFilters = () => setFilters({})

  const handleChartClick = (key, value) => {
    handleFilterChange(key, filters[key] === value ? null : value)
  }

  return (
    <div className="flex gap-4 p-4 max-w-[1400px] mx-auto">
      {/* Left sidebar */}
      <div className="w-[280px] shrink-0 flex flex-col gap-4">
        <FileUpload runId={runId} setRunId={setRunId} />
        <ProcessingStatus runId={runId} onComplete={loadData} />
      </div>

      {/* Main area */}
      <div className="flex-1 flex flex-col gap-4 min-w-0">
        {/* Top bar: title + download buttons */}
        <div className="bg-white rounded-xl p-4 flex items-center justify-between shadow-sm">
          <div>
            <h2 className="text-lg font-semibold text-gray-800">Сводка по текущей загрузке</h2>
            <p className="text-xs text-gray-400">Фильтры графиков синхронизированы с таблицами и экспортом</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => runId && downloadReport(runId)}
              disabled={!runId}
              className="px-4 py-2 bg-[#0d7377] text-white rounded-lg text-sm font-medium hover:bg-[#0a5c5f] disabled:opacity-40"
            >
              Отчет
            </button>
          </div>
        </div>

        {/* Metrics */}
        {stats && <MetricCards stats={stats} />}

        {/* Filters */}
        <FilterBar
          filters={filters}
          filterCount={filterCount}
          chartData={chartData}
          onChange={handleFilterChange}
          onReset={resetFilters}
        />

        {/* Charts */}
        {chartData && (
          <Charts data={chartData} filters={filters} onChartClick={handleChartClick} />
        )}

        {/* Tables */}
        {runId && (
          <DataTabs runId={runId} filters={filters} onExport={() => {
            exportAppeals(runId, filters)
            downloadReport(runId)
          }} />
        )}
      </div>
    </div>
  )
}
