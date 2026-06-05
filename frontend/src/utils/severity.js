export const SEVERITY_ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']

export const SEVERITY_LABELS = {
  CRITICAL: 'Критическая',
  HIGH: 'Высокая',
  MEDIUM: 'Средняя',
  LOW: 'Низкая',
}

export const SEVERITY_SHORT_LABELS = {
  CRITICAL: 'Критич.',
  HIGH: 'Высокая',
  MEDIUM: 'Средняя',
  LOW: 'Низкая',
}

export const SEVERITY_COLORS = {
  CRITICAL: '#ef4444',
  HIGH: '#f97316',
  MEDIUM: '#eab308',
  LOW: '#22c55e',
}

export const SEVERITY_STYLES = {
  CRITICAL: 'bg-red-100 text-red-700',
  HIGH: 'bg-orange-100 text-orange-700',
  MEDIUM: 'bg-yellow-100 text-yellow-700',
  LOW: 'bg-green-100 text-green-700',
}

export function formatSeverity(value, { short = false } = {}) {
  const labels = short ? SEVERITY_SHORT_LABELS : SEVERITY_LABELS
  return labels[value] || value || 'Не определено'
}
