import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { getResolvedAnalytics, getStats } from '../api.js'
import GovIcon from '../components/GovIcon.jsx'

const STATUS_COLORS = {
  resolved: '#10b981',
  open: '#f59e0b',
  processed: '#2563eb',
  problem: '#ef4444',
  neutral: '#64748b',
}

const CLOSURE_COLORS = {
  closed: '#10b981',
  warning: '#f97316',
  open: '#64748b',
  skipped: '#94a3b8',
  processed: '#2563eb',
}

function num(value) {
  return (value || 0).toLocaleString('ru')
}

function pct(part, total) {
  return total > 0 ? Math.round((part / total) * 1000) / 10 : 0
}

function monthLabel(value) {
  if (!value) return 'Без даты'
  return new Date(value).toLocaleDateString('ru', { month: 'short', year: '2-digit' })
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs shadow-lg">
      {label && <p className="mb-1 font-medium text-gray-800">{label}</p>}
      <div className="space-y-0.5">
        {payload.map((item) => (
          <div key={item.dataKey || item.name} className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full" style={{ background: item.color || item.fill }} />
            <span className="text-gray-500">{item.name}:</span>
            <span className="font-semibold text-gray-900">{num(item.value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function ChartFrame({ height, children }) {
  const ref = useRef(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    if (!ref.current) return undefined
    const element = ref.current
    const update = () => setWidth(Math.max(1, Math.floor(element.getBoundingClientRect().width)))
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  return (
    <div ref={ref} className="min-w-0" style={{ height }}>
      {width > 0 ? children(width, height) : null}
    </div>
  )
}

function Metric({ icon, label, value, hint, accent = 'border-gray-200', iconBg = 'bg-gray-100', valueClass = 'text-gray-800' }) {
  return (
    <div className={`rounded-xl border-l-4 bg-white p-4 shadow-sm ${accent}`}>
      <div className="mb-2 flex items-start justify-between">
        <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
        <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${iconBg}`}>
          <GovIcon name={icon} className="h-5 w-5 text-gray-700" />
        </span>
      </div>
      <p className={`text-2xl font-bold ${valueClass}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-gray-500">{hint}</p>}
    </div>
  )
}

function StatusDonut({ resolved, active, unsolvable }) {
  const total = resolved + active + (unsolvable || 0)
  const rows = [
    { name: 'Решено', value: resolved, fill: STATUS_COLORS.resolved },
    { name: 'Актуальные', value: active, fill: STATUS_COLORS.open },
    { name: 'Нерешаемые', value: unsolvable || 0, fill: STATUS_COLORS.neutral },
  ].filter(item => item.value > 0)

  return (
    <section className="min-w-0 rounded-xl bg-white p-4 shadow-sm">
      <h3 className="mb-3 text-sm font-semibold text-gray-700">Статус обращений</h3>
      <div className="relative h-[260px] min-w-0">
        <ChartFrame height={260}>
          {(width, height) => (
          <PieChart width={width} height={height}>
            <Pie data={rows} innerRadius={72} outerRadius={104} paddingAngle={3} dataKey="value" nameKey="name">
              {rows.map((item) => <Cell key={item.name} fill={item.fill} />)}
            </Pie>
            <Tooltip content={<ChartTooltip />} />
          </PieChart>
          )}
        </ChartFrame>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-2xl font-bold text-gray-800">{pct(resolved, total)}%</span>
          <span className="text-xs text-gray-400">решено</span>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        {rows.map((item) => (
          <div key={item.name} className="rounded-lg bg-gray-50 px-3 py-2">
            <div className="mb-1 flex items-center gap-2 text-gray-500">
              <span className="h-2 w-2 rounded-full" style={{ background: item.fill }} />
              {item.name}
            </div>
            <p className="text-lg font-bold text-gray-800">{num(item.value)}</p>
          </div>
        ))}
      </div>
    </section>
  )
}

function MunicipalityStack({ items }) {
  const rows = (items || []).slice(0, 10).map(item => ({
    ...item,
    name: item.municipality || 'Не указано',
  }))

  return (
    <section className="min-w-0 rounded-xl bg-white p-4 shadow-sm">
      <h3 className="mb-3 text-sm font-semibold text-gray-700">Районы: решено и актуальные</h3>
      <ChartFrame height={340}>
        {(width, height) => (
        <BarChart width={width} height={height} data={rows} layout="vertical" margin={{ top: 4, right: 18, bottom: 4, left: 18 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#eef2f7" />
          <XAxis type="number" tick={{ fontSize: 11, fill: '#64748b' }} />
          <YAxis
            type="category"
            dataKey="name"
            width={130}
            tick={{ fontSize: 11, fill: '#64748b' }}
            tickFormatter={(value) => String(value).length > 18 ? `${String(value).slice(0, 18)}…` : value}
          />
          <Tooltip content={<ChartTooltip />} />
          <Bar name="Решено" dataKey="resolved" stackId="status" fill={STATUS_COLORS.resolved} radius={[3, 0, 0, 3]} />
          <Bar name="Актуальные" dataKey="open" stackId="status" fill={STATUS_COLORS.open} radius={[0, 3, 3, 0]} />
        </BarChart>
        )}
      </ChartFrame>
    </section>
  )
}

function CategoryBars({ items }) {
  const rows = (items || []).slice(0, 8).map(item => ({
    ...item,
    name: item.category || 'Другое',
  }))

  return (
    <section className="min-w-0 rounded-xl bg-white p-4 shadow-sm">
      <h3 className="mb-3 text-sm font-semibold text-gray-700">Категории: решено и актуальные</h3>
      <ChartFrame height={300}>
        {(width, height) => (
        <BarChart width={width} height={height} data={rows} margin={{ top: 4, right: 8, bottom: 54, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#eef2f7" />
          <XAxis
            dataKey="name"
            angle={-32}
            textAnchor="end"
            height={72}
            interval={0}
            tick={{ fontSize: 10, fill: '#64748b' }}
            tickFormatter={(value) => String(value).length > 14 ? `${String(value).slice(0, 14)}…` : value}
          />
          <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
          <Tooltip content={<ChartTooltip />} />
          <Bar name="Решено" dataKey="resolved" stackId="status" fill={STATUS_COLORS.resolved} radius={[0, 0, 0, 0]} />
          <Bar name="Актуальные" dataKey="open" stackId="status" fill={STATUS_COLORS.open} radius={[4, 4, 0, 0]} />
        </BarChart>
        )}
      </ChartFrame>
    </section>
  )
}

function TimelineLine({ items }) {
  const rows = (items || []).map(item => ({
    ...item,
    label: monthLabel(item.date),
  }))

  return (
    <section className="min-w-0 rounded-xl bg-white p-4 shadow-sm">
      <h3 className="mb-3 text-sm font-semibold text-gray-700">Динамика закрытия</h3>
      <ChartFrame height={300}>
        {(width, height) => (
        <LineChart width={width} height={height} data={rows} margin={{ top: 8, right: 14, bottom: 10, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#64748b' }} />
          <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
          <Tooltip content={<ChartTooltip />} />
          <Line
            name="Закрыто"
            type="monotone"
            dataKey="count"
            stroke="#8b5cf6"
            strokeWidth={3}
            dot={{ r: 3, fill: '#8b5cf6' }}
            activeDot={{ r: 5 }}
          />
        </LineChart>
        )}
      </ChartFrame>
    </section>
  )
}

function StatusSummary({ title, description, items, total, percentLabel = 'от всех записей' }) {
  const max = Math.max(...(items || []).map(item => item.count || 0), 1)

  return (
    <section className="min-w-0 rounded-xl bg-white p-4 shadow-sm">
      <div className="mb-3">
        <h3 className="text-sm font-semibold text-gray-700">{title}</h3>
        {description && <p className="mt-1 text-xs text-gray-400">{description}</p>}
      </div>
      <div className="space-y-3">
        {(items || []).map((item) => {
          const color = CLOSURE_COLORS[item.tone] || CLOSURE_COLORS.open
          return (
            <div key={item.status} className="rounded-lg bg-gray-50 px-3 py-2">
              <div className="mb-1 flex items-center justify-between gap-3 text-sm">
                <span className="truncate font-medium text-gray-700" title={item.status}>{item.status}</span>
                <span className="shrink-0 font-semibold text-gray-700">{num(item.count)}</span>
              </div>
              {item.detail && <p className="mb-2 text-xs text-gray-400">{item.detail}</p>}
              <div className="h-2 overflow-hidden rounded-full bg-white">
                <div className="h-full rounded-full" style={{ width: `${Math.max(3, item.count / max * 100)}%`, background: color }} />
              </div>
              <p className="mt-1 text-[11px] text-gray-400">{pct(item.count, total)}% {percentLabel}</p>
            </div>
          )
        })}
      </div>
    </section>
  )
}

export default function ResolvedPage({ runId, processingStatus, dataVersion }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const isProcessing = processingStatus?.status === 'running' || processingStatus?.status === 'pending'

  useEffect(() => {
    if (!runId) {
      setData(null)
      return
    }
    let cancelled = false
    setLoading(true)
    Promise.all([getResolvedAnalytics(runId), getStats(runId)])
      .then(([next, stats]) => {
        if (!cancelled) {
          const problemCount = stats?.problem_count ?? next.active_problem_count ?? next.problem_count ?? 0
          setData({ ...next, active_problem_count: problemCount, problem_count: problemCount })
        }
      })
      .catch(() => { if (!cancelled) setData(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [runId, dataVersion])

  const processedTotal = data?.processed_records || 0
  const activeTotal = data?.active_records || data?.open_count || processedTotal
  const activeProblemCount = data?.active_problem_count ?? data?.problem_count ?? 0
  const rawTotal = data?.raw_records || processedTotal

  const coverage = useMemo(() => {
    if (!data) return []
    return [
      ['В исходном файле', rawTotal, STATUS_COLORS.neutral],
      ['Актуальных', activeTotal, STATUS_COLORS.processed],
      ['Проблемных', activeProblemCount, STATUS_COLORS.problem],
    ]
  }, [data, rawTotal, activeTotal, activeProblemCount])

  if (!runId) {
    return (
      <div className="flex h-[80vh] items-center justify-center">
        <p className="text-gray-400">Сначала загрузите и обработайте файл на дашборде</p>
      </div>
    )
  }

  if (isProcessing && !data?.processed_records) {
    return (
      <div className="flex h-[80vh] flex-col items-center justify-center gap-2">
        <GovIcon name="spinner" className="h-6 w-6 animate-spin text-[#2B3990]" />
        <p className="text-gray-400">
          {processingStatus?.status === 'pending'
            ? `Файл в очереди на обработку${processingStatus.queue_position ? ` (позиция ${processingStatus.queue_position})` : ''} — аналитика появится автоматически`
            : 'Файл обрабатывается — аналитика появится автоматически'}
        </p>
      </div>
    )
  }

  if (loading || !data) {
    return (
      <div className="flex h-[80vh] items-center justify-center">
        <p className="text-gray-400">Загрузка аналитики...</p>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl p-4">
      <div className="mb-4 rounded-xl bg-white p-5 shadow-sm">
        <h2 className="flex items-center gap-2 text-lg font-semibold text-gray-800">
          <GovIcon name="check" className="h-5 w-5 text-[#2B3990]" />
          Решенные проблемы
        </h2>
        <div className="mt-4 grid gap-2 md:grid-cols-3">
          {coverage.map(([label, value, color]) => (
            <div key={label} className="rounded-lg bg-gray-50 px-3 py-2">
              <div className="mb-1 flex items-center gap-2 text-xs text-gray-500">
                <span className="h-2 w-2 rounded-full" style={{ background: color }} />
                {label}
              </div>
              <p className="text-xl font-bold text-gray-800">{num(value)}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="mb-4 grid gap-3 md:grid-cols-3 xl:grid-cols-5">
        <Metric
          icon="check"
          label="Решено"
          value={num(data.resolved_count)}
          hint={
            data.resolved_prefiltered > 0
              ? `${data.resolution_rate || 0}% от всех записей, из них ${num(data.resolved_prefiltered)} закрыто до анализа`
              : `${data.resolution_rate || 0}% от всех записей`
          }
          accent="border-emerald-300"
          iconBg="bg-emerald-50"
          valueClass="text-emerald-700"
        />
        <Metric
          icon="clock"
          label="Актуальные"
          value={num(activeTotal)}
          hint="итог пустой, открыт, отложен или не решен"
          accent="border-orange-300"
          iconBg="bg-orange-50"
          valueClass="text-orange-600"
        />
        <Metric
          icon="close"
          label="Нерешаемые"
          value={num(data.unsolvable_count || 0)}
          hint={`${pct(data.unsolvable_count || 0, rawTotal)}% от всех записей`}
          accent="border-gray-300"
          iconBg="bg-gray-100"
          valueClass="text-gray-600"
        />
        <Metric
          icon="warning"
          label="Проблемных"
          value={num(activeProblemCount)}
          hint={`${pct(activeProblemCount, activeTotal)}% от актуальных`}
          accent="border-red-300"
          iconBg="bg-red-50"
          valueClass="text-red-600"
        />
        <Metric
          icon="clock"
          label="Средний срок"
          value={data.avg_resolution_days ? `${data.avg_resolution_days} дн.` : 'нет данных'}
          hint="по строкам с закрытым итогом и датами"
          accent="border-indigo-300"
          iconBg="bg-indigo-50"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <StatusDonut
          resolved={data.resolved_count || 0}
          active={activeTotal}
          unsolvable={data.unsolvable_count || 0}
        />
        <MunicipalityStack items={data.by_municipality || []} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <CategoryBars items={data.by_category || []} />
        <TimelineLine items={data.timeline || []} />
      </div>
    </div>
  )
}
