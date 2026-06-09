import { useMemo } from 'react'
import { formatSeverity } from '../utils/severity.js'
import GovIcon from './GovIcon.jsx'

const SEV_PALETTE = {
  CRITICAL: '#ef4444',
  HIGH: '#f97316',
  MEDIUM: '#eab308',
  LOW: '#22c55e',
  UNKNOWN: '#9ca3af',
}

const SENT_PALETTE = {
  DESPERATE: '#7c3aed',
  ANGRY: '#dc2626',
  NEUTRAL: '#9ca3af',
  CALM: '#10b981',
}

const SENT_LABEL = {
  DESPERATE: 'отчаяние',
  ANGRY: 'гнев',
  NEUTRAL: 'нейтрально',
  CALM: 'спокойно',
}

function MiniBar({ breakdown }) {
  const entries = Object.entries(breakdown || {})
  const total = entries.reduce((s, [, v]) => s + v, 0)
  if (!total) return null
  return (
    <div className="flex h-1.5 rounded-full overflow-hidden mt-2">
      {entries.map(([sev, count]) => (
        <div
          key={sev}
          title={`${formatSeverity(sev)}: ${count}`}
          style={{ width: `${(count / total) * 100}%`, background: SEV_PALETTE[sev] || '#9ca3af' }}
        />
      ))}
    </div>
  )
}

function TopList({ items, color = 'text-gray-700' }) {
  if (!items?.length) return null
  return (
    <div className="space-y-0.5 mt-2">
      {items.slice(0, 3).map((item, i) => (
        <div key={i} className="flex justify-between text-xs">
          <span className={`truncate pr-2 ${color}`} title={item.name}>{item.name || '—'}</span>
          <span className="font-medium text-gray-500 shrink-0">{item.count?.toLocaleString('ru')}</span>
        </div>
      ))}
    </div>
  )
}

