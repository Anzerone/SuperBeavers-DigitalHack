import { useState, useEffect } from 'react'
import { getClusters, getAppeals } from '../api.js'
import SimilarModal from './SimilarModal.jsx'
import { SEVERITY_STYLES, formatSeverity } from '../utils/severity.js'
import GovIcon from './GovIcon.jsx'

function displayCategory(item) {
  return item?.category || item?.group_name || 'Другое'
}

function displaySeverity(item) {
  return item?.severity || item?.cluster_severity || 'MEDIUM'
}

// Убираем дублирование: имя кластера часто начинается с названия категории
// ("Дороги: ..."), а категория уже показана в отдельном столбце.
function cleanClusterName(name, category) {
  let result = (name || '').trim()
  if (category) {
    const prefix = `${category}:`
    if (result.toLowerCase().startsWith(prefix.toLowerCase())) {
      result = result.slice(prefix.length).trim()
    }
  }
  result = result.replace(/^["'«»\s]+/, '').replace(/[\s.…]+$/, '')
  return result || name || ''
}

export default function DataTabs({ runId, filters, refreshKey = 0, onExport, activeTab, onTabChange }) {
  const [internalTab, setInternalTab] = useState(activeTab || 'clusters')
  const [clusters, setClusters] = useState(null)
  const [appeals, setAppeals] = useState(null)
  const [clusterFilter, setClusterFilter] = useState(null)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [similarFor, setSimilarFor] = useState(null)
  const tab = activeTab || internalTab

  const changeTab = (nextTab) => {
    setInternalTab(nextTab)
    onTabChange?.(nextTab)
  }

  useEffect(() => {
    setPage(1)
    setClusterFilter(null)
  }, [runId, filters])

  // Load clusters
  useEffect(() => {
    if (!runId || tab !== 'clusters') return
    let cancelled = false
    setClusters(null)
    getClusters(runId, { ...filters, search: search || undefined, page, page_size: 15 })
      .then(data => {
        if (!cancelled) setClusters(data)
      })
      .catch(error => {
        if (!cancelled) console.error(error)
      })
    return () => {
      cancelled = true
    }
  }, [runId, tab, filters, page, search, refreshKey])

  // Load appeals
  useEffect(() => {
    if (!runId || tab !== 'appeals') return
    let cancelled = false
    setAppeals(null)
    const params = { ...filters, page, page_size: 20, search: search || undefined }
    if (clusterFilter) params.cluster_id = clusterFilter
    getAppeals(runId, params)
      .then(data => {
        if (!cancelled) setAppeals(data)
      })
      .catch(error => {
        if (!cancelled) console.error(error)
      })
    return () => {
      cancelled = true
    }
  }, [runId, tab, filters, clusterFilter, page, search, refreshKey])

  const goToAppeals = (clusterId) => {
    setClusterFilter(clusterId)
    setPage(1)
    changeTab('appeals')
  }

  return (
    <div className="bg-white rounded-xl shadow-sm overflow-hidden">
      {/* Tab header */}
      <div className="flex items-center justify-between px-4 pt-3 pb-0 border-b">
        <div className="flex gap-1 items-center">
          <button
            onClick={() => { changeTab('clusters'); setClusterFilter(null); setPage(1) }}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition ${
              tab === 'clusters' ? 'border-[#0d7377] text-[#0d7377]' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            Кластеры проблем
          </button>
          <button
            onClick={() => { changeTab('appeals'); setPage(1) }}
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
              Скачать
            </button>
          )}
        </div>
        <div className="flex items-center gap-2 pb-2">
          {clusterFilter && (
            <button
              onClick={() => { setClusterFilter(null); setPage(1) }}
              className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-[#0d7377]/10 text-[#0d7377] rounded-full"
            >
              Кластер <GovIcon name="close" className="h-3 w-3" />
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
      {tab === 'clusters' && (
        <div>
          {!clusters ? (
            <div className="px-4 py-10 text-center text-gray-400 text-sm">Загрузка кластеров…</div>
          ) : clusters.items?.length === 0 ? (
            <div className="px-4 py-10 text-center text-gray-400 text-sm">Кластеры проблем не найдены</div>
          ) : (
            <>
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
                      <td className="px-4 py-3 max-w-[300px] truncate" title={cleanClusterName(c.cluster_name, c.category)}>{cleanClusterName(c.cluster_name, c.category)}</td>
                      <td className="px-4 py-3 text-gray-600">{c.municipality}</td>
                      <td className="px-4 py-3 text-gray-600">{c.category}</td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${SEVERITY_STYLES[displaySeverity(c)] || 'bg-gray-100'}`}>
                          {formatSeverity(displaySeverity(c), { short: true })}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right font-medium">{c.appeal_count}</td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => goToAppeals(c.id)}
                          className="text-gray-400 hover:text-[#0d7377] transition"
                          title="Показать обращения"
                          aria-label="Показать обращения"
                        >
                          <GovIcon name="arrowRight" className="h-4 w-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pagination data={clusters} page={page} setPage={setPage} />
            </>
          )}
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
                <th className="px-2 py-2 w-10"></th>
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
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${SEVERITY_STYLES[displaySeverity(a)] || 'bg-gray-100'}`}>
                      {formatSeverity(displaySeverity(a), { short: true })}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-600">{displayCategory(a)}</td>
                  <td className="px-4 py-3 text-gray-500">{a.outcome || ''}</td>
                  <td className="px-2 py-3">
                    <button
                      onClick={() => setSimilarFor(a)}
                      title="Найти похожие обращения"
                      className="text-gray-400 hover:text-teal-700 text-sm"
                    >
                      <GovIcon name="search" className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination data={appeals} page={page} setPage={setPage} />
        </div>
      )}

      {similarFor && (
        <SimilarModal appeal={similarFor} onClose={() => setSimilarFor(null)} />
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
          <GovIcon name="arrowLeft" className="h-4 w-4" />
        </button>
        <span className="px-3 py-1 text-sm text-gray-600">
          {page} / {data.pages}
        </span>
        <button
          onClick={() => setPage(Math.min(data.pages, page + 1))}
          disabled={page >= data.pages}
          className="px-3 py-1 text-sm border rounded hover:bg-gray-50 disabled:opacity-40"
        >
          <GovIcon name="arrowRight" className="h-4 w-4" />
        </button>
      </div>
    </div>
  )
}
