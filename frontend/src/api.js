import axios from 'axios'

const api = axios.create({ baseURL: '/api' })
const TOKEN_KEY = 'authToken'

export function getAuthToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setAuthToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

api.interceptors.request.use((config) => {
  const token = getAuthToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401) {
      setAuthToken(null)
    }
    return Promise.reject(error)
  }
)

export async function login(username, password) {
  const { data } = await api.post('/auth/login', { username, password })
  setAuthToken(data.access_token)
  return data.user
}

export async function getCurrentUser() {
  const { data } = await api.get('/auth/me')
  return data
}

export function logout() {
  setAuthToken(null)
}

// Мультивыбор: значения фильтров могут быть массивами — склеиваем в строку
// через разделитель, которого заведомо нет в данных (запятая встречается
// в самих значениях, напр. "Омская область, другое"). Бэкенд разбирает обратно.
export const FILTER_SEP = '\u001f'

function flattenFilters(params = {}) {
  const out = {}
  for (const [k, v] of Object.entries(params)) {
    if (v == null) continue
    if (Array.isArray(v)) {
      if (v.length) out[k] = v.join(FILTER_SEP)
    } else {
      out[k] = v
    }
  }
  return out
}

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

export async function getLatestRun() {
  const { data } = await api.get('/processing/latest')
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

export async function getChartData(runId, filters = {}, scope = 'clusters') {
  const params = new URLSearchParams({ ...flattenFilters(filters), scope })
  const { data } = await api.get(`/dashboard/charts/${runId}?${params}`)
  return data
}

export async function getTimeline(runId, filters = {}, granularity = 'week', scope = 'appeals') {
  const params = new URLSearchParams({ granularity, scope, ...flattenFilters(filters) })
  const { data } = await api.get(`/dashboard/timeline/${runId}?${params}`)
  return data
}

export async function getFilterPresets() {
  const { data } = await api.get('/presets')
  return data.presets || []
}

export async function saveFilterPreset(name, filters) {
  const { data } = await api.post('/presets', { name, filters })
  return data
}

export async function deleteFilterPreset(presetId) {
  const { data } = await api.delete(`/presets/${presetId}`)
  return data
}

export async function getClusters(runId, params = {}) {
  const { data } = await api.get(`/clusters/${runId}`, { params: flattenFilters(params) })
  return data
}

export async function getAppeals(runId, params = {}) {
  const { data } = await api.get(`/appeals/${runId}`, { params: flattenFilters(params) })
  return data
}

export async function exportAppeals(runId, filters = {}) {
  const params = new URLSearchParams(flattenFilters(filters))
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

export async function getSimilar(appealId, k = 10) {
  const { data } = await api.get(`/appeals/${appealId}/similar?k=${k}`)
  return data
}

export async function getAlerts(runId) {
  const { data } = await api.get(`/alerts/${runId}`)
  return data
}

export async function getLearningQueue(runId, limit = 15) {
  const { data } = await api.get(`/learning/${runId}/queue?limit=${limit}`)
  return data
}

export async function getLearningStats() {
  const { data } = await api.get('/learning/stats')
  return data
}

export async function annotateAppeal(payload) {
  const { data } = await api.post('/learning/annotate', payload)
  return data
}

export async function sendChatMessage(runId, message) {
  const { data } = await api.post(`/chat?run_id=${runId}&message=${encodeURIComponent(message)}`)
  return data
}

export async function resetChat(runId) {
  const { data } = await api.post(`/chat/reset?run_id=${runId}`)
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