function SentimentBar({ breakdown }) {
  const entries = Object.entries(breakdown || {})
  const total = entries.reduce((s, [, v]) => s + v, 0)
  if (!total) return null
  return (
    <div className="mt-2">
      <div className="flex h-1.5 rounded-full overflow-hidden">
        {['DESPERATE', 'ANGRY', 'NEUTRAL', 'CALM'].map(k => {
          const v = breakdown[k] || 0
          if (!v) return null
          return (
            <div
              key={k}
              title={`${SENT_LABEL[k]}: ${v}`}
              style={{ width: `${(v / total) * 100}%`, background: SENT_PALETTE[k] }}
            />
          )
        })}
      </div>
      <div className="flex justify-between mt-1">
        {[
          ['DESPERATE', 'отчаяние'],
          ['ANGRY', 'гнев'],
          ['NEUTRAL', 'нейтральное'],
          ['CALM', 'спокойно'],
        ].map(([k, label]) => (
          <div key={k} className="flex flex-col items-center gap-0.5">
            <span className="w-3 h-1.5 rounded-full" style={{ background: SENT_PALETTE[k] }} />
            <span className="text-[10px] text-gray-400">{label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function formatSignedNumber(value) {
  if (value == null) return '—'
  const abs = Math.abs(value).toLocaleString('ru')
  if (value > 0) return `+${abs}`
  if (value < 0) return `-${abs}`
  return '0'
}

function formatGrowthHint(growth) {
  if (!growth?.has_previous) return 'Нет предыдущей обработки'
  const previous = (growth.previous_problem_count ?? 0).toLocaleString('ru')
  if (growth.delta_percent == null) return `Было ${previous}`
  const pct = Math.abs(growth.delta_percent).toLocaleString('ru', { maximumFractionDigits: 1 })
  if (growth.direction === 'up') return `+${pct}% к прошлой обработке`
  if (growth.direction === 'down') return `-${pct}% к прошлой обработке`
  return `Без изменений, было ${previous}`
}

export default function MetricCards({ stats }) {
  const sentimentBreakdown = stats.sentiment_breakdown || {}
  const angryCount = (sentimentBreakdown.ANGRY || 0) + (sentimentBreakdown.DESPERATE || 0)
  const totalSent = Object.values(sentimentBreakdown).reduce((s, v) => s + v, 0)
  const angryPct = totalSent > 0 ? Math.round(angryCount / totalSent * 100) : 0
  const problemGrowth = stats.problem_growth || {}
  const hasProblemGrowth = Boolean(problemGrowth.has_previous)
  const growthDirection = problemGrowth.direction || 'none'
  const growthColor = growthDirection === 'up'
    ? 'text-red-600'
    : growthDirection === 'down'
      ? 'text-emerald-600'
      : 'text-gray-800'
  const growthAccent = !hasProblemGrowth
    ? 'border-gray-200'
    : growthDirection === 'up'
      ? 'border-red-300'
      : growthDirection === 'down'
        ? 'border-emerald-300'
        : 'border-gray-300'
  const growthIconBg = !hasProblemGrowth
    ? 'bg-gray-100'
    : growthDirection === 'up'
      ? 'bg-red-50'
      : growthDirection === 'down'
        ? 'bg-emerald-50'
        : 'bg-gray-100'

  const cards = useMemo(() => [
    {
      key: 'raw',
      label: 'В исходном файле',
      value: stats.raw_records?.toLocaleString('ru') || '0',
      accent: 'border-gray-200',
      icon: 'download',
      iconBg: 'bg-gray-100',
    },
    {
      key: 'problems',
      label: 'Проблемных обращений',
      value: stats.problem_count?.toLocaleString('ru') || '0',
      hint: `${stats.problem_percent ?? 0}% от актуальных`,
      accent: 'border-orange-300',
      icon: 'warning',
      iconBg: 'bg-orange-50',
      footer: <MiniBar breakdown={stats.severity_breakdown} />,
    },
    {
      key: 'problem-growth',
      label: 'Прирост проблем',
      value: hasProblemGrowth ? formatSignedNumber(problemGrowth.delta || 0) : '—',
      hint: formatGrowthHint(problemGrowth),
      accent: growthAccent,
      icon: 'trend',
      iconBg: growthIconBg,
      valueClass: growthColor,
    },
    {
      key: 'severe',
      label: 'Критич. + высокие',
      value: stats.severe_count?.toLocaleString('ru') || '0',
      hint: `${stats.severe_percent ?? 0}% проблемных`,
      accent: 'border-red-300',
      icon: 'priority',
      iconBg: 'bg-red-50',
      valueClass: 'text-red-600',
    },
    {
      key: 'municipalities',
      label: 'Муниципалитетов',
      value: stats.municipality_count?.toLocaleString('ru') || '0',
      hint: `~${stats.avg_per_municipality ?? 0} обращ./район`,
      accent: 'border-indigo-200',
      icon: 'building',
      iconBg: 'bg-indigo-50',
      footer: <TopList items={stats.top_municipalities} />,
    },
    {
      key: 'categories',
      label: 'Категорий',
      value: stats.distinct_categories?.toLocaleString('ru') || '0',
      hint: 'Группа тем',
      accent: 'border-emerald-200',
      icon: 'folder',
      iconBg: 'bg-emerald-50',
      footer: <TopList items={stats.top_categories} />,
    },
    {
      key: 'clusters',
      label: 'Кластеров проблем',
      value: stats.cluster_count?.toLocaleString('ru') || '0',
      hint: stats.avg_cluster_size ? `~${stats.avg_cluster_size} обращений в кластере` : '',
      accent: 'border-teal-300',
      icon: 'cluster',
      iconBg: 'bg-teal-50',
      footer: stats.largest_cluster ? (
        <div className="text-xs text-gray-500 mt-2 line-clamp-2" title={stats.largest_cluster.name}>
          <span className="font-medium">Крупнейший:</span>{' '}
          {stats.largest_cluster.count?.toLocaleString('ru')} —{' '}
          {stats.largest_cluster.municipality}
        </div>
      ) : null,
    },
    {
      key: 'sentiment',
      label: 'Эмоциональный фон',
      value: `${angryPct}%`,
      hint: `гнев + отчаяние из ${totalSent.toLocaleString('ru')} проблем`,
      accent: 'border-purple-300',
      icon: 'mood',
      iconBg: 'bg-purple-50',
      valueClass: angryPct > 30 ? 'text-purple-700' : 'text-gray-800',
      footer: <SentimentBar breakdown={sentimentBreakdown} />,
    },
  ], [stats, sentimentBreakdown, angryCount, totalSent, angryPct, hasProblemGrowth, problemGrowth, growthAccent, growthIconBg, growthColor])

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
      {cards.map((c) => (
        <div
          key={c.key}
          className={`bg-white rounded-xl p-4 shadow-sm border-l-4 ${c.accent} transition hover:shadow-md hover:-translate-y-0.5`}
        >
          <div className="flex items-start justify-between mb-2">
            <p className="text-xs text-gray-500 uppercase tracking-wide leading-tight">{c.label}</p>
            <span className={`w-8 h-8 rounded-lg flex items-center justify-center text-base ${c.iconBg}`}>
              <GovIcon name={c.icon} className="h-5 w-5 text-gray-700" />
            </span>
          </div>
          <p className={`text-2xl font-bold ${c.valueClass || 'text-gray-800'}`}>{c.value}</p>
          {c.hint && <p className="text-xs text-gray-500 mt-1">{c.hint}</p>}
          {c.footer}
        </div>
      ))}
    </div>
  )
}
