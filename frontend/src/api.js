import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export async function uploadFile(file) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post('/upload', form)
  return data
}

export async function startProcessing(filepath, filename) {
  const { data } = await api.post(`/process?filepath=${encodeURIComponent(filepath)}&filename=${encodeURIComponent(filename)}`)
  return data
}

export async function getStatus(runId) {
  const { data } = await api.get(`/processing/${runId}/status`)
  return data
}

export async function getStats(runId) {
  const { data } = await api.get(`/dashboard/stats/${runId}`)
  return data
}

export async function getTopDistricts(runId, n = 10) {
  const { data } = await api.get(`/dashboard/top/${runId}?n=${n}`)
  return data
}

export async function getChartData(runId, filters = {}) {
  const params = new URLSearchParams()
  if (filters.municipality) params.set('municipality', filters.municipality)
  if (filters.severity) params.set('severity', filters.severity)
  if (filters.category) params.set('category', filters.category)
  const { data } = await api.get(`/dashboard/charts/${runId}?${params}`)
  return data
}

export async function getClusters(runId, params = {}) {
  const { data } = await api.get(`/clusters/${runId}`, { params })
  return data
}

export async function getAppeals(runId, params = {}) {
  const { data } = await api.get(`/appeals/${runId}`, { params })
  return data
}

export async function exportAppeals(runId, filters = {}) {
  const params = new URLSearchParams(filters)
  const resp = await api.get(`/appeals/${runId}/export?${params}`, { responseType: 'blob' })
  const url = URL.createObjectURL(resp.data)
  const a = document.createElement('a')
  a.href = url
  a.download = 'appeals_export.xlsx'
  a.click()
  URL.revokeObjectURL(url)
}

export async function downloadReport(runId) {
  const resp = await api.get(`/reports/${runId}/excel`, { responseType: 'blob' })
  const url = URL.createObjectURL(resp.data)
  const a = document.createElement('a')
  a.href = url
  a.download = `report_${runId}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}

export async function sendChatMessage(runId, message) {
  const { data } = await api.post(`/chat?run_id=${runId}&message=${encodeURIComponent(message)}`)
  return data
}

export async function exportChatResult(runId) {
  const resp = await api.post(`/chat/export?run_id=${runId}`, {}, { responseType: 'blob' })
  const url = URL.createObjectURL(resp.data)
  const a = document.createElement('a')
  a.href = url
  a.download = 'chat_result.xlsx'
  a.click()
  URL.revokeObjectURL(url)
}
