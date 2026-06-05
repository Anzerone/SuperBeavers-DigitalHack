import { useState, useEffect } from 'react'
import { getClusters, getAppeals } from '../api.js'
import { SEVERITY_STYLES, formatSeverity } from '../utils/severity.js'

export default function DataTabs({ runId, filters, onExport }) {
  const [tab, setTab] = useState('clusters')
  const [clusters, setClusters] = useState(null)
  const [appeals, setAppeals] = useState(null)
  const [clusterFilter, setClusterFilter] = useState(null)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')

  // Load clusters
  useEffect(() => {
    if (!runId || tab !== 'clusters') return
    getClusters(runId, { ...filters, search: search || undefined, page, page_size: 15 })
      .then(setClusters).catch(console.error)
  }, [runId, tab, filters, page, search])

  // Load appeals
  useEffect(() => {
    if (!runId || tab !== 'appeals') return
    const params = { ...filters, page, page_size: 20, search: search || undefined }
    if (clusterFilter) params.cluster_id = clusterFilter
    getAppeals(runId, params).then(setAppeals).catch(console.error)
  }, [runId, tab, filters, clusterFilter, page, search])

  const goToAppeals = (clusterId) => {
    setClusterFilter(clusterId)
    setPage(1)
    setTab('appeals')
  }

  return (
    <div className="bg-white rounded-xl shadow-sm overflow-hidden">
      {/* Tab header */}
      <div className="flex items-center justify-between px-4 pt-3 pb-0 border-b">
        <div className="flex gap-1 items-center">
          <button
            onClick={() => { setTab('clusters'); setClusterFilter(null); setPage(1) }}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition ${
              tab === 'clusters' ? 'border-[#0d7377] text-[#0d7377]' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Кластеры проблем
          </button>
          <button
            onClick={() => { setTab('appeals'); setPage(1) }}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition ${
              tab === 'appeals' ? 'border-[#0d7377] text-[#0d7377]' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Обращения
          </button>
          {onExport && (
            <button
              onClick={onExport}
              className="ml-3 px-3 py-1.5 bg-white border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50"
            >
              Загрузить
            </button>
          )}
        </div>
        <div className="flex items-center gap-2 pb-2">
          {clusterFilter && (
            <button
              onClick={() => { setClusterFilter(null); setPage(1) }}
              className="text-xs px-2 py-1 bg-[#0d7377]/10 text-[#0d7377] rounded-full"
            >
              Кластер ✕
            </button>
          )}
          <input
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1) }}
            placeholder="Поиск по району или теме"
            className="text-sm border border-gray-200 rounded-lg px-3 py-1.5 w-56"
          />
        </div>
      </div>

      {/* Clusters tab */}
      {tab === 'clusters' && clusters && (
        <div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-4 py-2 font-medium text-gray-500">ПРОБЛЕМА</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">РАЙОН</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">КАТЕГОРИЯ</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">ТЯЖЕСТЬ</th>
                <th className="text-right px-4 py-2 font-medium text-gray-500">ОБРАЩЕНИЙ</th>
                <th className="px-4 py-2 w-10"></th>
              </tr>
            </thead>
            <tbody>
              {clusters.items?.map(c => (
                <tr key={c.id} className="border-t hover:bg-gray-50">
                  <td className="px-4 py-3 max-w-[300px] truncate">{c.cluster_name}</td>
                  <td className="px-4 py-3 text-gray-600">{c.municipality}</td>
                  <td className="px-4 py-3 text-gray-600">{c.category}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${SEVERITY_STYLES[c.severity] || 'bg-gray-100'}`}>
                      {formatSeverity(c.severity, { short: true })}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right font-medium">{c.appeal_count}</td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => goToAppeals(c.id)}
                      className="text-gray-400 hover:text-[#0d7377] transition"
                    >
                      →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination data={clusters} page={page} setPage={setPage} />
        </div>
      )}

      {/* Appeals tab */}
      {tab === 'appeals' && appeals && (
        <div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-4 py-2 font-medium text-gray-500">ТЕКСТ ОБРАЩЕНИЯ</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">РАЙОН</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">ТЯЖЕСТЬ</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">КАТЕГОРИЯ</th>
                <th className="text-left px-4 py-2 font-medium text-gray-500">ИТОГ</th>
              </tr>
            </thead>
            <tbody>
              {appeals.items?.map(a => (
                <tr key={a.id} className="border-t hover:bg-gray-50">
                  <td className="px-4 py-3 max-w-[400px]">
                    <p className="truncate text-gray-800">{a.incident_text}</p>
                  </td>
                  <td className="px-4 py-3 text-gray-600 whitespace-nowrap">{a.municipality}</td>
                  <td className="px-4 py-3">
                    {a.severity && (
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${SEVERITY_STYLES[a.severity] || 'bg-gray-100'}`}>
                        {formatSeverity(a.severity, { short: true })}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-600">{a.category}</td>
                  <td className="px-4 py-3 text-gray-500">{a.outcome || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination data={appeals} page={page} setPage={setPage} />
        </div>
      )}
    </div>
  )
}

function Pagination({ data, page, setPage }) {
  if (!data || data.pages <= 1) return null
  return (
    <div className="flex items-center justify-between px-4 py-3 border-t">
      <span className="text-xs text-gray-400">
        Показано {(page - 1) * data.page_size + 1}-{Math.min(page * data.page_size, data.total)} из {data.total}
      </span>
      <div className="flex gap-1">
        <button
          onClick={() => setPage(Math.max(1, page - 1))}
          disabled={page <= 1}
          className="px-3 py-1 text-sm border rounded hover:bg-gray-50 disabled:opacity-40"
        >
          ←
        </button>
        <span className="px-3 py-1 text-sm text-gray-600">
          {page} / {data.pages}
        </span>
        <button
          onClick={() => setPage(Math.min(data.pages, page + 1))}
          disabled={page >= data.pages}
          className="px-3 py-1 text-sm border rounded hover:bg-gray-50 disabled:opacity-40"
        >
          →
        </button>
      </div>
    </div>
  )
}
